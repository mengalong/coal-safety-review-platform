from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from time import monotonic
from typing import Any

from coal_platform.document_parser import parse_document
from coal_platform.ocr import OCRBackend
from coal_platform.storage import ObjectStorage
from coal_platform.store_protocol import PlatformStore

logger = logging.getLogger(__name__)


def _issue(rule: Mapping[str, Any], description: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "issue_code": f"RULE-{rule.get('rule_code', 'UNKNOWN')}",
        "title": rule.get("rule_name", "规则检查"),
        "description": description,
        "category_code": rule.get("default_issue_category", "technical_compliance"),
        "severity": rule.get("default_severity", "一般"),
        "system_conclusion": "failed",
        "affects_conclusion": bool(rule.get("affects_suggested_conclusion", False)),
        "customer_evidence": payload.get("evidence", []),
        "standard_evidence": payload.get("standard_evidence", []),
    }


def evaluate_builtin(rule: Mapping[str, Any], parameters: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    code = rule.get("executor_code")
    values = payload.get("fields", payload)
    values = values if isinstance(values, Mapping) else {}
    passed = True
    description = ""
    if code == "required_field":
        missing = [field for field in parameters.get("field_codes", []) if values.get(field) in (None, "")]
        passed = not missing
        description = f"缺少必填字段: {', '.join(missing)}" if missing else "必填字段均已提供"
    elif code == "regex_format":
        value = payload.get("value", values.get(parameters.get("field_code", ""), ""))
        flags = re.IGNORECASE if "IGNORECASE" in parameters.get("flags", []) else 0
        passed = re.fullmatch(parameters.get("pattern", ""), str(value), flags=flags) is not None
        description = "字段格式符合要求" if passed else "字段格式不符合正则约束"
    elif code in {"exact_compare", "normalized_compare"}:
        compared = [values.get(field) for field in parameters.get("field_codes", [])]
        if code == "normalized_compare":
            compared = [str(value).strip().lower() if value is not None else None for value in compared]
        passed = len(set(compared)) <= 1
        description = "字段值一致" if passed else "字段值存在不一致"
    elif code == "numeric_compare":
        actual = payload.get("value")
        operator = parameters.get("operator", "eq")
        threshold = parameters.get("threshold")
        passed = {"eq": actual == threshold, "ne": actual != threshold, "gt": actual > threshold,
                  "gte": actual >= threshold, "lt": actual < threshold, "lte": actual <= threshold,
                  "between": parameters.get("minimum") <= actual <= parameters.get("maximum")}.get(operator, False)
        description = "数值满足约束" if passed else "数值不满足约束"
    elif code == "evidence_required":
        passed = len(payload.get("evidence", [])) >= parameters.get("minimum_customer_evidence", 0) and len(payload.get("standard_evidence", [])) >= parameters.get("minimum_standard_evidence", 0)
        description = "证据数量满足要求" if passed else "证据数量不足"
    else:
        return {"outcome": "unable_to_determine", "warnings": [f"未注册内置执行器: {code}"]}
    result = {"outcome": "passed" if passed else "failed", "normalized_input": dict(values), "description": description}
    if not passed:
        result["issue"] = _issue(rule, description, payload)
    return result


def run_rule_execution(store: PlatformStore, execution_id: str, input_payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    execution = store.get_rule_execution(execution_id)
    if not execution:
        return None
    version = store.get_rule_version(execution["rule_version_id"])
    if not version:
        return None
    rule = next((item for item in store.list_rules() if item.get("id") == version.get("rule_definition_id")), None)
    if not rule:
        return None
    payload = input_payload or execution.get("input_snapshot") or {}
    started = monotonic()
    try:
        result = evaluate_builtin({**rule, "executor_code": version.get("executor_code")}, version.get("parameters") or {}, payload)
        status = "succeeded" if result["outcome"] == "passed" else result["outcome"]
        return store.record_execution_attempt(execution_id, {
            "status": status, "attempt_kind": "normal", "input_payload": payload,
            "output_payload": result, "elapsed_ms": round((monotonic() - started) * 1000),
        })
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return store.record_execution_attempt(execution_id, {
            "status": "exception", "attempt_kind": "normal", "input_payload": payload,
            "error_payload": {"code": "EXECUTOR_EXCEPTION", "message": str(exc)},
            "elapsed_ms": round((monotonic() - started) * 1000),
        })


def process_queue_job(
    store: PlatformStore,
    job_id: str,
    object_storage: ObjectStorage | None = None,
    ocr_backend: OCRBackend | None = None,
    ocr_dpi: int = 200,
) -> dict[str, Any] | None:
    job = store.get_queue_job(job_id)
    if not job:
        return None
    if job["status"] not in {"queued", "pending"}:
        raise ValueError("queue job is not runnable")
    store.update_queue_job(job_id, {"status": "running"})
    created_asset_keys: set[str] = set()
    try:
        if job["job_type"] == "document_parse":
            payload = job["payload"]
            file_id = payload["file_id"]
            context = {
                "_operator_user_id": payload.get("operator_user_id"),
                "_trace_id": payload.get("trace_id"),
                "_storage_key": payload.get("storage_key"),
            }
            if object_storage is None:
                raise ValueError("object storage is required for document parsing")
            if not store.start_task_file_parse(file_id, context):
                raise ValueError("task file is not available for parsing")
            content = object_storage.get(payload["storage_key"])
            if content is None:
                raise ValueError("task file object is missing")
            parsed = parse_document(
                content,
                payload["file_name"],
                payload.get("file_type"),
                ocr_backend,
                ocr_dpi,
            )
            page_assets = []
            current_asset_keys: set[str] = set()
            for asset in parsed.get("page_assets", []):
                asset_key = f"{payload['storage_key']}.pages/{asset['page_no']:04d}.png"
                object_storage.put(asset_key, asset["content"], "image/png")
                created_asset_keys.add(asset_key)
                current_asset_keys.add(asset_key)
                page_assets.append(
                    {
                        key: value
                        for key, value in asset.items()
                        if key != "content"
                    }
                    | {"thumbnail_storage_key": asset_key}
                )
            for old_asset in payload.get("page_assets", []):
                old_key = old_asset.get("thumbnail_storage_key")
                if old_key and old_key not in current_asset_keys:
                    object_storage.delete(old_key)
            completed = store.complete_task_file_parse(
                file_id,
                parsed["blocks"],
                parsed["summary"],
                context,
                page_assets,
            )
            if not completed:
                raise ValueError("task file parse result could not be saved")
            return store.update_queue_job(job_id, {"status": "succeeded", "result": parsed["summary"]})
        if job["job_type"] == "rule_test_run":
            payload = job["payload"]
            version = store.get_rule_version(payload["rule_version_id"])
            if not version:
                raise ValueError("rule version not found")
            rule = next((item for item in store.list_rules() if item["id"] == version["rule_definition_id"]), None)
            if not rule:
                raise ValueError("rule definition not found")
            input_payload = {
                **(payload.get("input_payload") or {}),
                "evidence": payload.get("evidence") or [],
                "standard_evidence": payload.get("standard_evidence") or [],
            }
            result = evaluate_builtin({**rule, "executor_code": version.get("executor_code")}, payload.get("parameters") or {}, input_payload)
            return store.update_queue_job(job_id, {"status": "succeeded", "result": result})
        if job["job_type"] == "audit":
            payload = job["payload"]
            executions = [
                item for item in (store.list_rule_executions(payload["round_id"]) or [])
                if item["audit_run_id"] == payload["audit_run_id"] and item["status"] == "pending"
            ]
            results = [run_rule_execution(store, item["id"]) for item in executions]
            exceptions = sum(1 for item in results if item and item.get("status") == "exception")
            summary = {"total": len(results), "exceptions": exceptions}
            store.complete_audit_run(payload["audit_run_id"], {"status": "failed" if exceptions else "succeeded", "summary": summary})
            return store.update_queue_job(job_id, {"status": "failed" if exceptions else "succeeded", "result": summary})
        raise ValueError(f"unsupported queue job type: {job['job_type']}")
    except Exception as exc:
        if job["job_type"] == "document_parse":
            payload = job.get("payload") or {}
            if object_storage:
                for asset_key in created_asset_keys:
                    try:
                        object_storage.delete(asset_key)
                    except Exception:
                        logger.warning("failed to clean up OCR page asset %s", asset_key, exc_info=True)
            store.fail_task_file_parse(
                payload.get("file_id", ""),
                {"code": "DOCUMENT_PARSE_FAILED", "message": str(exc)},
                {
                    "_operator_user_id": payload.get("operator_user_id"),
                    "_trace_id": payload.get("trace_id"),
                    "_storage_key": payload.get("storage_key"),
                },
            )
        store.update_queue_job(job_id, {"status": "failed", "error": {"code": "JOB_EXECUTION_FAILED", "message": str(exc)}})
        raise
