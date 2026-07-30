import pytest

from coal_platform.file_classification import (
    AuditReadinessError,
    assess_audit_readiness,
    classify_file_name,
    require_audit_ready,
    trigger_file_types,
)


@pytest.mark.parametrize(
    ("file_name", "expected_type", "required"),
    [
        ("DSJ120总装图.pdf", "product_drawing", True),
        ("产品使用说明书.docx", "product_manual", True),
        ("受控件明细表.xlsx", "controlled_component_list", True),
        ("企业标准_QJ-2026.pdf", "enterprise_standard", False),
        ("补充证明材料.pdf", "other", False),
    ],
)
def test_classifies_uploaded_files_by_name(file_name: str, expected_type: str, required: bool) -> None:
    result = classify_file_name(file_name)

    assert result["file_type"] == expected_type
    assert result["is_required"] is required


def test_readiness_requires_all_mandatory_files_to_be_parsed_and_rules_confirmed() -> None:
    files = [
        {"id": "drawing", "file_name": "总装图.pdf", "file_type": "product_drawing", "status": "parsed"},
        {"id": "manual", "file_name": "说明书.docx", "file_type": "product_manual", "status": "parse_pending"},
    ]

    readiness = assess_audit_readiness(files, rules_confirmed=False)

    assert readiness["can_start"] is False
    assert {item["code"] for item in readiness["blockers"]} == {
        "REQUIRED_FILE_NOT_PARSED",
        "REQUIRED_FILE_MISSING",
        "RULES_NOT_CONFIRMED",
    }
    with pytest.raises(AuditReadinessError):
        require_audit_ready(files, rules_confirmed=False)


def test_trigger_types_keep_semantic_category_and_file_extension() -> None:
    values = trigger_file_types([
        {"file_name": "产品使用说明书.docx", "file_type": "product_manual", "status": "parsed"},
        {"file_name": "受控件明细表.xlsx", "file_type": "controlled_component_list", "status": "parsed"},
    ])

    assert values == ["controlled_component_list", "docx", "product_manual", "xlsx"]
