"""Offline by default; --live probes read-only tools in bounded subprocesses."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import shutil
import sys
from tempfile import TemporaryDirectory
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LIVE_CASES = {
    'get_stock_quote': {'codes': ['000001', '600036']},
    'get_kline': {'code': '000001', 'days': 60},
    'get_stock_news': {'code': '000001', 'limit': 5},
    'get_hot_stocks': {'top_n': 5, 'by': 'amount'},
    'get_market_sentiment': {}, 'get_watchlist': {},
    'calculate_indicators': {'code': '000001'},
    'detect_anomalies': {'code': '000001'},
    'analyze_stock': {'code': '000001'},
    'screen_stocks': {'min_change': 0, 'limit': 5},
    'load_skill': {'skill_name': 'morning_report'},
}


def bootstrap(root):
    from config import config
    # Override paths and credentials BEFORE importing core globals.
    config.OPENAI_API_KEY = ''
    config.FEISHU_APP_ID = config.FEISHU_APP_SECRET = ''
    config.WECOM_CORP_ID = config.WECOM_CORP_SECRET = ''
    # The legacy channel modules also read os.environ directly.
    for name in ('FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'WECOM_CORP_ID', 'WECOM_CORP_SECRET',
                 'FEISHU_ALERT_USERS', 'WECOM_ALERT_USERS'):
        os.environ[name] = ''
    config.DATA_DIR, config.MEMORY_DIR = root / 'data', root / 'memory'
    config.WATCHLIST_FILE = root / 'data' / 'watchlist.json'
    config.ALERT_HISTORY_FILE = root / 'data' / 'alerts.json'
    config.DAILY_REPORT_DIR = root / 'data' / 'reports'
    config.LOG_FILE = root / 'data' / 'check.log'
    for path in (config.DATA_DIR, config.MEMORY_DIR, config.DAILY_REPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'skills', root / 'skills')
    config.WATCHLIST_FILE.write_text(json.dumps({'stocks': [{'code': '000001'}, {'code': '600036'}]}), encoding='utf-8')
    import core.agent
    from tools.all_tools import ALL_TOOLS
    from tools.registry import registry
    for cls in ALL_TOOLS:
        tool = cls()
        registry.unregister(tool.name)
        registry.register(tool)
    return registry


def validate(name, data):
    if name == 'get_stock_quote':
        assert all(data.get(code, {}).get('price', 0) > 0 and data[code].get('name')
                   for code in LIVE_CASES[name]['codes']), '行情缺少股票或价格'
    elif name == 'get_kline':
        assert len(data) >= 30 and all(row.get('close', 0) > 0 for row in data), 'K线不足或价格无效'
        assert any(row.get('volume') is not None for row in data), 'K线缺少成交量'
    elif name == 'calculate_indicators':
        assert data.get('ma20') is not None and data.get('rsi14') is not None, '指标为空'
    elif name == 'detect_anomalies':
        assert data.get('current_price', 0) > 0 and isinstance(data.get('anomalies'), list), '异常检测缺少行情'
    elif name == 'analyze_stock':
        assert data.get('current_price', 0) > 0 and data.get('technical', {}).get('ma20') is not None, '综合分析缺少行情或指标'
    elif name == 'get_market_sentiment':
        assert isinstance(data.get('up_count'), int) and isinstance(data.get('down_count'), int), '缺少涨跌家数'
    elif name == 'load_skill':
        assert data.get('content'), 'Skill 为空'
    elif name in {'get_stock_news', 'screen_stocks'}:
        assert isinstance(data, list), '数据不是列表'
    else:
        assert isinstance(data, list) and data, '列表为空'
        assert all(row.get('code') and row.get('name') for row in data), '缺少股票名称或代码'


async def worker(name, root):
    result = await bootstrap(root).execute(name, **LIVE_CASES[name])
    if not result.success:
        return {'tool': name, 'status': 'FAIL', 'error': str(result.error)[:300]}
    try:
        validate(name, result.data)
    except (AssertionError, TypeError, KeyError) as exc:
        return {'tool': name, 'status': 'FAIL', 'error': str(exc)}
    status = 'EMPTY' if name in {'get_stock_news', 'screen_stocks'} and not result.data else 'PASS'
    return {'tool': name, 'status': status, 'records': len(result.data)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='检查真实公开数据，不调用模型、不发送消息')
    parser.add_argument('--tool', choices=sorted(LIVE_CASES), help='仅检查一个只读工具，须加 --live')
    parser.add_argument('--json', type=Path, help='保存 JSON 检查结果')
    parser.add_argument('--timeout', type=int, default=50, help='每个真实工具子进程的超时秒数')
    parser.add_argument('--suite', choices=['all', 'tools', 'monitoring'], default='tools')
    parser.add_argument('--quick', action='store_true', help='兼容旧参数；默认已经离线')
    parser.add_argument('--worker', choices=sorted(LIVE_CASES), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        with TemporaryDirectory(prefix='astack-tool-worker-') as directory:
            try:
                result = asyncio.run(worker(args.worker, Path(directory)))
            except Exception as exc:
                result = {'tool': args.worker, 'status': 'FAIL', 'error_type': type(exc).__name__}
            finally:
                logging.shutdown()
        print('TOOL_RESULT ' + json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in {'PASS', 'EMPTY'} else 1
    if args.tool and not args.live:
        parser.error('--tool 需要 --live')
    if args.timeout < 1:
        parser.error('--timeout 必须为正数')
    if not args.live:
        with TemporaryDirectory(prefix='astack-tests-') as directory:
            bootstrap(Path(directory))
            pattern = {'all': 'test_*.py', 'tools': 'test_tools.py', 'monitoring': 'test_monitoring.py'}[args.suite]
            suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=pattern)
            try:
                result = unittest.TextTestRunner(verbosity=2).run(suite)
            finally:
                logging.shutdown()
        return 0 if result.wasSuccessful() and result.testsRun else 1
    results = []
    for name in [args.tool] if args.tool else LIVE_CASES:
        started = time.monotonic()
        try:
            process = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(__file__)), '--worker', name],
                                     cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=args.timeout)
            line = next((line for line in reversed(process.stdout.splitlines()) if line.startswith('TOOL_RESULT ')), None)
            result = json.loads(line[len('TOOL_RESULT '):]) if line else {
                'tool': name, 'status': 'FAIL', 'error': '检查子进程未返回结果', 'exit_code': process.returncode}
        except subprocess.TimeoutExpired:
            result = {'tool': name, 'status': 'TIMEOUT', 'error': f'超过 {args.timeout} 秒'}
        result['seconds'] = round(time.monotonic() - started, 2)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    with TemporaryDirectory(prefix='astack-inventory-') as directory:
        registry = bootstrap(Path(directory))
        inventory = [{'tool': name, 'live': 'read_only' if name in LIVE_CASES else 'offline_only'}
                     for name in registry.list_tools()]
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'inventory': inventory, 'results': results,
              'note': 'EMPTY 代表接口成功但没有记录；离线项目不代表真实模型通过。'}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Offline-only: ' + ', '.join(item['tool'] for item in inventory if item['live'] == 'offline_only'))
    return 1 if any(row['status'] not in {'PASS', 'EMPTY'} for row in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
