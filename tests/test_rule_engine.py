from coal_platform.rule_engine import (
    evaluate_trigger_condition,
    validate_dependency_graph,
    validate_parameters,
)


def test_parameter_validation_reports_nested_json_path() -> None:
    errors = validate_parameters(
        {"limits": {"minimum": "six"}},
        {
            "type": "object",
            "properties": {
                "limits": {
                    "type": "object",
                    "properties": {"minimum": {"type": "integer", "minimum": 0}},
                    "required": ["minimum"],
                    "additionalProperties": False,
                }
            },
            "required": ["limits"],
            "additionalProperties": False,
        },
    )

    assert errors[0]["code"] == "INVALID_PARAMETERS"
    assert errors[0]["path"] == "parameters.limits.minimum"


def test_dependency_validation_rejects_missing_rule_and_cycle() -> None:
    errors = validate_dependency_graph(
        {"RULE_A": ["RULE_B"], "RULE_B": ["RULE_A", "RULE_MISSING"]},
        {"RULE_A", "RULE_B"},
    )

    assert {item["code"] for item in errors} == {"DEPENDENCY_NOT_FOUND", "DEPENDENCY_CYCLE"}


def test_file_trigger_requires_matching_task_context() -> None:
    condition = {"source_type": "file_trigger", "min_files": 2, "file_types_any": ["pdf"]}

    disabled, _reason = evaluate_trigger_condition(
        condition,
        file_types=["docx"],
        confirmed_standard_count=0,
    )
    enabled, reason = evaluate_trigger_condition(
        condition,
        file_types=["pdf", "docx"],
        confirmed_standard_count=0,
    )

    assert disabled is False
    assert enabled is True
    assert reason == "任务文件和数据满足触发条件"
