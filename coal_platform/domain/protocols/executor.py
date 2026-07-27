from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class ExecutorKind(StrEnum):
    BUILTIN = "builtin"
    AI = "ai"
    COMPOSITE = "composite"


class ExecutionOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNABLE_TO_DETERMINE = "unable_to_determine"
    EXCEPTION = "exception"
    CANCELED = "canceled"


class EvidenceKind(StrEnum):
    CUSTOMER = "customer"
    STANDARD = "standard"


class RecommendedSeverity(StrEnum):
    SEVERE = "severe"
    NORMAL = "normal"
    HINT = "hint"


@dataclass(frozen=True)
class EvidenceRef:
    kind: EvidenceKind
    source_id: UUID | str
    file_id: UUID | None = None
    clause_id: UUID | None = None
    page_no: int | None = None
    bbox: Mapping[str, Any] | None = None
    excerpt_text: str | None = None
    artifact_uri: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class ExecutorContext:
    task_id: UUID
    round_id: UUID
    audit_run_id: UUID
    rule_execution_id: UUID
    executor_version_id: UUID
    rule_version_id: UUID
    operator_user_id: UUID | None = None
    trace_id: str | None = None
    snapshot_refs: Mapping[str, str] = field(default_factory=dict)
    now: datetime | None = None


@dataclass(frozen=True)
class ExecutorRequest:
    context: ExecutorContext
    parameters: Mapping[str, Any]
    input_payload: Mapping[str, Any]
    evidence: list[EvidenceRef] = field(default_factory=list)
    standard_evidence: list[EvidenceRef] = field(default_factory=list)
    dry_run: bool = False


@dataclass(frozen=True)
class ExecutorResult:
    outcome: ExecutionOutcome
    issue_title: str | None = None
    issue_description: str | None = None
    customer_evidence: list[EvidenceRef] = field(default_factory=list)
    standard_evidence: list[EvidenceRef] = field(default_factory=list)
    confidence: float | None = None
    recommended_severity: RecommendedSeverity | None = None
    affects_suggested_conclusion: bool = False
    normalized_input: Mapping[str, Any] = field(default_factory=dict)
    output_payload: Mapping[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    elapsed_ms: int | None = None
    token_usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutorError:
    code: str
    message: str
    retryable: bool = False
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutorDescriptor:
    code: str
    name: str
    version: str
    kind: ExecutorKind
    input_type: str
    output_type: str
    parameter_schema: Mapping[str, Any]
    result_schema: Mapping[str, Any]
    default_timeout_seconds: int
    supports_batch: bool
    entrypoint: str | None = None


class RuleExecutor(ABC):
    code: str
    version: str

    @abstractmethod
    def describe(self) -> ExecutorDescriptor: ...

    @abstractmethod
    def validate_parameters(self, parameters: Mapping[str, Any]) -> None: ...

    @abstractmethod
    def validate_input(self, request: ExecutorRequest) -> None: ...

    @abstractmethod
    def execute(self, request: ExecutorRequest) -> ExecutorResult: ...


class StepKind(StrEnum):
    REQUIRED = "required"
    DEGRADABLE = "degradable"
    CONDITIONAL = "conditional"


@dataclass(frozen=True)
class CompositeStep:
    step_code: str
    executor_code: str
    step_kind: StepKind
    parameters_override: Mapping[str, Any] = field(default_factory=dict)
    condition_expr: str | None = None
    retryable: bool = True


@dataclass(frozen=True)
class CompositePlan:
    plan_code: str
    plan_version: str
    steps: list[CompositeStep]


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[tuple[str, str], RuleExecutor] = {}

    def register(self, executor: RuleExecutor) -> None:
        descriptor = executor.describe()
        key = (descriptor.code, descriptor.version)
        if key in self._executors:
            raise ValueError(f"executor already registered: code={descriptor.code}, version={descriptor.version}")
        self._executors[key] = executor

    def get(self, code: str, version: str | None = None) -> RuleExecutor:
        if version is not None:
            return self._executors[(code, version)]
        for (registered_code, _version), executor in self._executors.items():
            if registered_code == code:
                return executor
        raise KeyError(code)

    def list(self) -> list[ExecutorDescriptor]:
        return [executor.describe() for executor in self._executors.values()]

    def validate_snapshot(self, descriptors: list[ExecutorDescriptor]) -> None:
        snapshot_keys = {(item.code, item.version) for item in descriptors}
        registered_keys = set(self._executors.keys())
        if snapshot_keys != registered_keys:
            missing = snapshot_keys - registered_keys
            extra = registered_keys - snapshot_keys
            raise ValueError(f"executor registry mismatch: missing={missing}, extra={extra}")
