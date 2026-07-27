from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from coal_platform.database import Base
from coal_platform.domain.enums import CoverageStatus


class IdMixin:
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class AuditMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, IdMixin, AuditMixin):
    __tablename__ = "sys_user"

    login_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(128))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base, IdMixin, AuditMixin):
    __tablename__ = "auth_session"

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sys_user.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)


class AuditTask(Base, IdMixin, AuditMixin):
    __tablename__ = "audit_task"

    task_no: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    product_name: Mapped[str] = mapped_column(String(256), nullable=False)
    product_model: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("sys_user.id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    current_round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    final_conclusion: Mapped[str | None] = mapped_column(String(32))
    current_round_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("audit_round.id", name="fk_audit_task_current_round", use_alter=True),
    )

    __table_args__ = (
        UniqueConstraint("task_no", name="uq_audit_task_task_no"),
    )


class AuditRound(Base, IdMixin, AuditMixin):
    __tablename__ = "audit_round"

    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_task.id"), nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    suggested_conclusion: Mapped[str | None] = mapped_column(String(32))
    manual_conclusion: Mapped[str | None] = mapped_column(String(32))
    round_note: Mapped[str | None] = mapped_column(Text)
    basic_info_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    standard_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    rule_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    executor_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    model_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    prompt_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))

    __table_args__ = (UniqueConstraint("task_id", "round_no", name="uq_audit_round_task_round"),)


class TaskFile(Base, IdMixin, AuditMixin):
    __tablename__ = "task_file"

    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_task.id"), nullable=False)
    round_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"))
    storage_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    original_name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded", index=True)
    parse_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("task_id", "sha256", name="uq_task_file_task_sha256"),)


class ParsedBlock(Base, IdMixin, AuditMixin):
    __tablename__ = "parsed_block"

    file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("task_file.id"), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.0)
    source_ref: Mapped[str | None] = mapped_column(String(128))


class FieldDefinition(Base, IdMixin, AuditMixin):
    __tablename__ = "field_definition"

    field_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_dimension: Mapped[str | None] = mapped_column(String(64))
    multi_valued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    normalization_rule: Mapped[str | None] = mapped_column(String(128))
    validation_rule: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)


class ExtractedField(Base, IdMixin, AuditMixin):
    __tablename__ = "extracted_field"

    file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("task_file.id"), nullable=False)
    field_definition_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("field_definition.id"), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.0)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    manual_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="recognized", index=True)


class SemanticFact(Base, IdMixin, AuditMixin):
    __tablename__ = "semantic_fact"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    fact_code: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_name: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    unit_code: Mapped[str | None] = mapped_column(String(32))
    source_file_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("task_file.id"))
    source_block_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("parsed_block.id"))
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.0)


class UnitDefinition(Base, IdMixin, AuditMixin):
    __tablename__ = "unit_definition"

    unit_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    unit_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension_code: Mapped[str] = mapped_column(String(64), nullable=False)
    standard_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    precision_scale: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)


class UnitAlias(Base, IdMixin, AuditMixin):
    __tablename__ = "unit_alias"

    unit_definition_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("unit_definition.id"), nullable=False)
    alias_text: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class Standard(Base, IdMixin, AuditMixin):
    __tablename__ = "standard"

    standard_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    standard_name: Mapped[str] = mapped_column(String(256), nullable=False)
    standard_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_text: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str] | None] = mapped_column(JSON)
    alias_texts: Mapped[list[str] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)


class StandardVersion(Base, IdMixin, AuditMixin):
    __tablename__ = "standard_version"

    standard_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard.id"), nullable=False)
    full_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    publish_date: Mapped[date | None] = mapped_column(Date)
    implement_date: Mapped[date | None] = mapped_column(Date)
    abolish_date: Mapped[date | None] = mapped_column(Date)
    publisher: Mapped[str | None] = mapped_column(String(128))
    mandatory_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    original_file_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("task_file.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    superseded_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_version.id"))


class StandardParseRevision(Base, IdMixin, AuditMixin):
    __tablename__ = "standard_parse_revision"

    standard_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_version.id"), nullable=False)
    revision_no: Mapped[str] = mapped_column(String(32), nullable=False)
    revision_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    impact_flag: Mapped[str] = mapped_column(String(16), nullable=False, default="no_impact")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("standard_version_id", "revision_no", name="uq_standard_revision"),)


class StandardClause(Base, IdMixin, AuditMixin):
    __tablename__ = "standard_clause"

    parse_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_parse_revision.id"), nullable=False)
    clause_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    parent_clause_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_clause.id"))
    clause_level: Mapped[int] = mapped_column(Integer, nullable=False)
    clause_type: Mapped[str] = mapped_column(String(32), nullable=False)
    constraint_level: Mapped[str] = mapped_column(String(16), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.0)
    proof_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


class StandardRelation(Base, IdMixin, AuditMixin):
    __tablename__ = "standard_relation"

    source_standard_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_version.id"), nullable=False)
    target_standard_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_version.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    relation_note: Mapped[str | None] = mapped_column(Text)


class RoundStandard(Base, IdMixin, AuditMixin):
    __tablename__ = "round_standard"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    standard_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_version.id"), nullable=False)
    parse_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_parse_revision.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("sys_user.id"))
    skip_reason: Mapped[str | None] = mapped_column(Text)
    snapshot_no: Mapped[str] = mapped_column(String(32), nullable=False)


class RoundStandardCoverage(Base, IdMixin, AuditMixin):
    __tablename__ = "round_standard_coverage"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    standard_clause_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_clause.id"), nullable=False)
    dynamic_item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("dynamic_audit_item.id"))
    coverage_status: Mapped[str] = mapped_column(String(32), nullable=False, default=CoverageStatus.TO_CONFIRM.value)
    reason: Mapped[str | None] = mapped_column(Text)
    publish_check_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")


class ExecutorDefinition(Base, IdMixin, AuditMixin):
    __tablename__ = "executor_definition"

    executor_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    executor_name: Mapped[str] = mapped_column(String(128), nullable=False)
    executor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    input_type: Mapped[str] = mapped_column(String(64), nullable=False)
    output_type: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published", index=True)


class ExecutorVersion(Base, IdMixin, AuditMixin):
    __tablename__ = "executor_version"

    executor_definition_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("executor_definition.id"), nullable=False)
    version_no: Mapped[str] = mapped_column(String(32), nullable=False)
    parameter_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    default_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    supports_batch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    entrypoint: Mapped[str | None] = mapped_column(String(256))
    image_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="published", index=True)

    __table_args__ = (UniqueConstraint("executor_definition_id", "version_no", name="uq_executor_version"),)


class RulePack(Base, IdMixin, AuditMixin):
    __tablename__ = "rule_pack"

    pack_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    pack_name: Mapped[str] = mapped_column(String(128), nullable=False)
    stage_code: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_condition: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)


class RuleDefinition(Base, IdMixin, AuditMixin):
    __tablename__ = "rule_definition"

    rule_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    executor_definition_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("executor_definition.id"), nullable=False)
    default_issue_category: Mapped[str] = mapped_column(String(64), nullable=False)
    default_severity: Mapped[str] = mapped_column(String(16), nullable=False)
    affects_suggested_conclusion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RuleVersion(Base, IdMixin, AuditMixin):
    __tablename__ = "rule_version"

    rule_definition_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("rule_definition.id"), nullable=False)
    version_no: Mapped[str] = mapped_column(String(32), nullable=False)
    executor_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("executor_version.id"), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scope_files: Mapped[list[str] | dict[str, Any]] = mapped_column(JSON, nullable=False, default=list)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    stage_code: Mapped[str] = mapped_column(String(32), nullable=False)
    dependency_rule_codes: Mapped[list[str] | dict[str, Any] | None] = mapped_column(JSON)
    task_override_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)

    __table_args__ = (UniqueConstraint("rule_definition_id", "version_no", name="uq_rule_version"),)


class RulePackItem(Base, IdMixin, AuditMixin):
    __tablename__ = "rule_pack_item"

    rule_pack_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("rule_pack.id"), nullable=False)
    rule_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("rule_version.id"), nullable=False)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RoundRule(Base, IdMixin, AuditMixin):
    __tablename__ = "round_rule"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    rule_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("rule_version.id"), nullable=False)
    executor_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("executor_version.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    override_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    disable_reason: Mapped[str | None] = mapped_column(Text)
    snapshot_no: Mapped[str] = mapped_column(String(32), nullable=False)


class DynamicAuditItem(Base, IdMixin, AuditMixin):
    __tablename__ = "dynamic_audit_item"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    parent_clause_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_clause.id"))
    source_clause_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_clause.id"))
    subject_code: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(128), nullable=False)
    applicability_status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    input_profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    customer_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    standard_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    manual_state: Mapped[str | None] = mapped_column(String(32))
    merged_issue_key: Mapped[str | None] = mapped_column(String(128))


class AuditRun(Base, IdMixin, AuditMixin):
    __tablename__ = "audit_run"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    run_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    model_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    prompt_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class RuleExecution(Base, IdMixin, AuditMixin):
    __tablename__ = "rule_execution"

    audit_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_run.id"), nullable=False)
    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    rule_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("rule_version.id"), nullable=False)
    dynamic_item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("dynamic_audit_item.id"))
    executor_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("executor_version.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    normalized_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    is_expired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("audit_run_id", "rule_version_id", "normalized_input_hash", name="uq_rule_execution_key"),
    )


class RuleExecutionAttempt(Base, IdMixin, AuditMixin):
    __tablename__ = "rule_execution_attempt"

    rule_execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("rule_execution.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    executor_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("executor_version.id"), nullable=False)
    model_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    token_usage: Mapped[dict[str, int] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)


class ExecutionStep(Base, IdMixin, AuditMixin):
    __tablename__ = "execution_step"

    rule_execution_attempt_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rule_execution_attempt.id"), nullable=False
    )
    step_code: Mapped[str] = mapped_column(String(64), nullable=False)
    step_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reused_from_step_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("execution_step.id"))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    error_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class RuleImpactAnalysis(Base, IdMixin, AuditMixin):
    __tablename__ = "rule_impact_analysis"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    affected_rule_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    affected_issue_ids: Mapped[list[str] | None] = mapped_column(JSON)
    estimated_rerun_scope: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)


class AuditIssue(Base, IdMixin, AuditMixin):
    __tablename__ = "audit_issue"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category_code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    system_conclusion: Mapped[str | None] = mapped_column(Text)
    manual_conclusion: Mapped[str | None] = mapped_column(Text)
    affects_conclusion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manual_reason: Mapped[str | None] = mapped_column(Text)


class IssueSource(Base, IdMixin, AuditMixin):
    __tablename__ = "issue_source"

    issue_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_issue.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_execution_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("rule_execution.id"))
    dynamic_item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("dynamic_audit_item.id"))
    source_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class IssueEvidence(Base, IdMixin, AuditMixin):
    __tablename__ = "issue_evidence"

    issue_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_issue.id"), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("task_file.id"))
    clause_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("standard_clause.id"))
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    excerpt_text: Mapped[str | None] = mapped_column(Text)
    artifact_uri: Mapped[str | None] = mapped_column(String(256))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))


class Report(Base, IdMixin, AuditMixin):
    __tablename__ = "report"

    round_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("audit_round.id"), nullable=False)
    report_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    conclusion: Mapped[str] = mapped_column(String(32), nullable=False)
    word_object_key: Mapped[str | None] = mapped_column(String(256))
    pdf_object_key: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationLog(Base, IdMixin, AuditMixin):
    __tablename__ = "operation_log"

    operator_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("sys_user.id"))
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action_code: Mapped[str] = mapped_column(String(64), nullable=False)
    before_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(128))


class ModelProvider(Base, IdMixin, AuditMixin):
    __tablename__ = "model_provider"

    provider_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)


class ModelConfig(Base, IdMixin, AuditMixin):
    __tablename__ = "model_config"

    provider_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("model_provider.id"), nullable=False)
    model_code: Mapped[str] = mapped_column(String(128), nullable=False)
    model_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    api_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    concurrency_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)


class PromptTemplate(Base, IdMixin, AuditMixin):
    __tablename__ = "prompt_template"

    template_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    template_name: Mapped[str] = mapped_column(String(128), nullable=False)
    template_body: Mapped[str] = mapped_column(Text, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)


class SystemParameter(Base, IdMixin, AuditMixin):
    __tablename__ = "system_parameter"

    param_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    param_value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="global")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)


class QueueJob(Base, IdMixin, AuditMixin):
    __tablename__ = "queue_job"

    job_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemAlert(Base, IdMixin, AuditMixin):
    __tablename__ = "system_alert"

    alert_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new", index=True)
