"""
早间报告服务
支持生成晨报并发送到企业微信
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from config import config
from channels import wecom_channel, feishu_channel
from core.notifier import notification_manager

logger = logging.getLogger(__name__)


class MorningReportService:
    """早间报告服务 - 生成晨报并发送到配置的用户"""

    def __init__(self):
        self.report_dir = config.DAILY_REPORT_DIR
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self._wecom_target_users: List[str] = []
        self._feishu_target_users: List[str] = []
        self._load_target_users()

    def _load_target_users(self):
        """加载目标用户列表（从环境变量）"""
        import os
        # 企业微信用户
        wecom_users_str = os.getenv("WECOM_REPORT_USERS", "")
        if wecom_users_str:
            self._wecom_target_users = [u.strip() for u in wecom_users_str.split(",") if u.strip()]
            logger.info(f"📱 晨报将发送给 {len(self._wecom_target_users)} 位企业微信用户")

        # 飞书用户
        feishu_users_str = os.getenv("FEISHU_REPORT_USERS", "")
        if feishu_users_str:
            self._feishu_target_users = [u.strip() for u in feishu_users_str.split(",") if u.strip()]
            logger.info(f"📱 晨报将发送给 {len(self._feishu_target_users)} 位飞书用户")

    async def send_report_to_feishu_users(self, title: str, content: str, user_ids: Optional[List[str]] = None):
        """发送报告给飞书用户"""
        target_users = user_ids or self._feishu_target_users

        if not target_users:
            logger.info("没有配置飞书目标用户，跳过发送")
            return

        if not feishu_channel.enabled:
            logger.warning("飞书渠道未启用，无法发送报告")
            return

        for user_id in target_users:
            try:
                success = await feishu_channel.send_report(
                    chat_id=user_id,
                    title=title,
                    content=content
                )
                if success:
                    logger.info(f"✅ 飞书报告已发送给用户: {user_id}")
                else:
                    logger.warning(f"❌ 发送飞书报告给用户失败: {user_id}")
            except Exception as e:
                logger.error(f"发送飞书报告给用户 {user_id} 时出错: {e}")

    def _load_target_users(self):
        """加载目标用户列表（从环境变量）"""
        import os
        users_str = os.getenv("WECOM_REPORT_USERS", "")
        if users_str:
            self._wecom_target_users = [u.strip() for u in users_str.split(",") if u.strip()]
            logger.info(f"📱 晨报将发送给 {len(self._wecom_target_users)} 位用户")

    def save_report(self, date: str, content: str) -> Path:
        """保存报告到文件"""
        try:
            filename = f"daily_report_{date}.md"
            filepath = self.report_dir / filename
            filepath.write_text(content, encoding='utf-8')
            logger.info(f"💾 报告已保存: {filepath}")
            return filepath
        except Exception as e:
            logger.error(f"保存报告失败: {e}")
            return None

    async def send_report_to_users(self, title: str, content: str, user_ids: Optional[List[str]] = None):
        """
        发送报告给指定用户

        Args:
            title: 报告标题
            content: 报告内容
            user_ids: 用户ID列表，None则使用配置的目标用户
        """
        target_users = user_ids or self._wecom_target_users

        if not target_users:
            logger.info("没有配置目标用户，跳过发送")
            return

        if not wecom_channel.enabled:
            logger.warning("企业微信渠道未启用，无法发送报告")
            return

        # 构建Markdown格式的报告
        markdown_content = f"""📰 **{title}**

{content}

---
_发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}_
"""

        # 发送给每个用户
        for user_id in target_users:
            try:
                success = await wecom_channel.send_message(
                    user_id,
                    markdown_content,
                    msg_type="markdown"
                )
                if success:
                    logger.info(f"✅ 报告已发送给用户: {user_id}")
                else:
                    logger.warning(f"❌ 发送报告给用户失败: {user_id}")
            except Exception as e:
                logger.error(f"发送报告给用户 {user_id} 时出错: {e}")

        # 同时发送给飞书用户
        await self.send_report_to_feishu_users(title, content)

    async def send_report_if_normal(self, title: str, content: str, user_ids: Optional[List[str]] = None):
        """
        发送报告（Agent可调用，决定是否发送）

        注意：如果内容为空或Agent认为不需要发送，则不发送
        """
        if not content or content.strip() == "":
            logger.info("报告内容为空，跳过发送")
            return

        # 检查是否需要发送（Agent可以添加特殊标记）
        if "[SKIP_SEND]" in content:
            logger.info("Agent标记为不发送，跳过")
            return

        await self.send_report_to_users(title, content, user_ids)


# 全局实例
morning_report_service = MorningReportService()
