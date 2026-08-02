"""
技术指标计算模块
提供常用技术分析指标计算，复用并优化自原screening_service.py
"""
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd


class TechnicalIndicators:
    """技术指标计算器"""

    @staticmethod
    def sma(values: List[float], window: int) -> Optional[float]:
        """计算简单移动平均线（SMA）"""
        xs = [float(x) for x in values if x is not None]
        if window <= 0 or len(xs) < window:
            return None
        return sum(xs[-window:]) / window

    @staticmethod
    def ema(values: List[float], span: int) -> List[float]:
        """计算指数移动平均线（EMA）序列"""
        if not values:
            return []

        alpha = 2 / (span + 1)
        out = [values[0]]

        for v in values[1:]:
            if v is not None:
                out.append(alpha * v + (1 - alpha) * out[-1])
            else:
                out.append(out[-1])

        return out

    @staticmethod
    def rsi(closes: List[float], period: int = 14) -> Optional[float]:
        """
        计算RSI指标

        Args:
            closes: 收盘价列表
            period: RSI周期

        Returns:
            RSI值 (0-100)
        """
        xs = [float(x) for x in closes if x is not None]
        if period <= 0 or len(xs) < period + 1:
            return None

        # 计算涨跌
        gains = []
        losses = []
        for i in range(1, len(xs)):
            d = xs[i] - xs[i - 1]
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))

        # 初始平均
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # 平滑
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, Optional[float]]:
        """
        计算MACD指标

        Returns:
            {'macd': MACD柱, 'dif': DIF线, 'dea': DEA线}
        """
        xs = [float(x) for x in closes if x is not None]
        if len(xs) < max(fast, slow) + signal:
            return {'macd': None, 'dif': None, 'dea': None}

        # 计算EMA
        ema_fast = TechnicalIndicators.ema(xs, fast)
        ema_slow = TechnicalIndicators.ema(xs, slow)

        # DIF = EMA12 - EMA26
        dif = [a - b for a, b in zip(ema_fast, ema_slow)]

        # DEA = EMA(DIF, 9)
        dea = TechnicalIndicators.ema(dif, signal)

        # MACD = (DIF - DEA) * 2
        macd = [(d - s) * 2 for d, s in zip(dif, dea)]

        return {
            'macd': macd[-1],
            'dif': dif[-1],
            'dea': dea[-1]
        }

    @staticmethod
    def boll(closes: List[float], window: int = 20, k: float = 2.0) -> Dict[str, Optional[float]]:
        """
        计算布林带(BOLL)指标

        Returns:
            {'mid': 中轨, 'upper': 上轨, 'lower': 下轨}
        """
        xs = [float(x) for x in closes if x is not None]
        if window <= 1 or len(xs) < window:
            return {'mid': None, 'upper': None, 'lower': None}

        w = xs[-window:]
        mid = sum(w) / window
        var = sum((x - mid) ** 2 for x in w) / window
        std = var ** 0.5

        return {
            'mid': mid,
            'upper': mid + k * std,
            'lower': mid - k * std
        }

    @staticmethod
    def kdj(highs: List[float], lows: List[float], closes: List[float],
            n: int = 9, m1: int = 3, m2: int = 3) -> Dict[str, Optional[float]]:
        """
        计算KDJ指标

        Args:
            highs: 最高价列表
            lows: 最低价列表
            closes: 收盘价列表
            n: RSV周期
            m1: K值平滑系数
            m2: D值平滑系数

        Returns:
            {'k': K值, 'd': D值, 'j': J值}
        """
        hs = [float(x) for x in highs if x is not None]
        ls = [float(x) for x in lows if x is not None]
        cs = [float(x) for x in closes if x is not None]

        if len(hs) < n or len(ls) < n or len(cs) < n:
            return {'k': None, 'd': None, 'j': None}

        k_values = []
        d_values = []

        # 初始值
        k = d = 50

        for i in range(n - 1, len(cs)):
            hn = max(hs[i - n + 1:i + 1])
            ln = min(ls[i - n + 1:i + 1])
            cn = cs[i]

            if hn == ln:
                rsv = 50
            else:
                rsv = (cn - ln) / (hn - ln) * 100

            k = (2 / (m1 + 1)) * rsv + (1 - 2 / (m1 + 1)) * k
            d = (2 / (m2 + 1)) * k + (1 - 2 / (m2 + 1)) * d

            k_values.append(k)
            d_values.append(d)

        if not k_values:
            return {'k': None, 'd': None, 'j': None}

        k = k_values[-1]
        d = d_values[-1]
        j = 3 * k - 2 * d

        return {'k': k, 'd': d, 'j': j}

    @staticmethod
    def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
        """
        计算ATR指标（平均真实波幅）
        """
        hs = [float(x) for x in highs if x is not None]
        ls = [float(x) for x in lows if x is not None]
        cs = [float(x) for x in closes if x is not None]

        n = min(len(hs), len(ls), len(cs))
        if n < period + 1:
            return None

        hs, ls, cs = hs[-n:], ls[-n:], cs[-n:]

        trs = []
        for i in range(1, n):
            tr = max(
                hs[i] - ls[i],
                abs(hs[i] - cs[i - 1]),
                abs(ls[i] - cs[i - 1])
            )
            trs.append(tr)

        if len(trs) < period:
            return None

        atr = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atr = (atr * (period - 1) + trs[i]) / period

        return atr

    @staticmethod
    def volume_ratio(volumes: List[float], period: int = 5) -> Optional[float]:
        """计算量比"""
        vs = [float(x) for x in volumes if x is not None]
        if len(vs) < period + 1:
            return None

        current_volume = vs[-1]
        avg_volume = sum(vs[-(period + 1):-1]) / period

        if avg_volume == 0:
            return None

        return current_volume / avg_volume

    @staticmethod
    def calculate_all(kline_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        计算所有技术指标

        Args:
            kline_data: K线数据列表

        Returns:
            包含所有指标的字典
        """
        if not kline_data or len(kline_data) < 30:
            return {}

        closes = [x.get('close') for x in kline_data]
        highs = [x.get('high') for x in kline_data]
        lows = [x.get('low') for x in kline_data]
        volumes = [x.get('volume') for x in kline_data]

        result = {
            # 移动平均线
            'ma5': TechnicalIndicators.sma(closes, 5),
            'ma10': TechnicalIndicators.sma(closes, 10),
            'ma20': TechnicalIndicators.sma(closes, 20),
            'ma60': TechnicalIndicators.sma(closes, 60),

            # RSI
            'rsi6': TechnicalIndicators.rsi(closes, 6),
            'rsi14': TechnicalIndicators.rsi(closes, 14),

            # MACD
            **TechnicalIndicators.macd(closes),

            # 布林带
            **{f'boll_{k}': v for k, v in TechnicalIndicators.boll(closes).items()},

            # KDJ
            **TechnicalIndicators.kdj(highs, lows, closes),

            # ATR
            'atr14': TechnicalIndicators.atr(highs, lows, closes, 14),

            # 量比
            'volume_ratio': TechnicalIndicators.volume_ratio(volumes, 5),
        }

        return result

    @staticmethod
    def detect_anomalies(kline_data: List[Dict[str, Any]],
                        current_quote: Dict[str, Any],
                        indicators: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        检测技术异常

        Returns:
            异常列表
        """
        anomalies = []

        if not kline_data or not current_quote:
            return anomalies

        # 价格变动异常
        change_pct = current_quote.get('change_percent') or 0
        if abs(change_pct) >= 5:
            anomalies.append({
                'type': 'price_spike',
                'level': 'high' if abs(change_pct) >= 7 else 'medium',
                'message': f"价格大幅{'上涨' if change_pct > 0 else '下跌'} {abs(change_pct):.2f}%",
                'value': change_pct
            })

        # 成交量异常
        volume_ratio = current_quote.get('volume_ratio')
        if volume_ratio and volume_ratio >= 3:
            anomalies.append({
                'type': 'volume_surge',
                'level': 'high' if volume_ratio >= 5 else 'medium',
                'message': f"成交量异常放大，量比 {volume_ratio:.2f}",
                'value': volume_ratio
            })

        # RSI超买超卖
        rsi14 = indicators.get('rsi14')
        if rsi14 is not None:
            if rsi14 >= 70:
                anomalies.append({
                    'type': 'rsi_overbought',
                    'level': 'medium',
                    'message': f"RSI超买 ({rsi14:.2f})，可能存在回调风险",
                    'value': rsi14
                })
            elif rsi14 <= 30:
                anomalies.append({
                    'type': 'rsi_oversold',
                    'level': 'medium',
                    'message': f"RSI超卖 ({rsi14:.2f})，可能存在反弹机会",
                    'value': rsi14
                })

        # MACD金叉死叉
        dif = indicators.get('dif')
        dea = indicators.get('dea')
        if dif is not None and dea is not None:
            # 需要前一天的DIF和DEA来判断交叉
            if len(kline_data) >= 2:
                prev_indicators = TechnicalIndicators.calculate_all(kline_data[:-1])
                prev_dif = prev_indicators.get('dif')
                prev_dea = prev_indicators.get('dea')

                if prev_dif is not None and prev_dea is not None:
                    # 金叉: 昨天DIF<DEA，今天DIF>DEA
                    if prev_dif < prev_dea and dif > dea:
                        anomalies.append({
                            'type': 'macd_golden_cross',
                            'level': 'medium',
                            'message': "MACD金叉形成，买入信号",
                            'value': {'dif': dif, 'dea': dea}
                        })
                    # 死叉: 昨天DIF>DEA，今天DIF<DEA
                    elif prev_dif > prev_dea and dif < dea:
                        anomalies.append({
                            'type': 'macd_death_cross',
                            'level': 'medium',
                            'message': "MACD死叉形成，卖出信号",
                            'value': {'dif': dif, 'dea': dea}
                        })

        # 布林带突破
        price = current_quote.get('price')
        boll_upper = indicators.get('boll_upper')
        boll_lower = indicators.get('boll_lower')

        if price and boll_upper and boll_lower:
            if price > boll_upper:
                anomalies.append({
                    'type': 'boll_break_upper',
                    'level': 'medium',
                    'message': "价格突破布林带上轨，强势上涨",
                    'value': {'price': price, 'upper': boll_upper}
                })
            elif price < boll_lower:
                anomalies.append({
                    'type': 'boll_break_lower',
                    'level': 'medium',
                    'message': "价格跌破布林带下轨，超卖信号",
                    'value': {'price': price, 'lower': boll_lower}
                })

        # KDJ金叉死叉
        k = indicators.get('k')
        d = indicators.get('d')
        if k is not None and d is not None:
            if len(kline_data) >= 2:
                prev_indicators = TechnicalIndicators.calculate_all(kline_data[:-1])
                prev_k = prev_indicators.get('k')
                prev_d = prev_indicators.get('d')

                if prev_k is not None and prev_d is not None:
                    if prev_k < prev_d and k > d and k < 30:
                        anomalies.append({
                            'type': 'kdj_golden_cross',
                            'level': 'medium',
                            'message': f"KDJ低位金叉 ({k:.2f}, {d:.2f})，买入信号",
                            'value': {'k': k, 'd': d}
                        })
                    elif prev_k > prev_d and k < d and k > 70:
                        anomalies.append({
                            'type': 'kdj_death_cross',
                            'level': 'medium',
                            'message': f"KDJ高位死叉 ({k:.2f}, {d:.2f})，卖出信号",
                            'value': {'k': k, 'd': d}
                        })

        return anomalies


# 便捷函数
def calculate_ma(values: List[float], window: int) -> Optional[float]:
    """计算移动平均线"""
    return TechnicalIndicators.sma(values, window)


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """计算RSI"""
    return TechnicalIndicators.rsi(closes, period)


def calculate_macd(closes: List[float]) -> Dict[str, Optional[float]]:
    """计算MACD"""
    return TechnicalIndicators.macd(closes)


def calculate_boll(closes: List[float]) -> Dict[str, Optional[float]]:
    """计算布林带"""
    return TechnicalIndicators.boll(closes)
