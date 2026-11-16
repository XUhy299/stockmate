"""
会话管理模块 - 持久化对话历史
借鉴nanobot的Session设计，实现对话历史的保存和恢复
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from config import config

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """
    对话会话

    存储完整对话历史，支持从压缩位置恢复
    """

    session_id: str = "default"  # 会话ID，支持多会话
    messages: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_consolidated: int = 0  # 已压缩的消息索引
    compress_count: int = 0  # 压缩次数
    metadata: Dict[str, Any] = field(default_factory=dict)
    context_summary: str = ""  # Model context only; raw messages remain available to the UI.

    def add_message(self, role: str, content: str, **kwargs) -> Dict[str, Any]:
        """添加消息到会话"""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now().isoformat()
        return msg

    def get_history(self, max_messages: int = 0) -> List[Dict[str, Any]]:
        """
        获取对话历史

        Args:
            max_messages: 最大消息数，0表示全部

        Returns:
            从last_consolidated开始的消息列表
        """
        # 从已压缩位置开始
        # Old files with only an index have no recoverable summary: retain history.
        start_idx = self.last_consolidated if self.context_summary else 0
        history = [m for m in self.messages[start_idx:] if not m.get("display_only")]

        if max_messages > 0 and len(history) > max_messages:
            # 保留最近的消息，但确保从user消息开始
            history = history[-max_messages:]
            # 找到第一个user消息
            for i, msg in enumerate(history):
                if msg.get("role") == "user":
                    history = history[i:]
                    break

        # 清理内部字段，保留必要的
        cleaned = []
        for msg in history:
            clean_msg = {
                "role": msg["role"],
                "content": msg.get("content", "")
            }
            # 保留工具调用相关字段
            for key in ["tool_calls", "tool_call_id", "name", "reasoning_content"]:
                if key in msg:
                    clean_msg[key] = msg[key]
            cleaned.append(clean_msg)

        if self.context_summary:
            cleaned.insert(0, {"role": "system", "content": "[历史对话摘要，仅作背景，行情以最新工具结果为准]\n" + self.context_summary})
        return cleaned

    def commit_compaction(self, summary: str, new_index: int):
        """Advance an exact raw-message boundary, never remove the chat transcript."""
        minimum = self.last_consolidated if self.context_summary else 0
        if not summary.strip() or not minimum < new_index < len(self.messages):
            raise ValueError("无效的会话压缩摘要或位置")
        if self.messages[new_index].get("role") != "user" or self.messages[new_index].get("display_only"):
            raise ValueError("压缩位置必须位于完整用户轮次的起点")
        self.context_summary = summary.strip()
        self.last_consolidated = new_index
        self.compress_count += 1
        self.updated_at = datetime.now().isoformat()
        logger.info("会话 %s: 保存摘要，压缩位置 %s", self.session_id, new_index)

    def update_consolidated(self, new_index: int):
        """更新已压缩位置"""
        if new_index > self.last_consolidated:
            self.last_consolidated = new_index
            self.compress_count += 1
            self.updated_at = datetime.now().isoformat()
            logger.info(f"会话 {self.session_id}: 更新压缩位置到 {new_index}")

    def clear(self):
        """清空会话"""
        self.messages = []
        self.last_consolidated = 0
        self.compress_count = 0
        self.context_summary = ""
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            "session_id": self.session_id,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_consolidated": self.last_consolidated,
            "compress_count": self.compress_count,
            "metadata": self.metadata,
            "context_summary": self.context_summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """从字典创建"""
        return cls(
            session_id=data.get("session_id", "default"),
            messages=data.get("messages", []),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            last_consolidated=data.get("last_consolidated", 0),
            compress_count=data.get("compress_count", 0),
            metadata=data.get("metadata", {}),
            context_summary=data.get("context_summary", ""),
        )


class SessionManager:
    """
    会话管理器

    管理多个会话的加载、保存和缓存
    """

    def __init__(self, memory_dir: Optional[Path] = None):
        # 会话保存在 memory 目录下
        self.memory_dir = memory_dir or config.MEMORY_DIR
        self.sessions_dir = self.memory_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self._cache: Dict[str, Session] = {}
        self._current_session: Optional[Session] = None

        logger.info(f"会话管理器初始化，存储目录: {self.sessions_dir}")

    def _get_session_path(self, session_id: str) -> Path:
        """获取会话文件路径"""
        # 清理文件名中的非法字符
        safe_id = "".join(c for c in session_id if c.isalnum() or c in "_-").rstrip()
        return self.sessions_dir / f"{safe_id}.json"

    def get_or_create(self, session_id: str = "default") -> Session:
        """
        获取或创建会话

        Args:
            session_id: 会话ID

        Returns:
            Session对象
        """
        # 先检查缓存
        if session_id in self._cache:
            return self._cache[session_id]

        # 尝试从文件加载
        session = self._load(session_id)
        if session is None:
            # 创建新会话
            session = Session(session_id=session_id)
            logger.info(f"创建新会话: {session_id}")
        else:
            logger.info(f"加载已有会话: {session_id}, "
                       f"消息数: {len(session.messages)}, "
                       f"已压缩: {session.last_consolidated}")

        self._cache[session_id] = session
        self._current_session = session
        return session

    def _load(self, session_id: str) -> Optional[Session]:
        """从文件加载会话"""
        path = self._get_session_path(session_id)
        if not path.exists():
            return None

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            session = Session.from_dict(data)
            logger.debug(f"从文件加载会话: {session_id}")
            return session
        except Exception as e:
            logger.error(f"加载会话 {session_id} 失败: {e}")
            return None

    def save(self, session: Optional[Session] = None) -> bool:
        """
        保存会话到文件

        Args:
            session: 要保存的会话，None表示保存当前会话

        Returns:
            是否成功
        """
        session = session or self._current_session
        if not session:
            logger.warning("没有可保存的会话")
            return False

        try:
            path = self._get_session_path(session.session_id)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)

            # 更新缓存
            self._cache[session.session_id] = session
            logger.debug(f"保存会话: {session.session_id}, "
                        f"消息数: {len(session.messages)}")
            return True
        except Exception as e:
            logger.error(f"保存会话失败: {e}")
            return False

    def get_current(self) -> Optional[Session]:
        """获取当前会话"""
        return self._current_session

    def set_current(self, session: Session):
        """设置当前会话"""
        self._current_session = session
        self._cache[session.session_id] = session

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有会话"""
        sessions = []
        for path in self.sessions_dir.glob("*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                sessions.append({
                    "session_id": data.get("session_id", path.stem),
                    "message_count": len(data.get("messages", [])),
                    "last_consolidated": data.get("last_consolidated", 0),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "path": str(path)
                })
            except Exception:
                continue

        # 按更新时间排序
        return sorted(sessions,
                     key=lambda x: x.get("updated_at", ""),
                     reverse=True)

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        path = self._get_session_path(session_id)
        try:
            if path.exists():
                path.unlink()
            self._cache.pop(session_id, None)
            if self._current_session and self._current_session.session_id == session_id:
                self._current_session = None
            logger.info(f"删除会话: {session_id}")
            return True
        except Exception as e:
            logger.error(f"删除会话失败: {e}")
            return False

    def clear_history(self, session_id: Optional[str] = None):
        """清空指定或当前会话的历史"""
        session = None
        if session_id:
            session = self._cache.get(session_id)
        if not session:
            session = self._current_session

        if session:
            session.clear()
            self.save(session)
            logger.info(f"清空会话历史: {session.session_id}")


# 全局实例
session_manager = SessionManager()
