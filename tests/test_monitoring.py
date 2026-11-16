"""Current MonitoringService contract, with no real model or channel messages."""
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import core.agent
from services.monitoring_service import MonitoringService
from tools.base import ToolResult


class MonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = MonitoringService()
        self.service.watchlist = ['000001']
        self.service._notify_agent_alert = AsyncMock()
        self.service._notify_agent_routine = AsyncMock()

    async def asyncTearDown(self):
        self.service.stop()

    async def test_alert_is_not_repeated_inside_cooldown(self):
        result = ToolResult(success=True, data={
            'current_price': 10, 'change_percent': 8,
            'anomalies': [{'type': 'price_spike', 'level': 'high', 'message': '异动'}]})
        with patch.object(self.service._detect_tool, 'run', new=AsyncMock(return_value=result)), \
             patch('services.monitoring_service.time.time', return_value=1000):
            await self.service._check_all_stocks()
            await self.service._check_all_stocks()
        self.service._notify_agent_alert.assert_awaited_once()
        self.assertEqual(len(self.service.alert_history), 1)
        self.assertEqual(self.service.last_alert_ts['000001'], 1000)

    async def test_failed_tool_does_not_create_alert(self):
        with patch.object(self.service._detect_tool, 'run', new=AsyncMock(return_value=ToolResult(success=False, error='unavailable'))):
            await self.service._check_all_stocks()
        self.service._notify_agent_alert.assert_not_called()
        self.assertEqual(self.service.alert_history, [])

    async def test_routine_interval(self):
        with patch('services.monitoring_service.time.time', return_value=10000):
            await self.service._check_routine_analysis()
            await self.service._check_routine_analysis()
        self.service._notify_agent_routine.assert_awaited_once()

    async def test_current_trading_time_method(self):
        for when, expected in [(datetime(2026, 11, 14, 10), False), (datetime(2026, 11, 16, 10), True),
                               (datetime(2026, 11, 16, 12), False), (datetime(2026, 11, 16, 14), True)]:
            with self.subTest(when=when), patch('services.monitoring_service.datetime', SimpleNamespace(now=lambda: when)):
                self.assertEqual(self.service.is_trading_time(), expected)

    async def test_empty_watchlist_does_not_start_background_loop(self):
        self.service.watchlist = []
        with patch.object(self.service, '_load_watchlist', new=AsyncMock()):
            await self.service.start()
        self.assertFalse(self.service.running)
        self.assertIsNone(self.service._task)
