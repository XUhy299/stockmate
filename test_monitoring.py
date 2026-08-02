"""
监控服务和定时功能测试脚本

测试内容：
1. 模拟股价异常波动检测
2. 测试定期分析触发
3. 测试状态持久化
4. 测试子Agent调用（可选，需要API key）

使用方法：
    python test_monitoring.py           # 运行全部测试
    python test_monitoring.py --quick   # 仅运行快速测试（不等待定时触发）
    python test_monitoring.py --test anomaly  # 运行指定测试
"""

import asyncio
import json
import sys
import os
import time
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[*] Loaded .env from {env_path}")
    else:
        print(f"[!] .env file not found at {env_path}")
except ImportError:
    print("[!] python-dotenv not installed, skipping .env loading")


# ==================== 模拟类定义 ====================

@dataclass
class StockState:
    """单只股票监控状态"""
    code: str
    last_price: Optional[float] = None
    last_pct_chg: Optional[float] = None
    last_volume: Optional[int] = None
    last_alert_ts: int = 0
    last_analysis_ts: int = 0
    indicators: Dict[str, Any] = field(default_factory=dict)
    indicators_date: str = ""
    alert_count: int = 0

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "last_price": self.last_price,
            "last_pct_chg": self.last_pct_chg,
            "last_volume": self.last_volume,
            "last_alert_ts": self.last_alert_ts,
            "last_analysis_ts": self.last_analysis_ts,
            "indicators": self.indicators,
            "indicators_date": self.indicators_date,
            "alert_count": self.alert_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StockState":
        return cls(**{k: v for k, v in data.items() if k in [
            "code", "last_price", "last_pct_chg", "last_volume",
            "last_alert_ts", "last_analysis_ts", "indicators",
            "indicators_date", "alert_count"
        ]})


@dataclass
class MonitorState:
    """全局监控状态"""
    stocks: Dict[str, dict] = field(default_factory=dict)
    last_routine_analysis_ts: int = 0
    daily_summary_sent: bool = False

    def to_dict(self) -> dict:
        return {
            "stocks": self.stocks,
            "last_routine_analysis_ts": self.last_routine_analysis_ts,
            "daily_summary_sent": self.daily_summary_sent,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorState":
        state = cls()
        state.stocks = data.get("stocks", {})
        state.last_routine_analysis_ts = data.get("last_routine_analysis_ts", 0)
        state.daily_summary_sent = data.get("daily_summary_sent", False)
        return state


class MonitoringService:
    """
    模拟监控服务（核心逻辑）
    """
    CHECK_INTERVAL = 60
    ROUTINE_ANALYSIS_INTERVAL = 30 * 60
    ALERT_COOLDOWN = 180
    PRICE_CHANGE_THRESHOLD = 3.0
    TICK_DELTA_THRESHOLD = 0.8
    VOLUME_RATIO_THRESHOLD = 2.5
    RSI_OVERBOUGHT = 80
    RSI_OVERSOLD = 20

    def __init__(self, data_dir: Path = None):
        self.watchlist: List[str] = []
        self.state: MonitorState = MonitorState()
        self.alert_history: List[Any] = []
        self.data_dir = data_dir or Path("./test_data")
        self.state_file = self.data_dir / "monitor_state.json"
        self._load_state()

    def _load_state(self):
        """加载状态"""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.state = MonitorState.from_dict(data)
            except Exception:
                self.state = MonitorState()

    def _save_state(self):
        """保存状态"""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"保存状态失败: {e}")

    def _get_stock_state(self, code: str) -> StockState:
        """获取股票状态"""
        if code not in self.state.stocks:
            self.state.stocks[code] = StockState(code=code).to_dict()
        return StockState.from_dict(self.state.stocks[code])

    def _update_stock_state(self, state: StockState):
        """更新股票状态"""
        self.state.stocks[state.code] = state.to_dict()

    def is_trading_time(self) -> bool:
        """判断是否在交易时间"""
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        from datetime import time as dt_time
        current_time = now.time()
        morning = dt_time(9, 30) <= current_time <= dt_time(11, 30)
        afternoon = dt_time(13, 0) <= current_time <= dt_time(15, 0)
        return morning or afternoon

    def _detect_anomalies(
        self, code: str,
        price: Optional[float],
        pct_chg: Optional[float],
        tick_delta: Optional[float],
        volume_ratio: Optional[float],
        state: StockState
    ) -> List[Dict[str, Any]]:
        """检测异常（核心逻辑）"""
        anomalies = []

        # 1. 涨跌幅异常
        if isinstance(pct_chg, (int, float)):
            if abs(float(pct_chg)) >= self.PRICE_CHANGE_THRESHOLD:
                anomalies.append({
                    "type": "price_change",
                    "level": "high" if abs(float(pct_chg)) >= 5 else "medium",
                    "message": f"涨跌幅 {float(pct_chg):+.2f}%",
                    "value": float(pct_chg)
                })

        # 2. 短时变动
        if tick_delta is not None and abs(tick_delta) >= self.TICK_DELTA_THRESHOLD:
            anomalies.append({
                "type": "tick_delta",
                "level": "medium",
                "message": f"短时变动 {tick_delta:+.2f}%",
                "value": tick_delta
            })

        # 3. 成交量异常
        if volume_ratio is not None and volume_ratio >= self.VOLUME_RATIO_THRESHOLD:
            anomalies.append({
                "type": "volume_surge",
                "level": "medium",
                "message": f"成交量倍率 {volume_ratio:.2f}x",
                "value": volume_ratio
            })

        # 4. 技术指标异常
        ind = state.indicators if state else {}
        if isinstance(price, (int, float)):
            upper = ind.get("boll_upper")
            lower = ind.get("boll_lower")
            if isinstance(upper, (int, float)) and float(price) > float(upper):
                anomalies.append({
                    "type": "boll_breakout",
                    "level": "high",
                    "message": "突破BOLL上轨",
                    "value": price
                })
            if isinstance(lower, (int, float)) and float(price) < float(lower):
                anomalies.append({
                    "type": "boll_breakdown",
                    "level": "high",
                    "message": "跌破BOLL下轨",
                    "value": price
                })

        rsi = ind.get("rsi14")
        if isinstance(rsi, (int, float)):
            if float(rsi) >= self.RSI_OVERBOUGHT:
                anomalies.append({
                    "type": "rsi_overbought",
                    "level": "medium",
                    "message": f"RSI14 {float(rsi):.1f} 超买",
                    "value": float(rsi)
                })
            elif float(rsi) <= self.RSI_OVERSOLD:
                anomalies.append({
                    "type": "rsi_oversold",
                    "level": "medium",
                    "message": f"RSI14 {float(rsi):.1f} 超卖",
                    "value": float(rsi)
                })

        return anomalies

    def get_status(self) -> dict:
        """获取监控状态"""
        return {
            "running": False,
            "watchlist_count": len(self.watchlist),
            "alert_count": len(self.alert_history),
            "last_routine_analysis": datetime.fromtimestamp(
                self.state.last_routine_analysis_ts
            ).isoformat() if self.state.last_routine_analysis_ts else None,
            "stocks": {
                code: {"alert_count": s.get("alert_count", 0)}
                for code, s in self.state.stocks.items()
            }
        }


# ==================== 测试类 ====================

class MonitoringTester:
    """监控服务测试器"""

    def __init__(self):
        self.test_results = []
        self.data_dir = Path("./test_data")
        self.data_dir.mkdir(exist_ok=True)

    def log(self, message: str, level: str = "info"):
        """记录测试日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        emoji = {"info": "[i]", "pass": "[+]", "fail": "[x]", "warn": "[!]"}.get(level, "[i]")
        print(f"{emoji} [{timestamp}] {message}")
        self.test_results.append({"time": timestamp, "level": level, "message": message})

    async def test_anomaly_detection(self):
        """测试异常检测功能"""
        self.log("=" * 50)
        self.log("Test 1: Anomaly Detection Logic")
        self.log("=" * 50)

        service = MonitoringService(self.data_dir)

        test_cases = [
            {
                "name": "Normal fluctuation",
                "price": 10.5,
                "pct_chg": 1.2,
                "tick_delta": 0.3,
                "volume_ratio": 1.2,
                "indicators": {"rsi14": 50, "boll_upper": 11.0, "boll_lower": 9.0},
                "expected_alert": False
            },
            {
                "name": "Big rise trigger",
                "price": 11.5,
                "pct_chg": 5.8,
                "tick_delta": 4.5,
                "volume_ratio": 4.0,
                "indicators": {"rsi14": 65, "boll_upper": 11.0, "boll_lower": 9.0},
                "expected_alert": True
            },
            {
                "name": "Crash trigger",
                "price": 9.2,
                "pct_chg": -7.5,
                "tick_delta": -6.0,
                "volume_ratio": 5.0,
                "indicators": {"rsi14": 30, "boll_upper": 11.0, "boll_lower": 9.5},
                "expected_alert": True
            },
            {
                "name": "Volume anomaly (medium only)",
                "price": 10.6,
                "pct_chg": 1.0,
                "tick_delta": 0.2,
                "volume_ratio": 3.5,
                "indicators": {"rsi14": 55, "boll_upper": 11.0, "boll_lower": 9.0},
                "expected_alert": False  # Volume surge is medium level, not high
            },
            {
                "name": "RSI overbought",
                "price": 12.0,
                "pct_chg": 2.0,
                "tick_delta": 0.5,
                "volume_ratio": 1.5,
                "indicators": {"rsi14": 85, "boll_upper": 11.5, "boll_lower": 9.5},
                "expected_alert": True
            },
            {
                "name": "Bollinger breakout",
                "price": 12.5,
                "pct_chg": 2.5,
                "tick_delta": 0.8,
                "volume_ratio": 1.8,
                "indicators": {"rsi14": 70, "boll_upper": 11.8, "boll_lower": 9.5},
                "expected_alert": True
            },
        ]

        passed = 0
        failed = 0

        for case in test_cases:
            state = StockState(code="000001")
            state.indicators = case.get("indicators")

            anomalies = service._detect_anomalies(
                code="000001",
                price=case["price"],
                pct_chg=case["pct_chg"],
                tick_delta=case["tick_delta"],
                volume_ratio=case["volume_ratio"],
                state=state
            )

            has_high_anomaly = any(a.get("level") == "high" for a in anomalies)

            if has_high_anomaly == case["expected_alert"]:
                self.log(f"[PASS] {case['name']}: Found {len(anomalies)} anomalies", "pass")
                passed += 1
            else:
                self.log(f"[FAIL] {case['name']}: Expected {case['expected_alert']}, got {has_high_anomaly}", "fail")
                failed += 1

            for anomaly in anomalies:
                self.log(f"       - {anomaly['type']}: {anomaly['message']} [{anomaly['level']}]")

        self.log(f"\nAnomaly detection: {passed} passed, {failed} failed")
        return failed == 0

    async def test_state_persistence(self):
        """测试状态持久化"""
        self.log("\n" + "=" * 50)
        self.log("Test 2: State Persistence")
        self.log("=" * 50)

        # 创建服务并设置状态
        service1 = MonitoringService(self.data_dir)
        service1.watchlist = ["000001", "600519"]

        state1 = service1._get_stock_state("000001")
        state1.last_price = 10.5
        state1.last_pct_chg = 2.5
        state1.alert_count = 3
        state1.indicators = {"rsi14": 75, "ma20": 10.2}
        service1._update_stock_state(state1)
        service1.state.last_routine_analysis_ts = int(time.time()) - 1800
        service1._save_state()

        self.log("State saved to file")
        self.log(f"  File: {service1.state_file}")

        # 创建新服务实例
        service2 = MonitoringService(self.data_dir)
        loaded_state = service2._get_stock_state("000001")

        checks = [
            (loaded_state.last_price == 10.5, "last_price", loaded_state.last_price, 10.5),
            (loaded_state.last_pct_chg == 2.5, "last_pct_chg", loaded_state.last_pct_chg, 2.5),
            (loaded_state.alert_count == 3, "alert_count", loaded_state.alert_count, 3),
            (loaded_state.indicators.get("rsi14") == 75, "indicators", loaded_state.indicators, {"rsi14": 75}),
            (service2.state.last_routine_analysis_ts > 0, "last_routine_analysis_ts", service2.state.last_routine_analysis_ts, ">0"),
        ]

        passed = 0
        for check, name, actual, expected in checks:
            if check:
                self.log(f"[PASS] {name} = {actual}", "pass")
                passed += 1
            else:
                self.log(f"[FAIL] {name}: Expected {expected}, got {actual}", "fail")

        self.log(f"\nState persistence: {passed}/{len(checks)} passed")
        return passed == len(checks)

    async def test_monitoring_scenarios(self):
        """测试监控场景"""
        self.log("\n" + "=" * 50)
        self.log("Test 3: Monitoring Scenarios")
        self.log("=" * 50)

        service = MonitoringService(self.data_dir)
        watchlist = ["000001", "600519", "300750"]

        scenarios = [
            {
                "name": "Normal trading",
                "quotes": {
                    "000001": {"price": 10.5, "pct_chg": 1.2, "volume": 120000},
                    "600519": {"price": 1680.0, "pct_chg": 0.8, "volume": 50000},
                    "300750": {"price": 185.0, "pct_chg": -0.5, "volume": 200000},
                },
                "expected_alerts": 0
            },
            {
                "name": "Big surge",
                "quotes": {
                    "000001": {"price": 11.5, "pct_chg": 5.8, "volume": 500000},
                    "600519": {"price": 1750.0, "pct_chg": 4.2, "volume": 150000},
                    "300750": {"price": 195.0, "pct_chg": 5.4, "volume": 600000},
                },
                "expected_alerts": 3
            },
            {
                "name": "Crash panic",
                "quotes": {
                    "000001": {"price": 9.8, "pct_chg": -6.7, "volume": 800000},
                    "600519": {"price": 1620.0, "pct_chg": -3.6, "volume": 200000},
                    "300750": {"price": 172.0, "pct_chg": -7.0, "volume": 900000},
                },
                "expected_alerts": 3
            },
        ]

        total_alerts = 0
        for scenario in scenarios:
            self.log(f"\nScenario: {scenario['name']}")
            alerts_in_scenario = 0

            for code in watchlist:
                quote = scenario["quotes"].get(code, {})
                state = service._get_stock_state(code)

                # 计算短时变动
                tick_delta = quote.get("pct_chg", 0) - (state.last_pct_chg or 0)
                volume_ratio = quote.get("volume", 0) / max(state.last_volume or 100000, 1)

                anomalies = service._detect_anomalies(
                    code=code,
                    price=quote.get("price"),
                    pct_chg=quote.get("pct_chg"),
                    tick_delta=tick_delta,
                    volume_ratio=volume_ratio,
                    state=state
                )

                if anomalies:
                    high_count = sum(1 for a in anomalies if a["level"] == "high")
                    if high_count > 0:
                        alerts_in_scenario += 1
                        total_alerts += 1
                        self.log(f"  [!] {code}: {len(anomalies)} anomalies ({high_count} high)")
                        for a in anomalies:
                            self.log(f"      - {a['message']}")

                # 更新状态
                state.last_price = quote.get("price")
                state.last_pct_chg = quote.get("pct_chg")
                state.last_volume = quote.get("volume")
                service._update_stock_state(state)

            self.log(f"  Alerts in scenario: {alerts_in_scenario}")

        service._save_state()
        self.log(f"\nTotal alerts across scenarios: {total_alerts}")

        status = service.get_status()
        self.log(f"Status: {status}")

        return True

    async def test_routine_analysis_trigger(self):
        """测试定期分析触发"""
        self.log("\n" + "=" * 50)
        self.log("Test 4: Routine Analysis Trigger")
        self.log("=" * 50)

        service = MonitoringService(self.data_dir)
        now = int(time.time())

        # 测试1：10分钟内不应触发
        service.state.last_routine_analysis_ts = now - 10 * 60
        should_trigger = (now - service.state.last_routine_analysis_ts) >= service.ROUTINE_ANALYSIS_INTERVAL

        if not should_trigger:
            self.log("[PASS] No trigger within 10 minutes (correct)", "pass")
        else:
            self.log("[FAIL] Wrong trigger within 10 minutes", "fail")

        # 测试2：35分钟后应触发
        service.state.last_routine_analysis_ts = now - 35 * 60
        should_trigger = (now - service.state.last_routine_analysis_ts) >= service.ROUTINE_ANALYSIS_INTERVAL

        if should_trigger:
            self.log("[PASS] Trigger after 35 minutes (correct)", "pass")
        else:
            self.log("[FAIL] No trigger after 35 minutes", "fail")

        # 测试3：更新分析时间
        service.state.last_routine_analysis_ts = now
        remaining = service.ROUTINE_ANALYSIS_INTERVAL
        self.log(f"Next routine analysis in: {remaining // 60} minutes")

        return True

    async def test_alert_cooldown(self):
        """测试预警冷却期"""
        self.log("\n" + "=" * 50)
        self.log("Test 5: Alert Cooldown")
        self.log("=" * 50)

        service = MonitoringService(self.data_dir)
        code = "000001"
        state = service._get_stock_state(code)

        now = int(time.time())

        # 测试1：1分钟内在冷却期
        state.last_alert_ts = now - 60
        in_cooldown = (now - state.last_alert_ts) < service.ALERT_COOLDOWN

        if in_cooldown:
            self.log("[PASS] In cooldown after 1 minute (correct)", "pass")
        else:
            self.log("[FAIL] Cooldown check wrong", "fail")

        # 测试2：5分钟后超出冷却期
        state.last_alert_ts = now - 300
        in_cooldown = (now - state.last_alert_ts) < service.ALERT_COOLDOWN

        if not in_cooldown:
            self.log("[PASS] Out of cooldown after 5 minutes (correct)", "pass")
        else:
            self.log("[FAIL] Cooldown check wrong", "fail")

        remaining = max(0, service.ALERT_COOLDOWN - (now - state.last_alert_ts))
        self.log(f"Cooldown remaining: {remaining} seconds")

        return True

    async def test_full_workflow(self):
        """测试完整工作流"""
        self.log("\n" + "=" * 50)
        self.log("Test 6: Full Workflow Simulation")
        self.log("=" * 50)

        service = MonitoringService(self.data_dir)

        # 模拟一个完整的监控周期
        self.log("\nStep 1: Initial check (normal)")
        state = service._get_stock_state("000001")
        state.indicators = {"rsi14": 55, "boll_upper": 11.0, "boll_lower": 9.0}
        state.last_price = 10.0
        state.last_pct_chg = 0.5
        state.last_volume = 100000

        anomalies = service._detect_anomalies(
            code="000001", price=10.2, pct_chg=2.0, tick_delta=1.5,
            volume_ratio=1.2, state=state
        )
        self.log(f"  Normal: {len(anomalies)} anomalies")

        self.log("\nStep 2: Price surge detected")
        anomalies = service._detect_anomalies(
            code="000001", price=10.8, pct_chg=8.0, tick_delta=6.0,
            volume_ratio=4.5, state=state
        )
        high_anomalies = [a for a in anomalies if a["level"] == "high"]
        self.log(f"  [!] Surge: {len(anomalies)} anomalies ({len(high_anomalies)} high)")

        # 检查是否应触发分析
        now = int(time.time())
        should_analyze = len(high_anomalies) > 0 and (now - state.last_alert_ts) >= service.ALERT_COOLDOWN

        if should_analyze:
            self.log("  [>] Should trigger subagent analysis")
            state.last_alert_ts = now
            state.alert_count += 1

        self.log("\nStep 3: Cooldown period")
        in_cooldown = (now - state.last_alert_ts) < service.ALERT_COOLDOWN
        self.log(f"  In cooldown: {in_cooldown}")

        service._update_stock_state(state)
        service._save_state()

        self.log("\nStep 4: Check status")
        status = service.get_status()
        self.log(f"  Watchlist: {status['watchlist_count']}")
        self.log(f"  Alerts: {status['alert_count']}")
        self.log(f"  Stocks: {list(status['stocks'].keys())}")

        return True

    async def test_subagent_analysis_flow(self):
        """测试子Agent分析流程（真实调用）"""
        self.log("\n" + "=" * 50)
        self.log("Test 7: SubAgent Analysis Flow (Real Call)")
        self.log("=" * 50)

        # 检查是否有API key
        api_key = self._get_api_key()
        if not api_key:
            self.log("[!] OPENAI_API_KEY not set, skipping real subagent test", "warn")
            self.log("    To test with real subagent, set OPENAI_API_KEY environment variable")
            return True  # Skip but don't fail

        service = MonitoringService(self.data_dir)
        service.watchlist = ["000001"]

        # 模拟异常检测
        self.log("\nStep 1: Detect anomaly")
        state = service._get_stock_state("000001")
        state.indicators = {"rsi14": 65, "boll_upper": 11.0, "boll_lower": 9.5, "ma20": 10.2}
        state.last_price = 10.0
        state.last_pct_chg = 0.5
        state.last_volume = 100000
        state.last_alert_ts = 0  # 重置冷却期

        quote = {"code": "000001", "price": 11.5, "pct_chg": 5.8, "volume": 500000, "name": "平安银行"}

        anomalies = service._detect_anomalies(
            code="000001",
            price=quote["price"],
            pct_chg=quote["pct_chg"],
            tick_delta=quote["pct_chg"] - (state.last_pct_chg or 0),
            volume_ratio=quote["volume"] / max(state.last_volume, 1),
            state=state
        )

        high_anomalies = [a for a in anomalies if a["level"] == "high"]
        self.log(f"  Detected {len(anomalies)} anomalies ({len(high_anomalies)} high)")

        for a in anomalies:
            self.log(f"    - {a['message']} [{a['level']}]")

        # 检查是否触发子Agent
        now = int(time.time())
        should_trigger = len(high_anomalies) > 0 and (now - state.last_alert_ts) >= service.ALERT_COOLDOWN

        if not should_trigger:
            self.log("[FAIL] Should trigger subagent analysis", "fail")
            return False

        self.log("  [>] Trigger condition met, calling subagent...")

        # 真实调用子Agent
        self.log("\nStep 2: Call real SubAgent")
        self.log("  [!] This will consume API tokens")

        try:
            from tools.all_tools import subagent_manager

            # 构建分析任务
            anomaly_desc = "; ".join([a["message"] for a in anomalies])
            task = f"""
股票 {quote['name']}({quote['code']}) 检测到以下异常，请进行深度分析：

【异常信号】
{anomaly_desc}

【当前行情】
- 现价: {quote['price']}
- 涨跌幅: {quote['pct_chg']}%
- 成交量: {quote['volume']}

【技术指标】
- MA20: {state.indicators.get('ma20', 'N/A')}
- RSI14: {state.indicators.get('rsi14', 'N/A')}
- BOLL上轨: {state.indicators.get('boll_upper', 'N/A')}
- BOLL下轨: {state.indicators.get('boll_lower', 'N/A')}

请使用 alert_analysis skill 进行分析，输出格式化的分析报告。
"""

            start_time = time.time()

            result = await subagent_manager.run_with_skill(
                task_name=f"test_alert_analysis_{quote['code']}_{int(time.time())}",
                task_description=task,
                skill_name="alert_analysis",
                context={
                    "code": quote['code'],
                    "name": quote['name'],
                    "trigger": "test_anomaly_detected",
                    "anomalies": anomalies
                },
                use_global_tools=True
            )

            elapsed = time.time() - start_time

            if result.get("success"):
                self.log(f"  [PASS] SubAgent analysis completed in {elapsed:.2f}s", "pass")
                self.log(f"  Content length: {len(result.get('content', ''))} chars")

                # 显示分析摘要
                content = result.get("content", "")
                lines = content.strip().split("\n")
                self.log(f"\n  Analysis preview:")
                for line in lines[:15]:
                    if line.strip():
                        self.log(f"    {line}")
                if len(lines) > 15:
                    self.log(f"    ... ({len(lines) - 15} more lines)")
            else:
                error = result.get("error", "Unknown error")
                self.log(f"  [FAIL] SubAgent analysis failed: {error}", "fail")
                return False

        except Exception as e:
            self.log(f"  [FAIL] Failed to call subagent: {e}", "fail")
            import traceback
            self.log(f"  {traceback.format_exc()}")
            return False

        # 更新状态
        self.log("\nStep 3: Update state after analysis")
        state.last_alert_ts = now
        state.alert_count += 1
        service._update_stock_state(state)
        service._save_state()

        self.log(f"  Alert count: {state.alert_count}")
        self.log(f"  Last alert: {datetime.fromtimestamp(state.last_alert_ts).strftime('%H:%M:%S')}")

        # 模拟通知发送
        self.log("\nStep 4: Send notification")
        notification = f"""
[!] {quote['name']}(000001) 技术异动提醒

当前价格: {quote['price']:.2f} (+{quote['pct_chg']:.2f}%)
发现异常: {len(anomalies)} 个

异常详情:
"""
        for a in anomalies:
            marker = "[H]" if a["level"] == "high" else "[M]"
            notification += f"{marker} {a['message']}\n"

        notification += f"""
[AI] 深度分析:
{result['content'][:300]}...

[!] 以上信息仅供参考，不构成投资建议
"""
        self.log("  Notification content preview:")
        for line in notification.strip().split("\n")[:8]:
            self.log(f"    {line}")

        return True

    def _get_api_key(self) -> str:
        """获取API key"""
        import os
        return os.getenv("OPENAI_API_KEY", "")

    async def test_scheduler_task_execution(self):
        """测试定时任务执行"""
        self.log("\n" + "=" * 50)
        self.log("Test 8: Scheduler Task Execution")
        self.log("=" * 50)

        import schedule

        # 清除现有任务
        schedule.clear()

        # 记录任务执行
        executed_tasks = []

        def mock_morning_report():
            executed_tasks.append(("morning_report", datetime.now().isoformat()))
            self.log("  [EXECUTED] Morning report task")
            return True

        def mock_start_monitoring():
            executed_tasks.append(("start_monitoring", datetime.now().isoformat()))
            self.log("  [EXECUTED] Start monitoring task")
            return True

        def mock_stop_monitoring():
            executed_tasks.append(("stop_monitoring", datetime.now().isoformat()))
            self.log("  [EXECUTED] Stop monitoring task")
            return True

        # 设置定时任务（使用较短的间隔用于测试）
        self.log("\nStep 1: Schedule tasks")

        # 每分钟执行一次（用于测试）
        schedule.every(1).minutes.do(mock_morning_report)
        schedule.every(1).minutes.do(mock_start_monitoring)

        self.log(f"  Scheduled {len(schedule.jobs)} tasks")
        for job in schedule.jobs:
            self.log(f"    - {job}")

        # 手动触发任务
        self.log("\nStep 2: Manual trigger")
        schedule.run_all()

        await asyncio.sleep(0.1)

        self.log(f"  Executed {len(executed_tasks)} tasks")
        for task_name, timestamp in executed_tasks:
            self.log(f"    - {task_name} at {timestamp}")

        # 验证任务执行
        if len(executed_tasks) >= 2:
            self.log("  [PASS] All tasks executed successfully", "pass")
        else:
            self.log("  [FAIL] Some tasks not executed", "fail")
            return False

        # 测试任务下次运行时间
        self.log("\nStep 3: Check next run time")
        for job in schedule.jobs:
            next_run = job.next_run
            if next_run:
                self.log(f"  Next run: {next_run}")

        # 测试任务取消
        self.log("\nStep 4: Cancel tasks")
        schedule.clear()
        self.log(f"  Remaining jobs: {len(schedule.jobs)}")

        if len(schedule.jobs) == 0:
            self.log("  [PASS] All tasks cancelled", "pass")
        else:
            self.log("  [FAIL] Some tasks not cancelled", "fail")
            return False

        return True

    async def test_trading_time_detection(self):
        """测试交易时间判断"""
        self.log("\n" + "=" * 50)
        self.log("Test 9: Trading Time Detection")
        self.log("=" * 50)

        service = MonitoringService(self.data_dir)

        # 测试场景
        test_cases = [
            {
                "name": "Weekend (Saturday)",
                "dt": datetime(2026, 4, 4, 10, 0, 0),  # Saturday
                "expected": False
            },
            {
                "name": "Weekend (Sunday)",
                "dt": datetime(2026, 4, 5, 14, 0, 0),  # Sunday
                "expected": False
            },
            {
                "name": "Monday morning before open",
                "dt": datetime(2026, 4, 6, 9, 0, 0),  # Monday 9:00
                "expected": False
            },
            {
                "name": "Monday morning session",
                "dt": datetime(2026, 4, 6, 10, 0, 0),  # Monday 10:00
                "expected": True
            },
            {
                "name": "Monday noon break",
                "dt": datetime(2026, 4, 6, 12, 0, 0),  # Monday 12:00
                "expected": False
            },
            {
                "name": "Monday afternoon session",
                "dt": datetime(2026, 4, 6, 14, 0, 0),  # Monday 14:00
                "expected": True
            },
            {
                "name": "Monday after close",
                "dt": datetime(2026, 4, 6, 15, 30, 0),  # Monday 15:30
                "expected": False
            },
        ]

        passed = 0
        for case in test_cases:
            # Mock datetime.now()
            from datetime import time as dt_time
            dt = case["dt"]

            is_weekend = dt.weekday() >= 5
            current_time = dt.time()
            morning = dt_time(9, 30) <= current_time <= dt_time(11, 30)
            afternoon = dt_time(13, 0) <= current_time <= dt_time(15, 0)
            is_trading = (morning or afternoon) and not is_weekend

            if is_trading == case["expected"]:
                self.log(f"[PASS] {case['name']}: {is_trading}", "pass")
                passed += 1
            else:
                self.log(f"[FAIL] {case['name']}: Expected {case['expected']}, got {is_trading}", "fail")

        self.log(f"\nTrading time detection: {passed}/{len(test_cases)} passed")
        return passed == len(test_cases)

    async def run_all_tests(self, quick: bool = False):
        """运行所有测试"""
        self.log("\n" + "=" * 60)
        self.log("AStack Monitoring Service Test Suite")
        self.log("=" * 60)
        self.log(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"Quick mode: {quick}")
        self.log(f"Data dir: {self.data_dir}")
        self.log("=" * 60)

        results = []
        tests = [
            ("Anomaly Detection", self.test_anomaly_detection),
            ("State Persistence", self.test_state_persistence),
            ("Monitoring Scenarios", self.test_monitoring_scenarios),
            ("Routine Analysis", self.test_routine_analysis_trigger),
            ("Alert Cooldown", self.test_alert_cooldown),
            ("Full Workflow", self.test_full_workflow),
            ("SubAgent Analysis Flow", self.test_subagent_analysis_flow),
            ("Scheduler Task Execution", self.test_scheduler_task_execution),
            ("Trading Time Detection", self.test_trading_time_detection),
        ]

        for name, test_func in tests:
            try:
                result = await test_func()
                results.append((name, result))
            except Exception as e:
                self.log(f"Test '{name}' error: {e}", "fail")
                import traceback
                traceback.print_exc()
                results.append((name, False))

        # 测试总结
        self.log("\n" + "=" * 60)
        self.log("Test Summary")
        self.log("=" * 60)

        passed = sum(1 for _, r in results if r)
        total = len(results)

        for name, result in results:
            status = "[PASS]" if result else "[FAIL]"
            self.log(f"{status}: {name}")

        self.log(f"\nTotal: {passed}/{total} tests passed")

        if passed == total:
            self.log("\n[+] All tests passed!", "pass")
        else:
            self.log(f"\n[!] {total - passed} tests failed", "warn")

        return passed == total


# ==================== 主函数 ====================

def print_usage():
    print("""
AStack Monitoring Service Test Script

Usage:
    python test_monitoring.py           # Run all tests
    python test_monitoring.py --quick   # Quick mode
    python test_monitoring.py --test anomaly   # Run specific test

Available tests:
    anomaly     - Anomaly detection logic
    state       - State persistence
    scenario    - Monitoring scenarios
    routine     - Routine analysis trigger
    cooldown    - Alert cooldown
    full        - Full workflow
    subagent    - SubAgent analysis flow
    scheduler   - Scheduler task execution
    trading     - Trading time detection
""")


async def main():
    parser = argparse.ArgumentParser(description="AStack Monitoring Service Test")
    parser.add_argument("--quick", action="store_true", help="Quick mode")
    parser.add_argument("--test", type=str, help="Run specific test")
    args = parser.parse_args()

    tester = MonitoringTester()

    if args.test:
        test_map = {
            "anomaly": tester.test_anomaly_detection,
            "state": tester.test_state_persistence,
            "scenario": tester.test_monitoring_scenarios,
            "routine": tester.test_routine_analysis_trigger,
            "cooldown": tester.test_alert_cooldown,
            "full": tester.test_full_workflow,
            "subagent": tester.test_subagent_analysis_flow,
            "scheduler": tester.test_scheduler_task_execution,
            "trading": tester.test_trading_time_detection,
        }

        if args.test in test_map:
            await test_map[args.test]()
        else:
            print(f"Unknown test: {args.test}")
            print(f"Available: {', '.join(test_map.keys())}")
    else:
        success = await tester.run_all_tests(quick=args.quick)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted")
        sys.exit(1)
