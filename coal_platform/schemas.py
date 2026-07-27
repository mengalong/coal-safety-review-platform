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
    parameters: dict[str, Any] = Field(default_factory=dict)
    scope_files: list[str] = Field(default_factory=list)
    priority: int = 100
    stage_code: str = "standard_compliance"
    dependency_rule_codes: list[str] = Field(default_factory=list)
    task_override_allowed: bool = True
    executor_version_id: str | None = None


class IssueUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    category_code: str | None = None
    severity: str | None = None
    affects_conclusion: bool | None = None
    reason: str | None = None


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


class ReportCreateRequest(BaseModel):
    report_type: str = "formal"
    conclusion: str = "through"
