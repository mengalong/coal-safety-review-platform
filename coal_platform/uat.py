from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


@dataclass
class Check:
    name: str
    status: str
    detail: str


def active_standard_version(standards: list[dict[str, Any]]) -> dict[str, Any] | None:
    for standard in standards:
        for version in standard.get("versions") or []:
            if version.get("status") == "active":
                return version
    return None


def verify_real_model_evidence(
    executions: list[dict[str, Any]], model_call_logs: list[dict[str, Any]]
) -> dict[str, Any]:
    dynamic_executions = [item for item in executions if item.get("dynamic_item_id")]
    if not dynamic_executions:
        raise RuntimeError("full UAT requires at least one dynamic model execution")
    request_ids: set[str] = set()
    for execution in dynamic_executions:
        result = execution.get("result_payload") or {}
        sufficiency = result.get("evidence_sufficiency") or {}
        if execution.get("status") != "succeeded" or result.get("outcome") not in {
            "passed",
            "failed",
        }:
            raise RuntimeError(
                f"dynamic execution {execution.get('id')} did not produce a determinate model decision"
            )
        if not sufficiency.get("sufficient") or not sufficiency.get("citations_valid"):
            raise RuntimeError(f"dynamic execution {execution.get('id')} has invalid evidence citations")
        request_id = result.get("model_request_id")
        if not request_id:
            raise RuntimeError(f"dynamic execution {execution.get('id')} has no model request id")
        request_ids.add(request_id)
    succeeded_logs = {
        item.get("request_id")
        for item in model_call_logs
        if item.get("status") == "succeeded" and item.get("operation") == "chat"
    }
    missing_logs = request_ids - succeeded_logs
    if missing_logs:
        raise RuntimeError(f"successful model call audit logs are missing: {', '.join(sorted(missing_logs))}")
    return {"dynamic_execution_count": len(dynamic_executions), "model_request_count": len(request_ids)}


class UATRunner:
    def __init__(self, client: httpx.Client, timeout_seconds: int = 600) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.checks: list[Check] = []

    def api(self, method: str, path: str, name: str, **kwargs) -> Any:
        response = self.client.request(method, path, **kwargs)
        if not response.is_success:
            self.checks.append(Check(name, "failed", f"HTTP {response.status_code}: {response.text[:300]}"))
            response.raise_for_status()
        self.checks.append(Check(name, "passed", f"HTTP {response.status_code}"))
        payload = response.json()
        return payload.get("data", payload)

    def wait_job(self, job_id: str, name: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            jobs = self.api("GET", "/api/v1/jobs", f"{name}状态查询")
            job = next((item for item in jobs if item.get("id") == job_id), None)
            if job and job.get("status") in {"succeeded", "failed", "canceled"}:
                if job["status"] != "succeeded":
                    raise RuntimeError(f"{name} ended with {job['status']}: {job.get('error')}")
                self.checks.append(Check(name, "passed", f"作业 {job_id} 执行成功"))
                return job
            time.sleep(2)
        raise TimeoutError(f"{name} did not finish within {self.timeout_seconds} seconds")

    def basic_workflow(self) -> tuple[dict[str, Any], dict[str, Any]]:
        me = self.api("GET", "/api/v1/auth/me", "当前用户与会话")
        if me.get("role") != "admin":
            raise RuntimeError("UAT requires a dedicated administrator account")
        suffix = uuid4().hex[:10]
        task = self.api(
            "POST",
            "/api/v1/tasks",
            "创建单型号审核任务",
            json={
                "customer_name": "二期生产 UAT",
                "product_name": "矿用隔爆型设备",
                "product_model": f"UAT-{suffix}",
                "round_note": "生产候选版本端到端验收",
            },
        )
        if not task.get("product_model") or task.get("current_round_no") != 1:
            raise RuntimeError("task did not preserve the one-model initial-round contract")
        upload = self.api(
            "POST",
            f"/api/v1/tasks/{task['id']}/files",
            "上传技术资料并创建解析作业",
            files={
                "files": (
                    "uat-technical-document.txt",
                    "产品型号 UAT，额定电压 1140V，防爆标志 Ex db I Mb。\n",
                    "text/plain; charset=utf-8",
                )
            },
        )
        file_item = upload["files"][0]
        parse_job = upload["parse_jobs"][0]
        self.wait_job(parse_job["id"], "文档解析")
        blocks = self.api(
            "GET",
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/blocks",
            "解析证据块可追溯",
        )
        if not blocks or not any("1140V" in (item.get("content_text") or "") for item in blocks):
            raise RuntimeError("parsed evidence does not contain the UAT marker")
        self.api(
            "POST",
            f"/api/v1/tasks/{task['id']}/files/{file_item['id']}/parse-review",
            "人工接受解析质量",
            json={"decision": "accepted", "reason": "UAT 人工确认文本与原始资料一致"},
        )
        return task, file_item

    def full_audit_workflow(self, task: dict[str, Any]) -> dict[str, Any]:
        round_id = task["current_round_id"]
        models = self.api("GET", "/api/v1/settings/models", "读取模型配置")
        text_model = next(
            (item for item in models if item.get("model_kind") == "text" and item.get("status") == "active"),
            None,
        )
        if not text_model:
            raise RuntimeError("UAT requires an active text model configuration")
        connection = self.api(
            "POST",
            f"/api/v1/settings/models/{text_model['id']}/test",
            "真实文本模型连通性",
            json={},
        )
        if not connection.get("reachable") or not connection.get("request_id"):
            raise RuntimeError("text model connection test did not return a provider request")
        standards = self.api("GET", "/api/v1/standards", "读取正式标准目录")
        version = active_standard_version(standards)
        if not version:
            raise RuntimeError("UAT requires at least one active standard version")
        selected = self.api(
            "POST",
            f"/api/v1/rounds/{round_id}/standards",
            "选择固定标准版本",
            json={"standard_version_id": version["id"], "source_type": "uat_selection"},
        )
        self.api(
            "POST",
            f"/api/v1/rounds/{round_id}/standards/{selected['id']}/confirm",
            "人工确认适用标准",
        )
        dynamic_items = self.api("GET", f"/api/v1/rounds/{round_id}/dynamic-items", "生成动态原子审核项")
        for item in dynamic_items:
            self.api(
                "POST",
                f"/api/v1/rounds/{round_id}/dynamic-items/{item['id']}/confirm",
                f"确认动态项 {item.get('subject_code')}",
                json={"reason": "UAT 验证动态审核链路"},
            )
        self.api("POST", f"/api/v1/rounds/{round_id}/rules/assemble", "三层规则装配与版本快照", json={})
        audit = self.api("POST", f"/api/v1/rounds/{round_id}/audit/start", "启动真实审核", json={})
        if audit.get("job_id"):
            self.wait_job(audit["job_id"], "审核执行")
        executions = self.api("GET", f"/api/v1/rounds/{round_id}/rule-executions", "审核执行记录与证据结果")
        model_logs = self.api("GET", "/api/v1/settings/model-call-logs?limit=500", "模型调用审计记录")
        model_evidence = verify_real_model_evidence(executions, model_logs)
        self.checks.append(
            Check(
                "真实模型证据闭环",
                "passed",
                f"{model_evidence['dynamic_execution_count']} 个动态项，"
                f"{model_evidence['model_request_count']} 个已审计模型请求",
            )
        )
        issues = self.api("GET", f"/api/v1/issues?round_id={round_id}", "读取问题复核队列")
        for issue in issues:
            self.api(
                "POST",
                f"/api/v1/issues/{issue['id']}/confirm",
                f"人工最终确认问题 {issue.get('issue_code')}",
                json={"reason": "UAT 人工核对证据后确认", "manual_conclusion": "confirmed"},
            )
        rule_codes = list(
            dict.fromkeys(
                item.get("rule_code")
                for item in executions
                if item.get("rule_code") and not item.get("dynamic_item_id")
            )
        )
        if not rule_codes:
            raise RuntimeError("UAT requires at least one snapshotted static rule for local rerun")
        rerun = self.api(
            "POST",
            f"/api/v1/rounds/{round_id}/audit/local-rerun",
            "确认式局部重跑",
            json={
                "affected_rule_codes": rule_codes[:1],
                "reason": "UAT 验证受控局部重跑",
                "input_change": {"uat": True},
            },
        )
        if rerun.get("job_id"):
            self.wait_job(rerun["job_id"], "局部重跑")
        coverage = self.api("POST", f"/api/v1/rounds/{round_id}/coverage/check", "标准覆盖发布检查")
        if not coverage.get("can_publish"):
            raise RuntimeError(f"round is not publishable: {coverage.get('blockers')}")
        report = self.api(
            "POST",
            "/api/v1/reports",
            "生成正式报告",
            json={"round_id": round_id, "report_type": "formal", "conclusion": "through"},
        )
        published = self.api("POST", f"/api/v1/reports/{report['id']}/publish", "人工发布正式报告", json={})
        for artifact_type, signature in (("word", b"PK"), ("pdf", b"%PDF-")):
            response = self.client.get(f"/api/v1/reports/{report['id']}/artifacts/{artifact_type}/download")
            if not response.is_success or not response.content.startswith(signature):
                raise RuntimeError(f"published {artifact_type} artifact is invalid")
            self.checks.append(Check(f"下载 {artifact_type} 报告", "passed", f"{len(response.content)} bytes"))
        return published


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Production UAT evidence runner")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--login-name", required=True)
    parser.add_argument("--ca-file")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--full-audit", action="store_true")
    parser.add_argument("--confirm-model-cost", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.confirm_write:
        raise SystemExit("UAT creates acceptance records and requires --confirm-write")
    if args.full_audit and not args.confirm_model_cost:
        raise SystemExit("full audit calls configured external models and requires --confirm-model-cost")
    password = os.getenv("COAL_UAT_PASSWORD")
    if not password:
        raise SystemExit("COAL_UAT_PASSWORD is required")
    verify: bool | str = args.ca_file or True
    runner: UATRunner | None = None
    started = time.time()
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/"), verify=verify, timeout=60) as client:
            login = client.post("/api/v1/auth/login", json={"login_name": args.login_name, "password": password})
            login.raise_for_status()
            client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            runner = UATRunner(client, timeout_seconds=args.timeout_seconds)
            task, _file = runner.basic_workflow()
            if args.full_audit:
                runner.full_audit_workflow(task)
        status, error = "passed", None
    except Exception as exc:  # noqa: BLE001 - the evidence file must capture any failed UAT stage.
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    result = {
        "status": status,
        "mode": "full-audit" if args.full_audit else "basic",
        "base_url": args.base_url,
        "started_at_epoch": started,
        "elapsed_seconds": round(time.time() - started, 2),
        "checks": [asdict(item) for item in runner.checks] if runner else [],
        "error": error,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
