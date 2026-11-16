"""Small file-backed scheduler for user-created tasks; execution stays in Agent's queue."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
logger = logging.getLogger(__name__)


class ScheduledTaskService:
    def __init__(self, path: Path):
        self.path = path
        self.agent = None
        self._loop = None

    @property
    def running(self):
        return self._loop is not None and not self._loop.done()

    def _read(self):
        if not self.path.exists():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding='utf-8'))['tasks']
            if not isinstance(rows, list):
                raise ValueError('tasks must be a list')
            return rows
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('定时任务文件读取失败，请检查 scheduled_tasks.json') from exc

    def _save(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'version': 1, 'tasks': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def list(self, session_id):
        return deepcopy([row for row in self._read() if row['session_id'] == session_id])

    @staticmethod
    def _next(row, now):
        if row['frequency'] == 'once':
            return datetime.fromisoformat(row['run_at']).astimezone(TZ)
        hour, minute = map(int, row['time'].split(':'))
        candidate = now.astimezone(TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        while row['frequency'] == 'weekdays' and candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def create(self, session_id, *, title='', action='daily_brief', prompt='', frequency='weekdays',
               time='09:00', run_at=None, delay_minutes=None, now=None):
        now = now or datetime.now(TZ)
        if not re.fullmatch(r'[A-Za-z0-9_\-\u4e00-\u9fff]{1,80}', session_id):
            raise ValueError('无效会话')
        if action not in {'daily_brief', 'chat'} or frequency not in {'once', 'daily', 'weekdays'}:
            raise ValueError('请选择有效任务类型和执行频率')
        if not isinstance(title, str) or not isinstance(prompt, str) or len(title) > 80 or len(prompt) > 4000:
            raise ValueError('任务名称限80字，研究内容限4000字')
        prompt = prompt.strip()
        if action == 'chat' and not prompt:
            raise ValueError('请填写需要 Agent 执行的研究内容')
        row = {'id': uuid4().hex, 'session_id': session_id, 'title': title.strip() or ('每日简报' if action == 'daily_brief' else '定时研究'),
               'action': action, 'prompt': prompt if action == 'chat' else '', 'frequency': frequency,
               'timezone': 'Asia/Shanghai', 'time': None, 'run_at': None,
               'enabled': True, 'created_at': now.isoformat(), 'last_run_at': None,
               'last_status': None, 'last_error': None, 'pending_run_id': None}
        if frequency == 'once':
            if delay_minutes is not None:
                if isinstance(delay_minutes, bool) or not isinstance(delay_minutes, int) or not 1 <= delay_minutes <= 10080:
                    raise ValueError('延迟应为1到10080分钟')
                when = now + timedelta(minutes=delay_minutes)
            else:
                try:
                    when = datetime.fromisoformat(run_at)
                except (TypeError, ValueError) as exc:
                    raise ValueError('请选择单次任务的执行日期和时间') from exc
                when = when.replace(tzinfo=TZ) if when.tzinfo is None else when.astimezone(TZ)
            if when <= now:
                raise ValueError('执行时间必须晚于当前时间')
            row['run_at'] = when.isoformat()
        else:
            if not isinstance(time, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', time):
                raise ValueError('时间格式应为 HH:mm')
            row['time'] = time
        row['next_run_at'] = self._next(row, now).isoformat()
        rows = self._read()
        # Avoid duplicate daily tasks when an HTTP or model request is retried.
        keys = ('session_id', 'title', 'action', 'prompt', 'frequency', 'time', 'run_at')
        for existing in rows:
            if all(existing.get(key) == row.get(key) for key in keys):
                return deepcopy(existing)
        if len([item for item in rows if item['session_id'] == session_id]) >= 30:
            raise ValueError('当前会话最多保留30个定时任务')
        rows.append(row)
        self._save(rows)
        return deepcopy(row)

    def update(self, session_id, task_id, enabled, now=None):
        if not isinstance(enabled, bool):
            raise ValueError('enabled 应为布尔值')
        rows = self._read()
        row = next((row for row in rows if row['id'] == task_id and row['session_id'] == session_id), None)
        if row is None:
            raise ValueError('定时任务不存在')
        if enabled:
            next_run = self._next(row, now or datetime.now(TZ))
            if next_run <= (now or datetime.now(TZ)):
                raise ValueError('单次任务的时间已过，请新建任务')
            row['next_run_at'] = next_run.isoformat()
        elif row['last_status'] == 'queued':
            row['pending_run_id'] = None
            row['last_status'] = 'cancelled'
        row['enabled'] = enabled
        self._save(rows)
        return deepcopy(row)

    def delete(self, session_id, task_id):
        rows = self._read()
        kept = [row for row in rows if not (row['id'] == task_id and row['session_id'] == session_id)]
        if len(kept) == len(rows):
            raise ValueError('定时任务不存在')
        self._save(kept)
        return {'id': task_id, 'deleted': True}

    def begin(self, task_id, run_id):
        rows = self._read()
        row = next((row for row in rows if row['id'] == task_id and row.get('pending_run_id') == run_id), None)
        if row is None or row['last_status'] != 'queued':
            return False
        row['last_status'] = 'running'
        self._save(rows)
        return True

    def finish(self, task_id, run_id, status, error=None):
        rows = self._read()
        row = next((row for row in rows if row['id'] == task_id and row.get('pending_run_id') == run_id), None)
        if row:
            row.update(last_status=status, last_error=error, pending_run_id=None)
            self._save(rows)

    async def tick(self, now=None):
        if self.agent is None or self.agent._active_task or self.agent._pending_web:
            return
        now = now or datetime.now(TZ)
        rows = self._read()
        due = [row for row in rows if row['session_id'] == self.agent.session.session_id and row['enabled']
               and not row.get('pending_run_id') and row.get('next_run_at')
               and datetime.fromisoformat(row['next_run_at']) <= now]
        if not due:
            return
        row = min(due, key=lambda item: item['next_run_at'])
        run_id = uuid4().hex
        row.update(pending_run_id=run_id, last_run_at=now.isoformat(), last_status='queued', last_error=None)
        if row['frequency'] == 'once':
            row.update(enabled=False, next_run_at=None)
        else:
            row['next_run_at'] = self._next(row, now).isoformat()
        self._save(rows)
        content = f"定时任务：{row['title']}\n" + ('请生成我的每日简报。' if row['action'] == 'daily_brief' else row['prompt'])
        try:
            await self.agent.post_event('scheduled_task', content, metadata={
                'source': 'schedule', 'scheduled_task_id': row['id'], 'scheduled_run_id': run_id,
                'scheduled_action': row['action'], 'session_id': row['session_id'], 'task_id': run_id})
        except Exception:
            self.finish(row['id'], run_id, 'failed', '任务入队失败，请重新创建或启用')
            raise

    async def start(self, agent):
        if self.running:
            return
        self.agent = agent
        # Recover status, not unfinished model calls. Queued mailbox events remain usable.
        try:
            queued = set()
            for path in agent.mailbox_dir.glob('*.json'):
                try:
                    queued.add(json.loads(path.read_text(encoding='utf-8')).get('metadata', {}).get('scheduled_run_id'))
                except (ValueError, OSError):
                    continue
            rows = self._read()
            changed = False
            for row in rows:
                if row['session_id'] == agent.session.session_id and row.get('pending_run_id'):
                    if row['last_status'] == 'running' or row['pending_run_id'] not in queued:
                        row.update(pending_run_id=None, last_status='failed', last_error='上次任务因服务退出未完成')
                        changed = True
            if changed:
                self._save(rows)
        except RuntimeError:
            logger.exception('Unable to load scheduled tasks')
        self._loop = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Scheduled task dispatch failed')
            await asyncio.sleep(1)

    async def stop(self):
        if self._loop:
            self._loop.cancel()
            await asyncio.gather(self._loop, return_exceptions=True)
            self._loop = None
