# 规则执行器 Python 接口协议

## 1. 协议目标

执行器由研发实现并注册，管理员只配置规则实例，不上传代码。
该协议用于统一描述执行器版本、参数、输入、输出、异常、证据和组合步骤，确保：

1. 规则执行器可插拔，但实现入口必须在白名单内。
2. 确定性执行器、AI 执行器、组合执行器共享同一结果协议。
3. 规则执行结果必须能回填到 `rule_execution`、`rule_execution_attempt` 和 `issue_source`。
4. “无法判断”与“执行异常”分离，业务结果和技术故障不混淆。

## 2. 包结构建议

```text
coal_platform/
  domain/
    protocols/
      executor.py
  executors/
    builtin/
      required_field.py
      regex_format.py
      date_validity.py
      exact_compare.py
      normalized_compare.py
      numeric_compare.py
      standard_status.py
      semantic_compare.py
      evidence_required.py
```
## 3. 核心对象

### 3.1 执行器分类

| 类型 | 说明 |
|---|---|
| `builtin` | 研发实现的确定性执行器 |
| `ai` | 通过模型网关调用外部模型的执行器 |
| `composite` | 由多个已注册执行器步骤组成的组合执行器 |

### 3.2 统一输入

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID


class ExecutionOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNABLE_TO_DETERMINE = "unable_to_determine"
    EXCEPTION = "exception"
    CANCELED = "canceled"


class EvidenceKind(StrEnum):
    CUSTOMER = "customer"
    STANDARD = "standard"


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
```

### 3.3 统一输出

```python
class RecommendedSeverity(StrEnum):
    SEVERE = "severe"
    NORMAL = "normal"
    HINT = "hint"


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
```

### 3.4 失败对象

```python
@dataclass(frozen=True)
class ExecutorError:
    code: str
    message: str
    retryable: bool = False
    detail: Mapping[str, Any] = field(default_factory=dict)
```

## 4. 执行器接口

```python
from abc import ABC, abstractmethod


@dataclass(frozen=True)
class ExecutorDescriptor:
    code: str
    name: str
    version: str
    kind: str
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
```

### 4.1 约束

1. `validate_parameters` 必须先于执行。
2. `validate_input` 负责检查字段存在、单位维度、输入类型和证据是否满足执行前置条件。
3. `execute` 不得直接写数据库，只能返回结果。
4. AI 执行器必须输出结构化字段、证据和置信度。
5. 确定性执行器返回“无法判断”时必须说明缺失条件或输入歧义。

## 5. 组合执行器协议

```python
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
```

组合执行要求：

1. 必需步骤失败时，整体进入 `exception` 或 `unable_to_determine`，不得伪装成不符合。
2. 可降级步骤失败时允许继续，但结果必须标记降级。
3. 条件步骤仅在条件满足时执行。
4. 每个步骤都要写入 `execution_step`。

## 6. 执行器注册表

```python
class ExecutorRegistry:
    def __init__(self) -> None: ...

    def register(self, executor: RuleExecutor) -> None: ...

    def get(self, code: str, version: str | None = None) -> RuleExecutor: ...

    def list(self) -> list[ExecutorDescriptor]: ...

    def validate_snapshot(self, descriptors: list[ExecutorDescriptor]) -> None: ...
```

注册表规则：

1. 同一 `code + version` 唯一。
2. 数据库里的 `executor_version.entrypoint` 必须命中白名单。
3. Worker 启动时校验代码注册表和数据库元数据一致。

## 7. 规则执行回填约定

执行器返回结果后，业务层应回填以下字段：

| 目标表 | 关键字段 |
|---|---|
| `rule_execution` | `status`, `result_payload`, `confidence`, `elapsed_ms`, `is_expired` |
| `rule_execution_attempt` | `attempt_no`, `status`, `output_payload`, `error_payload`, `token_usage` |
| `audit_issue` | `title`, `description`, `severity`, `affects_conclusion` |
| `issue_source` | `source_status`, `source_payload` |
| `issue_evidence` | `file_id`, `clause_id`, `page_no`, `bbox`, `excerpt_text` |

## 8. 错误码建议

| 错误码 | 语义 |
|---|---|
| `PARAM_INVALID` | 参数校验失败 |
| `INPUT_MISSING` | 输入缺失 |
| `UNIT_MISMATCH` | 单位维度不匹配 |
| `EVIDENCE_INSUFFICIENT` | 证据不足 |
| `MODEL_TIMEOUT` | 模型超时 |
| `MODEL_NETWORK_ERROR` | 模型网络错误 |
| `MODEL_CIRCUIT_OPEN` | 模型熔断中 |
| `MODEL_INVALID_RESPONSE` | 模型响应结构无效 |

AI 执行器不得直接创建 HTTP 客户端，只能使用平台注入的统一模型网关。网关提供 `chat`、
`multimodal_chat`、`embed` 和 `rerank` 四个方法，并统一负责 Bearer 鉴权、超时、并发限制、
指数退避重试、熔断、响应大小限制和脱敏调用审计。执行器输出中只允许引用平台 `request_id`，
禁止携带 API Key、授权头或供应商原始错误体。
| `MODEL_SCHEMA_ERROR` | AI 输出结构错误 |
| `EXECUTOR_RUNTIME_ERROR` | 执行器内部异常 |
| `SNAPSHOT_EXPIRED` | 输入快照已过期 |
| `CANCELED` | 人工取消 |

## 9. 建议的首期内置执行器

1. `required_field`
2. `regex_format`
3. `date_validity`
4. `exact_compare`
5. `normalized_compare`
6. `numeric_compare`
7. `standard_status`
8. `semantic_compare`
9. `evidence_required`

## 10. 最小示例

```python
class RequiredFieldExecutor(RuleExecutor):
    code = "required_field"
    version = "1.0.0"

    def describe(self) -> ExecutorDescriptor:
        ...

    def validate_parameters(self, parameters: Mapping[str, Any]) -> None:
        ...

    def validate_input(self, request: ExecutorRequest) -> None:
        ...

    def execute(self, request: ExecutorRequest) -> ExecutorResult:
        ...
```
