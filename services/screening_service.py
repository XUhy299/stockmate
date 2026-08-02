"""
选股服务
提供多维度选股功能
"""
import logging
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime

from config import config
from data.akshare_client import akshare_client
from data.indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


@dataclass
class ScreeningCriteria:
    """选股条件"""
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_change: Optional[float] = None  # 最小涨跌幅
    max_change: Optional[float] = None  # 最大涨跌幅
    min_volume_ratio: Optional[float] = None  # 最小量比
    min_turnover: Optional[float] = None  # 最小换手率
    max_turnover: Optional[float] = None  # 最大换手率
    rsi_range: Optional[tuple] = None  # RSI范围 (min, max)
    macd_signal: Optional[str] = None  # MACD信号: golden_cross, death_cross, above_zero, below_zero


class ScreeningService:
    """
    选股服务
    提供基于技术面和资金面的选股功能
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    async def screen_by_technical(self, criteria: ScreeningCriteria, limit: int = 20) -> List[Dict[str, Any]]:
        """
        基于技术面选股

        Args:
            criteria: 选股条件
            limit: 返回数量

        Returns:
            符合条件的股票列表
        """
        logger.info(f"🔍 开始技术面选股: {criteria}")

        # 获取候选股票（使用热门股票作为候选池）
        candidates = await akshare_client.get_hot_stocks(top_n=100)
        results = []

        for stock in candidates:
            code = stock.get("code")
            if not code:
                continue

            try:
                # 初步过滤（快速排除）
                if not self._quick_filter(stock, criteria):
                    continue

                # 获取K线数据进行深度分析
                kline = await akshare_client.get_kline(code, 60)
                if not kline:
                    continue

                # 计算指标
                indicators = TechnicalIndicators.calculate_all(kline)

                # 深度过滤
                if not self._deep_filter(stock, indicators, criteria):
                    continue

                # 通过所有筛选，组装结果
                result = {
                    "code": code,
                    "name": stock.get("name"),
                    "price": stock.get("price"),
                    "change_percent": stock.get("change_percent"),
                    "turnover_rate": stock.get("turnover_rate"),
                    "volume_ratio": stock.get("volume_ratio"),
                    "indicators": {
                        "ma5": indicators.get("ma5"),
                        "ma20": indicators.get("ma20"),
                        "rsi14": indicators.get("rsi14"),
                        "macd": indicators.get("macd"),
                        "kdj_k": indicators.get("k"),
                        "kdj_d": indicators.get("d"),
                    }
                }

                results.append(result)

                if len(results) >= limit:
                    break

            except Exception as e:
                logger.debug(f"分析 {code} 时出错: {e}")
                continue

        logger.info(f"✅ 选股完成，找到 {len(results)} 只符合条件的股票")
        return results

    async def screen_by_momentum(self, days: int = 5, limit: int = 20) -> List[Dict[str, Any]]:
        """
        基于动量选股（近期强势股）

        Args:
            days: 计算动量的天数
            limit: 返回数量

        Returns:
            动量最强的股票列表
        """
        logger.info(f"🚀 开始动量选股，周期{days}天")

        # 获取热门股票
        stocks = await akshare_client.get_hot_stocks(top_n=100)

        momentum_list = []

        for stock in stocks:
            code = stock.get("code")
            if not code:
                continue

            try:
                # 获取K线
                kline = await akshare_client.get_kline(code, days + 10)
                if len(kline) < days:
                    continue

                # 计算动量（N天累计涨幅）
                recent_kline = kline[-days:]
                start_price = recent_kline[0].get("close", 0)
                end_price = recent_kline[-1].get("close", 0)

                if start_price <= 0:
                    continue

                momentum = (end_price - start_price) / start_price * 100

                # 计算波动率
                volatility = self._calculate_volatility(kline)

                momentum_list.append({
                    "code": code,
                    "name": stock.get("name"),
                    "price": end_price,
                    "momentum": momentum,
                    "volatility": volatility,
                    "change_percent": stock.get("change_percent"),
                    "turnover_rate": stock.get("turnover_rate"),
                })

            except Exception as e:
                logger.debug(f"计算 {code} 动量时出错: {e}")
                continue

        # 按动量排序
        momentum_list.sort(key=lambda x: x["momentum"], reverse=True)

        return momentum_list[:limit]

    async def screen_by_value(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        基于价值选股（低估值）

        Args:
            limit: 返回数量

        Returns:
            低估值股票列表
        """
        logger.info(f"💎 开始价值选股")

        # 获取全市场数据
        stocks = await akshare_client.get_hot_stocks(top_n=200)

        value_stocks = []

        for stock in stocks:
            code = stock.get("code")
            pe = stock.get("pe")
            pb = stock.get("pb")

            # 筛选低估值股票
            if pe and pe > 0 and pe < 20:  # PE < 20
                if pb and pb > 0 and pb < 2:  # PB < 2
                    value_stocks.append({
                        "code": code,
                        "name": stock.get("name"),
                        "price": stock.get("price"),
                        "pe": pe,
                        "pb": pb,
                        "change_percent": stock.get("change_percent"),
                    })

        # 按PE排序
        value_stocks.sort(key=lambda x: x["pe"])

        return value_stocks[:limit]

    async def find_breakout_stocks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        寻找突破股票（突破布林带上轨或新高）

        Args:
            limit: 返回数量

        Returns:
            突破股票列表
        """
        logger.info(f"📈 寻找突破股票")

        stocks = await akshare_client.get_hot_stocks(top_n=100)
        breakouts = []

        for stock in stocks:
            code = stock.get("code")
            if not code:
                continue

            try:
                kline = await akshare_client.get_kline(code, 60)
                if len(kline) < 20:
                    continue

                # 计算布林带
                closes = [k.get("close") for k in kline]
                boll = TechnicalIndicators.boll(closes)

                current_price = stock.get("price", 0)
                upper_band = boll.get("upper")
                lower_band = boll.get("lower")

                if not upper_band or not lower_band:
                    continue

                # 检查是否突破上轨
                if current_price > upper_band * 0.99:  # 允许1%的误差
                    # 计算突破强度
                    breakout_strength = (current_price - upper_band) / upper_band * 100

                    breakouts.append({
                        "code": code,
                        "name": stock.get("name"),
                        "price": current_price,
                        "boll_upper": upper_band,
                        "breakout_strength": breakout_strength,
                        "change_percent": stock.get("change_percent"),
                        "type": "upper_breakout"
                    })

                # 检查是否跌破下轨（反弹机会）
                elif current_price < lower_band * 1.01:
                    breakouts.append({
                        "code": code,
                        "name": stock.get("name"),
                        "price": current_price,
                        "boll_lower": lower_band,
                        "change_percent": stock.get("change_percent"),
                        "type": "lower_breakout"
                    })

            except Exception as e:
                logger.debug(f"分析 {code} 突破时出错: {e}")
                continue

        # 按突破强度排序
        breakouts.sort(key=lambda x: abs(x.get("breakout_strength", 0)), reverse=True)

        return breakouts[:limit]

    def _quick_filter(self, stock: Dict[str, Any], criteria: ScreeningCriteria) -> bool:
        """快速过滤（基于已有数据）"""
        # 价格过滤
        price = stock.get("price", 0)
        if criteria.min_price is not None and price < criteria.min_price:
            return False
        if criteria.max_price is not None and price > criteria.max_price:
            return False

        # 涨跌幅过滤
        change = stock.get("change_percent", 0)
        if criteria.min_change is not None and change < criteria.min_change:
            return False
        if criteria.max_change is not None and change > criteria.max_change:
            return False

        # 换手率过滤
        turnover = stock.get("turnover_rate", 0)
        if criteria.min_turnover is not None and turnover < criteria.min_turnover:
            return False
        if criteria.max_turnover is not None and turnover > criteria.max_turnover:
            return False

        # 量比过滤
        volume_ratio = stock.get("volume_ratio", 0)
        if criteria.min_volume_ratio is not None and volume_ratio < criteria.min_volume_ratio:
            return False

        return True

    def _deep_filter(self, stock: Dict[str, Any], indicators: Dict[str, Any], criteria: ScreeningCriteria) -> bool:
        """深度过滤（基于技术指标）"""
        # RSI过滤
        if criteria.rsi_range:
            rsi = indicators.get("rsi14")
            if rsi is not None:
                min_rsi, max_rsi = criteria.rsi_range
                if rsi < min_rsi or rsi > max_rsi:
                    return False

        # MACD信号过滤
        if criteria.macd_signal:
            macd = indicators.get("macd")
            dif = indicators.get("dif")
            dea = indicators.get("dea")

            if criteria.macd_signal == "above_zero":
                if not (macd and macd > 0):
                    return False
            elif criteria.macd_signal == "below_zero":
                if not (macd and macd < 0):
                    return False
            elif criteria.macd_signal == "golden_cross":
                if not (dif and dea and dif > dea):
                    return False
            elif criteria.macd_signal == "death_cross":
                if not (dif and dea and dif < dea):
                    return False

        return True

    def _calculate_volatility(self, kline: List[Dict[str, Any]]) -> float:
        """计算波动率"""
        if len(kline) < 2:
            return 0

        changes = []
        for i in range(1, len(kline)):
            prev_close = kline[i-1].get("close", 0)
            curr_close = kline[i].get("close", 0)
            if prev_close > 0:
                change = abs(curr_close - prev_close) / prev_close
                changes.append(change)

        if not changes:
            return 0

        return sum(changes) / len(changes) * 100

    async def generate_screening_report(self, criteria: ScreeningCriteria) -> str:
        """
        生成选股报告

        Args:
            criteria: 选股条件

        Returns:
            报告内容
        """
        results = await self.screen_by_technical(criteria, limit=20)

        lines = [
            "# 📊 选股报告\n",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
            "**选股条件**:",
        ]

        if criteria.min_change is not None:
            lines.append(f"- 最小涨幅: {criteria.min_change}%")
        if criteria.min_turnover is not None:
            lines.append(f"- 最小换手率: {criteria.min_turnover}%")
        if criteria.rsi_range:
            lines.append(f"- RSI范围: {criteria.rsi_range}")

        lines.append(f"\n**选股结果**: 共找到 {len(results)} 只股票\n")

        if results:
            lines.append("| 代码 | 名称 | 价格 | 涨跌幅 | RSI | MACD |")
            lines.append("|------|------|------|--------|-----|------|")

            for r in results:
                indicators = r.get("indicators", {})
                rsi = indicators.get("rsi14", "N/A")
                macd = indicators.get("macd", "N/A")

                if isinstance(macd, float):
                    macd_str = f"{macd:.2f}"
                else:
                    macd_str = str(macd)

                lines.append(
                    f"| {r['code']} | {r['name']} | {r['price']:.2f} | "
                    f"{r['change_percent']:+.2f}% | {rsi if isinstance(rsi, str) else f'{rsi:.1f}'} | {macd_str} |"
                )
        else:
            lines.append("未找到符合条件的股票，请调整选股条件。")

        return "\n".join(lines)


# 全局实例
screening_service = ScreeningService()
