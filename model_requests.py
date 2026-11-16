"""Small process-local request pacing for the configured model service."""
from asyncio import Lock, sleep
from collections import deque
from time import monotonic

from config import config
from execution_context import emit_progress

_request_times = deque()
_request_lock = Lock()


async def _reserve_slot():
    limit = config.OPENAI_REQUESTS_PER_MINUTE
    if limit <= 0:
        return
    async with _request_lock:
        now = monotonic()
        while _request_times and now - _request_times[0] >= 61:
            _request_times.popleft()
        if len(_request_times) >= limit:
            delay = max(0, 61 - (now - _request_times[0]))
            emit_progress("progress", label=f"模型服务限速，等待 {int(delay) + 1} 秒")
            await sleep(delay)
            now = monotonic()
            while _request_times and now - _request_times[0] >= 61:
                _request_times.popleft()
        _request_times.append(monotonic())


async def request_completion(client, **kwargs):
    await _reserve_slot()
    try:
        return await client.chat.completions.create(**kwargs)
    except Exception as error:
        body = getattr(error, "body", {}) or {}
        detail = body.get("error", body) if isinstance(body, dict) else {}
        kind = detail.get("type", "") if isinstance(detail, dict) else ""
        if getattr(error, "status_code", None) != 429 or kind != "rate_limit_reached_error":
            raise
        # Retry once only for the provider's explicit rate limit, never for quota
        # or billing errors. The SDK's hidden retries are disabled.
        headers = getattr(getattr(error, "response", None), "headers", {}) or {}
        try:
            delay = min(60, max(1, float(headers.get("retry-after", 2))))
        except (ValueError, TypeError):
            delay = 2
        emit_progress("progress", label=f"模型服务限速，{int(delay)} 秒后继续")
        await sleep(delay)
        await _reserve_slot()
        return await client.chat.completions.create(**kwargs)
