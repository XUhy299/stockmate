"""Task-local hooks shared by the existing tools and channel-independent runtime."""
from contextvars import ContextVar
import logging

progress_sink = ContextVar("progress_sink", default=None)
direct_results = ContextVar("direct_results", default=False)
scheduled_service = ContextVar("scheduled_service", default=None)
active_session_id = ContextVar("active_session_id", default=None)
scheduled_execution = ContextVar("scheduled_execution", default=False)


def emit_progress(kind, **data):
    sink = progress_sink.get()
    if sink is not None:
        try:
            sink(kind, data)
        except Exception:
            logging.getLogger(__name__).exception("Progress subscriber failed")
