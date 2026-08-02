"""
通知管理模块
处理各种通知渠道
"""
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

from config import config

logger = logging.getLogger(__name__)


class NotificationChannel(Enum):
    """通知渠道"""
    CONSOLE = "console"
    DINGTALK = "dingtalk"
    WECOM = "wecom"
    EMAIL = "email"


@dataclass
class Notification:
    """通知消息"""
    title: str
    content: str
    level: str = "info"  # info, warning, error, critical
    timestamp: str = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()
        if self.metadata is None:
            self.metadata = {}


class NotificationManager:
    """通知管理器"""

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
        self.channels: List[NotificationChannel] = []
        self._setup_channels()

    def _setup_channels(self):
        """设置通知渠道"""
        if config.NOTIFICATION_ENABLED:
            for ch in config.NOTIFICATION_CHANNELS:
                try:
                    self.channels.append(NotificationChannel(ch))
                except ValueError:
                    logger.warning(f"未知的通知渠道: {ch}")

    async def send(self, notification: Notification):
        """
        发送通知

        Args:
            notification: 通知消息
        """
        for channel in self.channels:
            try:
                if channel == NotificationChannel.CONSOLE:
                    self._send_console(notification)
                elif channel == NotificationChannel.DINGTALK:
                    await self._send_dingtalk(notification)
                elif channel == NotificationChannel.WECOM:
                    await self._send_wecom(notification)
            except Exception as e:
                logger.error(f"发送通知失败 ({channel.value}): {e}")

    def _send_console(self, notification: Notification):
        """控制台通知"""
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨"
        }
        emoji = emoji_map.get(notification.level, "ℹ️")

        print(f"\n{emoji} [{notification.level.upper()}] {notification.title}")
        print("-" * 50)
        print(notification.content)
        print("-" * 50)

    async def _send_dingtalk(self, notification: Notification):
        """钉钉通知"""
        if not config.DINGTALK_WEBHOOK:
            return

        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": notification.title,
                        "text": f"### {notification.title}\n\n{notification.content}"
                    }
                }

                async with session.post(
                    config.DINGTALK_WEBHOOK,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"钉钉通知发送失败: {resp.status}")
        except Exception as e:
            logger.error(f"钉钉通知失败: {e}")

    async def _send_wecom(self, notification: Notification):
        """企业微信通知"""
        if not config.WECOM_WEBHOOK:
            return

        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "content": f"**{notification.title}**\n\n{notification.content}"
                    }
                }

                async with session.post(
                    config.WECOM_WEBHOOK,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"企业微信通知发送失败: {resp.status}")
        except Exception as e:
            logger.error(f"企业微信通知失败: {e}")

    async def send_alert(
        self,
        code: str,
        name: str,
        anomalies: List[Dict[str, Any]],
        quote: Dict[str, Any],
        analysis: Optional[str] = None
    ):
        """
        发送异动提醒

        Args:
            code: 股票代码
            name: 股票名称
            anomalies: 异常列表
            quote: 行情数据
            analysis: 可选的AI分析内容
        """
        price = quote.get("price", 0)
        change = quote.get("change_percent", 0) or quote.get("pct_chg", 0)

        title = f"🚨 {name}({code}) 技术异动提醒"

        content_parts = [
            f"**股票**: {name} ({code})",
            f"**当前价格**: {price:.2f} ({change:+.2f}%)",
            f"**发现异常**: {len(anomalies)} 个",
            "",
            "**异常详情**:",
        ]

        for anomaly in anomalies:
            level_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(anomaly.get("level"), "⚪")
            content_parts.append(f"{level_emoji} {anomaly.get('message', '')}")

        # 添加AI分析内容（如果有）
        if analysis:
            content_parts.extend([
                "",
                "**🤖 AI深度分析**:",
                "---",
                analysis[:1500] + "..." if len(analysis) > 1500 else analysis,
                "---"
            ])

        content_parts.extend([
            "",
            "_⚠️ 以上信息仅供参考，不构成投资建议_",
            f"_时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
        ])

        notification = Notification(
            title=title,
            content="\n".join(content_parts),
            level="warning",
            metadata={"code": code, "anomalies_count": len(anomalies), "has_analysis": analysis is not None}
        )

        await self.send(notification)

    async def send_report(self, title: str, content: str):
        """
        发送报告

        Args:
            title: 报告标题
            content: 报告内容
        """
        notification = Notification(
            title=title,
            content=content,
            level="info"
        )

        await self.send(notification)


# 全局实例
notification_manager = NotificationManager()
