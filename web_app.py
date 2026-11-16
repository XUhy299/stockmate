"""Local-only aiohttp adapter. Model credentials never reach the browser."""
import asyncio
from collections import deque
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from aiohttp import web
from tools.registry import registry

DIST = Path(__file__).parent / "front" / "dist"
TOOL_LABELS = {
    "get_stock_quote": "查询股票行情", "get_kline": "读取 K 线数据",
    "get_stock_news": "获取相关新闻", "get_watchlist": "读取自选股",
    "get_market_sentiment": "获取市场情绪",
    "calculate_indicators": "计算技术指标", "detect_anomalies": "检查异动",
    "spawn_subagent": "执行研究 Skill", "load_skill": "加载分析 Skill",
    "compress_context": "整理对话上下文", "analyze_stock": "分析个股",
    "screen_stocks": "筛选股票", "get_hot_stocks": "查询热门股票",
    "update_user_profile": "更新个人偏好", "idle": "结束本轮分析",
    "create_scheduled_task": "创建定时任务", "list_scheduled_tasks": "查询定时任务",
    "update_scheduled_task": "更新定时任务", "delete_scheduled_task": "删除定时任务",
}


class WebState:
    def __init__(self, agent):
        self.agent = agent
        self.events = deque(maxlen=200)
        self.listeners = set()
        self.task = None
        self.sequence = 0

    def on_event(self, kind, data):
        if data.get("source") not in {"web", "schedule", "routine_analysis", "price_alert"}:
            return
        if kind == "started":
            self.task = {key: data[key] for key in ("task_id", "source", "status", "kind") if key in data}
        self.sequence += 1
        event = {"id": self.sequence, "type": kind, **data}
        if "tool" in event:
            event["label"] = TOOL_LABELS.get(event["tool"], "执行分析工具")
        if self.task and self.task["task_id"] == data.get("task_id"):
            self.task.update({k: v for k, v in data.items() if k in {"status", "error"}})
        self.events.append(event)
        for queue in tuple(self.listeners):
            if queue.full():
                self.listeners.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(event)

    def snapshot(self):
        visible = []
        for index, message in enumerate(self.agent.session.messages):
            if message.get("role") not in {"user", "assistant"}:
                continue
            if message.get("tool_calls") or not message.get("content"):
                continue
            if message.get("visible") is False:
                continue
            if "💤" in message["content"] and not message.get("visible"):
                continue
            visible.append({
                "id": str(index), "role": message["role"],
                "content": message["content"], "task_id": message.get("task_id"),
                "display": message.get("display"),
            })
        return {
            "messages": visible, "task": self.task, "events": list(self.events),
            "configured": self.agent.client is not None,
            "busy": bool(self.agent._active_task or self.agent._pending_web),
        }


def create_app(agent, *, dist=DIST, port=8088):
    state = WebState(agent)
    agent.set_event_handler(state.on_event)

    @web.middleware
    async def local_only(request, handler):
        try:
            host = request.headers.get("Host", "").lower()
            if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                raise web.HTTPForbidden(text="只允许本机访问")
            origin = request.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != "http" or parsed.netloc.lower() != host:
                    raise web.HTTPForbidden(text="不允许跨站访问")
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                raise web.HTTPForbidden(text="不允许跨站访问")
            if request.method == "POST" and request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType(text="请使用 JSON 请求")
            response = await handler(request)
        except web.HTTPException as exc:
            response = web.json_response({"error": exc.text}, status=exc.status)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    app = web.Application(middlewares=[local_only], client_max_size=64 * 1024)
    app["web_state"] = state

    async def session(request):
        return web.json_response(state.snapshot())

    async def scheduled_tasks(request):
        session_id = agent.session.session_id
        service = agent.scheduled_tasks
        try:
            if request.method == 'GET':
                return web.json_response({'tasks': service.list(session_id), 'timezone': 'Asia/Shanghai', 'running': service.running})
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('任务格式不正确')
            task_id = request.match_info.get('task_id')
            if request.path.endswith('/delete'):
                result = service.delete(session_id, task_id)
            elif task_id:
                if set(body) != {'enabled'}:
                    raise ValueError('请提供 enabled 开关')
                result = service.update(session_id, task_id, body['enabled'])
            else:
                allowed = {'title', 'action', 'prompt', 'frequency', 'time', 'run_at', 'delay_minutes'}
                if set(body) - allowed:
                    raise ValueError('任务包含不支持的字段')
                if not {'title', 'action', 'frequency'} <= body.keys():
                    raise ValueError('请提供任务名称、类型和执行频率')
                result = service.create(session_id, **body)
            return web.json_response({'task': result})
        except (ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        except (RuntimeError, OSError):
            raise web.HTTPInternalServerError(text='无法读写本地定时任务文件')

    async def submit(request):
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="消息格式不正确")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="消息格式不正确")
        if agent._pending_web or agent._active_task or agent.status == "working":
            raise web.HTTPConflict(text="已有分析正在运行，请稍候")
        if agent.client is None:
            raise web.HTTPServiceUnavailable(text="请在项目 .env 中配置 OPENAI_API_KEY 后重启")
        brief = request.path.endswith("daily-brief")
        content = "请生成我的自选股每日简报。" if brief else body.get("message")
        if not isinstance(content, str) or not content.strip() or len(content) > 12000:
            raise web.HTTPBadRequest(text="请输入 1–12000 字的消息")
        task_id = uuid4().hex
        agent._pending_web = task_id
        state.task = {"task_id": task_id, "status": "queued", "kind": "brief" if brief else "chat"}
        try:
            await agent.post_event(
                "web_daily_brief" if brief else "user_message", content.strip(),
                {"source": "web", "task_id": task_id},
            )
        except Exception:
            agent._pending_web = None
            state.task.update(status="failed", error="任务未能提交，请重试")
            raise web.HTTPInternalServerError(text="任务未能提交，请重试")
        return web.json_response(state.task, status=202)

    async def query(request):
        name = "get_watchlist" if request.path.endswith("watchlist") else "get_market_sentiment"
        result = await registry.execute(name)
        if not result.success:
            raise web.HTTPBadGateway(text="数据暂时不可用，请稍后重试")
        return web.json_response({
            "data": result.data, "retrieved_at": datetime.now().astimezone().isoformat(),
        })

    async def show_watchlist(request):
        result = await registry.execute("get_watchlist")
        if not result.success:
            raise web.HTTPBadGateway(text="自选列表读取失败，请重试")
        task_id = uuid4().hex
        start = len(agent.session.messages)
        agent.session.add_message("user", "我的自选", source="web", visible=True,
                                  display_only=True, task_id=task_id)
        agent.session.add_message(
            "assistant", "我的自选", source="web", visible=True,
            display_only=True, task_id=task_id,
            display={"type": "watchlist", "stocks": result.data or []},
        )
        if not agent.save_session():
            del agent.session.messages[start:]
            raise web.HTTPInternalServerError(text="自选列表未能保存到聊天记录")
        state.on_event("history_updated", {"source": "web", "task_id": task_id})
        return web.json_response(state.snapshot())

    async def events(request):
        queue = asyncio.Queue(maxsize=200)
        state.listeners.add(queue)
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        })
        await response.prepare(request)

        async def send(payload):
            await response.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode())

        try:
            await send({"type": "snapshot", **state.snapshot()})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    await response.write(b": heartbeat\n\n")
                    continue
                if event is None:
                    break
                await send(event)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            state.listeners.discard(queue)
        return response

    async def static(request):
        relative = request.match_info.get("path", "") or "index.html"
        target = (Path(dist) / relative).resolve()
        root = Path(dist).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise web.HTTPNotFound(text="页面不存在，请先构建 front")
        return web.FileResponse(target)

    app.router.add_get("/api/session", session)
    app.router.add_get('/api/scheduled-tasks', scheduled_tasks)
    app.router.add_post('/api/scheduled-tasks', scheduled_tasks)
    app.router.add_post('/api/scheduled-tasks/{task_id}/update', scheduled_tasks)
    app.router.add_post('/api/scheduled-tasks/{task_id}/delete', scheduled_tasks)
    app.router.add_post("/api/chat", submit)
    app.router.add_post("/api/actions/daily-brief", submit)
    app.router.add_get("/api/watchlist", query)
    app.router.add_post("/api/actions/watchlist", show_watchlist)
    app.router.add_get("/api/market", query)
    app.router.add_get("/api/events", events)
    app.router.add_get("/{path:.*}", static)
    return app


async def serve(agent, scheduler=None):
    if not (DIST / "index.html").is_file():
        raise RuntimeError("请先执行 npm --prefix front install 和 npm --prefix front run build")
    runner = web.AppRunner(create_app(agent), shutdown_timeout=2)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 8088).start()
        await agent.start()
        if scheduler is not None:
            await scheduler.start()
        print("\n股伴 StockMate 已启动：http://127.0.0.1:8088\n按 Ctrl+C 退出。")
        print("用户定时任务：开启 · 内置固定时间表：" + ("开启" if scheduler is not None else "关闭"))
        await asyncio.Event().wait()
    finally:
        if scheduler is not None:
            await scheduler.stop()
        for queue in tuple(runner.app["web_state"].listeners):
            if not queue.full():
                queue.put_nowait(None)
        await runner.cleanup()
