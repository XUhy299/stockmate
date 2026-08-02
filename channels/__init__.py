"""
外部渠道接入模块
支持企业微信、钉钉、飞书等渠道的双向通信
"""
from .wecom import wecom_channel
from .feishu import feishu_channel

__all__ = ["wecom_channel", "feishu_channel"]
