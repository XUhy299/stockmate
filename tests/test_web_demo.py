"""Offline regression tests. No provider or notification network calls."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from core.agent import AStockAgent, AgentEvent
from core.turn_runtime import process_turn
from config import config
from tools.base import ToolResult
from tools.registry import registry
from tools.all_tools import SpawnSubAgentTool, subagent_manager
from tools.all_tools import GetWatchlistTool
from execution_context import direct_results
from data.akshare_client_fixed import AKShareClient
from model_errors import model_error_message
from web_app import create_app
from aiohttp.test_utils import TestClient, TestServer


def answer(content="分析结果", calls=None, reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=reason,
        message=SimpleNamespace(content=content, tool_calls=calls or []),
    )])


def call(name, args=None):
    payload = {"id": "call-1", "type": "function",
               "function": {"name": name, "arguments": json.dumps(args or {})}}
    return SimpleNamespace(
        id="call-1", function=SimpleNamespace(**payload["function"]),
        model_dump=lambda: payload,
    )


def event(content="分析一下", kind="user_message", source="web", task_id="task-1", **meta):
    return AgentEvent(kind, content, {"source": source, "task_id": task_id, **meta})


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(config, "MEMORY_DIR", root / "memory"),
            patch.object(config, "DATA_DIR", root / "data"),
            patch.object(config, "OPENAI_API_KEY", ""),
        ]
        for item in self.patches:
            item.start()
        self.agent = AStockAgent(session_id="test")
        self.agent.client = object()  # No real provider can be reached.
        self.agent._call_llm = AsyncMock(return_value=answer())
        self.seen = []
        self.agent.set_event_handler(lambda kind, data: self.seen.append((kind, data)))
        self.feishu = patch("core.turn_runtime.feishu_channel.send_message", new_callable=AsyncMock)
        self.wecom = patch("core.turn_runtime.wecom_channel.send_message", new_callable=AsyncMock)
        self.send_feishu = self.feishu.start()
        self.send_wecom = self.wecom.start()
        self.client = None

    async def asyncTearDown(self):
        await self.agent.stop()
        if self.client:
            for queue in tuple(self.client.app["web_state"].listeners):
                if not queue.full():
                    queue.put_nowait(None)
            await self.client.close()
        self.feishu.stop()
        self.wecom.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    async def http(self):
        self.client = TestClient(TestServer(create_app(self.agent)))
        await self.client.start_server()
        return self.client

    async def test_follow_up_includes_previous_turn_once(self):
        await process_turn(self.agent, event("研究招商银行"))
        await process_turn(self.agent, event("刚才那只股票有什么风险", task_id="task-2"))
        messages = self.agent._call_llm.call_args.args[0]
        users = [m["content"] for m in messages if m["role"] == "user"]
        self.assertEqual(users, ["研究招商银行", "刚才那只股票有什么风险"])
        self.assertEqual(len([m for m in self.agent.session.messages if m["role"] == "assistant"]), 2)
        self.send_feishu.assert_not_called()
        self.send_wecom.assert_not_called()

    async def test_profile_is_rebuilt_for_each_turn(self):
        with patch.object(self.agent, "_build_system_prompt", side_effect=["旧偏好", "新偏好"]):
            await process_turn(self.agent, event())
            await process_turn(self.agent, event(task_id="task-2"))
        self.assertEqual(self.agent._call_llm.call_args.args[0][0]["content"], "新偏好")

    async def test_long_tool_history_keeps_report_and_call_pairs(self):
        self.agent.session.add_message("user", "上一轮研究")
        for index in range(12):
            self.agent.session.add_message("assistant", "", tool_calls=[{
                "id": str(index), "type": "function",
                "function": {"name": "get_stock_quote", "arguments": "{}"},
            }])
            self.agent.session.add_message("tool", "数据", tool_call_id=str(index))
        self.agent.session.add_message("assistant", "上一轮重要报告")
        await process_turn(self.agent, event("请继续解释报告"))
        messages = self.agent._call_llm.call_args.args[0]
        self.assertIn("上一轮重要报告", str(messages))
        self.assertEqual(sum(m["role"] == "tool" for m in messages), 12)

    async def test_cli_without_key_retains_mock_reply(self):
        self.agent.client = None
        handler = []
        self.agent.set_message_handler(handler.append)
        await process_turn(self.agent, event("帮助", source="user_message"))
        self.assertEqual(len(handler), 1)
        self.assertIn("模拟模式", handler[0])

    async def test_brief_is_direct_once_and_can_be_followed_up(self):
        async def brief(**kwargs):
            self.assertEqual(kwargs["skill_name"], "morning_report")
            self.assertTrue(direct_results.get())
            return {"content": "# 每日简报\n数据与风险"}
        with patch.object(subagent_manager, "run_with_skill", side_effect=brief):
            await process_turn(self.agent, event("生成简报", kind="web_daily_brief"))
        self.assertFalse(list(self.agent.subagent_results_dir.glob("*.json")))
        self.assertEqual(sum(m["content"].startswith("# 每日简报") for m in self.agent.session.messages), 1)
        await process_turn(self.agent, event("解释简报", task_id="follow"))
        self.assertIn("# 每日简报", str(self.agent._call_llm.call_args.args[0]))

    async def test_spawn_report_is_not_duplicated(self):
        self.agent._call_llm.return_value = answer("", [call("spawn_subagent")], "tool_calls")
        with patch.object(registry, "execute", new=AsyncMock(return_value=ToolResult(
            True, data={"content": "完整研究报告"}
        ))):
            await process_turn(self.agent, event())
        visible = [m for m in self.agent.session.messages if m.get("visible")]
        self.assertEqual([m["content"] for m in visible if m["role"] == "assistant"], ["完整研究报告"])
        self.assertEqual(self.seen[-1][0], "completed")

    async def test_failed_subagent_and_model_and_limit_end_busy_state(self):
        self.agent._call_llm.return_value = answer("", [call("spawn_subagent")], "tool_calls")
        with patch.object(registry, "execute", new=AsyncMock(return_value=ToolResult(False, error="失败"))):
            await process_turn(self.agent, event())
        self.assertEqual(self.seen[-1][0], "failed")
        self.agent._call_llm.side_effect = ValueError("provider secret must not leak")
        await process_turn(self.agent, event(task_id="two"))
        self.assertNotIn("secret", self.seen[-1][1]["error"])
        self.agent._call_llm.side_effect = None
        self.agent._call_llm.return_value = answer("", [call("get_stock_quote")], "tool_calls")
        self.agent.max_work_iterations = 1
        with patch.object(registry, "execute", new=AsyncMock(return_value=ToolResult(True, data={}))):
            await process_turn(self.agent, event(task_id="three"))
        self.assertEqual(self.seen[-1][0], "failed")
        self.assertIsNone(self.agent._active_task)
        self.assertEqual(self.agent.status, "idle")

    async def test_channel_delivery_is_bound_to_own_event(self):
        await process_turn(self.agent, event(source="feishu_message", kind="feishu_message", chat_id="chat-a"))
        self.send_feishu.assert_awaited_once_with("chat-a", "分析结果", msg_type="interactive")
        await process_turn(self.agent, event(task_id="web"))
        self.assertEqual(self.send_feishu.await_count, 1)
        await process_turn(self.agent, event(source="wecom_message", kind="wecom_message", from_user="user-b"))
        self.send_wecom.assert_awaited_once()

    async def test_serial_mailbox_does_not_drop_simultaneous_events(self):
        await self.agent.post_event("user_message", "第一条", {"source": "web", "task_id": "first"})
        await self.agent.post_event("user_message", "第二条", {"source": "web", "task_id": "second"})
        terminal = asyncio.Event()
        def record(kind, data):
            self.seen.append((kind, data))
            if kind == "completed" and data["task_id"] == "second":
                terminal.set()
        self.agent.set_event_handler(record)
        await self.agent.start()
        await asyncio.wait_for(terminal.wait(), 3)
        users = [m["content"] for m in self.agent.session.messages if m["role"] == "user"]
        self.assertEqual(users, ["第一条", "第二条"])

    async def test_watchlist_and_market_do_not_touch_memory_or_model(self):
        client = await self.http()
        self.agent.save_session()
        before = deepcopy(self.agent.session.messages)
        path = self.agent.session_manager._get_session_path("test")
        before_file = path.read_bytes()
        with patch.object(registry, "execute", new=AsyncMock(return_value=ToolResult(True, data=[]))):
            for route in ("/api/watchlist", "/api/market"):
                response = await client.get(route, headers={"Host": "127.0.0.1:8088"})
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())["data"], [])
        self.assertEqual(self.agent.session.messages, before)
        self.assertEqual(path.read_bytes(), before_file)
        self.agent._call_llm.assert_not_called()

    async def test_watchlist_is_visible_and_persisted_but_not_in_model_context(self):
        client = await self.http()
        stocks = [{"code": "600036", "notes": "不要送入模型的备注"}]
        with patch.object(registry, "execute", new=AsyncMock(return_value=ToolResult(True, data=stocks))):
            response = await client.post("/api/actions/watchlist", json={},
                                         headers={"Host": "127.0.0.1:8088"})
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(data["messages"][-2]["content"], "我的自选")
        self.assertEqual(data["messages"][-1]["display"]["stocks"], stocks)
        saved = self.agent.session_manager._get_session_path("test").read_text(encoding="utf-8")
        self.assertIn("不要送入模型的备注", saved)
        self.agent._call_llm.assert_not_called()
        await process_turn(self.agent, event("研究银行板块"))
        prompt = self.agent._call_llm.call_args.args[0]
        self.assertNotIn("我的自选", str([m for m in prompt if m["role"] != "system"]))
        self.assertNotIn("不要送入模型的备注", str(prompt))

    async def test_watchlist_resolves_names_without_rewriting_saved_data(self):
        from data.akshare_client_fixed import akshare_client
        tool = GetWatchlistTool()
        tool.watchlist_path = Path(self.temp.name) / "watchlist.json"
        tool.watchlist_path.write_text(json.dumps({"stocks": [
            {"code": "000001", "notes": "银行"},
            {"code": "600036"},
        ]}), encoding="utf-8")
        original = tool.watchlist_path.read_bytes()
        names = {"000001": {"name": "平安银行"}, "600036": {"name": "招商银行"}}
        with patch.object(akshare_client, "get_batch_quotes", new=AsyncMock(return_value=names)):
            result = await tool.run()
        self.assertTrue(result.success)
        self.assertEqual([s["name"] for s in result.data], ["平安银行", "招商银行"])
        self.assertEqual(tool.watchlist_path.read_bytes(), original)
        self.agent._call_llm.assert_not_called()

    async def test_watchlist_still_opens_when_name_service_is_unavailable(self):
        from data.akshare_client_fixed import akshare_client
        tool = GetWatchlistTool()
        tool.watchlist_path = Path(self.temp.name) / "watchlist.json"
        tool.watchlist_path.write_text('{"stocks":[{"code":"600036"}]}', encoding="utf-8")
        with patch.object(akshare_client, "get_batch_quotes", new=AsyncMock(side_effect=RuntimeError("unavailable"))):
            result = await tool.run()
        self.assertTrue(result.success)
        self.assertEqual(result.data[0]["code"], "600036")

    async def test_qwen_thinking_options_match_compatible_api(self):
        with patch.object(config, "OPENAI_MODEL", "qwen3.8-flash"), \
             patch.object(config, "OPENAI_THINKING", "disabled"):
            self.assertEqual(config.model_request_options(), {"extra_body": {"enable_thinking": False}})
        with patch.object(config, "OPENAI_MODEL", "kimi-k2.6"), \
             patch.object(config, "OPENAI_THINKING", "disabled"):
            self.assertEqual(config.model_request_options(), {"extra_body": {"thinking": {"type": "disabled"}}})

    async def test_market_parser_accepts_changed_page_and_correct_labels(self):
        html = """<p>统计日期：2026-11-16 15:00:00</p>
        <table><tr><td>上涨</td><td>1,109</td><td>下跌</td><td>4019</td><td>平盘</td><td>89</td></tr>
        <tr><td>涨停</td><td>33</td><td>跌停</td><td>68</td></tr></table>"""
        result = AKShareClient.parse_market_activity(html)
        self.assertEqual(result["up_count"], 1109)
        self.assertEqual(result["down_count"], 4019)
        self.assertEqual(result["limit_up"], 33)
        self.assertEqual(result["timestamp"], "2026-11-16 15:00:00")
        with self.assertRaises(ValueError):
            AKShareClient.parse_market_activity("<html>页面访问受限</html>")

    async def test_market_source_failure_is_not_empty_success(self):
        import requests
        from data.akshare_client_fixed import akshare_client
        akshare_client._cache.pop("market_sentiment", None)
        with patch("data.akshare_client_fixed.requests.get", side_effect=requests.Timeout):
            result = await registry.execute("get_market_sentiment")
        self.assertFalse(result.success)
        self.assertIn("超时", result.error)

    async def test_tencent_quote_parser_uses_source_time_and_units(self):
        fields = [""] * 40
        for index, value in {1: "平安银行", 3: "12.30", 4: "12.07", 5: "12.06", 6: "2170130",
                             30: "20261116161500", 31: "0.23", 32: "1.91", 33: "12.30",
                             34: "12.05", 35: "12.30/2170130/2645742574", 38: "1.12"}.items():
            fields[index] = value
        text = 'v_sz000001="' + "~".join(fields) + '";'
        quote = AKShareClient.parse_tencent_quotes(text)["000001"]
        self.assertEqual(quote["price"], 12.3)
        self.assertEqual(quote["volume"], 217013000)
        self.assertEqual(quote["amount"], 2645742574)
        self.assertEqual(quote["timestamp"], "2026-11-16T16:15:00")
        self.assertEqual(AKShareClient.parse_tencent_quotes('v_pv_none_match="1";'), {})

    async def test_provider_errors_are_specific_without_echoing_credentials(self):
        for status, expected in [(401, "认证失败"), (404, "不可用"), (400, "参数不兼容"), (429, "额度")]:
            exc = SimpleNamespace(status_code=status)
            self.assertIn(expected, model_error_message(exc, "kimi-k2.6"))

    async def test_model_pacing_respects_configured_rpm(self):
        from collections import deque
        import model_requests
        now = [0.0]
        async def advance(delay):
            now[0] += delay
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=AsyncMock(return_value="ok")
        )))
        with patch.object(config, "OPENAI_REQUESTS_PER_MINUTE", 3), \
             patch.object(model_requests, "_request_times", deque()), \
             patch.object(model_requests, "monotonic", side_effect=lambda: now[0]), \
             patch.object(model_requests, "sleep", side_effect=advance):
            for _ in range(4):
                self.assertEqual(await model_requests.request_completion(fake, model="test"), "ok")
        self.assertGreaterEqual(now[0], 61)

    async def test_http_missing_key_duplicates_and_security(self):
        client = await self.http()
        headers = {"Host": "127.0.0.1:8088"}
        self.agent.client = None
        response = await client.post("/api/chat", json={"message": "测试"}, headers=headers)
        self.assertEqual(response.status, 503)
        self.agent.client = object()
        response = await client.post("/api/chat", json={"message": "测试"}, headers=headers)
        self.assertEqual(response.status, 202)
        response = await client.post("/api/actions/daily-brief", json={}, headers=headers)
        self.assertEqual(response.status, 409)
        response = await client.post("/api/chat", json={}, headers={**headers, "Origin": "https://attacker.example"})
        self.assertEqual(response.status, 403)
        response = await client.get("/api/session", headers={"Host": "attacker.example"})
        self.assertEqual(response.status, 403)

    async def test_sse_disconnect_does_not_cancel_turn_and_reconnect_has_snapshot(self):
        client = await self.http()
        headers = {"Host": "127.0.0.1:8088"}
        response = await client.get("/api/events", headers=headers)
        line = await asyncio.wait_for(response.content.readline(), 2)
        self.assertIn(b'snapshot', line)
        response.close()
        await process_turn(self.agent, event())
        response = await client.get("/api/events", headers=headers)
        line = await asyncio.wait_for(response.content.readline(), 2)
        data = json.loads(line.decode().removeprefix("data: "))
        self.assertEqual(data["messages"][-1]["content"], "分析结果")
        self.assertFalse(data["busy"])
        response.close()

    async def test_tool_query_failure_is_explicit(self):
        client = await self.http()
        with patch.object(registry, "execute", new=AsyncMock(return_value=ToolResult(False, error="raw provider secret"))):
            response = await client.get("/api/market", headers={"Host": "127.0.0.1:8088"})
            self.assertEqual(response.status, 502)
            self.assertNotIn("secret", await response.text())

    async def test_post_loop_and_session_round_trip(self):
        client = await self.http()
        completed = asyncio.Event()
        original = self.agent._event_handler
        def observe(kind, data):
            original(kind, data)
            if kind == "completed":
                completed.set()
        self.agent.set_event_handler(observe)
        await self.agent.start()
        response = await client.post("/api/chat", json={"message": "完整链路"},
                                     headers={"Host": "127.0.0.1:8088"})
        self.assertEqual(response.status, 202)
        await asyncio.wait_for(completed.wait(), 3)
        response = await client.get("/api/session", headers={"Host": "127.0.0.1:8088"})
        data = await response.json()
        self.assertEqual(data["task"]["status"], "completed")
        self.assertFalse(data["busy"])
        self.assertEqual([m["content"] for m in data["messages"]], ["完整链路", "分析结果"])

    async def test_web_startup_never_calls_terminal_onboarding(self):
        from main import AStockSystem
        system = AStockSystem(session_id="startup-test")
        system._load_user_memory = AsyncMock()
        system._start_channel_servers = AsyncMock()
        with patch("web_app.serve", new_callable=AsyncMock) as serve:
            await system.start("web")
            serve.assert_awaited_once_with(system.agent)
        system._load_user_memory.assert_not_called()

    async def test_web_schedule_is_an_explicit_opt_in(self):
        from main import AStockSystem
        system = AStockSystem(session_id="scheduled-startup")
        system._load_user_memory = AsyncMock()
        system._start_channel_servers = AsyncMock()
        with patch("web_app.serve", new_callable=AsyncMock) as serve:
            await system.start("web", enable_schedule=True)
            serve.assert_awaited_once_with(system.agent, scheduler=system.scheduler)
        system._load_user_memory.assert_not_called()

    async def test_background_tasks_reach_web_but_do_not_send_feishu_replies(self):
        client = await self.http()
        state = client.app['web_state']
        for index, (kind, source) in enumerate((('morning_report', 'schedule'),
                                               ('routine_analysis', 'routine_analysis'),
                                               ('price_alert', 'price_alert'))):
            task_id = f'timer-{index}'
            await process_turn(self.agent, event('后台分析任务', kind=kind, source=source, task_id=task_id))
            self.assertEqual(state.task['task_id'], task_id)
            self.assertEqual(state.task['status'], 'completed')
            self.assertTrue(any(item['type'] == 'completed' and item['task_id'] == task_id for item in state.events))
            self.assertIn('分析结果', str(state.snapshot()['messages']))
        self.send_feishu.assert_not_awaited()
        self.send_wecom.assert_not_awaited()

    async def test_natural_language_can_create_a_real_saved_task(self):
        self.agent._call_llm.side_effect = [answer('', [call('create_scheduled_task', {
            'title': '银行研究', 'action': 'chat', 'prompt': '分析平安银行',
            'frequency': 'once', 'delay_minutes': 10})]), answer('已安排十分钟后的研究')]
        await process_turn(self.agent, event('十分钟后分析平安银行'))
        tasks = self.agent.scheduled_tasks.list('test')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['title'], '银行研究')
        self.assertTrue(self.agent.scheduled_tasks.path.is_file())

    async def test_scheduled_task_api_and_due_skill_result_share_the_chat(self):
        from datetime import datetime, timedelta
        from scheduled_tasks import TZ
        client = await self.http()
        headers = {'Host': '127.0.0.1:8088'}
        body = {'title': '演示简报', 'action': 'daily_brief', 'frequency': 'once', 'delay_minutes': 1}
        response = await client.post('/api/scheduled-tasks', json=body, headers=headers)
        self.assertEqual(response.status, 200)
        task = (await response.json())['task']
        response = await client.get('/api/scheduled-tasks', headers=headers)
        self.assertEqual(len((await response.json())['tasks']), 1)
        service = self.agent.scheduled_tasks
        service.agent = self.agent
        await service.tick(datetime.now(TZ) + timedelta(minutes=2))
        events = self.agent._drain_mailbox()
        self.assertEqual(len(events), 1)
        with patch.object(subagent_manager, 'run_with_skill', new=AsyncMock(return_value={'content': '# 定时简报结果'})) as skill:
            await process_turn(self.agent, events[0])
            skill.assert_awaited_once()
        self.assertEqual(service.list('test')[0]['last_status'], 'completed')
        self.assertIn('# 定时简报结果', str(client.app['web_state'].snapshot()['messages']))
        self.send_feishu.assert_not_awaited()
        response = await client.post(f"/api/scheduled-tasks/{task['id']}/delete", json={}, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(service.list('test'), [])

    async def test_task_api_validation_pause_and_cross_origin_guard(self):
        client = await self.http()
        headers = {'Host': '127.0.0.1:8088'}
        for body in ([], {'frequency': 'cron'}, {'time': '25:00'}, {'session_id': 'other'}):
            response = await client.post('/api/scheduled-tasks', json=body, headers=headers)
            self.assertEqual(response.status, 400)
        response = await client.post('/api/scheduled-tasks', json={'title': '每日研究', 'action': 'daily_brief', 'frequency': 'weekdays'}, headers=headers)
        task_id = (await response.json())['task']['id']
        response = await client.post(f'/api/scheduled-tasks/{task_id}/update', json={'enabled': False}, headers=headers)
        self.assertFalse((await response.json())['task']['enabled'])
        response = await client.post('/api/scheduled-tasks', json={}, headers={**headers, 'Origin': 'https://example.com'})
        self.assertEqual(response.status, 403)

    async def test_scheduled_job_without_model_key_is_failed_not_mock_success(self):
        from datetime import datetime, timedelta
        from scheduled_tasks import TZ
        service = self.agent.scheduled_tasks
        service.create('test', title='失败检查', frequency='once', delay_minutes=1)
        service.agent = self.agent
        await service.tick(datetime.now(TZ) + timedelta(minutes=2))
        self.agent.client = None
        await process_turn(self.agent, self.agent._drain_mailbox()[0])
        self.assertEqual(service.list('test')[0]['last_status'], 'failed')
        self.assertIn('OPENAI_API_KEY', service.list('test')[0]['last_error'])

    async def test_web_compaction_is_saved_and_reused_on_the_next_turn(self):
        from tools.all_tools import context_compressor
        for index in range(7):
            self.agent.session.add_message('user', f'旧问题{index}')
            self.agent.session.add_message('assistant', f'旧回答{index}')
        original_count = len(self.agent.session.messages)
        with patch.object(context_compressor, 'token_threshold', 1), \
             patch.object(context_compressor._tool, '_summarize_with_llm', new=AsyncMock(return_value='用户偏好稳健，已研究000001')) as summarize:
            await process_turn(self.agent, event('继续研究银行股'))
        self.assertEqual(self.agent.session.compress_count, 1)
        self.assertEqual(len(self.agent.session.messages), original_count + 2)
        saved = self.agent.session_manager._get_session_path('test').read_text(encoding='utf-8')
        self.assertEqual(json.loads(saved)['context_summary'], '用户偏好稳健，已研究000001')
        await process_turn(self.agent, event('按我之前的偏好比较', task_id='follow-up'))
        model_messages = self.agent._call_llm.call_args.args[0]
        self.assertIn('用户偏好稳健', str(model_messages))
        self.assertNotIn('旧问题0', str(model_messages))
        self.assertIn('旧问题0', str(self.agent.session.messages))
        summarize.assert_awaited_once()

    async def test_validation_and_static_traversal(self):
        client = await self.http()
        headers = {"Host": "127.0.0.1:8088"}
        for body in ({}, {"message": ""}, {"message": "x" * 12001}, []):
            response = await client.post("/api/chat", json=body, headers=headers)
            self.assertEqual(response.status, 400)
        response = await client.post("/api/chat", data="message=hello", headers=headers)
        self.assertEqual(response.status, 415)
        response = await client.get("/%2e%2e/config.py", headers=headers)
        self.assertEqual(response.status, 404)

    async def test_brief_tool_direct_return_does_not_write_mailbox(self):
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=AsyncMock(return_value=answer("实际简报内容"))
        )), close=AsyncMock())
        tool = SpawnSubAgentTool()
        tool.mailbox_dir = Path(self.temp.name) / "results"
        tool.mailbox_dir.mkdir()
        with patch.object(config, "OPENAI_API_KEY", "test-only"), patch("tools.all_tools.AsyncOpenAI", return_value=fake):
            token = direct_results.set(True)
            try:
                result = await tool.run(task_name="brief", task_description="生成简报", skill_name="morning_report")
            finally:
                direct_results.reset(token)
        self.assertTrue(result.success)
        self.assertEqual(result.data["content"], "实际简报内容")
        self.assertFalse(list(tool.mailbox_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
