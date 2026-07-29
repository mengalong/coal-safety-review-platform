from __future__ import annotations

import httpx
import pytest

from coal_platform.config import Settings
from coal_platform.model_gateway import ModelGateway, ModelGatewayError, bootstrap_qianfan_models
from coal_platform.model_security import ModelSecretCipher, ModelSecretError
from coal_platform.store import DemoStore


class FakeTransport:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def post(self, url: str, **kwargs) -> httpx.Response:
        self.requests.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        pass


def _store_with_model(kind: str = "text", model_code: str = "deepseek-v4-pro") -> tuple[DemoStore, dict]:
    store = DemoStore.seed()
    model = store.create_model_config(
        {
            "provider_code": "qianfan",
            "provider_name": "百度千帆",
            "base_url": "https://qianfan.test/v2",
            "model_code": model_code,
            "model_kind": kind,
            "api_key": "provider-secret",
            "timeout_seconds": 5,
            "concurrency_limit": 2,
        }
    )
    assert model
    return store, model


def test_model_secret_cipher_round_trip_and_wrong_key_rejection() -> None:
    cipher = ModelSecretCipher("a-development-master-secret-key")
    envelope = cipher.encrypt("provider-secret")

    assert envelope.startswith("aesgcm:v1:")
    assert "provider-secret" not in envelope
    assert cipher.decrypt(envelope) == "provider-secret"
    with pytest.raises(ModelSecretError):
        ModelSecretCipher("another-development-master-key").decrypt(envelope)


def test_qianfan_chat_uses_bearer_auth_retries_and_audits() -> None:
    store, model = _store_with_model()
    transport = FakeTransport(
        [
            httpx.Response(429, json={"error": {"message": "busy"}}),
            httpx.Response(
                200,
                headers={"X-Request-Id": "provider-request"},
                json={
                    "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 3},
                },
            ),
        ]
    )
    gateway = ModelGateway(store, transport=transport, settings=Settings(model_max_retries=1), sleep=lambda _: None)

    result = gateway.chat(model["id"], [{"role": "user", "content": "test"}], trace_id="trace-1")

    assert result["content"] == "OK"
    assert len(transport.requests) == 2
    request = transport.requests[0]
    assert request["url"] == "https://qianfan.test/v2/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer provider-secret"
    assert request["json"]["model"] == "deepseek-v4-pro"
    logs = store.list_model_call_logs()
    assert logs[0]["status"] == "succeeded"
    assert logs[0]["attempt_count"] == 2
    assert logs[0]["provider_request_id"] == "provider-request"
    assert "provider-secret" not in str(logs)


@pytest.mark.parametrize(
    ("kind", "model_code", "response", "expected_key", "expected_path"),
    [
        ("multimodal", "ernie-5.0", {"choices": [{"message": {"content": "图纸"}}]}, "content", "/chat/completions"),
        ("embedding", "embedding-v1", {"data": [{"embedding": [0.1, 0.2]}]}, "embeddings", "/embeddings"),
        ("reranker", "bce-reranker-base", {"results": [{"index": 0, "relevance_score": 0.9}]}, "results", "/rerank"),
    ],
)
def test_unified_gateway_supports_all_model_kinds(kind, model_code, response, expected_key, expected_path) -> None:
    store, model = _store_with_model(kind, model_code)
    transport = FakeTransport([httpx.Response(200, json=response)])
    gateway = ModelGateway(store, transport=transport, settings=Settings())

    if kind == "multimodal":
        result = gateway.multimodal_chat(model["id"], [{"role": "user", "content": "查看图纸"}])
    elif kind == "embedding":
        result = gateway.embed(model["id"], ["标准条款"])
    else:
        result = gateway.rerank(model["id"], "防爆", ["防爆要求"])

    assert expected_key in result
    assert transport.requests[0]["url"].endswith(expected_path)


def test_gateway_opens_circuit_and_never_exposes_provider_error_body() -> None:
    store, model = _store_with_model()
    transport = FakeTransport(
        [httpx.Response(401, json={"error": {"message": "provider-secret invalid"}}) for _ in range(2)]
    )
    settings = Settings(model_max_retries=0, model_circuit_failure_threshold=2)
    gateway = ModelGateway(store, transport=transport, settings=settings)

    for _ in range(2):
        with pytest.raises(ModelGatewayError, match="rejected") as caught:
            gateway.chat(model["id"], [{"role": "user", "content": "test"}])
        assert "provider-secret invalid" not in str(caught.value)
    with pytest.raises(ModelGatewayError) as caught:
        gateway.chat(model["id"], [{"role": "user", "content": "test"}])
    assert caught.value.code == "MODEL_CIRCUIT_OPEN"
    assert len(transport.requests) == 2


def test_qianfan_bootstrap_is_idempotent() -> None:
    store = DemoStore.seed()
    settings = Settings(qianfan_api_key="bootstrap-secret")

    bootstrap_qianfan_models(store, settings)
    bootstrap_qianfan_models(store, settings)

    qianfan_models = [item for item in store.list_model_configs() if item["provider_code"] == "qianfan"]
    assert {item["model_code"] for item in qianfan_models} == {
        "deepseek-v4-pro", "ernie-5.0", "embedding-v1", "bce-reranker-base"
    }
    assert len(qianfan_models) == 4
