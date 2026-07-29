import pytest

from coal_platform.uat import active_standard_version, verify_real_model_evidence


def test_active_standard_version_selects_only_published_catalog_content() -> None:
    standards = [
        {"versions": [{"id": "draft", "status": "draft"}]},
        {"versions": [{"id": "active", "status": "active"}]},
    ]
    assert active_standard_version(standards) == {"id": "active", "status": "active"}
    assert active_standard_version([{"versions": [{"id": "draft", "status": "draft"}]}]) is None


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
