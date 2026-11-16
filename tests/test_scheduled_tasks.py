import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from scheduled_tasks import ScheduledTaskService, TZ


class ScheduledTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tasks.json'
        self.service = ScheduledTaskService(self.path)
        self.now = datetime(2026, 11, 17, 8, 0, tzinfo=TZ)
        self.agent = SimpleNamespace(session=SimpleNamespace(session_id='test'), _active_task=None, _pending_web=None,
                                     post_event=AsyncMock(), mailbox_dir=Path(self.temp.name) / 'mailbox')
        self.agent.mailbox_dir.mkdir()
        self.service.agent = self.agent

    async def asyncTearDown(self):
        await self.service.stop()
        self.temp.cleanup()

    async def test_persistent_create_dedup_and_session_isolation(self):
        task = self.service.create('test', title='晨报', now=self.now)
        duplicate = self.service.create('test', title='晨报', now=self.now)
        self.assertEqual(task['id'], duplicate['id'])
        self.assertEqual(len(ScheduledTaskService(self.path).list('test')), 1)
        self.assertEqual(self.service.list('other'), [])
        with self.assertRaises(ValueError):
            self.service.delete('other', task['id'])

    async def test_once_relative_time_and_weekend_rollover(self):
        once = self.service.create('test', frequency='once', delay_minutes=10, now=self.now)
        self.assertEqual(datetime.fromisoformat(once['run_at']), self.now + timedelta(minutes=10))
        friday = datetime(2026, 11, 20, 18, 0, tzinfo=TZ)
        daily = self.service.create('test', frequency='weekdays', time='09:00', now=friday)
        self.assertEqual(datetime.fromisoformat(daily['next_run_at']).weekday(), 0)

    async def test_invalid_times_and_payloads_are_rejected(self):
        for kwargs in ({'frequency': 'cron'}, {'action': 'shell'}, {'time': '24:00'},
                       {'frequency': 'once', 'run_at': '2020-01-01T09:00'},
                       {'frequency': 'once', 'delay_minutes': True}, {'action': 'chat', 'prompt': ''}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service.create('test', now=self.now, **kwargs)

    async def test_due_once_is_enqueued_once_and_deleted_queue_is_ignored(self):
        task = self.service.create('test', frequency='once', delay_minutes=1, now=self.now)
        await self.service.tick(self.now + timedelta(minutes=2))
        await self.service.tick(self.now + timedelta(minutes=3))
        self.agent.post_event.assert_awaited_once()
        metadata = self.agent.post_event.call_args.kwargs['metadata']
        self.assertEqual(metadata['session_id'], 'test')
        self.service.delete('test', task['id'])
        self.assertFalse(self.service.begin(task['id'], metadata['scheduled_run_id']))

    async def test_busy_agent_does_not_start_another_task(self):
        self.service.create('test', frequency='once', delay_minutes=1, now=self.now)
        self.agent._active_task = {'task_id': 'current'}
        await self.service.tick(self.now + timedelta(minutes=2))
        self.agent.post_event.assert_not_awaited()

    async def test_other_session_tasks_wait_for_their_owner_session(self):
        self.service.create('other', frequency='once', delay_minutes=1, now=self.now)
        await self.service.tick(self.now + timedelta(minutes=2))
        self.agent.post_event.assert_not_awaited()

    async def test_pause_resume_and_pending_cancellation(self):
        task = self.service.create('test', now=self.now)
        self.service.update('test', task['id'], False)
        await self.service.tick(self.now + timedelta(hours=2))
        self.agent.post_event.assert_not_awaited()
        self.service.update('test', task['id'], True, now=self.now)
        await self.service.tick(self.now + timedelta(hours=2))
        metadata = self.agent.post_event.call_args.kwargs['metadata']
        self.service.update('test', task['id'], False)
        self.assertFalse(self.service.begin(task['id'], metadata['scheduled_run_id']))

    async def test_completion_and_error_status_are_saved(self):
        task = self.service.create('test', now=self.now)
        await self.service.tick(self.now + timedelta(hours=2))
        run_id = self.agent.post_event.call_args.kwargs['metadata']['scheduled_run_id']
        self.assertTrue(self.service.begin(task['id'], run_id))
        self.assertFalse(self.service.begin(task['id'], run_id))
        self.service.finish(task['id'], run_id, 'failed', '模型暂不可用')
        row = self.service.list('test')[0]
        self.assertEqual(row['last_status'], 'failed')
        self.assertIsNone(row['pending_run_id'])

    async def test_restart_marks_interrupted_runs_failed_and_stop_cleans_loop(self):
        task = self.service.create('test', now=self.now)
        await self.service.tick(self.now + timedelta(hours=2))
        run_id = self.agent.post_event.call_args.kwargs['metadata']['scheduled_run_id']
        self.service.begin(task['id'], run_id)
        await self.service.start(self.agent)
        self.assertEqual(self.service.list('test')[0]['last_status'], 'failed')
        self.assertTrue(self.service.running)
        await self.service.stop()
        self.assertFalse(self.service.running)

    async def test_corrupt_file_is_not_overwritten(self):
        self.path.write_text('broken', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.service.create('test', now=self.now)
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'broken')

    async def test_scheduled_runs_cannot_create_more_schedules(self):
        from execution_context import scheduled_service, active_session_id, scheduled_execution
        from tools.all_tools import CreateScheduledTaskTool
        tokens = [scheduled_service.set(self.service), active_session_id.set('test'), scheduled_execution.set(True)]
        try:
            result = await CreateScheduledTaskTool().run(title='递归任务')
            self.assertFalse(result.success)
            self.assertEqual(self.service.list('test'), [])
        finally:
            scheduled_service.reset(tokens[0]); active_session_id.reset(tokens[1]); scheduled_execution.reset(tokens[2])
