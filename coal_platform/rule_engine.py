from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

FIXED_AUDIT_STAGES = (
    {"code": "document_parsing", "name": "资料解析", "order_no": 10},
    {"code": "basic_info", "name": "基本信息与标准识别", "order_no": 20},
    {"code": "document_completeness", "name": "资料完整性", "order_no": 30},
    {"code": "single_file_review", "name": "单文件审核", "order_no": 40},
    {"code": "cross_file_consistency", "name": "跨文件一致性", "order_no": 50},
    {"code": "standard_compliance", "name": "标准条款符合性", "order_no": 60},
    {"code": "controlled_parts", "name": "受控件专项", "order_no": 70},
    {"code": "issue_summary", "name": "问题汇总", "order_no": 80},
    {"code": "conclusion", "name": "结论建议", "order_no": 90},
)
FIXED_AUDIT_STAGE_CODES = frozenset(item["code"] for item in FIXED_AUDIT_STAGES)
FIXED_AUDIT_STAGE_ORDER = {item["code"]: item["order_no"] for item in FIXED_AUDIT_STAGES}


EXECUTOR_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "required_field": {
        "type": "object",
        "properties": {
            "field_codes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
            "on_missing": {"type": "string", "enum": ["failed", "manual_review"]},
        },
        "additionalProperties": False,
    },
    "regex_format": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "minLength": 1, "format": "regex"},
            "flags": {"type": "array", "items": {"type": "string", "enum": ["IGNORECASE", "MULTILINE"]}},
        },
        "additionalProperties": False,
    },
    "date_validity": {
        "type": "object",
        "properties": {
            "base_date": {"type": "string"},
            "minimum_remaining_months": {"type": "integer", "minimum": 0, "maximum": 120},
            "on_unknown": {"type": "string", "enum": ["unable_to_determine", "manual_review"]},
        },
        "additionalProperties": False,
    },
    "exact_compare": {
        "type": "object",
        "properties": {
            "field_codes": {"type": "array", "items": {"type": "string"}, "minItems": 2, "uniqueItems": True},
            "case_sensitive": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "normalized_compare": {
        "type": "object",
        "properties": {
            "field_codes": {"type": "array", "items": {"type": "string"}, "minItems": 2, "uniqueItems": True},
            "normalizers": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        },
        "additionalProperties": False,
    },
    "numeric_compare": {
        "type": "object",
        "properties": {
            "operator": {"type": "string", "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "between"]},
            "threshold": {"type": "number"},
            "minimum": {"type": "number"},
            "maximum": {"type": "number"},
            "unit": {"type": "string", "minLength": 1},
            "tolerance": {"type": "number", "minimum": 0},
        },
        "additionalProperties": False,
    },
    "standard_status": {
        "type": "object",
        "properties": {
            "allowed_statuses": {
                "type": "array",
                "items": {"type": "string", "enum": ["active", "effective", "published"]},
                "minItems": 1,
                "uniqueItems": True,
            }
        },
        "additionalProperties": False,
    },
    "semantic_compare": {
        "type": "object",
        "properties": {
            "prompt_template_code": {"type": "string", "minLength": 1},
            "minimum_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "require_standard_evidence": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "evidence_required": {
        "type": "object",
        "properties": {
            "minimum_customer_evidence": {"type": "integer", "minimum": 0, "maximum": 20},
            "minimum_standard_evidence": {"type": "integer", "minimum": 0, "maximum": 20},
            "minimum_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "additionalProperties": False,
    },
}


DEFAULT_RULE_STAGE_BY_CODE = {
    "CONTROLLED_PART_CERT_VALIDITY": "controlled_parts",
    "PRODUCT_MODEL_CONSISTENCY": "cross_file_consistency",
    "STANDARD_VERSION_STATUS": "standard_compliance",
    "AI_EVIDENCE_REQUIRED": "standard_compliance",
}


DEFAULT_RULE_PACKS = (
    {
        "pack_code": "STANDARD_BASE",
        "pack_name": "标准引用和证据基础规则",
        "stage_code": "standard_compliance",
        "trigger_condition": {"source_type": "global", "always": True},
        "rule_codes": ["STANDARD_VERSION_STATUS", "AI_EVIDENCE_REQUIRED"],
    },
    {
        "pack_code": "CROSS_FILE_CONSISTENCY",
        "pack_name": "跨文件一致性",
        "stage_code": "cross_file_consistency",
        "trigger_condition": {"source_type": "file_trigger", "min_files": 2},
        "rule_codes": ["PRODUCT_MODEL_CONSISTENCY"],
    },
    {
        "pack_code": "CONTROLLED_PARTS",
        "pack_name": "受控件专项",
        "stage_code": "controlled_parts",
        "trigger_condition": {
            "source_type": "file_trigger",
            "file_types_any": ["xls", "xlsx", "csv", "controlled_parts"],
        },
        "rule_codes": ["CONTROLLED_PART_CERT_VALIDITY"],
    },
)


class RuleConfigurationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__("rule configuration validation failed")


def validation_error(code: str, message: str, path: str = "") -> dict[str, Any]:
    return {"code": code, "message": message, "path": path}


def validate_stage_code(stage_code: str) -> list[dict[str, Any]]:
    if stage_code in FIXED_AUDIT_STAGE_CODES:
        return []
    return [validation_error("INVALID_STAGE", f"unknown fixed audit stage: {stage_code}", "stage_code")]


def validate_parameters(parameters: Mapping[str, Any], parameter_schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        Draft202012Validator.check_schema(parameter_schema)
    except SchemaError as exc:
        return [validation_error("INVALID_PARAMETER_SCHEMA", exc.message, "parameter_schema")]

    validator = Draft202012Validator(parameter_schema, format_checker=FormatChecker())
    errors = []
    for item in sorted(
        validator.iter_errors(dict(parameters)),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    ):
        suffix = ".".join(str(part) for part in item.absolute_path)
        path = f"parameters.{suffix}" if suffix else "parameters"
        errors.append(validation_error("INVALID_PARAMETERS", item.message, path))
    return errors


def validate_dependency_graph(
    graph: Mapping[str, list[str]],
    known_rule_codes: set[str],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for rule_code, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in known_rule_codes:
                errors.append(
                    validation_error(
                        "DEPENDENCY_NOT_FOUND",
                        f"dependency rule is not published: {dependency}",
                        f"dependency_rule_codes.{rule_code}",
                    )
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(rule_code: str, path: list[str]) -> None:
        if rule_code in visiting:
            cycle_start = path.index(rule_code) if rule_code in path else 0
            cycle = path[cycle_start:]
            errors.append(validation_error("DEPENDENCY_CYCLE", " -> ".join(cycle), "dependency_rule_codes"))
            return
        if rule_code in visited:
            return
        visiting.add(rule_code)
        for dependency in graph.get(rule_code, []):
            if dependency in known_rule_codes:
                visit(dependency, [*path, dependency])
        visiting.remove(rule_code)
        visited.add(rule_code)

    for rule_code in graph:
        visit(rule_code, [rule_code])
    return errors


def validate_trigger_condition(condition: Mapping[str, Any]) -> list[dict[str, Any]]:
    schema = {
        "type": "object",
        "properties": {
            "source_type": {"type": "string", "enum": ["global", "file_trigger"]},
            "always": {"type": "boolean"},
            "min_files": {"type": "integer", "minimum": 0},
            "file_types_any": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "file_types_all": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "requires_confirmed_standard": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    return [
        {**item, "code": "INVALID_TRIGGER_CONDITION", "path": item["path"].replace("parameters", "trigger_condition", 1)}
        for item in validate_parameters(condition, schema)
    ]


def evaluate_trigger_condition(
    condition: Mapping[str, Any],
    *,
    file_types: list[str],
    confirmed_standard_count: int,
) -> tuple[bool, str]:
    source_type = str(condition.get("source_type") or "global")
    if condition.get("requires_confirmed_standard") and confirmed_standard_count == 0:
        return False, "本轮尚未确认适用标准"
    if len(file_types) < int(condition.get("min_files") or 0):
        return False, f"文件数量少于 {condition['min_files']}"
    available_types = set(file_types)
    required_any = set(condition.get("file_types_any") or [])
    if required_any and available_types.isdisjoint(required_any):
        return False, "未发现规则包要求的文件类型"
    required_all = set(condition.get("file_types_all") or [])
    if required_all and not required_all.issubset(available_types):
        return False, "缺少规则包要求的文件类型"
    if source_type == "global":
        return True, "全局基础规则"
    return True, "任务文件和数据满足触发条件"
