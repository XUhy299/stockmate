"""Persistent context and opt-in scheduler regression; no live model or channels."""
import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import core.agent
from config import config
from core.scheduler import TaskScheduler
from core.session import Session, SessionManager
from tools.all_tools import ContextCompressorManager, CompressContextTool
from web_app import serve


def conversation(count=8):
    session = Session(session_id='compaction')
    for index in range(count):
        session.add_message('user', f'分析第{index}轮000001')
        session.add_message('assistant', '', tool_calls=[{
            'id': f'call-{index}', 'type': 'function',
            'function': {'name': 'get_stock_quote', 'arguments': '{"codes":["000001"]}'}}])
        session.add_message('tool', '{"price":12.3}', tool_call_id=f'call-{index}', name='get_stock_quote')
        session.add_message('assistant', f'第{index}轮分析结果')
    return session


class PersistentContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = ContextCompressorManager()
        self.manager.token_threshold = 1
        self.summarize = AsyncMock(return_value='000001的历史分析，用户偏好稳健')
        self.mock_summary = patch.object(self.manager._tool, '_summarize_with_llm', new=self.summarize)
        self.mock_summary.start()

    async def asyncTearDown(self):
        self.mock_summary.stop()

    async def test_round_trip_retains_raw_transcript_and_exact_tool_pairs(self):
        session = conversation()
        original = deepcopy(session.messages)
        self.assertTrue(await self.manager.compact_session(session))
        self.assertEqual(session.messages, original)
        self.assertEqual(session.last_consolidated, 16)
        self.assertEqual(session.compress_count, 1)
        with TemporaryDirectory() as directory:
            storage = SessionManager(Path(directory))
            self.assertTrue(storage.save(session))
            reloaded = SessionManager(Path(directory)).get_or_create('compaction')
        self.assertEqual(reloaded.context_summary, session.context_summary)
        self.assertEqual(reloaded.messages, original)
        history = reloaded.get_history()
        self.assertEqual(history[0]['role'], 'system')
        self.assertNotIn('第0轮', str(history))
        self.assertIn('第7轮', str(history))
        calls = {call['id'] for message in history for call in message.get('tool_calls', [])}
        results = {message['tool_call_id'] for message in history if message['role'] == 'tool'}
        self.assertEqual(calls, results)
        self.assertEqual(calls, {f'call-{index}' for index in range(4, 8)})

    async def test_compaction_merges_previous_summary_without_reprocessing_old_turns(self):
        session = conversation()
        await self.manager.compact_session(session)
        for index in range(4):
            session.add_message('user', f'新增问题{index}')
            session.add_message('assistant', f'新增回答{index}')
        await self.manager.compact_session(session)
        prefix = self.summarize.call_args.args[0]
        self.assertIn('用户偏好稳健', str(prefix))
        self.assertNotIn('分析第0轮', str(prefix))
        self.assertIn('分析第4轮', str(prefix))
        self.assertEqual(session.compress_count, 2)

    async def test_display_only_cards_do_not_enter_summary(self):
        session = conversation()
        session.messages.insert(1, {'role': 'user', 'content': '浮层秘密内容', 'display_only': True})
        await self.manager.compact_session(session)
        self.assertNotIn('浮层秘密内容', str(self.summarize.call_args))
        self.assertNotIn('浮层秘密内容', str(session.get_history()))
        self.assertIn('浮层秘密内容', str(session.messages))

    async def test_summary_failure_does_not_advance_or_drop_history(self):
        for value in ('', RuntimeError('provider failed')):
            session = conversation()
            original = session.to_dict()
            self.summarize.return_value = value if isinstance(value, str) else None
            self.summarize.side_effect = value if isinstance(value, Exception) else None
            self.assertFalse(await self.manager.compact_session(session))
            self.assertEqual(session.to_dict(), original)

    async def test_short_or_recent_only_context_is_not_compacted(self):
        for count in (1, 4):
            session = conversation(count)
            self.assertFalse(await self.manager.compact_session(session, force=True))
        session = conversation()
        await self.manager.compact_session(session)
        self.summarize.reset_mock()
        self.assertFalse(await self.manager.compact_session(session))
        self.summarize.assert_not_awaited()

    async def test_legacy_index_without_summary_recovers_all_history_and_clear_resets_summary(self):
        session = conversation()
        session.last_consolidated = 30
        self.assertIn('分析第0轮', str(session.get_history()))
        await self.manager.compact_session(session)
        session.clear()
        self.assertEqual(session.context_summary, '')
        self.assertEqual(session.last_consolidated, 0)
        self.assertEqual(session.compress_count, 0)

    async def test_cancelled_summary_keeps_history_unchanged(self):
        session = conversation()
        self.summarize.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.manager.compact_session(session)
        self.assertEqual(session.context_summary, '')
        self.assertEqual(session.last_consolidated, 0)

    async def test_token_estimate_counts_tool_arguments(self):
        self.manager.token_threshold = 100
        self.assertTrue(self.manager.should_compress([{'role': 'assistant', 'content': '',
            'tool_calls': [{'function': {'arguments': 'x' * 1000}}]}]))


class SummaryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_summary_is_rejected_and_client_closed(self):
        reply = SimpleNamespace(choices=[SimpleNamespace(finish_reason='length', message=SimpleNamespace(content='未完成'))])
        client = SimpleNamespace(close=AsyncMock())
        with patch.object(config, 'OPENAI_API_KEY', 'offline-test-only'), \
             patch('tools.all_tools.AsyncOpenAI', return_value=client), \
             patch('tools.all_tools.request_completion', new=AsyncMock(return_value=reply)):
            with self.assertRaises(RuntimeError):
                await CompressContextTool()._summarize_with_llm([{'role': 'user', 'content': '测试'}], set(), strict=True)
        client.close.assert_awaited_once()


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.monitor = SimpleNamespace(set_agent=Mock(), start=AsyncMock(), stop=Mock())
        self.monitor_patch = patch('core.scheduler.monitoring_service', self.monitor)
        self.monitor_patch.start()
        self.agent = SimpleNamespace(post_event=AsyncMock())
        self.scheduler = TaskScheduler(self.agent)

    async def asyncTearDown(self):
        await self.scheduler.stop()
        self.monitor_patch.stop()

    async def test_start_stop_is_idempotent_and_clears_jobs(self):
        with patch.object(self.scheduler, 'is_trading_time', return_value=False):
            await self.scheduler.start()
            await self.scheduler.start()
        self.assertEqual(len(self.scheduler._schedule.jobs), 5)
        self.assertEqual(len(self.scheduler.tasks), 1)
        await self.scheduler.stop()
        await self.scheduler.stop()
        self.assertEqual(self.scheduler.tasks, [])
        self.assertEqual(self.scheduler._schedule.jobs, [])
        self.monitor.start.assert_not_awaited()

    async def test_scheduler_instances_do_not_share_jobs(self):
        other = TaskScheduler(self.agent)
        self.scheduler._setup_schedules()
        other._setup_schedules()
        await self.scheduler.stop()
        self.assertEqual(len(other._schedule.jobs), 5)
        await other.stop()

    async def test_pending_async_jobs_are_cancelled_on_shutdown(self):
        self.scheduler.running = True
        started = asyncio.Event()
        async def blocked():
            started.set()
            await asyncio.Event().wait()
        self.scheduler._spawn_job(blocked)
        await asyncio.wait_for(started.wait(), 1)
        task = next(iter(self.scheduler._jobs))
        await self.scheduler.stop()
        self.assertTrue(task.cancelled())
        self.assertEqual(self.scheduler._jobs, set())

    async def test_morning_report_posts_an_event_and_skips_weekends(self):
        with patch.object(self.scheduler, 'is_trading_day', return_value=False):
            await self.scheduler._generate_morning_report()
        self.agent.post_event.assert_not_awaited()
        with patch.object(self.scheduler, 'is_trading_day', return_value=True):
            await self.scheduler._generate_morning_report()
        self.assertEqual(self.agent.post_event.call_args.args[0], 'morning_report')
        self.assertEqual(self.agent.post_event.call_args.kwargs['metadata']['source'], 'schedule')

    async def test_schedule_runner_executes_the_registered_morning_job(self):
        self.scheduler.running = True
        self.scheduler._generate_morning_report = AsyncMock()
        self.scheduler._setup_schedules()
        self.scheduler._schedule.jobs[0].run()
        await asyncio.sleep(0)
        self.scheduler._generate_morning_report.assert_awaited_once()


class WebScheduleLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_stops_scheduler_on_cancellation_or_start_failure(self):
        for fail in (False, True):
            with self.subTest(start_failure=fail):
                entered = asyncio.Event()
                async def start():
                    entered.set()
                    if fail:
                        raise RuntimeError('test start failure')
                scheduler = SimpleNamespace(start=AsyncMock(side_effect=start), stop=AsyncMock())
                runner = SimpleNamespace(setup=AsyncMock(), cleanup=AsyncMock(),
                                         app={'web_state': SimpleNamespace(listeners=set())})
                agent = SimpleNamespace(start=AsyncMock())
                with patch('web_app.create_app', return_value=object()), \
                     patch('web_app.web.AppRunner', return_value=runner), \
                     patch('web_app.web.TCPSite', return_value=SimpleNamespace(start=AsyncMock())):
                    task = asyncio.create_task(serve(agent, scheduler=scheduler))
                    await asyncio.wait_for(entered.wait(), 1)
                    if fail:
                        with self.assertRaises(RuntimeError):
                            await task
                    else:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                scheduler.stop.assert_awaited_once()
                runner.cleanup.assert_awaited_once()
