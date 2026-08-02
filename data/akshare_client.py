"""
AKShare数据客户端
基于AKShare的股票数据获取，复用并优化自原akshare.py
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Union
from pathlib import Path
import json

import pandas as pd
import numpy as np
import akshare as ak

from config import config

logger = logging.getLogger(__name__)


class AKShareClient:
    """
    AKShare统一数据客户端
    提供标准化的A股数据接口
    """

    _instance = None

    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.connected = True
        self._cache: Dict[str, Any] = {}
        self._cache_time: Dict[str, datetime] = {}
        self._cache_ttl = 60  # 缓存60秒

        # 初始化股票列表缓存
        self._stock_list_cache: Optional[pd.DataFrame] = None
        self._stock_list_cache_time: Optional[datetime] = None

        logger.info("✅ AKShare客户端初始化完成")

    def _get_cache(self, key: str) -> Optional[Any]:
        """获取缓存数据"""
        if key in self._cache and key in self._cache_time:
            if datetime.now() - self._cache_time[key] < timedelta(seconds=self._cache_ttl):
                return self._cache[key]
        return None

    def _set_cache(self, key: str, value: Any):
        """设置缓存数据"""
        self._cache[key] = value
        self._cache_time[key] = datetime.now()

    @staticmethod
    def normalize_code(code: str) -> str:
        """
        标准化股票代码为6位数字

        Args:
            code: 原始股票代码（支持 sh600000, sz000001, 600000 等格式）

        Returns:
            6位数字代码
        """
        code = str(code).strip().lower()

        # 移除前缀
        for prefix in ['sh', 'sz', 'bj']:
            if code.startswith(prefix):
                code = code[2:]

        # 移除后缀
        if '.' in code:
            code = code.split('.')[0]

        # 确保6位
        code = code.zfill(6)

        return code

    @staticmethod
    def get_market(code: str) -> str:
        """根据代码判断市场"""
        code = AKShareClient.normalize_code(code)

        if code.startswith(('60', '68', '90')):
            return 'sh'
        elif code.startswith(('00', '30', '20')):
            return 'sz'
        elif code.startswith(('8', '4')):
            return 'bj'
        else:
            return 'sz'  # 默认深圳

    @staticmethod
    def get_full_code(code: str) -> str:
        """获取带市场前缀的完整代码"""
        code = AKShareClient.normalize_code(code)
        market = AKShareClient.get_market(code)
        return f"{market}{code}"

    def get_stock_name(self, code: str) -> str:
        """获取股票名称"""
        try:
            code6 = self.normalize_code(code)

            # 尝试从个股信息获取
            try:
                df = ak.stock_individual_info_em(symbol=code6)
                if not df.empty:
                    name_row = df[df['item'] == '股票简称']
                    if not name_row.empty:
                        return str(name_row['value'].iloc[0])
            except Exception:
                pass

            # 从股票列表获取
            stock_list = self.get_stock_list_sync()
            if stock_list is not None:
                row = stock_list[stock_list['code'] == code6]
                if not row.empty:
                    return str(row['name'].iloc[0])

            return f"股票{code6}"
        except Exception as e:
            logger.warning(f"获取股票名称失败 {code}: {e}")
            return f"股票{code}"

    def get_stock_list_sync(self) -> Optional[pd.DataFrame]:
        """同步获取股票列表（带缓存）"""
        try:
            # 检查缓存
            if (self._stock_list_cache is not None and
                self._stock_list_cache_time is not None and
                datetime.now() - self._stock_list_cache_time < timedelta(hours=1)):
                return self._stock_list_cache

            logger.info("📋 获取A股股票列表...")
            df = ak.stock_info_a_code_name()

            if df is None or df.empty:
                return None

            # 标准化列名
            df = df.rename(columns={
                'code': 'code',
                'name': 'name'
            })

            # 确保code是字符串
            df['code'] = df['code'].astype(str).str.zfill(6)

            # 更新缓存
            self._stock_list_cache = df
            self._stock_list_cache_time = datetime.now()

            logger.info(f"✅ 获取到 {len(df)} 只股票")
            return df

        except Exception as e:
            logger.error(f"❌ 获取股票列表失败: {e}")
            return None

    async def get_stock_list(self) -> List[Dict[str, str]]:
        """异步获取股票列表"""
        try:
            df = await asyncio.to_thread(self.get_stock_list_sync)
            if df is None:
                return []

            return df.to_dict('records')
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return []

    def get_realtime_quote_sync(self, code: str) -> Optional[Dict[str, Any]]:
        """同步获取单只股票实时行情"""
        try:
            code6 = self.normalize_code(code)
            cache_key = f"quote_{code6}"

            # 检查缓存
            cached = self._get_cache(cache_key)
            if cached:
                return cached

            # 使用 stock_bid_ask_em 获取单个股票实时行情
            try:
                df = ak.stock_bid_ask_em(symbol=code6)
                if df is not None and not df.empty:
                    data = dict(zip(df['item'], df['value']))

                    result = {
                        'code': code6,
                        'name': self.get_stock_name(code6),
                        'price': self._to_float(data.get('最新')),
                        'change': self._to_float(data.get('涨跌')),
                        'change_percent': self._to_float(data.get('涨幅')),
                        'open': self._to_float(data.get('今开')),
                        'high': self._to_float(data.get('最高')),
                        'low': self._to_float(data.get('最低')),
                        'pre_close': self._to_float(data.get('昨收')),
                        'volume': self._to_int(data.get('总手')) * 100,  # 手转股
                        'amount': self._to_float(data.get('金额')),
                        'turnover_rate': self._to_float(data.get('换手')),
                        'volume_ratio': self._to_float(data.get('量比')),
                        'timestamp': datetime.now().isoformat(),
                    }

                    self._set_cache(cache_key, result)
                    return result
            except Exception as e:
                logger.debug(f"stock_bid_ask_em 失败: {e}")

            # 备用方案：使用全市场快照
            quotes = self.get_batch_quotes_sync([code6])
            return quotes.get(code6)

        except Exception as e:
            logger.error(f"获取实时行情失败 {code}: {e}")
            return None

    async def get_realtime_quote(self, code: str) -> Optional[Dict[str, Any]]:
        """异步获取单只股票实时行情"""
        return await asyncio.to_thread(self.get_realtime_quote_sync, code)

    def get_batch_quotes_sync(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """同步批量获取股票实时行情"""
        try:
            codes = [self.normalize_code(c) for c in codes]

            # 使用新浪财经接口获取全市场快照
            df = ak.stock_zh_a_spot()

            if df is None or df.empty:
                logger.warning("⚠️ 全市场快照为空")
                return {}

            results = {}

            for _, row in df.iterrows():
                code = str(row.get('代码', '')).strip().zfill(6)

                if code not in codes:
                    continue

                results[code] = {
                    'code': code,
                    'name': str(row.get('名称', f'股票{code}')),
                    'price': self._to_float(row.get('最新价')),
                    'change': self._to_float(row.get('涨跌额')),
                    'change_percent': self._to_float(row.get('涨跌幅')),
                    'open': self._to_float(row.get('今开')),
                    'high': self._to_float(row.get('最高')),
                    'low': self._to_float(row.get('最低')),
                    'pre_close': self._to_float(row.get('昨收')),
                    'volume': self._to_int(row.get('成交量')),
                    'amount': self._to_float(row.get('成交额')),
                    'turnover_rate': self._to_float(row.get('换手率')),
                    'volume_ratio': self._to_float(row.get('量比')),
                    'pe': self._to_float(row.get('市盈率-动态')),
                    'pb': self._to_float(row.get('市净率')),
                    'total_mv': self._to_float(row.get('总市值')),
                    'circ_mv': self._to_float(row.get('流通市值')),
                    'timestamp': datetime.now().isoformat(),
                }

            return results

        except Exception as e:
            logger.error(f"批量获取行情失败: {e}")
            return {}

    async def get_batch_quotes(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """异步批量获取股票实时行情"""
        return await asyncio.to_thread(self.get_batch_quotes_sync, codes)

    def get_kline_sync(self, code: str, days: int = 60) -> List[Dict[str, Any]]:
        """
        同步获取股票K线数据

        Args:
            code: 股票代码
            days: 天数

        Returns:
            K线数据列表
        """
        try:
            code6 = self.normalize_code(code)
            cache_key = f"kline_{code6}_{days}"

            # 检查缓存
            cached = self._get_cache(cache_key)
            if cached:
                return cached

            end = datetime.now()
            start = end - timedelta(days=days * 2)  # 多取一些数据

            df = ak.stock_zh_a_hist(
                symbol=code6,
                period="daily",
                start_date=start.strftime('%Y%m%d'),
                end_date=end.strftime('%Y%m%d'),
                adjust="qfq"  # 前复权
            )

            if df is None or df.empty:
                return []

            # 转换为列表
            records = []
            for _, row in df.iterrows():
                records.append({
                    'date': str(row.get('日期', '')),
                    'open': self._to_float(row.get('开盘')),
                    'close': self._to_float(row.get('收盘')),
                    'high': self._to_float(row.get('最高')),
                    'low': self._to_float(row.get('最低')),
                    'volume': self._to_int(row.get('成交量')),
                    'amount': self._to_float(row.get('成交额')),
                    'change_percent': self._to_float(row.get('涨跌幅')),
                    'change': self._to_float(row.get('涨跌额')),
                    'turnover': self._to_float(row.get('换手率')),
                })

            # 只返回最近days天
            records = records[-days:] if len(records) > days else records

            self._set_cache(cache_key, records)
            return records

        except Exception as e:
            logger.error(f"获取K线数据失败 {code}: {e}")
            return []

    async def get_kline(self, code: str, days: int = 60) -> List[Dict[str, Any]]:
        """异步获取股票K线数据"""
        return await asyncio.to_thread(self.get_kline_sync, code, days)

    def get_stock_news_sync(self, code: str, limit: int = 20) -> List[Dict[str, Any]]:
        """同步获取股票新闻"""
        try:
            code6 = self.normalize_code(code)

            df = ak.stock_news_em(symbol=code6)

            if df is None or df.empty:
                return []

            news_list = []
            for _, row in df.head(limit).iterrows():
                title = str(row.get('新闻标题', '')).strip()
                if not title:
                    continue

                news_list.append({
                    'title': title,
                    'content': str(row.get('新闻内容', '')),
                    'url': str(row.get('新闻链接', '')),
                    'source': str(row.get('文章来源', '东方财富')),
                    'publish_time': str(row.get('发布时间', '')),
                })

            return news_list

        except Exception as e:
            logger.error(f"获取股票新闻失败 {code}: {e}")
            return []

    async def get_stock_news(self, code: str, limit: int = 20) -> List[Dict[str, Any]]:
        """异步获取股票新闻"""
        return await asyncio.to_thread(self.get_stock_news_sync, code, limit)

    def get_hot_stocks_sync(self, top_n: int = 20, by: str = 'amount') -> List[Dict[str, Any]]:
        """
        获取热门股票

        Args:
            top_n: 数量
            by: 排序方式 (amount=成交额, pct_chg=涨跌幅)
        """
        try:
            df = ak.stock_zh_a_spot_em()

            if df is None or df.empty:
                return []

            # 排序
            sort_col = '成交额' if by == 'amount' else '涨跌幅'
            df = df.sort_values(by=sort_col, ascending=False)

            results = []
            for _, row in df.head(top_n).iterrows():
                code = str(row.get('代码', '')).zfill(6)
                results.append({
                    'code': code,
                    'name': str(row.get('名称', '')),
                    'price': self._to_float(row.get('最新价')),
                    'change_percent': self._to_float(row.get('涨跌幅')),
                    'amount': self._to_float(row.get('成交额')),
                    'volume': self._to_int(row.get('成交量')),
                    'turnover_rate': self._to_float(row.get('换手率')),
                    'pe': self._to_float(row.get('市盈率-动态')),
                })

            return results

        except Exception as e:
            logger.error(f"获取热门股票失败: {e}")
            return []

    async def get_hot_stocks(self, top_n: int = 20, by: str = 'amount') -> List[Dict[str, Any]]:
        """异步获取热门股票"""
        return await asyncio.to_thread(self.get_hot_stocks_sync, top_n, by)

    def get_sector_flow_sync(self) -> List[Dict[str, Any]]:
        """获取板块资金流向"""
        try:
            df = ak.stock_sector_fund_flow_rank()

            if df is None or df.empty:
                return []

            results = []
            for _, row in df.head(10).iterrows():
                results.append({
                    'sector': str(row.get('行业', '')),
                    'change_percent': self._to_float(row.get('今日涨跌', '')),
                    'main_inflow': self._to_float(row.get('主力净流入', '')),
                })

            return results

        except Exception as e:
            logger.error(f"获取板块资金流向失败: {e}")
            return []

    async def get_sector_flow(self) -> List[Dict[str, Any]]:
        """异步获取板块资金流向"""
        return await asyncio.to_thread(self.get_sector_flow_sync)

    def get_market_sentiment_sync(self) -> Dict[str, Any]:
        """获取市场情绪指标"""
        try:
            # 获取涨跌家数
            df = ak.stock_zh_a_spot_em()

            if df is None or df.empty:
                return {}

            up_count = len(df[df['涨跌幅'] > 0])
            down_count = len(df[df['涨跌幅'] < 0])
            flat_count = len(df[df['涨跌幅'] == 0])

            # 涨跌停统计
            limit_up = len(df[df['涨跌幅'] >= 9.5])
            limit_down = len(df[df['涨跌幅'] <= -9.5])

            return {
                'up_count': up_count,
                'down_count': down_count,
                'flat_count': flat_count,
                'limit_up': limit_up,
                'limit_down': limit_down,
                'up_down_ratio': round(up_count / max(down_count, 1), 2),
                'sentiment': '乐观' if up_count > down_count else '谨慎',
                'timestamp': datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"获取市场情绪失败: {e}")
            return {}

    async def get_market_sentiment(self) -> Dict[str, Any]:
        """异步获取市场情绪"""
        return await asyncio.to_thread(self.get_market_sentiment_sync)

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        """安全转换为浮点数"""
        try:
            if pd.isna(value) or value is None:
                return None
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        """安全转换为整数"""
        try:
            if pd.isna(value) or value is None:
                return None
            return int(float(value))
        except (ValueError, TypeError):
            return None


# 全局客户端实例
akshare_client = AKShareClient()
