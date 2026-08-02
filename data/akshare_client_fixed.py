"""
AKShare数据客户端 - 终极防御版
1. 全局底层拦截 requests，注入 User-Agent 绕过新浪 HTML 拦截
2. 注入 Connection: close 解决所有 RemoteDisconnected 断连
3. 纯净的数据源切换：新浪(实时) + 腾讯(K线)
"""
from __future__ import annotations

import asyncio
import logging
import time
import random
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from functools import wraps
import pandas as pd

logger = logging.getLogger(__name__)

# ================= 终极安全底层请求补丁 =================
# 必须在 import akshare 之前执行，从最底层拦截所有流量！
import requests

def _apply_safe_requests_patch():
    """
    拦截 requests 的底层请求，一劳永逸解决：
    1. 新浪 HTML 拦截 (注入 User-Agent 和 Referer)
    2. 断连报错 RemoteDisconnected (注入 Connection: close)
    """
    if hasattr(requests, '_akshare_patched_safe'):
        return

    original_session_request = requests.Session.request
    original_api_request = requests.request

    def safe_patched_session_request(self, method, url, **kwargs):
        headers = kwargs.get('headers')
        headers = dict(headers) if isinstance(headers, dict) else {}
        
        # 1. 基础伪装与防断连
        if 'User-Agent' not in headers:
            headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        headers['Connection'] = 'close'

        # 2. 【核心修复】针对新浪的专属伪装：必须带上 Referer
        if 'sina.com.cn' in str(url).lower():
            headers['Referer'] = 'https://finance.sina.com.cn/'

        kwargs['headers'] = headers
        return original_session_request(self, method, url, **kwargs)

    def safe_patched_api_request(method, url, **kwargs):
        headers = kwargs.get('headers')
        headers = dict(headers) if isinstance(headers, dict) else {}
        
        # 1. 基础伪装与防断连
        if 'User-Agent' not in headers:
            headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        headers['Connection'] = 'close'

        # 2. 【核心修复】针对新浪的专属伪装：必须带上 Referer
        if 'sina.com.cn' in str(url).lower():
            headers['Referer'] = 'https://finance.sina.com.cn/'

        kwargs['headers'] = headers
        return original_api_request(method, url, **kwargs)

    # 替换底层方法
    requests.Session.request = safe_patched_session_request
    requests.request = safe_patched_api_request
    requests._akshare_patched_safe = True
    logger.info("🔧 全局防断连与反爬补丁已就绪 (已增强新浪 Referer 伪装)")

_apply_safe_requests_patch()

# 补丁打好后，再导入 AKShare
import akshare as ak
# ========================================================


def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 10.0):
    """重试装饰器 - 带指数退避"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except json.JSONDecodeError as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        jitter = random.uniform(0, 0.5)
                        sleep_time = delay + jitter
                        logger.warning(
                            f"[{func.__name__}] JSON解析失败 (尝试 {attempt + 1}/{max_retries}): "
                            f"{sleep_time:.1f}秒后重试..."
                        )
                        time.sleep(sleep_time)
                    else:
                        logger.error(f"[{func.__name__}] JSON解析失败，已达最大重试次数")
                except Exception as e:
                    last_exception = e
                    error_msg = str(e).lower()

                    retryable_errors = [
                        'remote disconnected', 'connection', 'timeout',
                        'network', 'reset', 'peer', 'broken pipe',
                        'ssl', 'handshake', 'temporary failure',
                        'rate limit', '429', '503', '502', '500',
                    ]

                    if any(err in error_msg for err in retryable_errors) and attempt < max_retries - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        jitter = random.uniform(0, 0.5)
                        sleep_time = delay + jitter
                        logger.warning(
                            f"[{func.__name__}] 请求失败 (尝试 {attempt + 1}/{max_retries}): "
                            f"{sleep_time:.1f}秒后重试..."
                        )
                        time.sleep(sleep_time)
                    else:
                        if attempt == max_retries - 1:
                            logger.error(f"[{func.__name__}] 请求彻底失败: {e}")
                        raise

            raise last_exception
        return wrapper
    return decorator


class AKShareClient:
    """AKShare统一数据客户端"""

    _instance = None

    def __new__(cls):
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
        self._cache_ttl = 60

        self._stock_list_cache: Optional[pd.DataFrame] = None
        self._stock_list_cache_time: Optional[datetime] = None

        logger.info("✅ AKShare客户端初始化完成 (安全增强版)")

    def _get_cache(self, key: str) -> Optional[Any]:
        if key in self._cache and key in self._cache_time:
            if datetime.now() - self._cache_time[key] < timedelta(seconds=self._cache_ttl):
                return self._cache[key]
        return None

    def _set_cache(self, key: str, value: Any):
        self._cache[key] = value
        self._cache_time[key] = datetime.now()

    @staticmethod
    def normalize_code(code: str) -> str:
        code = str(code).strip().lower()
        for prefix in ['sh', 'sz', 'bj']:
            if code.startswith(prefix):
                code = code[2:]
        if '.' in code:
            code = code.split('.')[0]
        code = code.zfill(6)
        return code

    @staticmethod
    def get_market(code: str) -> str:
        code = AKShareClient.normalize_code(code)
        if code.startswith(('60', '68', '90')):
            return 'sh'
        elif code.startswith(('00', '30', '20')):
            return 'sz'
        elif code.startswith(('8', '4')):
            return 'bj'
        else:
            return 'sz'

    @staticmethod
    def get_full_code(code: str) -> str:
        code = AKShareClient.normalize_code(code)
        market = AKShareClient.get_market(code)
        return f"{market}{code}"

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_stock_name(self, code: str) -> str:
        """从本地缓存获取名称，避免单点 API 请求"""
        try:
            code6 = self.normalize_code(code)
            stock_list = self.get_stock_list_sync()
            if stock_list is not None:
                row = stock_list[stock_list['code'] == code6]
                if not row.empty:
                    return str(row['name'].iloc[0])
            return f"股票{code6}"
        except Exception:
            return f"股票{code6}"

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_stock_list_sync(self) -> Optional[pd.DataFrame]:
        try:
            if (self._stock_list_cache is not None and
                self._stock_list_cache_time is not None and
                datetime.now() - self._stock_list_cache_time < timedelta(hours=1)):
                return self._stock_list_cache

            df = ak.stock_info_a_code_name()
            if df is None or df.empty:
                return None

            df = df.rename(columns={'code': 'code', 'name': 'name'})
            df['code'] = df['code'].astype(str).str.zfill(6)

            self._stock_list_cache = df
            self._stock_list_cache_time = datetime.now()
            return df
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return None

    async def get_stock_list(self) -> List[Dict[str, str]]:
        try:
            df = await asyncio.to_thread(self.get_stock_list_sync)
            if df is None:
                return []
            return df.to_dict('records')
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return []

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_realtime_quote_sync(self, code: str) -> Optional[Dict[str, Any]]:
        """复用批量快照，规避单点接口风控"""
        try:
            code6 = self.normalize_code(code)
            cache_key = f"quote_{code6}"

            cached = self._get_cache(cache_key)
            if cached:
                return cached

            quotes = self.get_batch_quotes_sync([code6])
            result = quotes.get(code6)
            
            if result:
                self._set_cache(cache_key, result)
            return result

        except Exception as e:
            logger.error(f"获取实时行情失败 {code}: {e}")
            return None

    async def get_realtime_quote(self, code: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_realtime_quote_sync, code)

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def get_market_spot_data(self) -> Optional[pd.DataFrame]:
        """统一获取全市场行情数据 - 切换为【新浪财经】数据源"""
        cache_key = "global_market_spot"
        now = datetime.now()

        if cache_key in self._cache and cache_key in self._cache_time:
            if now - self._cache_time[cache_key] < timedelta(seconds=10):
                return self._cache[cache_key]

        # 改用新浪源 (防 HTML 拦截补丁已在顶部生效)
        df = ak.stock_zh_a_spot()

        if df is not None and not df.empty:
            self._cache[cache_key] = df
            self._cache_time[cache_key] = now

        return df

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def get_batch_quotes_sync(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        同步批量获取股票实时行情 - 切换为【雪球】个股精速接口
        逐个获取，彻底告别全市场快照的封锁
        """
        results = {}
        for code in set(codes):
            code6 = self.normalize_code(code)
            # 雪球接口通常要求带市场前缀且大写，如 SH600036, SZ000001
            full_code = self.get_full_code(code).upper()  
            
            try:
                # 调用雪球的单股行情接口
                df = ak.stock_individual_spot_xq(symbol=full_code)
                if df is not None and not df.empty:
                    # 雪球返回格式通常是两列：item(指标名) 和 value(值)
                    data = dict(zip(df['item'], df['value']))
                    
                    results[code6] = {
                        'code': code6,
                        'name': str(data.get('股票简称', f'股票{code6}')),
                        'price': self._to_float(data.get('当前价', data.get('最新价'))),
                        'change': self._to_float(data.get('涨跌')),
                        'change_percent': self._to_float(data.get('涨跌幅')),
                        'open': self._to_float(data.get('今开')),
                        'high': self._to_float(data.get('最高')),
                        'low': self._to_float(data.get('最低')),
                        'pre_close': self._to_float(data.get('昨收')),
                        'volume': self._to_int(data.get('成交量')),
                        'amount': self._to_float(data.get('成交额')),
                        'turnover_rate': self._to_float(data.get('换手率', data.get('换手'))),
                        'timestamp': datetime.now().isoformat(),
                    }
            except Exception as e:
                logger.warning(f"获取 {code6} 雪球行情失败: {e}")
                
        return results

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def get_market_sentiment_sync(self) -> Dict[str, Any]:
        """获取市场情绪指标 - 切换为【乐咕乐股】赚钱效应专用接口"""
        try:
            logger.debug("获取市场情绪数据(Legu)...")
            
            df = ak.stock_market_activity_legu()
            if df is None or df.empty:
                return {}

            # 乐咕乐股返回格式：item 和 value
            data = dict(zip(df['item'], df['value']))

            up_count = self._to_int(data.get('上涨家数', 0))
            down_count = self._to_int(data.get('下跌家数', 0))
            flat_count = self._to_int(data.get('平盘家数', 0))
            limit_up = self._to_int(data.get('涨停家数', 0))
            limit_down = self._to_int(data.get('跌停家数', 0))

            return {
                'up_count': up_count,
                'down_count': down_count,
                'flat_count': flat_count,
                'limit_up': limit_up,
                'limit_down': limit_down,
                'up_down_ratio': round(up_count / max(down_count, 1), 2) if down_count else 0,
                'sentiment': '乐观' if up_count > down_count else '谨慎',
                'timestamp': datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"获取市场情绪失败: {e}")
            return {}

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    def get_hot_stocks_sync(self, top_n: int = 20, by: str = 'amount') -> List[Dict[str, Any]]:
        """获取热门股票 - 使用东方财富人气榜接口"""
        try:
            logger.debug("获取热门股票(东财人气榜)...")

            # 使用东方财富的人气榜接口
            df = ak.stock_hot_rank_em()

            if df is None or df.empty:
                return []

            results = []
            for _, row in df.head(top_n).iterrows():
                code = str(row.get('代码', ''))
                name = str(row.get('名称', ''))

                results.append({
                    'code': code,
                    'name': name,
                    'price': self._to_float(row.get('最新价', 0)),
                    'change_percent': self._to_float(row.get('涨跌幅', 0)),
                    'amount': self._to_float(row.get('成交额', 0)),
                    'turnover_rate': self._to_float(row.get('换手率', 0)),
                    'hot_rank': int(row.get('人气排名', 0)) if row.get('人气排名') else None,
                })
            return results
        except Exception as e:
            logger.error(f"获取热门股票失败: {e}")
            return []

    async def get_batch_quotes(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        return await asyncio.to_thread(self.get_batch_quotes_sync, codes)

    async def get_hot_stocks(self, top_n: int = 20, by: str = 'amount') -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_hot_stocks_sync, top_n, by)
    
    async def get_market_sentiment(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_market_sentiment_sync)

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_kline_sync(self, code: str, days: int = 60) -> List[Dict[str, Any]]:
        """同步获取股票K线数据 - 切换为【腾讯财经】数据源"""
        try:
            code6 = self.normalize_code(code)
            full_code = self.get_full_code(code)
            cache_key = f"kline_{code6}_{days}"

            cached = self._get_cache(cache_key)
            if cached:
                return cached

            end = datetime.now()
            start = end - timedelta(days=days * 2)

            df = ak.stock_zh_a_hist_tx(
                symbol=full_code,
                start_date=start.strftime('%Y%m%d'),
                end_date=end.strftime('%Y%m%d'),
                adjust="qfq"
            )

            if df is None or df.empty:
                return []

            records = []
            for _, row in df.iterrows():
                records.append({
                    'date': str(row.get('date', '')),
                    'open': self._to_float(row.get('open')),
                    'close': self._to_float(row.get('close')),
                    'high': self._to_float(row.get('high')),
                    'low': self._to_float(row.get('low')),
                    'volume': None,
                    'amount': self._to_float(row.get('amount')),
                    'change_percent': None,
                    'change': None,
                    'turnover': None,
                })

            records = records[-days:] if len(records) > days else records

            self._set_cache(cache_key, records)
            return records

        except Exception as e:
            logger.error(f"获取K线数据失败 {code}: {e}")
            return []

    async def get_kline(self, code: str, days: int = 60) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_kline_sync, code, days)

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_stock_news_sync(self, code: str, limit: int = 20) -> List[Dict[str, Any]]:
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
        except Exception:
            return []

    async def get_stock_news(self, code: str, limit: int = 20) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_stock_news_sync, code, limit)


    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_sector_flow_sync(self) -> List[Dict[str, Any]]:
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
        except Exception:
            return []

    async def get_sector_flow(self) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_sector_flow_sync)


    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            if pd.isna(value) or value is None:
                return None
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        try:
            if pd.isna(value) or value is None:
                return None
            return int(float(value))
        except (ValueError, TypeError):
            return None

akshare_client = AKShareClient()