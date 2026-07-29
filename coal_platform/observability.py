from __future__ import annotations

import json
import logging
import sys
import time
from collections import Counter as ValueCounter
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from coal_platform.config import Settings
from coal_platform.request_context import get_trace_id
from coal_platform.store_protocol import PlatformStore

REQUEST_COUNT = Counter(
    "coal_http_requests_total",
    "HTTP requests processed by the API.",
    ("method", "route", "status"),
)
REQUEST_LATENCY = Histogram(
    "coal_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 120, 300),
)
QUEUE_JOBS = Gauge("coal_queue_jobs", "Current queue jobs by status.", ("status",))
MODEL_CALLS_RECENT = Gauge("coal_model_calls_recent", "Model calls in the latest audit window.", ("status",))
MODEL_FAILURE_RATIO = Gauge("coal_model_failure_ratio", "Failure ratio in the latest model call audit window.")
DEPENDENCY_UP = Gauge("coal_dependency_up", "Whether a production dependency is reachable.", ("dependency",))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("event", "trace_id", "method", "route", "status", "latency_ms", "job_id"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def observe_request(method: str, route: str, status: int, started: float) -> None:
    duration = max(0.0, time.monotonic() - started)
    REQUEST_COUNT.labels(method=method, route=route, status=str(status)).inc()
    REQUEST_LATENCY.labels(method=method, route=route).observe(duration)
    logging.getLogger("coal_platform.access").info(
        "request completed",
        extra={
            "event": "http_request",
            "trace_id": get_trace_id(),
            "method": method,
            "route": route,
            "status": status,
            "latency_ms": round(duration * 1000),
        },
    )


def render_metrics(store: PlatformStore, settings: Settings) -> tuple[bytes, str]:
    jobs = store.list_queue_jobs()
    job_counts = ValueCounter(str(item.get("status") or "unknown") for item in jobs)
    for status in ("queued", "pending", "running", "succeeded", "failed", "canceled"):
        QUEUE_JOBS.labels(status=status).set(job_counts[status])

    calls = store.list_model_call_logs(limit=500)
    call_counts = ValueCounter(str(item.get("status") or "unknown") for item in calls)
    for status in ("succeeded", "failed"):
        MODEL_CALLS_RECENT.labels(status=status).set(call_counts[status])
    MODEL_FAILURE_RATIO.set(call_counts["failed"] / len(calls) if calls else 0)
    DEPENDENCY_UP.labels(dependency="database").set(1 if store.healthcheck() else 0)
    if settings.dispatch_jobs:
        try:
            from celery import Celery

            probe = Celery("coal_metrics_probe", broker=settings.redis_url)
            replies = probe.control.inspect(timeout=1).ping() or {}
            DEPENDENCY_UP.labels(dependency="worker").set(1 if replies else 0)
            with probe.connection_for_read() as connection:
                connection.ensure_connection(max_retries=0, timeout=1)
            DEPENDENCY_UP.labels(dependency="redis").set(1)
        except Exception:  # noqa: BLE001 - probes must degrade every client/transport failure to an offline metric.
            DEPENDENCY_UP.labels(dependency="worker").set(0)
            DEPENDENCY_UP.labels(dependency="redis").set(0)
    else:
        DEPENDENCY_UP.labels(dependency="worker").set(0)
        DEPENDENCY_UP.labels(dependency="redis").set(0)
    return generate_latest(), CONTENT_TYPE_LATEST
