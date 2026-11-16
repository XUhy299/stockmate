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
from bs4 import BeautifulSoup
import re

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
        if kwargs.get('timeout') is None:
            kwargs['timeout'] = 15
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
        if kwargs.get('timeout') is None:
            kwargs['timeout'] = 15
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

    @staticmethod
    def parse_tencent_quotes(text: str) -> Dict[str, Dict[str, Any]]:
        results = {}
        for line in text.splitlines():
            match = re.match(r'v_(?:sh|sz|bj)([0-9]{6})="([^"]*)";?', line.strip())
            if not match:
                continue
            fields = match.group(2).split("~")
            if len(fields) < 40:
                continue

            def number(index):
                try:
                    value = float(fields[index])
                    return value if pd.notna(value) else None
                except (ValueError, IndexError):
                    return None

            price = number(3)
            if price is None or price <= 0:
                continue
            stamp = fields[30]
            try:
                timestamp = datetime.strptime(stamp, "%Y%m%d%H%M%S").isoformat()
            except ValueError:
                timestamp = None
            detail = fields[35].split("/")
            try:
                amount = float(detail[2]) if len(detail) >= 3 else None
            except ValueError:
                amount = None
            volume_lots = number(6)
            results[match.group(1)] = {
                "code": match.group(1), "name": fields[1], "price": price,
                "pre_close": number(4), "open": number(5),
                "change": number(31), "change_percent": number(32),
                "high": number(33), "low": number(34),
                "volume": int(volume_lots * 100) if volume_lots is not None else None,
                "volume_unit": "股", "amount": amount, "turnover_rate": number(38),
                "timestamp": timestamp, "source": "腾讯财经",
            }
        return results

    def get_batch_quotes_sync(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch A-share quotes in one bounded request, preserving the tool shape."""
        normalized = list(dict.fromkeys(self.normalize_code(code) for code in codes))
        if not normalized:
            return {}
        if any(not re.fullmatch(r"[0-9]{6}", code) for code in normalized):
            raise ValueError("请输入有效的六位股票代码")
        results = {}
        missing = []
        for code in normalized:
            cached = self._get_cache(f"quote_{code}")
            if cached is not None:
                results[code] = cached
            else:
                missing.append(code)
        if missing:
            symbols = ",".join(self.get_full_code(code) for code in missing)
            response = requests.get(f"https://qt.gtimg.cn/q={symbols}", timeout=15)
            response.raise_for_status()
            response.encoding = "gbk"
            received = self.parse_tencent_quotes(response.text)
            for code in missing:
                if code in received:
                    results[code] = received[code]
                    self._set_cache(f"quote_{code}", received[code])
        if not results:
            raise RuntimeError("未能获取股票行情，请稍后重试")
        return results

    @staticmethod
    def parse_market_activity(html) -> Dict[str, Any]:
        """Read label/value table pairs; do not depend on removed CSS tags."""
        soup = BeautifulSoup(html, "html.parser")
        values = {}
        for row in soup.select("table tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.select("th,td")]
            for index in range(0, len(cells) - 1, 2):
                values[cells[index].strip(" ：:")] = cells[index + 1]

        def count(*labels):
            for label in labels:
                if label in values:
                    text = values[label].replace(",", "").replace("家", "").strip()
                    try:
                        value = float(text)
                    except ValueError:
                        return None
                    return int(value) if value >= 0 and value.is_integer() else None
            return None

        up = count("上涨", "上涨家数")
        down = count("下跌", "下跌家数")
        if up is None or down is None:
            raise ValueError("行情页面没有有效的涨跌家数")
        text = soup.get_text(" ", strip=True)
        date = re.search(r"(?:统计日期|统计时间|数据时间|更新时间)[：:\s]*(20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)", text)
        return {
            "up_count": up, "down_count": down,
            "flat_count": count("平盘", "平盘家数"),
            "limit_up": count("涨停", "涨停家数"),
            "limit_down": count("跌停", "跌停家数"),
            "up_down_ratio": round(up / down, 2) if down else None,
            "sentiment": "乐观" if up > down else "谨慎" if up < down else "平衡",
            "timestamp": date.group(1) if date else None,
            "source": "乐咕乐股",
        }

    def get_market_sentiment_sync(self) -> Dict[str, Any]:
        cached = self._get_cache("market_sentiment")
        if cached is not None:
            return cached
        try:
            response = requests.get(
                "https://legulegu.com/stockdata/market-activity",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                    "Referer": "https://legulegu.com/",
                },
                timeout=15,
            )
            response.raise_for_status()
            data = self.parse_market_activity(response.content)
        except requests.Timeout as exc:
            logger.warning("Market source timed out")
            raise RuntimeError("大盘数据获取超时，请重试") from exc
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Market source unavailable: %s", type(exc).__name__)
            raise RuntimeError("大盘数据源暂时不可用，请重试") from exc
        self._set_cache("market_sentiment", data)
        return data

    def get_hot_stocks_sync(self, top_n: int = 20, by: str = 'amount') -> List[Dict[str, Any]]:
        """新浪 A 股排行，服务端按成交额或涨幅排序，最多查询两页。"""
        if by not in {'amount', 'pct_chg'} or not 1 <= top_n <= 200:
            raise ValueError("排行支持 amount/pct_chg，数量应为 1 到 200")
        cache_key = f"hot_{by}_{top_n}"
        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached
        results = []
        for page in range(1, (top_n + 99) // 100 + 1):
            response = requests.get(
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                params={'page': page, 'num': 100, 'sort': 'amount' if by == 'amount' else 'changepercent',
                        'asc': 0, 'node': 'hs_a', 'symbol': '', '_s_r_a': 'page'},
                timeout=15,
            )
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                raise RuntimeError("股票排行接口未返回有效数据")
            for row in rows:
                code = str(row.get('code', ''))
                price = self._to_float(row.get('trade'))
                if not re.fullmatch(r'[0-9]{6}', code) or price is None or price <= 0:
                    continue
                results.append({
                    'code': code, 'name': row.get('name') or code, 'price': price,
                    'change_percent': self._to_float(row.get('changepercent')),
                    'amount': self._to_float(row.get('amount')),
                    'turnover_rate': self._to_float(row.get('turnoverratio')),
                    'timestamp': row.get('ticktime'), 'source': '新浪财经',
                    'ranking_by': by,
                })
        if not results:
            raise RuntimeError("股票排行接口未返回有效股票")
        results.sort(key=lambda row: row['amount' if by == 'amount' else 'change_percent']
                     if row['amount' if by == 'amount' else 'change_percent'] is not None else -float('inf'),
                     reverse=True)
        results = results[:top_n]
        self._set_cache(cache_key, results)
        return results

    async def get_batch_quotes(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        return await asyncio.to_thread(self.get_batch_quotes_sync, codes)

    async def get_hot_stocks(self, top_n: int = 20, by: str = 'amount') -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self.get_hot_stocks_sync, top_n, by)
    
    async def get_market_sentiment(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_market_sentiment_sync)

    def get_kline_sync(self, code: str, days: int = 60) -> List[Dict[str, Any]]:
        """腾讯最近交易日 K 线，避免旧接口先查询上市时间及整年数据。"""
        code6 = self.normalize_code(code)
        if not re.fullmatch(r'[0-9]{6}', code6) or not 5 <= days <= 365:
            raise ValueError("请提供有效的六位股票代码及 5 到 365 天范围")
        full_code = self.get_full_code(code6)
        cache_key = f"kline_{code6}_{days}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached
        for endpoint in (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
        ):
            try:
                response = requests.get(endpoint, params={'param': f'{full_code},day,,,{days + 1},qfq'}, timeout=12)
                response.raise_for_status()
                payload = response.json()
                stock = payload.get('data', {}).get(full_code, {})
                rows = stock.get('qfqday') or stock.get('day') or []
                records = []
                for row in rows:
                    if len(row) < 6:
                        continue
                    prices = [self._to_float(value) for value in row[1:5]]
                    if any(value is None or value <= 0 for value in prices):
                        continue
                    lots = self._to_float(row[5])
                    records.append({
                        'date': str(row[0]), 'open': prices[0], 'close': prices[1],
                        'high': prices[2], 'low': prices[3],
                        'volume': lots * 100 if lots is not None else None,
                        'volume_unit': '股', 'amount': None,
                        'change_percent': None, 'change': None, 'turnover': None,
                        'source': '腾讯财经', 'adjust': 'qfq' if stock.get('qfqday') else 'none',
                    })
                records = sorted({row['date']: row for row in records}.values(), key=lambda row: row['date'])[-days:]
                if not records:
                    raise ValueError('empty kline')
                self._set_cache(cache_key, records)
                return records
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                logger.warning("K线数据源未返回有效数据，尝试备用接口: %s", endpoint.split('/')[2])
        raise RuntimeError(f"无法获取 {code6} 的K线数据，请稍后重试")

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
        except Exception as exc:
            logger.warning("股票新闻接口失败: %s", type(exc).__name__)
            raise RuntimeError("股票新闻接口暂不可用，请稍后重试") from exc

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
