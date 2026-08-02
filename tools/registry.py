"""
工具注册表
管理所有可用的工具
借鉴nanobot的ToolRegistry设计
"""
from typing import Dict, List, Any, Optional
import logging

from .base import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表 - 单例模式"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools: Dict[str, Tool] = {}
            cls._instance._initialized = False
        return cls._instance

    def register(self, tool: Tool) -> "ToolRegistry":
        """
        注册工具

        Args:
            tool: 工具实例

        Returns:
            self
        """
        if tool.name in self._tools:
            logger.warning(f"工具 {tool.name} 已存在，将被覆盖")

        self._tools[tool.name] = tool
        logger.debug(f"✅ 注册工具: {tool.name}")
        return self

    def unregister(self, name: str) -> bool:
        """
        注销工具

        Args:
            name: 工具名称

        Returns:
            是否成功
        """
        if name in self._tools:
            del self._tools[name]
            logger.debug(f"🗑️ 注销工具: {name}")
            return True
        return False

    def get(self, name: str) -> Optional[Tool]:
        """
        获取工具

        Args:
            name: 工具名称

        Returns:
            工具实例或None
        """
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """
        列出所有工具名称

        Returns:
            工具名称列表
        """
        return list(self._tools.keys())

    def get_schemas(self) -> List[Dict[str, Any]]:
        """
        获取所有工具的OpenAI schemas

        Returns:
            schemas列表
        """
        return [tool.get_schema() for tool in self._tools.values()]

    async def execute(self, name: str, **kwargs) -> Any:
        """
        执行工具

        Args:
            name: 工具名称
            **kwargs: 参数

        Returns:
            执行结果
        """
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"工具 {name} 不存在")

        result = await tool.run(**kwargs)
        return result

    def __contains__(self, name: str) -> bool:
        """检查工具是否存在"""
        return name in self._tools

    def __iter__(self):
        """迭代所有工具"""
        return iter(self._tools.values())


# 全局注册表实例
registry = ToolRegistry()
