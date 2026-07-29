from __future__ import annotations

import json

from coal_platform.executor_runtime import evaluate_ai

RULE = {
    "rule_code": "DYNAMIC_STANDARD_CLAUSE_REVIEW",
    "rule_name": "动态标准条款语义审核",
    "default_issue_category": "standard_compliance",
    "default_severity": "一般",
    "affects_suggested_conclusion": True,
}


class FakeGateway:
    def __init__(self, decision: dict) -> None:
        self.decision = decision
        self.calls = []

    def chat(self, config_id, messages, **kwargs):
        self.calls.append({"config_id": config_id, "messages": messages, **kwargs})
        return {"content": json.dumps(self.decision), "request_id": "model-request-1", "usage": {"total_tokens": 20}}


def _payload() -> dict:
    return {
        "dynamic_item": {"subject_code": "5.3.2", "subject_name": "驱动功率配置"},
        "model_snapshot": {"config_id": "model-1", "model_code": "deepseek-v4-pro", "credential_version": 1},
        "evidence": [{"file_id": "file-1", "page_no": 2, "excerpt_text": "额定功率 55 kW", "confidence": 0.98}],
        "standard_evidence": [{"clause_id": "clause-1", "excerpt_text": "额定功率应满足设计输送能力", "confidence": 0.99}],
    }


def test_ai_executor_does_not_call_model_when_evidence_is_insufficient() -> None:
    gateway = FakeGateway({})
    payload = _payload()
    payload["standard_evidence"] = []

    result = evaluate_ai(RULE, {}, payload, gateway)

    assert result["outcome"] == "unable_to_determine"
    assert result["warnings"] == ["EVIDENCE_INSUFFICIENT"]
    assert gateway.calls == []


def test_ai_executor_constrains_low_confidence_or_invalid_citations() -> None:
    gateway = FakeGateway({"outcome": "failed", "reason": "不符合", "confidence": 0.4, "customer_evidence_indexes": [8], "standard_evidence_indexes": [0]})

    result = evaluate_ai(RULE, {"minimum_confidence": 0.65}, _payload(), gateway)

    assert result["outcome"] == "unable_to_determine"
    assert result["warnings"] == ["MODEL_DECISION_CONSTRAINED"]
    assert "issue" not in result


def test_ai_executor_creates_issue_only_from_cited_two_sided_evidence() -> None:
    gateway = FakeGateway({"outcome": "failed", "reason": "客户功率低于条款要求", "confidence": 0.92, "customer_evidence_indexes": [0], "standard_evidence_indexes": [0]})

    result = evaluate_ai(RULE, {}, _payload(), gateway)

    assert result["outcome"] == "failed"
    assert result["issue"]["issue_code"].endswith("-5.3.2")
    assert result["issue"]["customer_evidence"][0]["file_id"] == "file-1"
    assert result["issue"]["standard_evidence"][0]["clause_id"] == "clause-1"
    assert result["token_usage"]["total_tokens"] == 20
    assert gateway.calls[0]["response_format"] == {"type": "json_object"}
