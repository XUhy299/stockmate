"""
企业微信接入模块
提供双向通信：接收消息 -> 写入Mailbox -> Agent回复 -> 发送消息
"""
import asyncio
import json
import logging
import hmac
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass

import aiohttp
from aiohttp import web

from config import config
from core.notifier import notification_manager

logger = logging.getLogger(__name__)


@dataclass
class WeComMessage:
    """企业微信消息"""
    msg_type: str  # text, image, voice, video, location, link, event
    from_user: str  # 发送者UserID
    to_user: str    # 接收者（企业ID）
    content: str    # 消息内容
    msg_id: str     # 消息ID
    create_time: int
    raw_data: Dict[str, Any]  # 原始数据


class WeComChannel:
    """企业微信渠道处理器"""

    def __init__(self):
        self.enabled = False
        self.corp_id: Optional[str] = None
        self.corp_secret: Optional[str] = None
        self.agent_id: Optional[str] = None
        self.token: Optional[str] = None  # 用于消息加解密
        self.encoding_aes_key: Optional[str] = None  # 用于消息加解密

        # Webhook服务器
        self.webhook_app: Optional[web.Application] = None
        self.webhook_runner: Optional[web.AppRunner] = None
        self.webhook_port: int = 8080

        # 消息处理器（由Agent设置）
        self._message_handler: Optional[Callable[[WeComMessage], None]] = None

        # Access Token缓存
        self._access_token: Optional[str] = None
        self._token_expire_time: Optional[datetime] = None

        self._load_config()

    def _load_config(self):
        """从环境变量加载配置"""
        import os

        self.corp_id = os.getenv("WECOM_CORP_ID", "")
        self.corp_secret = os.getenv("WECOM_CORP_SECRET", "")
        self.agent_id = os.getenv("WECOM_AGENT_ID", "")
        self.token = os.getenv("WECOM_TOKEN", "")  # 可选，用于验证URL
        self.encoding_aes_key = os.getenv("WECOM_ENCODING_AES_KEY", "")  # 可选，用于解密

        # Webhook端口
        port = os.getenv("WECOM_WEBHOOK_PORT", "8080")
        self.webhook_port = int(port)

        # 检查是否启用
        if self.corp_id and self.corp_secret and self.agent_id:
            self.enabled = True
            logger.info(f"企业微信渠道已启用，Webhook端口: {self.webhook_port}")
        else:
            logger.info("企业微信渠道未启用（缺少WECOM_CORP_ID/WECOM_CORP_SECRET/WECOM_AGENT_ID）")

    def set_message_handler(self, handler: Callable[[WeComMessage], None]):
        """设置消息处理器（Agent调用）"""
        self._message_handler = handler

    async def start_webhook_server(self):
        """启动Webhook服务器接收企业微信消息"""
        if not self.enabled:
            logger.warning("企业微信渠道未启用，无法启动Webhook服务器")
            return False

        try:
            self.webhook_app = web.Application()
            self.webhook_app.router.add_get("/wecom/webhook", self._handle_verify_url)
            self.webhook_app.router.add_post("/wecom/webhook", self._handle_receive_message)

            self.webhook_runner = web.AppRunner(self.webhook_app)
            await self.webhook_runner.setup()

            site = web.TCPSite(self.webhook_runner, "0.0.0.0", self.webhook_port)
            await site.start()

            logger.info(f"✅ 企业微信Webhook服务器已启动: http://0.0.0.0:{self.webhook_port}/wecom/webhook")
            return True

        except Exception as e:
            logger.error(f"启动企业微信Webhook服务器失败: {e}")
            return False

    async def stop_webhook_server(self):
        """停止Webhook服务器"""
        if self.webhook_runner:
            await self.webhook_runner.cleanup()
            logger.info("企业微信Webhook服务器已停止")

    async def _handle_verify_url(self, request: web.Request) -> web.Response:
        """处理URL验证（首次配置时需要）"""
        # 企业微信验证URL时发送的参数
        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        echostr = request.query.get("echostr", "")

        # 如果有token配置，进行验证
        if self.token:
            # 按企业微信要求计算签名
            signature = self._calculate_signature(timestamp, nonce, echostr)
            if signature == msg_signature:
                return web.Response(text=echostr)
            else:
                logger.warning("企业微信URL验证签名失败")
                return web.Response(status=403, text="Forbidden")

        # 没有token配置，直接返回（不安全，仅用于测试）
        return web.Response(text=echostr)

    def _calculate_signature(self, timestamp: str, nonce: str, echostr: str) -> str:
        """计算签名"""
        tmp_list = [self.token, timestamp, nonce, echostr]
        tmp_list.sort()
        tmp_str = "".join(tmp_list)
        return hashlib.sha1(tmp_str.encode()).hexdigest()

    async def _handle_receive_message(self, request: web.Request) -> web.Response:
        """处理接收到的消息"""
        try:
            body = await request.text()
            logger.debug(f"收到企业微信消息: {body[:200]}...")

            # 解析XML
            msg = self._parse_xml_message(body)
            if not msg:
                return web.Response(text="success")

            # 转换为WeComMessage
            wecom_msg = WeComMessage(
                msg_type=msg.get("MsgType", ""),
                from_user=msg.get("FromUserName", ""),
                to_user=msg.get("ToUserName", ""),
                content=msg.get("Content", ""),
                msg_id=msg.get("MsgId", ""),
                create_time=int(msg.get("CreateTime", 0)),
                raw_data=msg
            )

            logger.info(f"收到企业微信消息: 用户={wecom_msg.from_user}, 类型={wecom_msg.msg_type}, 内容={wecom_msg.content[:50]}...")

            # 写入Mailbox
            await self._write_to_mailbox(wecom_msg)

            # 调用消息处理器（如果有）
            if self._message_handler:
                try:
                    self._message_handler(wecom_msg)
                except Exception as e:
                    logger.error(f"处理企业微信消息失败: {e}")

            return web.Response(text="success")

        except Exception as e:
            logger.error(f"处理企业微信消息失败: {e}")
            return web.Response(text="success")  # 返回success避免企业微信重试

    def _parse_xml_message(self, xml_body: str) -> Dict[str, str]:
        """解析XML消息"""
        try:
            root = ET.fromstring(xml_body)
            return {child.tag: child.text or "" for child in root}
        except Exception as e:
            logger.error(f"解析XML消息失败: {e}")
            return {}

    async def _write_to_mailbox(self, msg: WeComMessage):
        """将消息写入Mailbox"""
        mailbox_dir = config.MEMORY_DIR / "mailbox"
        mailbox_dir.mkdir(parents=True, exist_ok=True)

        event_data = {
            "event_type": "wecom_message",
            "content": msg.content,
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "from_user": msg.from_user,
                "to_user": msg.to_user,
                "msg_id": msg.msg_id,
                "msg_type": msg.msg_type,
                "channel": "wecom"
            }
        }

        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        event_file = mailbox_dir / f"wecom_{msg.from_user}_{timestamp}.json"

        try:
            event_file.write_text(json.dumps(event_data, ensure_ascii=False, indent=2), encoding='utf-8')
            logger.debug(f"企业微信消息已写入Mailbox: {event_file.name}")
        except Exception as e:
            logger.error(f"写入Mailbox失败: {e}")

    async def send_message(self, user_id: str, content: str, msg_type: str = "text") -> bool:
        """
        发送消息给企业微信用户

        Args:
            user_id: 用户ID
            content: 消息内容
            msg_type: 消息类型 (text, markdown)
        """
        if not self.enabled:
            logger.warning("企业微信渠道未启用，无法发送消息")
            return False

        try:
            access_token = await self._get_access_token()
            if not access_token:
                logger.error("获取Access Token失败")
                return False

            url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"

            if msg_type == "markdown":
                payload = {
                    "touser": user_id,
                    "msgtype": "markdown",
                    "agentid": self.agent_id,
                    "markdown": {"content": content},
                    "safe": 0
                }
            else:
                payload = {
                    "touser": user_id,
                    "msgtype": "text",
                    "agentid": self.agent_id,
                    "text": {"content": content},
                    "safe": 0
                }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    result = await resp.json()
                    if result.get("errcode") == 0:
                        logger.debug(f"消息发送成功: {user_id}")
                        return True
                    else:
                        logger.warning(f"消息发送失败: {result}")
                        return False

        except Exception as e:
            logger.error(f"发送企业微信消息失败: {e}")
            return False

    async def _get_access_token(self) -> Optional[str]:
        """获取企业微信Access Token"""
        # 检查缓存
        if self._access_token and self._token_expire_time:
            if datetime.now() < self._token_expire_time:
                return self._access_token

        try:
            url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
            params = {
                "corpid": self.corp_id,
                "corpsecret": self.corp_secret
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    result = await resp.json()
                    if result.get("errcode") == 0:
                        self._access_token = result["access_token"]
                        # Token有效期7200秒，提前5分钟过期
                        expires_in = result.get("expires_in", 7200)
                        self._token_expire_time = datetime.now().replace(second=0, microsecond=0)
                        self._token_expire_time = self._token_expire_time + __import__('datetime').timedelta(seconds=expires_in - 300)
                        return self._access_token
                    else:
                        logger.error(f"获取Access Token失败: {result}")
                        return None

        except Exception as e:
            logger.error(f"获取Access Token失败: {e}")
            return None

    async def send_report_to_user(self, user_id: str, title: str, content: str):
        """发送报告给用户"""
        markdown_content = f"""**{title}**

{content}

---
_发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}_
"""
        await self.send_message(user_id, markdown_content, msg_type="markdown")

    async def send_alert_to_user(self, user_id: str, code: str, name: str, anomalies: list, analysis: Optional[str] = None):
        """发送预警给用户"""
        title = f"🚨 {name}({code}) 技术异动提醒"

        content_parts = [f"**发现异常**: {len(anomalies)} 个"]
        for anomaly in anomalies:
            level_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(anomaly.get("level"), "⚪")
            content_parts.append(f"{level_emoji} {anomaly.get('message', '')}")

        if analysis:
            content_parts.extend([
                "",
                "**🤖 AI深度分析**:",
                analysis[:1500] + "..." if len(analysis) > 1500 else analysis
            ])

        markdown_content = f"""**{title}**

{chr(10).join(content_parts)}

---
_以上信息仅供参考，不构成投资建议_
_发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_
"""
        await self.send_message(user_id, markdown_content, msg_type="markdown")


# 全局实例
wecom_channel = WeComChannel()
