import json
import logging

from coal_platform.observability import JsonFormatter


def test_json_formatter_emits_trace_fields_without_unlisted_secrets() -> None:
    record = logging.LogRecord("coal_platform.access", logging.INFO, __file__, 1, "request completed", (), None)
    record.event = "http_request"
    record.trace_id = "trace-123"
    record.method = "POST"
    record.route = "/api/v1/auth/login"
    record.status = 401
    record.latency_ms = 12
    record.authorization = "Bearer must-not-appear"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["trace_id"] == "trace-123"
    assert payload["route"] == "/api/v1/auth/login"
    assert "must-not-appear" not in str(payload)
