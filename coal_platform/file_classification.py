from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

FILE_TYPE_LABELS = {
    "product_drawing": "产品图纸",
    "product_manual": "使用说明书",
    "controlled_component_list": "受控件明细表",
    "enterprise_standard": "企业标准",
    "other": "其他资料",
}

REQUIRED_FILE_TYPES = (
    "product_drawing",
    "product_manual",
    "controlled_component_list",
)

_CLASSIFICATION_KEYWORDS = (
    ("controlled_component_list", ("受控件", "受控部件", "controlled component", "controlled part")),
    ("enterprise_standard", ("企业标准", "企标", "enterprise standard")),
    ("product_manual", ("使用说明书", "产品说明书", "操作说明书", "操作手册", "user manual", "instruction manual", "manual")),
    ("product_drawing", ("图纸", "总装图", "装配图", "原理图", "系统图", "零件图", "cad", "drawing", "blueprint")),
)


class AuditReadinessError(ValueError):
    def __init__(self, readiness: dict[str, Any]) -> None:
        self.readiness = readiness
        super().__init__("审核启动条件未满足")


def classify_file_name(file_name: str) -> dict[str, Any]:
    normalized = Path(file_name).stem.casefold().replace("_", " ").replace("-", " ")
    for file_type, keywords in _CLASSIFICATION_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return {
                "file_type": file_type,
                "file_type_label": FILE_TYPE_LABELS[file_type],
                "is_required": file_type in REQUIRED_FILE_TYPES,
            }
    return {"file_type": "other", "file_type_label": FILE_TYPE_LABELS["other"], "is_required": False}


def trigger_file_types(files: Iterable[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for item in files:
        if item.get("status") == "deleted" or item.get("is_applicable") is False:
            continue
        values.add(item.get("file_type") or "other")
        suffix = Path(item.get("file_name") or item.get("original_name") or "").suffix.lower().lstrip(".")
        if suffix:
            values.add(suffix)
    return sorted(values)


def assess_audit_readiness(files: Iterable[dict[str, Any]], *, rules_confirmed: bool) -> dict[str, Any]:
    active_files = [
        item for item in files
        if item.get("status") != "deleted" and item.get("is_applicable") is not False
    ]
    requirements = []
    blockers = []
    for file_type in REQUIRED_FILE_TYPES:
        matching = [item for item in active_files if item.get("file_type") == file_type]
        parsed = [item for item in matching if item.get("status") == "parsed"]
        requirement = {
            "file_type": file_type,
            "label": FILE_TYPE_LABELS[file_type],
            "required": True,
            "present": bool(matching),
            "parsed": bool(parsed),
            "file_ids": [item.get("id") for item in matching if item.get("id")],
        }
        requirements.append(requirement)
        if not matching:
            blockers.append({
                "code": "REQUIRED_FILE_MISSING",
                "file_type": file_type,
                "message": f"缺少必选资料：{FILE_TYPE_LABELS[file_type]}",
            })
        elif not parsed:
            blockers.append({
                "code": "REQUIRED_FILE_NOT_PARSED",
                "file_type": file_type,
                "message": f"必选资料尚未解析成功：{FILE_TYPE_LABELS[file_type]}",
            })
    if not rules_confirmed:
        blockers.append({"code": "RULES_NOT_CONFIRMED", "message": "审核规则尚未确认"})
    return {
        "can_start": not blockers,
        "required_files_present": all(item["present"] for item in requirements),
        "required_files_parsed": all(item["parsed"] for item in requirements),
        "rules_confirmed": rules_confirmed,
        "requirements": requirements,
        "blockers": blockers,
    }


def require_audit_ready(files: Iterable[dict[str, Any]], *, rules_confirmed: bool) -> dict[str, Any]:
    readiness = assess_audit_readiness(files, rules_confirmed=rules_confirmed)
    if not readiness["can_start"]:
        raise AuditReadinessError(readiness)
    return readiness
