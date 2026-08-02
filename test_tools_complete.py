#!/usr/bin/env python3
"""
完整工具测试脚本
测试两种调用方式：
1. 直接调用：程序直接调用 tool.execute()
2. LLM自主调用：模拟LLM决定是否调用工具
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from tools.all_tools import (
    GetStockQuoteTool, GetKlineTool, GetStockNewsTool,
    GetHotStocksTool, GetMarketSentimentTool,
    AddToWatchlistTool, RemoveFromWatchlistTool, GetWatchlistTool,
    CalculateIndicatorsTool, DetectAnomaliesTool,
    AnalyzeStockTool, ScreenStocksTool,
    SpawnSubAgentTool, LoadSkillTool, CompressContextTool,
)
from tools.registry import registry


class ToolTester:
    """工具测试器"""

    def __init__(self):
        self.results = []
        self.passed = 0
        self.failed = 0

    def log(self, message: str, level="INFO"):
        """打印日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"INFO": "[INFO]", "OK": "[OK]", "FAIL": "[FAIL]", "WARN": "[WARN]"}.get(level, "[INFO]")
        print(f"{timestamp} {prefix} {message}")

    async def test_direct_call(self, tool_class, test_name: str, params: dict, validator=None):
        """
        测试方式1：直接调用 execute

        Args:
            tool_class: 工具类
            test_name: 测试名称
            params: 调用参数
            validator: 结果验证函数(可选)
        """
        self.log(f"\n{'='*60}")
        self.log(f"直接调用测试: {test_name}")
        self.log(f"{'='*60}")
        self.log(f"工具: {tool_class.__name__}")
        self.log(f"参数: {json.dumps(params, ensure_ascii=False)}")

        start_time = datetime.now()
        try:
            tool = tool_class()
            result = await tool.execute(**params)
            duration = (datetime.now() - start_time).total_seconds()

            self.log(f"耗时: {duration:.2f}s", "INFO")
            self.log(f"成功: {result.success}", "OK" if result.success else "FAIL")

            if result.success:
                self.log(f"消息: {result.message}", "INFO")
                if result.data:
                    data_preview = str(result.data)[:200]
                    if len(str(result.data)) > 200:
                        data_preview += "..."
                    self.log(f"数据: {data_preview}", "INFO")

                # 验证结果
                if validator and not validator(result.data):
                    self.log("验证失败: 数据不符合预期", "FAIL")
                    self.failed += 1
                    return False

                self.passed += 1
                return True
            else:
                self.log(f"错误: {result.error}", "FAIL")
                self.failed += 1
                return False

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            self.log(f"耗时: {duration:.2f}s", "INFO")
            self.log(f"异常: {type(e).__name__}: {e}", "FAIL")
            self.failed += 1
            return False

    async def test_llm_autonomous_call(self, user_query: str, expected_tools: list = None):
        """
        测试方式2：模拟LLM自主调用工具

        流程：
        1. 用户输入查询
        2. LLM分析需要调用哪些工具
        3. 执行工具调用
        4. LLM根据结果生成回复

        Args:
            user_query: 用户查询
            expected_tools: 预期会调用的工具列表(用于验证)
        """
        self.log(f"\n{'='*60}")
        self.log(f"LLM自主调用测试: {user_query}")
        self.log(f"{'='*60}")

        # 模拟LLM的"思考"过程
        self.log("[LLM思考] 分析用户意图...", "INFO")

        # 简单的意图识别逻辑（实际应由LLM完成）
        tool_calls = self._parse_intent(user_query)

        self.log(f"[LLM决定] 需要调用 {len(tool_calls)} 个工具:", "INFO")
        for call in tool_calls:
            self.log(f"  - {call['tool']}: {call['params']}", "INFO")

        # 验证预期工具
        if expected_tools:
            called_tools = [call['tool'] for call in tool_calls]
            missing = set(expected_tools) - set(called_tools)
            if missing:
                self.log(f"警告: 预期工具未调用: {missing}", "WARN")

        # 执行工具调用
        results = []
        for call in tool_calls:
            tool_name = call['tool']
            params = call['params']

            self.log(f"\n[执行] {tool_name}({json.dumps(params, ensure_ascii=False)})")

            try:
                tool = registry.get(tool_name)
                if tool:
                    result = await tool.run(**params)
                    results.append({
                        "tool": tool_name,
                        "success": result.success,
                        "message": result.message,
                        "data": result.data if result.success else None,
                        "error": result.error if not result.success else None
                    })
                    status = "OK" if result.success else "FAIL"
                    self.log(f"结果: {result.message}", status)
                else:
                    self.log(f"错误: 工具 {tool_name} 不存在", "FAIL")
                    results.append({"tool": tool_name, "success": False, "error": "工具不存在"})
            except Exception as e:
                self.log(f"异常: {e}", "FAIL")
                results.append({"tool": tool_name, "success": False, "error": str(e)})

        # 模拟LLM生成最终回复
        self.log("\n[LLM回复生成] 基于工具结果生成回复...", "INFO")
        response = self._generate_mock_response(user_query, results)
        self.log(f"回复: {response}", "OK")

        # 统计
        success_count = sum(1 for r in results if r['success'])
        if success_count == len(tool_calls):
            self.passed += 1
            return True
        else:
            self.failed += 1
            return False

    def _parse_intent(self, query: str) -> list:
        """
        模拟意图识别（简化版）
        实际应由LLM根据查询内容决定调用哪些工具
        """
        query_lower = query.lower()
        calls = []

        # 股票行情查询
        if any(kw in query_lower for kw in ['行情', '价格', '股价', '涨跌幅', '多少钱']):
            # 提取股票代码（简化）
            import re
            codes = re.findall(r'\b(\d{6})\b', query)
            if codes:
                calls.append({
                    "tool": "get_stock_quote",
                    "params": {"codes": codes}
                })

        # K线数据
        if any(kw in query_lower for kw in ['k线', '均线', 'macd', 'rsi', '技术分析']):
            import re
            codes = re.findall(r'\b(\d{6})\b', query)
            if codes:
                calls.append({
                    "tool": "get_kline",
                    "params": {"code": codes[0], "days": 60}
                })
                calls.append({
                    "tool": "calculate_indicators",
                    "params": {"code": codes[0]}
                })

        # 新闻查询
        if any(kw in query_lower for kw in ['新闻', '消息', '公告', '资讯']):
            import re
            codes = re.findall(r'\b(\d{6})\b', query)
            if codes:
                calls.append({
                    "tool": "get_stock_news",
                    "params": {"code": codes[0], "limit": 10}
                })

        # 热门股票
        if any(kw in query_lower for kw in ['热门', '涨幅榜', '成交额', '领涨']):
            calls.append({
                "tool": "get_hot_stocks",
                "params": {"top_n": 20, "by": "amount"}
            })

        # 市场情绪
        if any(kw in query_lower for kw in ['市场情绪', '大盘', '涨跌家数']):
            calls.append({
                "tool": "get_market_sentiment",
                "params": {}
            })

        # 综合分析
        if any(kw in query_lower for kw in ['分析', '怎么样', '看好', '评价']):
            import re
            codes = re.findall(r'\b(\d{6})\b', query)
            if codes:
                calls.append({
                    "tool": "analyze_stock",
                    "params": {"code": codes[0], "include_news": True}
                })

        # 选股
        if any(kw in query_lower for kw in ['选股', '筛选', '找出', '哪些股票']):
            calls.append({
                "tool": "screen_stocks",
                "params": {"min_change": -5, "max_change": 5, "limit": 20}
            })

        # 自选操作
        if any(kw in query_lower for kw in ['加自选', '添加自选']):
            import re
            codes = re.findall(r'\b(\d{6})\b', query)
            if codes:
                calls.append({
                    "tool": "add_to_watchlist",
                    "params": {"codes": codes, "notes": "用户添加"}
                })

        if any(kw in query_lower for kw in ['看自选', '我的自选', '自选列表']):
            calls.append({
                "tool": "get_watchlist",
                "params": {}
            })

        # 默认：如果没有匹配到任何工具，返回分析意图
        if not calls:
            import re
            codes = re.findall(r'\b(\d{6})\b', query)
            if codes:
                calls.append({
                    "tool": "analyze_stock",
                    "params": {"code": codes[0]}
                })

        return calls

    def _generate_mock_response(self, query: str, results: list) -> str:
        """模拟生成LLM回复"""
        if not results:
            return "抱歉，我无法理解您的查询，请提供更多细节。"

        success_results = [r for r in results if r['success']]
        if not success_results:
            return f"数据获取失败：{results[0].get('error', '未知错误')}"

        parts = []
        for result in success_results:
            tool = result['tool']
            msg = result['message']
            parts.append(f"[{tool}] {msg}")

        return " | ".join(parts)

    async def run_all_tests(self):
        """运行所有测试"""
        print("\n" + "="*60)
        print("AStack 工具测试 - 直接调用 vs LLM自主调用")
        print("="*60)

        print("\n" + "#"*60)
        print("# 第一部分：直接调用测试")
        print("#"*60)

        # 股票数据工具测试
        '''
        await self.test_direct_call(
            GetStockQuoteTool,
            "获取单只股票行情",
            {"codes": ["600036"]},
            validator=lambda d: d and len(d) > 0 and "price" in d[0] if isinstance(d, list) else False
        )

        await self.test_direct_call(
            GetStockQuoteTool,
            "获取多只股票行情",
            {"codes": ["600036", "000001", "002230"]}
        )

        await self.test_direct_call(
            GetKlineTool,
            "获取K线数据",
            {"code": "600036", "days": 30},
            validator=lambda d: isinstance(d, list) and len(d) > 0
        )

        await self.test_direct_call(
            GetStockNewsTool,
            "获取股票新闻",
            {"code": "600036", "limit": 5}
        )
        '''
        await self.test_direct_call(
            GetHotStocksTool,
            "获取热门股票（成交额）",
            {"top_n": 10, "by": "amount"}
        )

        await self.test_direct_call(
            GetHotStocksTool,
            "获取热门股票（涨跌幅）",
            {"top_n": 10, "by": "pct_chg"}
        )

        await self.test_direct_call(
            GetMarketSentimentTool,
            "获取市场情绪",
            {}
        )
        '''
        # 自选股操作测试（添加后删除）
        await self.test_direct_call(
            AddToWatchlistTool,
            "添加自选股",
            {"codes": ["600036"], "notes": "测试添加"}
        )

        await self.test_direct_call(
            GetWatchlistTool,
            "查看自选列表",
            {}
        )

        await self.test_direct_call(
            RemoveFromWatchlistTool,
            "移除自选股",
            {"codes": ["600036"]}
        )

        # 分析工具测试
        await self.test_direct_call(
            CalculateIndicatorsTool,
            "计算技术指标",
            {"code": "600036", "days": 60}
        )
        '''
        await self.test_direct_call(
            DetectAnomaliesTool,
            "检测技术异常",
            {"code": "600036"}
        )

        await self.test_direct_call(
            AnalyzeStockTool,
            "综合分析股票",
            {"code": "600036", "include_news": True}
        )

        await self.test_direct_call(
            ScreenStocksTool,
            "筛选股票",
            {"min_change": -3, "max_change": 5, "limit": 10}
        )

        # 系统工具测试
        await self.test_direct_call(
            LoadSkillTool,
            "加载Skill内容",
            {"skill_name": "research_report"}
        )

        await self.test_direct_call(
            LoadSkillTool,
            "加载不存在的Skill",
            {"skill_name": "non_existent_skill"}
        )

        await self.test_direct_call(
            CompressContextTool,
            "压缩上下文",
            {"messages": [
                {"role": "user", "content": "分析 600036"},
                {"role": "assistant", "content": "600036 是招商银行，当前价格..."},
                {"role": "user", "content": "再看看 000001"},
            ]}
        )

        print("\n" + "#"*60)
        print("# 第二部分：LLM自主调用测试（模拟）")
        print("#"*60)

        # LLM自主调用测试
        await self.test_llm_autonomous_call(
            "600036现在股价多少？",
            expected_tools=["get_stock_quote"]
        )

        await self.test_llm_autonomous_call(
            "分析一下招商银行的技术指标",
            expected_tools=["get_kline", "calculate_indicators"]
        )

        await self.test_llm_autonomous_call(
            "最近市场热点有哪些股票？",
            expected_tools=["get_hot_stocks"]
        )

        await self.test_llm_autonomous_call(
            "帮我看看平安银行的综合分析",
            expected_tools=["analyze_stock"]
        )

        await self.test_llm_autonomous_call(
            "筛选今天涨幅在3%到7%之间的股票",
            expected_tools=["screen_stocks"]
        )

        await self.test_llm_autonomous_call(
            "把000001加入我的自选",
            expected_tools=["add_to_watchlist"]
        )

        await self.test_llm_autonomous_call(
            "查看我的自选列表",
            expected_tools=["get_watchlist"]
        )

        await self.test_llm_autonomous_call(
            "今天市场情绪怎么样？",
            expected_tools=["get_market_sentiment"]
        )

        await self.test_llm_autonomous_call(
            "600036最近有什么新闻？",
            expected_tools=["get_stock_news"]
        )

        # 打印汇总
        self.print_summary()

    def print_summary(self):
        """打印测试汇总"""
        print("\n" + "="*60)
        print("测试汇总")
        print("="*60)
        print(f"通过: {self.passed}")
        print(f"失败: {self.failed}")
        print(f"总计: {self.passed + self.failed}")
        print(f"成功率: {self.passed/(self.passed+self.failed)*100:.1f}%" if (self.passed+self.failed) > 0 else "N/A")

        if self.failed == 0:
            print("\n[OK] 所有测试通过！")
        else:
            print(f"\n[WARN] 有 {self.failed} 个测试失败")


def main():
    """主函数"""
    print("AStack 工具完整测试")
    print("="*60)
    print("\n本测试包含：")
    print("1. 直接调用测试 - 程序直接调用 tool.execute()")
    print("2. LLM自主调用测试 - 模拟LLM分析意图并调用工具")
    print("\n注意：部分测试需要有效的OPENAI_API_KEY")
    print("="*60)

    tester = ToolTester()

    try:
        asyncio.run(tester.run_all_tests())
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
    except Exception as e:
        print(f"\n\n测试出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
