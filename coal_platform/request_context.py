from contextvars import ContextVar

trace_id_context: ContextVar[str | None] = ContextVar("trace_id", default=None)


def get_trace_id() -> str | None:
    return trace_id_context.get()
