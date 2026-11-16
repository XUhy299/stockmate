import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { displayValue, isBusy, nearBottom, taskEvents } from './state.mjs'
import './style.css'
import { ScheduledTasksPanel } from './ScheduledTasksPanel'

type Stock = { code: string; name?: string; notes?: string }
type Message = { id: string; role: 'user' | 'assistant'; content: string; task_id?: string; display?: { type: 'watchlist'; stocks: Stock[] } }
type Task = { task_id: string; status: string; kind: string; error?: string }
type Progress = { id: number; type: string; task_id?: string; label?: string; success?: boolean }
type Snapshot = { messages: Message[]; task: Task | null; events: Progress[]; configured: boolean; busy: boolean }
type IconName = 'arrow' | 'heart' | 'chart' | 'brief' | 'close' | 'chevron' | 'spark' | 'clock'

function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  const paths: Record<IconName, React.ReactNode> = {
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    arrow: <><path d="M12 19V5M5 12l7-7 7 7" /></>,
    heart: <path d="M20.8 4.6a5.4 5.4 0 0 0-7.6 0L12 5.8l-1.2-1.2a5.4 5.4 0 0 0-7.6 7.6L12 21l8.8-8.8a5.4 5.4 0 0 0 0-7.6Z" />,
    chart: <><path d="M4 4v16h17M7 14l4-4 4 3 6-8M17 5h4v4" /></>,
    brief: <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 8h6M9 12h6M9 16h3" /></>,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    chevron: <path d="m6 9 6 6 6-6" />,
    spark: <><path d="m12 3 2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3Z" /></>,
  }
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function Avatar({ large = false }: { large?: boolean }) {
  return <span className={'avatar' + (large ? ' avatar-large' : '')}>
    <svg viewBox="0 0 48 48" fill="none" aria-hidden="true">
      <path d="M12 17 9 8l11 6h8L39 8l-3 9v13c0 8-24 8-24 0Z" fill="currentColor" />
      <path d="M19 22v5m10-5v5" stroke="white" strokeWidth="3" strokeLinecap="round" />
      <path d="m22 31 2 1 2-1" stroke="white" strokeWidth="2" strokeLinecap="round" />
    </svg>
  </span>
}

function Decorations() {
  return <div className="decorations" aria-hidden="true">
    <svg className="candles" viewBox="0 0 240 340" fill="none">
      <path className="grid" d="M0 70h240M0 140h240M0 210h240M0 280h240M40 0v340M100 0v340M160 0v340M220 0v340" />
      <g className="candle-up"><path d="M30 236v77M70 198v80M150 116v92M190 66v89M230 25v80" /><rect x="21" y="255" width="18" height="37" rx="3" /><rect x="61" y="214" width="18" height="43" rx="3" /><rect x="141" y="137" width="18" height="51" rx="3" /><rect x="181" y="86" width="18" height="45" rx="3" /><rect x="221" y="43" width="18" height="39" rx="3" /></g>
      <g className="candle-down"><path d="M110 174v87" /><rect x="101" y="191" width="18" height="47" rx="3" /></g>
      <path className="trend" d="m12 300 43-43 39-7 44-61 42-34 51-98" />
    </svg>
    <svg className="rise-arrow" viewBox="0 0 240 200" fill="none"><path d="m23 163 61-64 43 27 86-99m-66 0h66v66" /></svg>
    <span className="decor-label">A LITTLE CLARITY.<br />A BETTER PERSPECTIVE.</span>
  </div>
}

async function api<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch('/api' + path, {
    ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), signal,
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || '请求失败，请稍后重试')
  return data
}

function Markdown({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img']}
    components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>
    {content}
  </ReactMarkdown>
}

function QuickPanel({ kind, onClose }: { kind: 'watchlist' | 'market'; onClose: () => void }) {
  const [data, setData] = useState<unknown>(null)
  const [date, setDate] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  const dialog = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api<{ data: unknown; retrieved_at: string }>('/' + kind, undefined, controller.signal)
      .then(result => { setData(result.data); setDate(result.retrieved_at) })
      .catch(e => { if (!controller.signal.aborted) setError(e.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [kind, retry])
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    dialog.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'Tab') {
        const items = dialog.current?.querySelectorAll<HTMLButtonElement>('button')
        if (!items?.length) return
        const first = items[0], last = items[items.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', key)
    return () => { window.removeEventListener('keydown', key); previous?.focus() }
  }, [onClose])
  const market = (data || {}) as Record<string, unknown>
  const stocks = Array.isArray(data) ? data as Record<string, unknown>[] : []
  return <div className="modal-shade" onClick={onClose}>
    <div className="quick-panel" role="dialog" aria-modal="true" aria-labelledby="panel-title" ref={dialog} onClick={e => e.stopPropagation()}>
      <div className="panel-heading"><div><span className="eyebrow">QUICK LOOK</span><h2 id="panel-title">{kind === 'watchlist' ? '我的自选' : '大盘速览'}</h2></div><button className="icon-button" aria-label="关闭浮层" onClick={onClose}><Icon name="close" /></button></div>
      {loading ? <p className="panel-empty" role="status">正在读取{kind === 'watchlist' ? '自选列表' : '市场数据'}…</p>
        : error ? <div className="panel-empty"><p role="alert">{error}</p><button className="secondary-button" onClick={() => setRetry(retry + 1)}>重新获取</button></div>
        : kind === 'watchlist' ? stocks.length ? <div className="stock-list">{stocks.map((stock, index) =>
          <article key={String(stock.code) + index}><span className="stock-icon"><Icon name="chart" /></span><div><strong>{displayValue(stock.code)}</strong><p>{stock.notes ? String(stock.notes) : '暂无备注'}</p></div><span className="stock-tag">自选</span></article>)}</div> : <div className="panel-empty"><Icon name="heart" /><p>还没有自选股</p><small>在对话中告诉股伴，你想关注哪只股票。</small></div>
        : <><div className="market-grid">{[['上涨家数', 'up_count'], ['下跌家数', 'down_count'], ['涨停家数', 'limit_up'], ['跌停家数', 'limit_down'], ['平盘家数', 'flat_count'], ['市场情绪', 'sentiment']].map(([label, key]) =>
          <div key={key} className={key === 'up_count' || key === 'limit_up' ? 'positive' : key === 'down_count' || key === 'limit_down' ? 'negative' : ''}><span>{label}</span><strong>{displayValue(market[key])}</strong></div>)}</div>{market.timestamp ? <p className="data-time">行情时间：{String(market.timestamp).replace('T', ' ')}</p> : null}</>}
      {!loading && !error && <p className="panel-foot">{market.source ? String(market.source) + ' · ' : ''}获取于 {date ? new Date(date).toLocaleTimeString('zh-CN') : '暂无'}</p>}
    </div>
  </div>
}

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [task, setTask] = useState<Task | null>(null)
  const [events, setEvents] = useState<Progress[]>([])
  const [configured, setConfigured] = useState(true)
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [connected, setConnected] = useState(false)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [panel, setPanel] = useState<'watchlist' | 'market' | 'schedule' | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [watchlistBusy, setWatchlistBusy] = useState(false)
  const feed = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const input = useRef<HTMLTextAreaElement>(null)
  const submitting = useRef(false)

  useEffect(() => {
    let disposed = false
    const apply = (snapshot: Snapshot) => {
      if (disposed) return
      setMessages(snapshot.messages); setTask(snapshot.task); setEvents(snapshot.events)
      setConfigured(snapshot.configured); setBusy(snapshot.busy || isBusy(snapshot.task)); setReady(true)
    }
    const refresh = () => api<Snapshot>('/session').then(apply).catch(() => { if (!disposed) setError('无法连接本地服务，请检查启动终端') })
    const stream = new EventSource('/api/events')
    stream.onopen = () => { if (!disposed) { setConnected(true); setError('') } }
    stream.onerror = () => { if (!disposed) setConnected(false) }
    stream.onmessage = event => {
      if (disposed) return
      const data = JSON.parse(event.data)
      if (data.type === 'snapshot') { apply(data); return }
      setEvents(current => [...current.slice(-199), data])
      if (data.type === 'history_updated') void refresh()
      if (data.type === 'started') {
        setTask({ task_id: data.task_id, status: 'running', kind: data.kind })
        setBusy(true); void refresh()
      }
      if (data.type === 'completed' || data.type === 'failed') {
        setBusy(false)
        setTask({ task_id: data.task_id, status: data.status, kind: data.kind, error: data.error })
        if (data.type === 'failed') setError(data.error)
        void refresh()
      }
    }
    // SSE always begins with an authoritative snapshot, including reconnects.
    return () => { disposed = true; stream.close() }
  }, [])
  useEffect(() => {
    if (follow.current && feed.current) feed.current.scrollTo({ top: feed.current.scrollHeight, behavior: 'smooth' })
  }, [messages, events, busy, expanded])

  async function submit(brief = false) {
    const value = text.trim()
    if (submitting.current || busy || !ready || !configured || (!brief && !value)) return
    submitting.current = true; setBusy(true); setError(''); setExpanded(false); follow.current = true
    try {
      const accepted = await api<{ task_id: string }>(brief ? '/actions/daily-brief' : '/chat', brief ? {} : { message: value })
      setTask(current => current?.task_id === accepted.task_id ? current : { task_id: accepted.task_id, status: 'queued', kind: brief ? 'web_daily_brief' : 'user_message' })
      if (!brief) setText('')
    } catch (e) {
      setBusy(false); setError((e as Error).message)
    } finally {
      submitting.current = false
    }
  }

  async function showWatchlist() {
    if (watchlistBusy) return
    setWatchlistBusy(true); setError(''); follow.current = true
    try {
      const snapshot = await api<Snapshot>('/actions/watchlist', {})
      setMessages(snapshot.messages)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setWatchlistBusy(false)
    }
  }

  const steps = taskEvents(events, task) as Progress[]
  const toolSteps = steps.filter(event => event.type === 'tool_started' || event.type === 'tool_completed' || event.type === 'progress')
  const latest = toolSteps.at(-1)
  const statusText = busy ? latest?.label || (task?.status === 'queued' ? '正在准备分析' : '正在分析') : task?.status === 'failed' ? '本次分析未完成' : '分析已完成'
  return <main className="page">
    <Decorations />
    <header className="site-header"><a className="brand" href="/"><Avatar /><span>股伴 <b>StockMate</b><small>你的个人股票研究助手</small></span></a><span className={'connection' + (connected ? ' online' : '')}><i />{connected ? '本地运行中' : '正在连接'}</span></header>
    <section className="workspace" aria-label="股票助手">
      <div className="workspace-top"><span><Icon name="spark" /> 研究，从一个好问题开始。</span><span className="subtle">A-SHARES · PERSONAL AGENT</span></div>
      <div className="chat-feed" ref={feed} onScroll={() => { if (feed.current) follow.current = nearBottom(feed.current.scrollHeight, feed.current.scrollTop, feed.current.clientHeight) }}>
        {!messages.length && !busy ? <div className="welcome">
          <div className="welcome-avatar"><Avatar large /><span className="avatar-spark">✦</span></div>
          <span className="eyebrow">LESS NOISE. MORE INSIGHT.</span>
          <h1>看懂行情，<span>从容一点。</span></h1>
          <p>我是股伴，陪你梳理市场信息，研究你关心的股票。<br />从一次提问开始，把纷繁的数据变成清晰的思路。</p>
          <div className="suggestions">{['帮我分析招商银行', '我偏好稳健投资，应该关注什么？', '如何理解成交量和股价的关系？'].map(prompt => <button key={prompt} onClick={() => { setText(prompt); input.current?.focus() }}>{prompt}<span>↗</span></button>)}</div>
        </div> : <div className="messages">{messages.map(message => <article key={message.id} className={'message ' + message.role}>
          {message.role === 'assistant' && <Avatar />}
          <div className="message-body">{message.role === 'assistant' && <div className="speaker">股伴 <span>研究助手</span></div>}<div className="message-content">{message.display?.type === 'watchlist' ? <div className="inline-watchlist">
            <h3>我的自选 <span>{message.display.stocks.length} 只</span></h3>
            {message.display.stocks.length ? <div className="stock-list">{message.display.stocks.map((stock, index) => <article key={stock.code + index}><span className="stock-icon"><Icon name="chart" /></span><div><strong>{stock.name || stock.code}</strong>{stock.name && <small>{stock.code}</small>}{stock.notes && <p>{stock.notes}</p>}</div></article>)}</div> : <p>还没有自选股，告诉我你想关注哪只股票。</p>}
          </div> : message.role === 'assistant' ? <Markdown content={message.content} /> : message.content}</div></div>
        </article>)}
          {task && <div className={'process ' + (busy ? 'running' : '')}>
            <button onClick={() => setExpanded(!expanded)} aria-expanded={expanded}><span className="process-symbol">{busy ? <span className="spinner" /> : task.status === 'failed' ? '!' : '✓'}</span>{statusText}<Icon name="chevron" className={expanded ? 'rotate' : ''} /></button>
            {expanded && <ol>{toolSteps.length ? toolSteps.map(step => <li key={step.id}><span>{step.type === 'tool_started' ? '开始' : step.success === false ? '失败' : step.type === 'tool_completed' ? '完成' : '进行中'}</span>{step.label}</li>) : <li>正在准备上下文与分析任务</li>}</ol>}
          </div>}
        </div>}
      </div>
      <div className="composer-area">
        {!configured && <p className="notice" role="alert">尚未配置模型密钥。请在项目 .env 中设置 OPENAI_API_KEY 后重启。</p>}
        {error && <p className="notice" role="alert">{error}</p>}
        <nav className="quick-actions" aria-label="快捷查看"><button disabled={watchlistBusy || !ready} onClick={() => void showWatchlist()}><Icon name="heart" />{watchlistBusy ? '读取中…' : '我的自选'}</button><button disabled={busy || !ready || !configured} onClick={() => void submit(true)}><Icon name="brief" />每日简报</button><button onClick={() => setPanel('market')}><Icon name="chart" />看大盘</button><button disabled={!ready} onClick={() => setPanel('schedule')}><Icon name="clock" />定时任务</button></nav>
        <form className="composer" onSubmit={e => { e.preventDefault(); void submit() }}>
          <textarea ref={input} aria-label="发送消息给股伴" placeholder="聊聊你关心的股票，或问一个投资研究问题…" value={text} maxLength={12000} rows={2} disabled={!ready || !configured || busy} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() } }} />
          <div className="composer-bottom"><span>{busy ? '正在分析…' : 'Enter 发送 · Shift + Enter 换行'}</span><button className="send-button" type="submit" aria-label="发送消息" disabled={!text.trim() || busy || !ready || !configured}>{busy ? <span className="spinner" /> : <Icon name="arrow" />}</button></div>
        </form>
      </div>
    </section>
    <footer className="site-footer"><span>CLARITY OVER COMPLEXITY</span><span>股伴 StockMate · 个人研究空间</span></footer>
    {panel === 'schedule' ? <ScheduledTasksPanel onClose={() => setPanel(null)} /> : panel && <QuickPanel kind={panel} onClose={() => setPanel(null)} />}
  </main>
}

createRoot(document.getElementById('root')!).render(<App />)
