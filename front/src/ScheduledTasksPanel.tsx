import React, { useEffect, useRef, useState } from 'react'

type ScheduledTask = {
  id: string; title: string; action: 'daily_brief' | 'chat'; prompt: string;
  frequency: 'once' | 'daily' | 'weekdays'; time: string | null; run_at: string | null;
  enabled: boolean; next_run_at: string | null; last_run_at: string | null;
  last_status: string | null; last_error: string | null;
}

async function request<T>(path = '', body?: unknown): Promise<T> {
  const response = await fetch('/api/scheduled-tasks' + path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error || '定时任务操作失败')
  return value
}

const when = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', {
  timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
}) : '—'
const statuses: Record<string, string> = { queued: '等待执行', running: '执行中', completed: '已完成', failed: '未完成', cancelled: '已取消' }

export function ScheduledTasksPanel({ onClose }: { onClose: () => void }) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [title, setTitle] = useState('每日简报')
  const [action, setAction] = useState<'daily_brief' | 'chat'>('daily_brief')
  const [prompt, setPrompt] = useState('')
  const [frequency, setFrequency] = useState<'once' | 'daily' | 'weekdays'>('weekdays')
  const [time, setTime] = useState('09:00')
  const [runAt, setRunAt] = useState(() => new Date(Date.now() + 600000).toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai' }).replace(' ', 'T').slice(0, 16))
  const dialog = useRef<HTMLDivElement>(null)
  const mounted = useRef(true)
  const close = useRef(onClose)
  close.current = onClose

  async function refresh() {
    const result = await request<{ tasks: ScheduledTask[] }>()
    if (mounted.current) setTasks(result.tasks)
  }
  useEffect(() => {
    mounted.current = true
    const load = () => refresh().catch(e => { if (mounted.current) setError(e.message) })
    void load().finally(() => { if (mounted.current) setLoading(false) })
    const timer = window.setInterval(() => void load(), 3000)
    return () => { mounted.current = false; window.clearInterval(timer) }
  }, [])
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close.current()
      if (event.key !== 'Tab') return
      const nodes = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, select, textarea')
      if (!nodes?.length) return
      const first = nodes[0], last = nodes[nodes.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('keydown', key); previous?.focus() }
  }, [])

  async function change(path: string, body: unknown) {
    if (saving) return
    setSaving(true); setError('')
    try { await request(path, body); await refresh() }
    catch (e) { if (mounted.current) setError((e as Error).message) }
    finally { if (mounted.current) setSaving(false) }
  }

  return <div className="modal-shade" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="quick-panel schedule-panel" role="dialog" aria-modal="true" aria-labelledby="schedule-title" ref={dialog}>
      <div className="panel-heading"><div><span className="eyebrow">YOUR RESEARCH, ON TIME</span><h2 id="schedule-title">定时任务</h2></div><button className="icon-button" aria-label="关闭定时任务" onClick={onClose}>×</button></div>
      <p className="schedule-intro">让研究按时发生 <span>北京时间</span></p>
      {error && <p className="notice" role="alert">{error}</p>}
      <form className="schedule-form" onSubmit={event => { event.preventDefault(); void change('', {
        title, action, prompt: action === 'chat' ? prompt : '', frequency,
        ...(frequency === 'once' ? { run_at: runAt } : { time }),
      }) }}>
        <div className="schedule-fields">
          <label>任务名称<input value={title} maxLength={80} required onChange={event => setTitle(event.target.value)} /></label>
          <label>研究内容<select value={action} onChange={event => { const value = event.target.value as typeof action; setAction(value); if (title === '每日简报' || title === '定时研究') setTitle(value === 'daily_brief' ? '每日简报' : '定时研究') }}><option value="daily_brief">每日简报</option><option value="chat">自定义研究</option></select></label>
          <label>执行频率<select value={frequency} onChange={event => setFrequency(event.target.value as typeof frequency)}><option value="once">仅一次</option><option value="daily">每天</option><option value="weekdays">工作日</option></select></label>
          {frequency === 'once' ? <label>执行日期与时间<input type="datetime-local" value={runAt} required onChange={event => setRunAt(event.target.value)} /></label> : <label>执行时间<input type="time" value={time} required onChange={event => setTime(event.target.value)} /></label>}
        </div>
        {action === 'chat' && <label className="schedule-prompt">研究要求<textarea value={prompt} required maxLength={4000} rows={3} placeholder="例如：分析平安银行的最新行情与技术指标" onChange={event => setPrompt(event.target.value)} /></label>}
        <div className="schedule-create"><span>也可以在对话中说：“每个工作日 9 点生成简报”</span><button className="schedule-primary" type="submit" disabled={saving}>{saving ? '保存中…' : '创建任务'}</button></div>
      </form>
      <div className="schedule-list-heading"><h3>已安排 <span>{tasks.length}</span></h3><button className="schedule-link" onClick={() => { setError(''); void refresh().catch(e => setError(e.message)) }}>刷新</button></div>
      {loading ? <p className="panel-empty">正在读取任务…</p> : !tasks.length ? <p className="panel-empty">还没有安排任务，创建你的第一份定时研究。</p> : <div className="schedule-list">{tasks.map(task => {
        const state = task.last_status === 'running' || task.last_status === 'queued' ? statuses[task.last_status] : task.enabled ? '已启用' : task.frequency === 'once' && task.last_status === 'completed' ? '已完成' : '已暂停'
        return <article key={task.id}>
          <div className="schedule-task-top"><strong>{task.title}</strong><span className={'schedule-badge ' + (task.enabled ? 'enabled' : '')}>{state}</span></div>
          <p>{task.frequency === 'once' ? '单次 · ' + when(task.run_at) : (task.frequency === 'daily' ? '每天' : '工作日') + ' · ' + task.time} · {task.action === 'daily_brief' ? '每日简报' : '自定义研究'}</p>
          {task.prompt && <p className="task-prompt">{task.prompt}</p>}
          <div className="schedule-task-bottom"><span>{task.enabled ? '下次 ' + when(task.next_run_at) : task.last_run_at ? '最近 ' + when(task.last_run_at) : '等待启用'}{task.last_status && ' · ' + statuses[task.last_status]}</span><div>{!(task.frequency === 'once' && !task.enabled && task.last_run_at) && <button className="schedule-link" disabled={saving} onClick={() => void change('/' + task.id + '/update', { enabled: !task.enabled })}>{task.enabled ? '暂停' : '启用'}</button>}<button className="schedule-link delete" disabled={saving} onClick={() => void change('/' + task.id + '/delete', {})}>删除</button></div></div>
          {task.last_error && <p className="schedule-error">{task.last_error}</p>}
        </article>
      })}</div>}
    </div>
  </div>
}
