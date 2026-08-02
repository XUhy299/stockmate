"""
实时监控服务 - 心跳机制 + Agent触发

核心流程:
1. 每分钟使用 DetectAnomaliesTool 检测自选股票异常
2. 检测到异常 -> 通过消息队列唤醒主Agent进行分析
3. 每30分钟无异常 -> 通过消息队列唤醒主Agent进行定期分析
"""
import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import config
from core.notifier import notification_manager
from tools.all_tools import DetectAnomaliesTool, GetWatchlistTool
from channels import wecom_channel, feishu_channel

import logging
logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """预警记录"""
    code: str
    name: str
    alert_type: str
    level: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class MonitoringService:
    """实时监控服务"""

    CHECK_INTERVAL = 60  # 检查间隔（秒）
    ROUTINE_INTERVAL = 30 * 60  # 定期分析间隔（30分钟）
    ALERT_COOLDOWN = 180  # 预警冷却期（3分钟）

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._agent = None

        self.watchlist: List[str] = []
        self.alert_history: List[Alert] = []
        self.last_alert_ts: Dict[str, int] = {}  # 股票上次告警时间
        self.last_routine_ts: int = 0  # 上次定期分析时间

        # 检测工具
        self._detect_tool = DetectAnomaliesTool()

        # 企业微信目标用户
        self._wecom_target_users: List[str] = []
        self._load_wecom_users()

        # 飞书目标用户
        self._feishu_target_users: List[str] = []
        self._load_feishu_users()

    def _load_wecom_users(self):
        """加载企业微信目标用户"""
        import os
        users_str = os.getenv("WECOM_ALERT_USERS", "")
        if users_str:
            self._wecom_target_users = [u.strip() for u in users_str.split(",") if u.strip()]
            self.logger.info(f"📱 企业微信预警将发送给 {len(self._wecom_target_users)} 位用户")

    def _load_feishu_users(self):
        """加载飞书目标用户"""
        import os
        users_str = os.getenv("FEISHU_ALERT_USERS", "")
        if users_str:
            self._feishu_target_users = [u.strip() for u in users_str.split(",") if u.strip()]
            self.logger.info(f"📱 飞书预警将发送给 {len(self._feishu_target_users)} 位用户")

    def set_agent(self, agent):
        """设置主Agent引用"""
        self._agent = agent

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

    async def start(self, watchlist: Optional[List[str]] = None):
        """启动监控服务"""
        if self.running:
            return

        if watchlist:
            self.watchlist = watchlist
        else:
            await self._load_watchlist()

        if not self.watchlist:
            self.logger.warning("自选列表为空，监控服务未启动")
            return

        self.running = True
        self.logger.info(f"🚀 启动监控服务，监控 {len(self.watchlist)} 只股票")

        self._task = asyncio.create_task(self._monitoring_loop())

    async def _load_watchlist(self):
        """加载自选列表"""
        try:
            result = await GetWatchlistTool().run()
            if result.success and result.data:
                self.watchlist = [item.get("code") for item in result.data if item.get("code")]
                self.logger.info(f"📋 加载自选列表: {len(self.watchlist)} 只股票")
        except Exception as e:
            self.logger.error(f"加载自选列表失败: {e}")

    async def _monitoring_loop(self):
        """监控主循环 - 每分钟执行一次"""
        while self.running:
            try:
                if not self.is_trading_time():
                    await asyncio.sleep(60)
                    continue

                # 1. 每分钟检测所有股票异常
                await self._check_all_stocks()

                # 2. 每30分钟触发定期分析
                await self._check_routine_analysis()

                await asyncio.sleep(self.CHECK_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"监控循环出错: {e}")
                await asyncio.sleep(5)

    async def _check_all_stocks(self):
        """检查所有自选股 - 使用 DetectAnomaliesTool"""
        now_ts = int(time.time())

        for code in self.watchlist:
            try:
                # 使用工具检测异常
                result = await self._detect_tool.run(code=code)

                if not result.success:
                    continue

                anomalies = result.data.get("anomalies", [])
                quote = {
                    "price": result.data.get("current_price"),
                    "pct_chg": result.data.get("change_percent"),
                    "name": code
                }

                # 有异常且不在冷却期 -> 通知主Agent
                if anomalies and now_ts - self.last_alert_ts.get(code, 0) >= self.ALERT_COOLDOWN:
                    high_anomalies = [a for a in anomalies if a.get("level") in ["high", "critical"]]
                    if high_anomalies:
                        await self._notify_agent_alert(code, quote, anomalies)
                        self.last_alert_ts[code] = now_ts
                        self._record_alert(code, quote, anomalies)

            except Exception as e:
                self.logger.error(f"检查 {code} 时出错: {e}")

    async def _check_routine_analysis(self):
        """检查是否需要定期分析"""
        now_ts = int(time.time())

        if now_ts - self.last_routine_ts < self.ROUTINE_INTERVAL:
            return

        self.logger.info("⏰ 触发定期走势分析")
        self.last_routine_ts = now_ts

        await self._notify_agent_routine()

    async def _notify_agent_alert(self, code: str, quote: dict, anomalies: list):
        """通知主Agent有异常，同时发送预警给企业微信用户"""
        anomaly_desc = "；".join([a.get("message", "") for a in anomalies])

        content = f"""股票 {code} 检测到异常：

【异常信号】{anomaly_desc}

【当前行情】
- 现价: {quote.get('price', 'N/A')}
- 涨跌幅: {quote.get('pct_chg', 'N/A')}%"""

        if self._agent and hasattr(self._agent, 'post_event'):
            try:
                await self._agent.post_event(
                    "price_alert",
                    content,
                    metadata={"code": code, "anomalies": anomalies, "quote": quote}
                )
                self.logger.info(f"🚨 已通知主Agent分析 {code}")
            except Exception as e:
                self.logger.error(f"通知主Agent失败: {e}")

        # 发送通知到企业微信用户
        await self._send_alert_to_wecom_users(code, quote, anomalies)

        # 发送通知到飞书用户
        await self._send_alert_to_feishu_users(code, quote, anomalies)

        # 同时发送控制台通知
        await notification_manager.send_alert(
            code=code,
            name=quote.get("name", code),
            anomalies=anomalies,
            quote=quote
        )

    async def _send_alert_to_wecom_users(self, code: str, quote: dict, anomalies: list):
        """发送预警给企业微信用户"""
        if not wecom_channel.enabled or not self._wecom_target_users:
            return

        name = quote.get("name", code)
        price = quote.get("price", "N/A")
        pct_chg = quote.get("pct_chg", "N/A")

        # 构建预警消息
        anomaly_lines = []
        for a in anomalies:
            level_emoji = {"high": "🔴", "critical": "🔴", "medium": "🟡", "low": "🟢"}.get(a.get("level"), "⚪")
            anomaly_lines.append(f"{level_emoji} {a.get('message', '')}")

        markdown_content = f"""🚨 **{name}({code}) 技术异动提醒**

**当前行情**
- 现价: {price}
- 涨跌幅: {pct_chg}%

**异常信号**
{chr(10).join(anomaly_lines)}

---
⚠️ 详细分析正在生成中，请稍候...
_发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"""

        for user_id in self._wecom_target_users:
            try:
                success = await wecom_channel.send_message(user_id, markdown_content, msg_type="markdown")
                if success:
                    self.logger.info(f"✅ 预警已发送给用户: {user_id}")
                else:
                    self.logger.warning(f"❌ 发送预警给用户失败: {user_id}")
            except Exception as e:
                self.logger.error(f"发送预警给用户 {user_id} 时出错: {e}")

    async def _send_alert_to_feishu_users(self, code: str, quote: dict, anomalies: list):
        """发送预警给飞书用户"""
        if not feishu_channel.enabled or not self._feishu_target_users:
            return

        name = quote.get("name", code)

        for user_id in self._feishu_target_users:
            try:
                success = await feishu_channel.send_alert(
                    chat_id=user_id,
                    code=code,
                    name=name,
                    anomalies=anomalies
                )
                if success:
                    self.logger.info(f"✅ 飞书预警已发送给用户: {user_id}")
                else:
                    self.logger.warning(f"❌ 发送飞书预警给用户失败: {user_id}")
            except Exception as e:
                self.logger.error(f"发送飞书预警给用户 {user_id} 时出错: {e}")

    async def _notify_agent_routine(self):
        """通知主Agent进行定期分析"""
        content = f"""【定期分析任务 - 30分钟周期】

请对自选股进行定期走势分析：
{json.dumps(self.watchlist, ensure_ascii=False)}

分析要点：
1. 各股票的技术走势变化
2. 整体市场环境判断
3. 需要特别关注的股票
4. 操作建议总结"""

        if self._agent and hasattr(self._agent, 'post_event'):
            try:
                await self._agent.post_event(
                    "routine_analysis",
                    content,
                    metadata={"watchlist": self.watchlist, "trigger": "30min"}
                )
                self.logger.info("📊 已通知主Agent进行定期分析")
            except Exception as e:
                self.logger.error(f"通知主Agent定期分析失败: {e}")

    def _record_alert(self, code: str, quote: dict, anomalies: list):
        """记录预警"""
        for anomaly in anomalies:
            alert = Alert(
                code=code,
                name=quote.get("name", code),
                alert_type=anomaly.get("type", "unknown"),
                level=anomaly.get("level", "low"),
                message=anomaly.get("message", "")
            )
            self.alert_history.append(alert)

    def get_status(self) -> dict:
        """获取监控状态"""
        return {
            "running": self.running,
            "watchlist_count": len(self.watchlist),
            "alert_count": len(self.alert_history),
            "last_routine": datetime.fromtimestamp(self.last_routine_ts).isoformat() if self.last_routine_ts else None
        }

    def stop(self):
        """停止监控服务"""
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self.logger.info("⏹️ 监控服务已停止")


# 全局实例
monitoring_service = MonitoringService()
