import pytest

from coal_platform.uat import (
    active_standard_version,
    required_active_models,
    verify_model_connection_audits,
    verify_real_model_evidence,
)


def test_active_standard_version_selects_only_published_catalog_content() -> None:
    standards = [
        {"versions": [{"id": "draft", "status": "draft"}]},
        {"versions": [{"id": "active", "status": "active"}]},
    ]
    assert active_standard_version(standards) == {"id": "active", "status": "active"}
    assert active_standard_version([{"versions": [{"id": "draft", "status": "draft"}]}]) is None


def test_required_active_models_selects_the_four_delivery_models_in_operation_order() -> None:
    models = [
        {"id": "rerank", "model_kind": "reranker", "model_code": "bce-reranker-base", "status": "active"},
        {"id": "wrong", "model_kind": "text", "model_code": "other-model", "status": "active"},
        {"id": "text", "model_kind": "text", "model_code": "deepseek-v4-pro", "status": "active"},
        {"id": "vision", "model_kind": "multimodal", "model_code": "ernie-5.0", "status": "active"},
        {"id": "embedding", "model_kind": "embedding", "model_code": "embedding-v1", "status": "active"},
    ]

    assert [(model["id"], operation) for model, operation in required_active_models(models)] == [
        ("text", "chat"),
        ("vision", "multimodal_chat"),
        ("embedding", "embedding"),
        ("rerank", "rerank"),
    ]


def test_required_active_models_rejects_missing_or_inactive_delivery_model() -> None:
    with pytest.raises(RuntimeError, match="multimodal/ernie-5.0"):
        required_active_models(
            [
                {"model_kind": "text", "model_code": "deepseek-v4-pro", "status": "active"},
                {"model_kind": "multimodal", "model_code": "ernie-5.0", "status": "disabled"},
                {"model_kind": "embedding", "model_code": "embedding-v1", "status": "active"},
                {"model_kind": "reranker", "model_code": "bce-reranker-base", "status": "active"},
            ]
        )


def test_model_connection_audits_require_matching_successful_operations() -> None:
    requests = {
        "request-text": "chat",
        "request-vision": "multimodal_chat",
        "request-embedding": "embedding",
        "request-rerank": "rerank",
    }
    logs = [
        {"request_id": "request-text", "operation": "chat", "status": "succeeded"},
        {"request_id": "request-vision", "operation": "multimodal_chat", "status": "succeeded"},
        {"request_id": "request-embedding", "operation": "embedding", "status": "succeeded"},
        {"request_id": "request-rerank", "operation": "rerank", "status": "succeeded"},
    ]

    assert verify_model_connection_audits(requests, logs) == {
        "model_count": 4,
        "operations": ["chat", "embedding", "multimodal_chat", "rerank"],
    }

    with pytest.raises(RuntimeError, match="embedding:request-embedding"):
        verify_model_connection_audits(
            requests,
            [
                logs[0],
                logs[1],
                {"request_id": "request-embedding", "operation": "embedding", "status": "failed"},
                logs[3],
            ],
        )


def test_model_connection_audits_reject_partial_request_sets() -> None:
    with pytest.raises(RuntimeError, match="four unique"):
        verify_model_connection_audits({"request-text": "chat"}, [])


def test_real_model_evidence_requires_determinate_cited_and_audited_dynamic_results() -> None:
    executions = [
        {
            "id": "execution-1",
            "dynamic_item_id": "dynamic-1",
            "status": "succeeded",
            "result_payload": {
                "outcome": "passed",
                "model_request_id": "request-1",
                "evidence_sufficiency": {"sufficient": True, "citations_valid": True},
            },
        }
    ]
    logs = [{"request_id": "request-1", "operation": "chat", "status": "succeeded"}]

    assert verify_real_model_evidence(executions, logs) == {
        "dynamic_execution_count": 1,
        "model_request_count": 1,
    }


@pytest.mark.parametrize(
    ("execution_update", "logs", "message"),
    [
        ({"status": "unable_to_determine"}, [], "did not produce a determinate"),
        ({"status": "failed"}, [], "did not produce a determinate"),
        (
            {"result_payload": {"outcome": "passed", "model_request_id": "request-1"}},
            [],
            "invalid evidence citations",
        ),
        ({}, [], "audit logs are missing"),
    ],
)
def test_real_model_evidence_rejects_incomplete_acceptance_proof(
    execution_update: dict, logs: list[dict], message: str
) -> None:
    execution = {
        "id": "execution-1",
        "dynamic_item_id": "dynamic-1",
        "status": "succeeded",
        "result_payload": {
            "outcome": "passed",
            "model_request_id": "request-1",
            "evidence_sufficiency": {"sufficient": True, "citations_valid": True},
        },
    }
    execution.update(execution_update)

    with pytest.raises(RuntimeError, match=message):
        verify_real_model_evidence([execution], logs)
