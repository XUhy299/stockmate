#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单的测试脚本，验证系统可以正常导入和运行基本功能
"""
import sys
from pathlib import Path

# 添加项目目录到路径
sys.path.insert(0, str(Path(__file__).parent))


def test_imports():
    """测试导入所有模块"""
    print("测试模块导入...")

    try:
        from config import config
        print("  config")

        from data.akshare_client import AKShareClient
        from data.indicators import TechnicalIndicators
        print("  data")

        from tools.base import Tool, ToolResult
        from tools.registry import ToolRegistry
        from tools.all_tools import GetStockQuoteTool, AnalyzeStockTool
        print("  tools")

        from core.agent import AStockAgent
        from core.memory import UserMemory
        from core.scheduler import TaskScheduler
        from core.notifier import NotificationManager
        print("  core")

        from services.monitoring_service import MonitoringService
        from services.morning_report_service import MorningReportService
        from services.screening_service import ScreeningService
        print("  services")

        # Skills are now loaded from Markdown files via SkillLoader
        print("  skills (Markdown-based)")

        print("所有模块导入成功!")
        return True

    except Exception as e:
        print(f"导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_basic_functionality():
    """测试基本功能"""
    print("\n测试基本功能...")

    try:
        # 测试技术指标计算
        from data.indicators import TechnicalIndicators

        test_closes = [10, 11, 12, 11, 13, 14, 15, 14, 16, 17]
        ma5 = TechnicalIndicators.sma(test_closes, 5)
        rsi = TechnicalIndicators.rsi(test_closes, 6)

        print(f"  MA5计算: {ma5}")
        print(f"  RSI计算: {rsi}")

        # 测试AKShare客户端（仅测试初始化）
        from data.akshare_client import akshare_client
        print(f"  AKShare客户端初始化成功")

        # 测试记忆系统
        from core.memory import user_memory
        context = user_memory.get_context()
        print(f"  记忆系统初始化成功")

        # 测试工具注册
        from tools.registry import registry
        from tools.all_tools import GetStockQuoteTool
        registry.register(GetStockQuoteTool())
        print(f"  工具注册成功，共 {len(registry.list_tools())} 个工具")

        print("基本功能测试通过!")
        return True

    except Exception as e:
        print(f"功能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_agent_initialization():
    """测试Agent初始化"""
    print("\n测试Agent初始化...")

    try:
        from core.agent import AStockAgent

        agent = AStockAgent()
        print("  Agent初始化成功")

        # 测试获取工具列表
        from tools.registry import registry
        tools = registry.list_tools()
        print(f"  可用工具: {', '.join(tools)}")

        print("Agent测试通过!")
        return True

    except Exception as e:
        print(f"Agent测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("="*60)
    print("A股分析Agent系统 - 测试脚本")
    print("="*60)
    print()

    results = []

    results.append(("模块导入", test_imports()))
    results.append(("基本功能", test_basic_functionality()))
    results.append(("Agent初始化", test_agent_initialization()))

    print("\n" + "="*60)
    print("测试结果汇总")
    print("="*60)

    for name, result in results:
        status = "通过" if result else "失败"
        print(f"{name}: {status}")

    all_passed = all(r for _, r in results)

    if all_passed:
        print("\n所有测试通过! 系统可以正常运行。")
        print("\n启动命令:")
        print("  python main.py          # 启动完整服务")
        print("  python main.py --cli    # 仅命令行交互")
    else:
        print("\n部分测试失败，请检查配置和依赖。")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
