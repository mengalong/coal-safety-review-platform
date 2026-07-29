from __future__ import annotations

import time
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from typing import Any, ClassVar, Protocol
from uuid import uuid4

import httpx

from coal_platform.config import Settings, get_settings
from coal_platform.store_protocol import PlatformStore


class ModelGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class ModelTransport(Protocol):
    def post(self, url: str, **kwargs: Any) -> httpx.Response: ...

    def close(self) -> None: ...


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


class ModelGateway:
    retryable_statuses: ClassVar[set[int]] = {408, 409, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        store: PlatformStore,
        transport: ModelTransport | None = None,
        settings: Settings | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self.transport = transport or httpx.Client(follow_redirects=False)
        self._owns_transport = transport is None
        self._sleep = sleep
        self._lock = Lock()
        self._semaphores: dict[str, BoundedSemaphore] = {}
        self._circuits: dict[str, _CircuitState] = {}

    def close(self) -> None:
        if self._owns_transport:
            self.transport.close()

    def chat(self, config_id: str, messages: list[dict[str, Any]], *, trace_id: str | None = None) -> dict:
        return self._invoke(config_id, "chat", "/chat/completions", {"messages": messages}, trace_id)

    def multimodal_chat(
        self, config_id: str, messages: list[dict[str, Any]], *, trace_id: str | None = None
    ) -> dict:
        return self._invoke(config_id, "multimodal_chat", "/chat/completions", {"messages": messages}, trace_id)

    def embed(self, config_id: str, inputs: str | list[str], *, trace_id: str | None = None) -> dict:
        return self._invoke(config_id, "embedding", "/embeddings", {"input": inputs}, trace_id)

    def rerank(
        self, config_id: str, query: str, documents: list[str], *, top_n: int | None = None, trace_id: str | None = None
    ) -> dict:
        payload: dict[str, Any] = {"query": query, "documents": documents}
        if top_n is not None:
            payload["top_n"] = top_n
        return self._invoke(config_id, "rerank", "/rerank", payload, trace_id)

    def test_connection(self, config_id: str, *, trace_id: str | None = None) -> dict:
        config = self._config(config_id)
        kind = config["model_kind"]
        if kind == "embedding":
            result = self.embed(config_id, "连接测试", trace_id=trace_id)
        elif kind == "reranker":
            result = self.rerank(config_id, "安全", ["煤矿安全技术文件"], top_n=1, trace_id=trace_id)
        elif kind == "multimodal":
            result = self.multimodal_chat(
                config_id, [{"role": "user", "content": "仅回复 OK"}], trace_id=trace_id
            )
        else:
            result = self.chat(config_id, [{"role": "user", "content": "仅回复 OK"}], trace_id=trace_id)
        return {"reachable": True, "model_code": config["model_code"], "request_id": result["request_id"]}

    def _config(self, config_id: str) -> dict[str, Any]:
        config = self.store.get_model_runtime_config(config_id)
        if not config:
            raise ModelGatewayError("MODEL_CONFIG_NOT_FOUND", "model configuration is unavailable")
        if config.get("status") != "active":
            raise ModelGatewayError("MODEL_CONFIG_DISABLED", "model configuration is disabled")
        if config.get("provider_code") != "qianfan":
            raise ModelGatewayError("MODEL_PROVIDER_UNSUPPORTED", "model provider is unsupported")
        return config

    def _invoke(
        self, config_id: str, operation: str, path: str, payload: dict[str, Any], trace_id: str | None
    ) -> dict:
        config = self._config(config_id)
        request_id = uuid4().hex
        started = time.monotonic()
        attempts = 0
        http_status: int | None = None
        provider_request_id: str | None = None
        error_code: str | None = None
        semaphore = self._semaphore(config_id, config["concurrency_limit"])
        if not semaphore.acquire(timeout=config["timeout_seconds"]):
            raise ModelGatewayError("MODEL_CONCURRENCY_LIMIT", "model concurrency limit reached")
        try:
            self._ensure_circuit_closed(config_id)
            body = {"model": config["model_code"], **payload}
            for attempts in range(1, self.settings.model_max_retries + 2):
                try:
                    response = self.transport.post(
                        f"{config['base_url'].rstrip('/')}{path}",
                        headers={
                            "Authorization": f"Bearer {config['api_key']}",
                            "Content-Type": "application/json",
                            "X-Request-Id": request_id,
                        },
                        json=body,
                        timeout=config["timeout_seconds"],
                    )
                    http_status = response.status_code
                    provider_request_id = response.headers.get("X-Request-Id")
                    if len(response.content) > self.settings.model_max_response_bytes:
                        raise ModelGatewayError("MODEL_RESPONSE_TOO_LARGE", "model response exceeds configured limit")
                    if response.status_code >= 400:
                        error_code = f"MODEL_HTTP_{response.status_code}"
                        if response.status_code in self.retryable_statuses and attempts <= self.settings.model_max_retries:
                            self._sleep(min(0.25 * (2 ** (attempts - 1)), 2.0))
                            continue
                        raise ModelGatewayError(error_code, "model provider rejected the request", http_status=http_status)
                    data = response.json()
                    self._validate_response(operation, data)
                    self._record_success(config_id)
                    result = self._normalize(operation, data)
                    result.update(request_id=request_id, provider_request_id=provider_request_id)
                    self._audit(config_id, request_id, trace_id, operation, "succeeded", attempts, started, http_status, provider_request_id, data.get("usage") or {})
                    return result
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    error_code = "MODEL_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "MODEL_NETWORK_ERROR"
                    if attempts <= self.settings.model_max_retries:
                        self._sleep(min(0.25 * (2 ** (attempts - 1)), 2.0))
                        continue
                    raise ModelGatewayError(error_code, "model provider is temporarily unavailable") from exc
                except (TypeError, ValueError) as exc:
                    error_code = "MODEL_INVALID_RESPONSE"
                    raise ModelGatewayError(error_code, "model provider returned an invalid response") from exc
        except ModelGatewayError as exc:
            error_code = exc.code
            self._record_failure(config_id)
            self._audit(config_id, request_id, trace_id, operation, "failed", max(attempts, 1), started, http_status, provider_request_id, {}, error_code)
            raise
        finally:
            semaphore.release()

    def _semaphore(self, config_id: str, limit: int) -> BoundedSemaphore:
        with self._lock:
            return self._semaphores.setdefault(config_id, BoundedSemaphore(limit))

    def _ensure_circuit_closed(self, config_id: str) -> None:
        with self._lock:
            state = self._circuits.setdefault(config_id, _CircuitState())
            if state.opened_at is None:
                return
            if time.monotonic() - state.opened_at >= self.settings.model_circuit_recovery_seconds:
                state.failures = 0
                state.opened_at = None
                return
        raise ModelGatewayError("MODEL_CIRCUIT_OPEN", "model circuit breaker is open")

    def _record_success(self, config_id: str) -> None:
        with self._lock:
            self._circuits[config_id] = _CircuitState()

    def _record_failure(self, config_id: str) -> None:
        with self._lock:
            state = self._circuits.setdefault(config_id, _CircuitState())
            state.failures += 1
            if state.failures >= self.settings.model_circuit_failure_threshold:
                state.opened_at = time.monotonic()

    @staticmethod
    def _validate_response(operation: str, data: Any) -> None:
        if not isinstance(data, dict):
            raise TypeError("response must be an object")
        if operation in {"chat", "multimodal_chat"} and not data.get("choices"):
            raise ValueError("chat choices missing")
        if operation == "embedding" and not data.get("data"):
            raise ValueError("embedding data missing")
        if operation == "rerank" and not (data.get("results") or data.get("data")):
            raise ValueError("rerank results missing")

    @staticmethod
    def _normalize(operation: str, data: dict[str, Any]) -> dict[str, Any]:
        if operation in {"chat", "multimodal_chat"}:
            choice = data["choices"][0]
            return {"content": choice.get("message", {}).get("content", ""), "finish_reason": choice.get("finish_reason"), "usage": data.get("usage") or {}}
        if operation == "embedding":
            return {"embeddings": [item["embedding"] for item in data["data"]], "usage": data.get("usage") or {}}
        return {"results": data.get("results") or data.get("data"), "usage": data.get("usage") or {}}

    def _audit(self, config_id: str, request_id: str, trace_id: str | None, operation: str, status: str, attempts: int, started: float, http_status: int | None, provider_request_id: str | None, usage: dict[str, Any], error_code: str | None = None) -> None:
        self.store.record_model_call({"model_config_id": config_id, "request_id": request_id, "trace_id": trace_id, "operation": operation, "status": status, "attempt_count": attempts, "latency_ms": max(0, round((time.monotonic() - started) * 1000)), "http_status": http_status, "provider_request_id": provider_request_id, "token_usage": usage, "error_code": error_code})


def bootstrap_qianfan_models(store: PlatformStore, settings: Settings | None = None) -> None:
    active_settings = settings or get_settings()
    if not active_settings.qianfan_api_key:
        return
    existing = {(item["provider_code"], item["model_code"]) for item in store.list_model_configs()}
    api_key = active_settings.qianfan_api_key.get_secret_value()
    for model_code, model_kind, concurrency_limit in (
        ("deepseek-v4-pro", "text", 4),
        ("ernie-5.0", "multimodal", 2),
        ("embedding-v1", "embedding", 8),
        ("bce-reranker-base", "reranker", 8),
    ):
        if ("qianfan", model_code) not in existing:
            store.create_model_config({"provider_code": "qianfan", "provider_name": "百度千帆", "base_url": active_settings.qianfan_base_url, "model_code": model_code, "model_kind": model_kind, "api_key": api_key, "timeout_seconds": 60, "concurrency_limit": concurrency_limit})
