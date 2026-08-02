"""
飞书/Lark 渠道接入模块
使用 WebSocket 长连接接收消息，支持双向通信
"""
import asyncio
import json
import os
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from config import config

import logging
logger = logging.getLogger(__name__)

# 检查是否安装了飞书SDK
FEISHU_AVAILABLE = False
try:
    import lark_oapi
    FEISHU_AVAILABLE = True
except ImportError:
    pass


class FeishuMessage:
    """飞书消息"""
    def __init__(
        self,
        msg_type: str,
        from_user: str,
        chat_id: str,
        content: str,
        msg_id: str,
        chat_type: str = "p2p",
        raw_data: Dict = None
    ):
        self.msg_type = msg_type  # text, post, image, file, etc.
        self.from_user = from_user  # 发送者open_id
        self.chat_id = chat_id  # 会话ID
        self.content = content  # 消息内容
        self.msg_id = msg_id  # 消息ID
        self.chat_type = chat_type  # p2p, group
        self.raw_data = raw_data or {}


class FeishuChannel:
    """
    飞书/Lark 渠道处理器

    使用 WebSocket 长连接接收消息，无需公网IP或Webhook配置
    """

    def __init__(self):
        self.enabled = False
        self.app_id: Optional[str] = None
        self.app_secret: Optional[str] = None
        self.encrypt_key: str = ""
        self.verification_token: str = ""
        self.react_emoji: str = "THUMBSUP"  # 收到消息后添加的反应表情

        # WebSocket相关
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 消息去重
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()

        # 当前用户映射（用于回复）
        self._current_chat_id: Optional[str] = None
        self._current_chat_type: Optional[str] = None

        # 消息处理器（由Agent设置）
        self._message_handler: Optional[Callable[[FeishuMessage], None]] = None

        self._load_config()

    def _load_config(self):
        """从环境变量加载配置"""
        self.app_id = os.getenv("FEISHU_APP_ID", "")
        self.app_secret = os.getenv("FEISHU_APP_SECRET", "")
        self.encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "")
        self.verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "")

        # 检查是否启用
        if self.app_id and self.app_secret:
            self.enabled = True
            logger.info("飞书渠道已启用")
        else:
            logger.info("飞书渠道未启用（缺少FEISHU_APP_ID/FEISHU_APP_SECRET）")

    def set_message_handler(self, handler: Callable[[FeishuMessage], None]):
        """设置消息处理器（Agent调用）"""
        self._message_handler = handler

    async def start(self) -> bool:
        """启动飞书机器人"""
        if not FEISHU_AVAILABLE:
            logger.error("飞书SDK未安装。运行: pip install lark-oapi")
            return False

        if not self.enabled:
            lark.LogLevel.WARNING("飞书渠道未启用，无法启动")
            return False

        try:
            import lark_oapi as lark

            self._running = True
            self._loop = asyncio.get_running_loop()

            # 创建 Lark 客户端（用于发送消息）
            self._client = lark.Client.builder() \
                .app_id(self.app_id) \
                .app_secret(self.app_secret) \
                .log_level(lark.LogLevel.WARNING) \
                .build()

            # 创建事件处理器
            event_handler = lark.EventDispatcherHandler.builder(
                self.encrypt_key or "",
                self.verification_token or "",
            ).register_p2_im_message_receive_v1(
                self._on_message_sync
            ).build()

            # 创建 WebSocket 客户端
            self._ws_client = lark.ws.Client(
                self.app_id,
                self.app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.WARNING
            )

            # 在单独线程中启动 WebSocket（带重连）
            def run_ws():
                import time
                import lark_oapi.ws.client as _lark_ws_client

                # 创建新的事件循环
                ws_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(ws_loop)
                _lark_ws_client.loop = ws_loop

                try:
                    while self._running:
                        try:
                            self._ws_client.start()
                        except Exception as e:
                            logger.warning(f"飞书WebSocket错误: {e}")
                        if self._running:
                            time.sleep(5)
                finally:
                    ws_loop.close()

            self._ws_thread = threading.Thread(target=run_ws, daemon=True)
            self._ws_thread.start()

            logger.info("✅ 飞书机器人已启动（WebSocket长连接）")
            logger.info("   无需公网IP，使用WebSocket接收消息")
            return True

        except Exception as e:
            logger.error(f"启动飞书机器人失败: {e}")
            return False

    async def stop(self):
        """停止飞书机器人"""
        self._running = False
        logger.info("飞书机器人已停止")

    def _on_message_sync(self, data: Any) -> None:
        """同步消息处理器（从WebSocket线程调用）"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: Any) -> None:
        """处理接收到的消息"""
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # 去重检查
            message_id = message.message_id
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None

            # 限制缓存大小
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

            # 跳过机器人消息
            if sender.sender_type == "bot":
                return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type  # p2p, group
            msg_type = message.message_type

            # 群消息需要@机器人才处理（简化处理）
            if chat_type == "group":
                if not self._is_bot_mentioned(message):
                    logger.debug("跳过群消息（未@机器人）")
                    return

            # 添加反应表情
            await self._add_reaction(message_id, self.react_emoji)

            # 解析内容
            content_text = self._extract_content(message, msg_type)

            if not content_text:
                logger.debug(f"无法解析消息内容: {msg_type}")
                return

            logger.info(f"收到飞书消息: 用户={sender_id}, 类型={msg_type}, 内容={content_text[:50]}...")

            # 创建 FeishuMessage
            feishu_msg = FeishuMessage(
                msg_type=msg_type,
                from_user=sender_id,
                chat_id=chat_id,
                content=content_text,
                msg_id=message_id,
                chat_type=chat_type,
                raw_data={"event": event, "message": message, "sender": sender}
            )

            # 记录当前会话信息，用于回复
            self._current_chat_id = chat_id
            self._current_chat_type = chat_type

            # 写入 Mailbox
            await self._write_to_mailbox(feishu_msg)

            # 调用消息处理器
            if self._message_handler:
                try:
                    self._message_handler(feishu_msg)
                except Exception as e:
                    logger.error(f"处理飞书消息失败: {e}")

        except Exception as e:
            logger.error(f"处理飞书消息失败: {e}")

    def _is_bot_mentioned(self, message: Any) -> bool:
        """检查消息中是否@了机器人"""
        # 检查 @all
        if "@_all" in (message.content or ""):
            return True

        # 检查 @机器人
        for mention in getattr(message, "mentions", None) or []:
            mid = getattr(mention, "id", None)
            if not mid:
                continue
            # 机器人mention的user_id为None但open_id以ou_开头
            if not getattr(mid, "user_id", None) and (getattr(mid, "open_id", None) or "").startswith("ou_"):
                return True
        return False

    def _extract_content(self, message: Any, msg_type: str) -> str:
        """从消息中提取文本内容"""
        try:
            content_json = json.loads(message.content) if message.content else {}
        except json.JSONDecodeError:
            content_json = {}

        if msg_type == "text":
            return content_json.get("text", "").strip()

        elif msg_type == "post":
            # 富文本消息
            return self._extract_post_text(content_json)

        elif msg_type in ("image", "audio", "file", "media"):
            return f"[{msg_type}]"

        else:
            return f"[{msg_type}]"

    def _extract_post_text(self, content_json: Dict) -> str:
        """从富文本消息中提取纯文本"""
        texts = []

        # 处理包装格式 {"post": {"zh_cn": {...}}}
        root = content_json
        if isinstance(root, dict) and isinstance(root.get("post"), dict):
            root = root["post"]

        # 尝试获取内容块
        for key in ("zh_cn", "en_us", "ja_jp"):
            if key in root:
                block = root[key]
                if isinstance(block, dict):
                    # 提取标题
                    if "title" in block:
                        texts.append(block["title"])
                    # 提取内容
                    for row in block.get("content", []):
                        if isinstance(row, list):
                            for el in row:
                                if isinstance(el, dict):
                                    tag = el.get("tag")
                                    if tag in ("text", "a"):
                                        texts.append(el.get("text", ""))
                                    elif tag == "at":
                                        texts.append(f"@{el.get('user_name', 'user')}")
                break

        return " ".join(texts).strip()

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """为消息添加表情反应"""
        if not self._client:
            return

        try:
            from lark_oapi.api.im.v1 import CreateMessageReactionRequest, CreateMessageReactionRequestBody, Emoji

            loop = asyncio.get_running_loop()

            def _add():
                try:
                    request = CreateMessageReactionRequest.builder() \
                        .message_id(message_id) \
                        .request_body(
                            CreateMessageReactionRequestBody.builder()
                            .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                            .build()
                        ).build()

                    response = self._client.im.v1.message_reaction.create(request)
                    if not response.success():
                        logger.debug(f"添加反应失败: {response.msg}")
                except Exception as e:
                    logger.debug(f"添加反应失败: {e}")

            await loop.run_in_executor(None, _add)

        except Exception as e:
            logger.debug(f"添加反应失败: {e}")

    async def _write_to_mailbox(self, msg: FeishuMessage):
        """将消息写入 Mailbox"""
        mailbox_dir = config.MEMORY_DIR / "mailbox"
        mailbox_dir.mkdir(parents=True, exist_ok=True)

        event_data = {
            "event_type": "feishu_message",
            "content": msg.content,
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "from_user": msg.from_user,
                "chat_id": msg.chat_id,
                "chat_type": msg.chat_type,
                "msg_id": msg.msg_id,
                "msg_type": msg.msg_type,
                "channel": "feishu"
            }
        }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        event_file = mailbox_dir / f"feishu_{msg.from_user}_{timestamp}.json"

        try:
            event_file.write_text(json.dumps(event_data, ensure_ascii=False, indent=2), encoding='utf-8')
            logger.debug(f"飞书消息已写入Mailbox: {event_file.name}")
        except Exception as e:
            logger.error(f"写入Mailbox失败: {e}")

    async def send_message(
        self,
        chat_id: str,
        content: str,
        msg_type: str = "text",
        reply_to: Optional[str] = None
    ) -> bool:
        """
        发送消息到飞书

        Args:
            chat_id: 会话ID（用户open_id或群chat_id）
            content: 消息内容
            msg_type: 消息类型 (text, markdown, interactive)
            reply_to: 回复的消息ID（可选）
        """
        if not self._client:
            logger.warning("飞书客户端未初始化")
            return False

        try:
            loop = asyncio.get_running_loop()

            # 确定接收ID类型
            receive_id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"

            if msg_type == "text":
                return await loop.run_in_executor(
                    None, self._send_text_sync, receive_id_type, chat_id, content, reply_to
                )
            elif msg_type in ("markdown", "interactive"):
                return await loop.run_in_executor(
                    None, self._send_card_sync, receive_id_type, chat_id, content, reply_to
                )
            else:
                logger.warning(f"不支持的消息类型: {msg_type}")
                return False

        except Exception as e:
            logger.error(f"发送飞书消息失败: {e}")
            return False

    def _send_text_sync(
        self,
        receive_id_type: str,
        receive_id: str,
        content: str,
        reply_to: Optional[str] = None
    ) -> bool:
        """同步发送文本消息"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, ReplyMessageRequest, ReplyMessageRequestBody

        try:
            # 截断超长内容
            if len(content) > 8000:
                content = content[:8000] + "\n...（内容已截断）"

            msg_content = json.dumps({"text": content}, ensure_ascii=False)

            # 如果有reply_to，使用回复API
            if reply_to:
                request = ReplyMessageRequest.builder() \
                    .message_id(reply_to) \
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("text")
                        .content(msg_content)
                        .build()
                    ).build()
                response = self._client.im.v1.message.reply(request)
            else:
                request = CreateMessageRequest.builder() \
                    .receive_id_type(receive_id_type) \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(receive_id)
                        .msg_type("text")
                        .content(msg_content)
                        .build()
                    ).build()
                response = self._client.im.v1.message.create(request)

            if response.success():
                logger.debug(f"消息发送成功: {receive_id}")
                return True
            else:
                logger.warning(f"消息发送失败: {response.code} - {response.msg}")
                return False

        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return False

    def _send_card_sync(
        self,
        receive_id_type: str,
        receive_id: str,
        content: str,
        reply_to: Optional[str] = None
    ) -> bool:
        """同步发送卡片消息（支持Markdown格式）"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody, ReplyMessageRequest, ReplyMessageRequestBody

        try:
            # 构建卡片
            elements = self._build_card_elements(content)
            card = {
                "config": {"wide_screen_mode": True},
                "elements": elements
            }
            msg_content = json.dumps(card, ensure_ascii=False)

            if reply_to:
                request = ReplyMessageRequest.builder() \
                    .message_id(reply_to) \
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("interactive")
                        .content(msg_content)
                        .build()
                    ).build()
                response = self._client.im.v1.message.reply(request)
            else:
                request = CreateMessageRequest.builder() \
                    .receive_id_type(receive_id_type) \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(receive_id)
                        .msg_type("interactive")
                        .content(msg_content)
                        .build()
                    ).build()
                response = self._client.im.v1.message.create(request)

            if response.success():
                logger.debug(f"卡片消息发送成功: {receive_id}")
                return True
            else:
                logger.warning(f"卡片消息发送失败: {response.code} - {response.msg}")
                return False

        except Exception as e:
            logger.error(f"发送卡片消息失败: {e}")
            return False

    def _build_card_elements(self, content: str) -> list:
        """构建卡片元素"""
        elements = []

        # 简单处理：将内容按段落分割
        paragraphs = content.split("\n\n")

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 处理标题 (# 开头)
            if para.startswith("# "):
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{para[2:].strip()}**"
                    }
                })
            elif para.startswith("## "):
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{para[3:].strip()}**"
                    }
                })
            # 处理代码块
            elif para.startswith("```"):
                code_content = para.strip("`").strip()
                elements.append({
                    "tag": "markdown",
                    "content": f"```\n{code_content}\n```"
                })
            else:
                # 普通文本
                elements.append({
                    "tag": "markdown",
                    "content": para
                })

        return elements if elements else [{"tag": "markdown", "content": content}]

    async def send_report(self, chat_id: str, title: str, content: str) -> bool:
        """发送报告（使用卡片格式）"""
        full_content = f"# {title}\n\n{content}\n\n---\n_发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}_"
        return await self.send_message(chat_id, full_content, msg_type="interactive")

    async def send_alert(
        self,
        chat_id: str,
        code: str,
        name: str,
        anomalies: list,
        analysis: Optional[str] = None
    ) -> bool:
        """发送预警消息"""
        anomaly_lines = []
        for a in anomalies:
            level_emoji = {"high": "🔴", "critical": "🔴", "medium": "🟡", "low": "🟢"}.get(a.get("level"), "⚪")
            anomaly_lines.append(f"{level_emoji} {a.get('message', '')}")

        content = f"""# 🚨 {name}({code}) 技术异动提醒

**异常信号**
{chr(10).join(anomaly_lines)}
"""
        if analysis:
            content += f"\n**AI深度分析**\n{analysis[:1500]}"

        content += f"\n\n---\n_发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"

        return await self.send_message(chat_id, content, msg_type="interactive")


# 全局实例
feishu_channel = FeishuChannel()
