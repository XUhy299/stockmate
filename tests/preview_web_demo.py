"""Explicit offline UI fixture. Never load real credentials or send notifications.

Run from the project root: python tests/preview_web_demo.py
"""
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import AStockAgent
from config import config
from execution_context import emit_progress
from tools.all_tools import subagent_manager
from tools.base import ToolResult
from tools.registry import registry
from web_app import serve

REPORT = """## 先看结论，再看依据

当前更适合关注**量价配合与风险边界**，结合你的研究周期判断，而不是仅凭单日涨跌得出结论。

> 以下是离线界面验收内容，非实时行情，不构成投资建议。

### 观察清单

| 维度 | 关注点 | 研究方法 |
| --- | --- | --- |
| 价格走势 | 趋势是否持续 | 对比阶段高低点 |
| 成交量 | 上涨是否获得量能支持 | 对比最近交易日 |
| 基本面 | 经营变化能否支撑预期 | 核对公告与财报 |

### 接下来可以做什么

1. 明确持有周期和风险承受范围。
2. 结合基本面与技术指标交叉验证。
3. 记录关注原因，定期复盘，而非追逐短时波动。

### 风险提示

历史价格不能保证未来表现。数据缺失时应保留判断，避免将推测当成事实。
"""


async def main():
    with TemporaryDirectory() as directory:
        with patch.object(config, "MEMORY_DIR", Path(directory)), patch.object(config, "DATA_DIR", Path(directory) / 'data'), patch.object(config, "OPENAI_API_KEY", ""):
            agent = AStockAgent(session_id="offline-visual-check")
        agent.client = object()
        original_execute = registry.execute
        async def query(name, **kwargs):
            if name in {'create_scheduled_task', 'list_scheduled_tasks', 'update_scheduled_task', 'delete_scheduled_task'}:
                return await original_execute(name, **kwargs)
            emit_progress("tool_started", tool=name)
            await asyncio.sleep(.35)
            emit_progress("tool_completed", tool=name, success=True)
            data = [
                {"code": "600036", "name": "招商银行", "notes": "离线验收样例 · 关注银行板块"},
                {"code": "300750", "name": "宁德时代", "notes": "离线验收样例 · 观察行业变化"},
            ] if name == "get_watchlist" else {
                "up_count": 2450, "down_count": 2670, "limit_up": 48,
                "limit_down": 12, "flat_count": None, "sentiment": "离线样例",
            }
            return ToolResult(True, data=data)

        async def model(messages):
            await asyncio.sleep(.45)
            last = messages[-1]
            if last["role"] != "tool":
                if "测试失败" in last.get("content", ""):
                    raise ValueError("Offline simulated provider failure")
                payload = {"id": "fixture-call", "type": "function",
                           "function": {"name": "get_stock_quote", "arguments": "{}"}}
                if any(word in last.get('content', '') for word in ('分钟后', '定时', '工作日')):
                    payload['function'] = {'name': 'create_scheduled_task', 'arguments': json.dumps({
                        'title': '离线样例研究', 'action': 'chat', 'prompt': '分析平安银行',
                        'frequency': 'once', 'delay_minutes': 10}, ensure_ascii=False)}
                tool = SimpleNamespace(id="fixture-call", function=SimpleNamespace(**payload["function"]),
                                       model_dump=lambda: payload)
                return SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason="tool_calls",
                    message=SimpleNamespace(content="", tool_calls=[tool]),
                )])
            return SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content='已创建离线样例定时研究，可在定时任务中查看。' if last.get('name') == 'create_scheduled_task' else REPORT, tool_calls=[]),
            )])

        async def brief(**kwargs):
            await query("get_watchlist")
            await query("get_market_sentiment")
            return {"content": "# 自选股每日简报\n\n" + REPORT}

        agent._call_llm = model
        with patch.object(registry, "execute", side_effect=query), patch.object(subagent_manager, "run_with_skill", side_effect=brief):
            try:
                print("OFFLINE UI FIXTURE: no model API, live market or Feishu calls.")
                await serve(agent)
            finally:
                await agent.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
