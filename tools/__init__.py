"""
工具模块 - 提供各种分析工具
所有工具定义在 all_tools.py 中，方便集中管理
"""
from .base import Tool, ToolResult
from .registry import ToolRegistry, registry
from .all_tools import (
    # 股票数据工具
    GetStockQuoteTool,
    GetKlineTool,
    GetStockNewsTool,
    GetHotStocksTool,
    GetMarketSentimentTool,
    AddToWatchlistTool,
    RemoveFromWatchlistTool,
    GetWatchlistTool,
    # 分析工具
    CalculateIndicatorsTool,
    DetectAnomaliesTool,
    AnalyzeStockTool,
    ScreenStocksTool,
    # 系统工具
    SpawnSubAgentTool,
    LoadSkillTool,
    CompressContextTool,
    UpdateUserProfileTool,
    # 工具列表（用于批量注册）
    ALL_TOOLS,
)

__all__ = [
    # 基础类
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "registry",
    # 股票工具
    "GetStockQuoteTool",
    "GetKlineTool",
    "GetStockNewsTool",
    "GetHotStocksTool",
    "GetMarketSentimentTool",
    "AddToWatchlistTool",
    "RemoveFromWatchlistTool",
    "GetWatchlistTool",
    # 分析工具
    "CalculateIndicatorsTool",
    "DetectAnomaliesTool",
    "AnalyzeStockTool",
    "ScreenStocksTool",
    # 系统工具
    "SpawnSubAgentTool",
    "LoadSkillTool",
    "CompressContextTool",
    "UpdateUserProfileTool",
    # 工具列表
    "ALL_TOOLS",
]

# 自动注册所有工具（导入时执行）
def _register_all_tools():
    """自动注册所有工具到全局注册表"""
    for tool_class in ALL_TOOLS:
        try:
            tool_instance = tool_class()
            registry.register(tool_instance)
        except Exception as e:
            print(f"注册工具 {tool_class.__name__} 失败: {e}")

# 执行注册
_register_all_tools()
