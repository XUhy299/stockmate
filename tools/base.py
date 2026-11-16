"""
工具基类定义
借鉴nanobot的工具设计理念
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
import asyncio
import logging
from execution_context import emit_progress

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "message": self.message
        }

    def __str__(self) -> str:
        if self.success:
            return f"✅ {self.message}"
        else:
            return f"❌ {self.error}"


class Tool(ABC):
    """
    工具基类
    所有工具必须继承此类
    """

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称（唯一标识）"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""
        pass

    @property
    def parameters(self) -> Dict[str, Any]:
        """
        工具参数定义（OpenAI function calling格式）
        """
        return {
            "type": "object",
            "properties": {}
        }

    def get_schema(self) -> Dict[str, Any]:
        """获取工具的OpenAI function schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行工具

        Args:
            **kwargs: 参数

        Returns:
            ToolResult: 执行结果
        """
        pass

    async def run(self, **kwargs: Any) -> ToolResult:
        """
        运行工具（带异常处理）
        """
        emit_progress("tool_started", tool=self.name)
        self.logger.info("Tool started: %s", self.name)
        try:
            self.logger.debug(f"执行工具 {self.name}: {kwargs}")
            result = await self.execute(**kwargs)
            self.logger.debug(f"工具 {self.name} 执行完成")
            self.logger.info("Tool completed: %s success=%s", self.name, result.success)
            emit_progress("tool_completed", tool=self.name, success=result.success)
            return result
        except Exception as e:
            self.logger.error(f"工具 {self.name} 执行失败: {e}")
            emit_progress("tool_completed", tool=self.name, success=False)
            return ToolResult(
                success=False,
                error=str(e),
                message=f"工具执行失败: {e}"
            )


class BatchTool(Tool):
    """批量工具基类"""

    async def execute_batch(self, items: List[Dict[str, Any]], max_concurrency: int = 5) -> List[ToolResult]:
        """
        批量执行

        Args:
            items: 参数列表
            max_concurrency: 最大并发数

        Returns:
            结果列表
        """
        semaphore = asyncio.Semaphore(max_concurrency)

        async def run_with_semaphore(item):
            async with semaphore:
                return await self.run(**item)

        tasks = [run_with_semaphore(item) for item in items]
        return await asyncio.gather(*tasks)
