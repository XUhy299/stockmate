"""Contracts for the tools actually registered by AStockAgent, never live services."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import core.agent
from config import config
import core.memory as memory
from data.akshare_client_fixed import akshare_client
from execution_context import direct_results
from tools.all_tools import ALL_TOOLS
from tools.registry import registry


def bars():
    return [{'date': f'2026-{9 + index // 28:02}-{1 + index % 28:02}',
             'open': 10 + index * .02, 'close': 10.1 + index * .02,
             'high': 10.3 + index * .02, 'low': 9.9 + index * .02,
             'volume': 100000 + index * 100, 'amount': None} for index in range(60)]


class ToolContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.quotes = {'000001': {'code': '000001', 'name': '平安银行', 'price': 12.3,
                                  'change_percent': 1.9, 'turnover_rate': 1.1, 'amount': 2000000}}
        self.patches = [
            patch.object(config, 'MEMORY_DIR', self.root / 'memory'),
            patch.object(config, 'DATA_DIR', Path(__file__).resolve().parents[1] / 'data_store'),
            patch.object(config, 'WATCHLIST_FILE', self.root / 'watchlist.json'),
            patch.object(config, 'OPENAI_API_KEY', 'offline-test-only'),
            patch.object(memory.UserMemory, '_instance', None),
            patch('requests.sessions.Session.request', side_effect=AssertionError('No real network in offline tests')),
            patch('httpx.AsyncClient.send', new=AsyncMock(side_effect=AssertionError('No live model'))),
            patch.object(akshare_client, 'get_batch_quotes', new=AsyncMock(return_value=self.quotes)),
            patch.object(akshare_client, 'get_realtime_quote', new=AsyncMock(return_value=self.quotes['000001'])),
            patch.object(akshare_client, 'get_kline', new=AsyncMock(return_value=bars())),
            patch.object(akshare_client, 'get_stock_news', new=AsyncMock(return_value=[{'title': '离线新闻', 'url': 'https://example.com/'}])),
            patch.object(akshare_client, 'get_hot_stocks', new=AsyncMock(return_value=list(self.quotes.values()))),
            patch.object(akshare_client, 'get_market_sentiment', new=AsyncMock(return_value={'up_count': 10, 'down_count': 20, 'sentiment': '谨慎'})),
        ]
        for item in self.patches:
            item.start()
        self.memory_patch = patch.object(memory, 'user_memory', memory.UserMemory())
        self.memory_patch.start()
        from execution_context import scheduled_service, active_session_id
        from scheduled_tasks import ScheduledTaskService
        self.schedule_token = scheduled_service.set(ScheduledTaskService(self.root / 'scheduled.json'))
        self.session_token = active_session_id.set('test')
        self.tools = {tool.name: tool for tool in (cls() for cls in ALL_TOOLS)}

    async def asyncTearDown(self):
        from execution_context import scheduled_service, active_session_id
        scheduled_service.reset(self.schedule_token)
        active_session_id.reset(self.session_token)
        self.memory_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    async def test_every_registered_tool_has_an_executable_contract(self):
        config.WATCHLIST_FILE.write_text('{"stocks":[]}', encoding='utf-8')
        args = {
            'get_stock_quote': {'codes': ['000001']}, 'get_kline': {'code': '000001'},
            'get_stock_news': {'code': '000001'}, 'get_hot_stocks': {}, 'get_market_sentiment': {},
            'add_to_watchlist': {'codes': ['000001'], 'notes': '离线测试'}, 'get_watchlist': {},
            'remove_from_watchlist': {'codes': ['000001']}, 'calculate_indicators': {'code': '000001'},
            'detect_anomalies': {'code': '000001'}, 'analyze_stock': {'code': '000001'},
            'screen_stocks': {'min_change': 1, 'min_turnover': 1, 'limit': 1},
            'load_skill': {'skill_name': 'morning_report'},
            'update_user_profile': {'field': 'risk_preference', 'value': '稳健', 'reason': '离线测试'},
            'compress_context': {'messages': [{'role': 'user', 'content': '分析000001'}], 'force': True},
            'spawn_subagent': {'task_name': 'offline', 'task_description': '离线简报', 'skill_name': 'morning_report'},
            'idle': {'reason': '离线完成'},
            'create_scheduled_task': {'title': '每日简报', 'action': 'daily_brief', 'frequency': 'weekdays', 'time': '09:00'},
            'list_scheduled_tasks': {},
            'update_scheduled_task': {'task_id': '', 'enabled': False},
            'delete_scheduled_task': {'task_id': ''},
        }
        self.assertEqual(set(self.tools), set(args), '新注册工具必须补充验收用例')
        self.assertTrue(set(config.MAIN_AGENT_TOOLS + config.SUBAGENT_TOOLS) <= set(args))
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='离线简报', tool_calls=[]))])
        token = direct_results.set(True)
        try:
            with patch('tools.all_tools.AsyncOpenAI', return_value=SimpleNamespace(close=AsyncMock())), \
                 patch('tools.all_tools.request_completion', new=AsyncMock(return_value=response)), \
                 patch.object(self.tools['compress_context'], '_summarize_with_llm', new=AsyncMock(return_value='000001的摘要')):
                for name, values in args.items():
                    with self.subTest(tool=name):
                        schema = self.tools[name].get_schema()['function']
                        self.assertEqual(schema['name'], name)
                        self.assertTrue(schema['description'])
                        result = await self.tools[name].run(**values)
                        if name == 'create_scheduled_task' and result.success:
                            args['update_scheduled_task']['task_id'] = result.data['id']
                            args['delete_scheduled_task']['task_id'] = result.data['id']
                        self.assertTrue(result.success, result.error)
                        self.assertIsNotNone(result.data)
                        if name == 'calculate_indicators':
                            self.assertIsNotNone(result.data['ma20'])
                        if name == 'spawn_subagent':
                            self.assertEqual(result.data['status'], 'completed')
                        if name == 'get_watchlist':
                            self.assertEqual(result.data[0]['name'], '平安银行')
        finally:
            direct_results.reset(token)

    async def test_empty_data_is_not_success(self):
        for name, method, args in (
            ('get_stock_quote', 'get_batch_quotes', {'codes': ['000001']}),
            ('get_kline', 'get_kline', {'code': '000001'}),
            ('get_hot_stocks', 'get_hot_stocks', {}),
            ('calculate_indicators', 'get_kline', {'code': '000001'}),
            ('detect_anomalies', 'get_kline', {'code': '000001'}),
            ('analyze_stock', 'get_kline', {'code': '000001'}),
        ):
            with self.subTest(tool=name), patch.object(akshare_client, method, new=AsyncMock(return_value=[])):
                self.assertFalse((await self.tools[name].run(**args)).success)

    async def test_source_exceptions_are_failures(self):
        for name, method, args in (
            ('get_stock_news', 'get_stock_news', {'code': '000001'}),
            ('get_hot_stocks', 'get_hot_stocks', {}),
            ('screen_stocks', 'get_hot_stocks', {}),
            ('get_market_sentiment', 'get_market_sentiment', {}),
        ):
            with self.subTest(tool=name), patch.object(akshare_client, method, new=AsyncMock(side_effect=RuntimeError('source failed'))):
                self.assertFalse((await self.tools[name].run(**args)).success)

    async def test_screening_allows_empty_results_and_ignores_missing_fields(self):
        with patch.object(akshare_client, 'get_hot_stocks', new=AsyncMock(return_value=[{'code': '000001', 'change_percent': None, 'turnover_rate': None}])):
            result = await self.tools['screen_stocks'].run(min_change=1, min_turnover=1)
        self.assertTrue(result.success)
        self.assertEqual(result.data, [])

    async def test_indicators_reject_insufficient_history(self):
        with patch.object(akshare_client, 'get_kline', new=AsyncMock(return_value=bars()[:10])):
            self.assertFalse((await self.tools['calculate_indicators'].run(code='000001')).success)

    async def test_empty_news_is_a_valid_empty_result(self):
        with patch.object(akshare_client, 'get_stock_news', new=AsyncMock(return_value=[])):
            result = await self.tools['get_stock_news'].run(code='000001')
        self.assertTrue(result.success)
        self.assertEqual(result.data, [])

    async def test_analysis_marks_unavailable_news_explicitly(self):
        with patch.object(akshare_client, 'get_stock_news', new=AsyncMock(side_effect=RuntimeError('offline'))):
            result = await self.tools['analyze_stock'].run(code='000001')
        self.assertTrue(result.success)
        self.assertEqual(result.data['news_status'], 'unavailable')
        self.assertTrue(result.data['warnings'])


class SourceParsers(unittest.TestCase):
    def setUp(self):
        self.cache = patch.object(akshare_client, '_cache', {})
        self.times = patch.object(akshare_client, '_cache_time', {})
        self.cache.start()
        self.times.start()
        self.addCleanup(self.cache.stop)
        self.addCleanup(self.times.stop)

    def test_ranking_sort_parameters_and_turnover(self):
        rows = [{'code': '000001', 'name': '平安银行', 'trade': '12.3', 'amount': 200,
                 'changepercent': '1.9', 'turnoverratio': '1.12'}]
        with patch('data.akshare_client_fixed.requests.get') as get:
            get.return_value.json.return_value = rows
            for sort, expected in [('amount', 'amount'), ('pct_chg', 'changepercent')]:
                result = akshare_client.get_hot_stocks_sync(5, sort)
                self.assertEqual(get.call_args.kwargs['params']['sort'], expected)
                self.assertEqual(result[0]['turnover_rate'], 1.12)
                self.assertEqual(result[0]['source'], '新浪财经')

    def test_ranking_fetches_second_page_and_propagates_invalid_payload(self):
        row = {'code': '000001', 'name': '银行', 'trade': 10, 'amount': 2}
        with patch('data.akshare_client_fixed.requests.get') as get:
            get.return_value.json.return_value = [row]
            akshare_client.get_hot_stocks_sync(200)
            self.assertEqual([call.kwargs['params']['page'] for call in get.call_args_list], [1, 2])
            get.return_value.json.return_value = None
            with self.assertRaises(RuntimeError):
                akshare_client.get_hot_stocks_sync(5)

    def test_kline_units_and_fallback(self):
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'data': {
            'sz000001': {'qfqday': [['2026-11-16', '12.06', '12.30', '12.30', '12.05', '2170130']]}}})
        import requests
        with patch('data.akshare_client_fixed.requests.get', side_effect=[requests.Timeout(), response]) as get:
            data = akshare_client.get_kline_sync('000001', 60)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(data[0]['volume'], 217013000)
        self.assertIsNone(data[0]['amount'])
        self.assertEqual(data[0]['adjust'], 'qfq')

    def test_kline_empty_sources_raise(self):
        with patch('data.akshare_client_fixed.requests.get') as get:
            get.return_value.json.return_value = {'data': {}}
            with self.assertRaises(RuntimeError):
                akshare_client.get_kline_sync('000001', 60)

    def test_news_network_failure_is_not_an_empty_list(self):
        with patch('data.akshare_client_fixed.ak.stock_news_em', side_effect=RuntimeError('connection')):
            with self.assertRaises(RuntimeError):
                akshare_client.get_stock_news_sync('000001')
