from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    login_name: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    display_name: str
    role: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class BasicInfoPayload(BaseModel):
    customer_name: str
    product_name: str
    product_model: str


class TaskCreateRequest(BaseModel):
    customer_name: str | None = None
    product_name: str | None = None
    product_model: str | None = None
    owner_user_id: str | None = None
    round_note: str | None = None


class RoundCreateRequest(BaseModel):
    round_note: str | None = None
    inherit_previous_snapshot: bool = True


class StandardCreateRequest(BaseModel):
    standard_code: str
    standard_name: str
    standard_type: str
    scope_text: str | None = None


class StandardVersionCreateRequest(BaseModel):
    version_label: str
    full_code: str | None = None
    publish_date: date | None = None
    implement_date: date | None = None
    abolish_date: date | None = None
    publisher: str | None = None
    mandatory_flag: bool = False
    status: str = "draft"


class StandardClausePayload(BaseModel):
    clause_code: str
    title: str | None = None
    clause_level: int = 1
    clause_type: str = "requirement"
    constraint_level: str = "待确认"
    original_text: str = ""
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    page_no: int | None = None
    bbox: dict[str, Any] | None = None
    confidence: float = 0.0
    proof_status: str = "pending"


class StandardParseRevisionCreateRequest(BaseModel):
    impact_flag: str = "no_impact"
    clauses: list[StandardClausePayload] | None = None


class StandardVersionAbolishRequest(BaseModel):
    abolish_date: date | None = None
    superseded_by_version_id: str | None = None


class RuleCreateRequest(BaseModel):
    rule_code: str
    rule_name: str
    rule_type: str
    executor_code: str
    default_issue_category: str
    default_severity: str
    affects_suggested_conclusion: bool = False
    is_mandatory: bool = False


class RuleVersionCreateRequest(BaseModel):
    version_no: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    scope_files: list[str] = Field(default_factory=list)
    priority: int = 100
    stage_code: str = "standard_compliance"
    dependency_rule_codes: list[str] = Field(default_factory=list)
    task_override_allowed: bool = True
    executor_version_id: str | None = None


class RuleTestRunRequest(BaseModel):
    input_payload: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    standard_evidence: list[dict[str, Any]] = Field(default_factory=list)


class RulePackCreateRequest(BaseModel):
    pack_code: str
    pack_name: str
    stage_code: str
    trigger_condition: dict[str, Any] = Field(default_factory=dict)
    rule_version_ids: list[str] = Field(default_factory=list)


class RulePackUpdateRequest(BaseModel):
    pack_name: str | None = None
    stage_code: str | None = None
    trigger_condition: dict[str, Any] | None = None
    rule_version_ids: list[str] | None = None
    status: str | None = None


class RoundRuleAssemblyRequest(BaseModel):
    rule_pack_ids: list[str] = Field(default_factory=list)


class ExecutionAttemptRequest(BaseModel):
    status: str = "succeeded"
    attempt_kind: str = "normal"
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)


class LocalRerunRequest(BaseModel):
    affected_rule_codes: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    input_change: dict[str, Any] = Field(default_factory=dict)


class DynamicItemDecisionRequest(BaseModel):
    reason: str | None = None


class IssueUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    category_code: str | None = None
    severity: str | None = None
    affects_conclusion: bool | None = None
    manual_conclusion: str | None = None
    reason: str | None = None


class ManualIssueCreateRequest(BaseModel):
    title: str
    description: str
    category_code: str
    severity: str = "一般"
    affects_conclusion: bool = False
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class PageRequest(BaseModel):
    page: int = 1
    page_size: int = 20


class StandardRelationPayload(BaseModel):
    relation_type: str
    relation_note: str | None = None


class ModelConfigRequest(BaseModel):
    provider_code: str
    provider_name: str
    base_url: str
    model_code: str
    model_kind: str
    api_key: str
    timeout_seconds: int = 60
    concurrency_limit: int = 1


class ModelConfigUpdateRequest(BaseModel):
    api_key: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    concurrency_limit: int | None = Field(default=None, ge=1, le=100)
    status: str | None = None


class IssueCategoryRequest(BaseModel):
    code: str
    name: str
    default_severity: str = "一般"
    description: str | None = None
    status: str = "active"


class ReportTemplateRequest(BaseModel):
    template_code: str
    template_name: str
    report_type: str = "formal"
    template_body: str
    version_no: int = Field(default=1, ge=1)
    status: str = "draft"


class SystemParameterRequest(BaseModel):
    param_value: dict[str, Any]
    scope: str = "global"
    status: str = "active"


class ReportCreateRequest(BaseModel):
    round_id: str
    report_type: str = "formal"
    conclusion: str = "through"
