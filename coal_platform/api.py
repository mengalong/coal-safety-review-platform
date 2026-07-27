from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from coal_platform.auth import access_token_expires_at, create_access_token, require_admin, require_user
from coal_platform.request_context import get_trace_id
from coal_platform.schemas import (
    BasicInfoPayload,
    IssueUpdateRequest,
    LoginRequest,
    LoginResponse,
    ReportCreateRequest,
    RoundCreateRequest,
    RuleCreateRequest,
    RuleVersionCreateRequest,
    StandardCreateRequest,
    StandardRelationPayload,
    TaskCreateRequest,
)
from coal_platform.store_protocol import PlatformStore


def get_store(request: Request) -> PlatformStore:
    return request.app.state.store


def _ok(data, message: str = "success"):
    return {"code": "OK", "message": message, "data": data, "trace_id": get_trace_id()}


health_router = APIRouter()
auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_admin)])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_user)])
rounds_router = APIRouter(prefix="/rounds", tags=["rounds"], dependencies=[Depends(require_user)])
standards_router = APIRouter(prefix="/standards", tags=["standards"], dependencies=[Depends(require_user)])
rules_router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[Depends(require_user)])
executors_router = APIRouter(prefix="/executors", tags=["executors"], dependencies=[Depends(require_user)])
issues_router = APIRouter(prefix="/issues", tags=["issues"], dependencies=[Depends(require_user)])
reports_router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_user)])
settings_router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_user)])
jobs_router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_admin)])
monitoring_router = APIRouter(prefix="/monitoring", tags=["monitoring"], dependencies=[Depends(require_admin)])
logs_router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[Depends(require_admin)])


def _current_user(request: Request) -> dict:
    return request.state.current_user


def _ensure_task_access(request: Request, task: dict) -> None:
    user = _current_user(request)
    if user["role"] != "admin" and task.get("owner_user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="task is assigned to another reviewer")


def _operation_context(request: Request) -> dict[str, str]:
    return {"_operator_user_id": _current_user(request)["id"], "_trace_id": get_trace_id() or ""}


@health_router.get("/healthz")
def healthz() -> dict:
    return _ok({"status": "ok"})


@health_router.get("/readyz")
def readyz(request: Request) -> dict:
    if not request.app.state.store.healthcheck():
        raise HTTPException(status_code=503, detail="database is unavailable")
    return _ok({"status": "ready"})


@auth_router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request) -> dict:
    store = request.app.state.store
    user = store.authenticate(payload.login_name, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid login name or password")
    expires_at = access_token_expires_at()
    session_id = store.create_auth_session(user["id"], expires_at)
    return {
        "access_token": create_access_token(user, session_id, expires_at),
        "token_type": "bearer",
        "user_id": user["id"],
        "display_name": user["display_name"],
        "role": user["role"],
    }


@auth_router.post("/logout", dependencies=[Depends(require_user)])
def logout(request: Request) -> dict:
    store = request.app.state.store
    store.revoke_auth_session(request.state.auth_session_id, request.state.current_user["id"])
    return _ok(True)


@auth_router.get("/me")
def me(user: Annotated[dict, Depends(require_user)]) -> dict:
    return _ok(user)


@users_router.get("")
def list_users(request: Request) -> dict:
    return _ok(request.app.state.store.list_users())


@tasks_router.get("")
def list_tasks(request: Request, page: int = 1, page_size: int = 20, status: str | None = None) -> dict:
    store = request.app.state.store
    user = _current_user(request)
    items = store.list_tasks(owner_user_id=None if user["role"] == "admin" else user["id"])
    if status:
        items = [item for item in items if item["status"] == status]
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return _ok({"items": items[start:end], "page": page, "page_size": page_size, "total": total})


@tasks_router.post("")
def create_task(payload: TaskCreateRequest, request: Request) -> JSONResponse:
    data = payload.model_dump()
    user = _current_user(request)
    if user["role"] != "admin" or not data.get("owner_user_id"):
        data["owner_user_id"] = user["id"]
    data.update(_operation_context(request))
    task = request.app.state.store.create_task(data)
    return JSONResponse(status_code=201, content=_ok(task, "task created"))


@tasks_router.get("/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    task = request.app.state.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    return _ok(task)


@tasks_router.patch("/{task_id}/basic-info")
def update_basic_info(task_id: str, payload: BasicInfoPayload, request: Request) -> dict:
    existing = request.app.state.store.get_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, existing)
    data = payload.model_dump()
    data.update(_operation_context(request))
    task = request.app.state.store.update_task_basic_info(task_id, data)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return _ok(task)


@tasks_router.post("/{task_id}/rounds")
def create_round(task_id: str, payload: RoundCreateRequest, request: Request) -> JSONResponse:
    task = request.app.state.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    data = payload.model_dump()
    data.update(_operation_context(request))
    round_item = request.app.state.store.create_round(task_id, data)
    if not round_item:
        raise HTTPException(status_code=404, detail="task not found")
    return JSONResponse(status_code=201, content=_ok(round_item, "round created"))


@tasks_router.get("/{task_id}/rounds")
def list_rounds(task_id: str, request: Request) -> dict:
    task = request.app.state.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    return _ok(task.get("rounds", []))


@tasks_router.post("/{task_id}/files")
async def upload_files(task_id: str, request: Request, files: Annotated[list[UploadFile], File()]) -> dict:
    store = request.app.state.store
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    context = _operation_context(request)
    file_records = []
    for upload in files:
        content = await upload.read()
        file_name = Path(upload.filename or "unnamed").name
        storage_key = f"tasks/{task_id}/{uuid4().hex}/{file_name}"
        await run_in_threadpool(request.app.state.object_storage.put, storage_key, content, upload.content_type)
        file_records.append(
            {
                "file_name": file_name,
                "file_type": Path(file_name).suffix.lower().lstrip(".") or "other",
                "content_type": upload.content_type,
                "file_size": len(content),
                "sha256": sha256(content).hexdigest(),
                "storage_key": storage_key,
                **context,
            }
        )
    try:
        created = store.add_task_files(task_id, file_records)
    except Exception:
        for item in file_records:
            await run_in_threadpool(request.app.state.object_storage.delete, item["storage_key"])
        raise
    referenced_keys = {item["storage_key"] for item in created or []}
    for item in file_records:
        if item["storage_key"] not in referenced_keys:
            await run_in_threadpool(request.app.state.object_storage.delete, item["storage_key"])
    return _ok({"files": created})


@rounds_router.get("/{round_id}")
def get_round(round_id: str, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    return _ok(round_item)


@rounds_router.post("/{round_id}/standards")
def add_round_standard(round_id: str, payload: dict, request: Request) -> JSONResponse:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    item = request.app.state.store.add_standard_to_round(round_id, payload)
    if not item:
        raise HTTPException(status_code=404, detail="round not found")
    return JSONResponse(status_code=201, content=_ok(item, "round standard selected"))


@rounds_router.get("/{round_id}/dynamic-items")
def list_dynamic_items(round_id: str) -> dict:
    return _ok(
        [
            {
                "id": "demo_dynamic_1",
                "round_id": round_id,
                "source_clause": "MT/T 820-2023 5.3.2",
                "subject_name": "驱动装置额定功率",
                "applicability_status": "applicable",
                "execution_mode": "deterministic",
            }
        ]
    )


@rounds_router.get("/{round_id}/coverage")
def list_coverage(round_id: str) -> dict:
    return _ok(
        {
            "round_id": round_id,
            "summary": {
                "executed_passed": 18,
                "executed_failed": 2,
                "unable_to_determine": 1,
                "unsupported": 3,
                "to_confirm": 0,
            },
            "items": [],
        }
    )


@rounds_router.post("/{round_id}/audit/start")
def start_audit(round_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content=_ok({"round_id": round_id, "audit_run_id": "demo_run", "status": "queued"}, "audit queued"),
    )


@standards_router.get("")
def list_standards(request: Request) -> dict:
    return _ok(request.app.state.store.list_standards())


@standards_router.post("", dependencies=[Depends(require_admin)])
def create_standard(payload: StandardCreateRequest, request: Request) -> JSONResponse:
    standard = {
        "id": "temp",
        "standard_code": payload.standard_code,
        "standard_name": payload.standard_name,
        "standard_type": payload.standard_type,
        "scope_text": payload.scope_text,
        "status": "draft",
    }
    return JSONResponse(status_code=201, content=_ok(standard, "standard created"))


@standards_router.get("/{standard_id}")
def get_standard(standard_id: str, request: Request) -> dict:
    standard = request.app.state.store.get_standard(standard_id)
    if not standard:
        raise HTTPException(status_code=404, detail="standard not found")
    return _ok(standard)


@standards_router.post("/{standard_id}/versions/{version_id}/relations", dependencies=[Depends(require_admin)])
def add_standard_relation(standard_id: str, version_id: str, payload: StandardRelationPayload) -> dict:
    return _ok(
        {
            "standard_id": standard_id,
            "version_id": version_id,
            "relation_type": payload.relation_type,
            "relation_note": payload.relation_note,
        }
    )


@rules_router.get("")
def list_rules(request: Request) -> dict:
    return _ok(request.app.state.store.list_rules())


@rules_router.post("", dependencies=[Depends(require_admin)])
def create_rule(payload: RuleCreateRequest) -> dict:
    return _ok(
        {
            "id": "temp",
            "rule_code": payload.rule_code,
            "rule_name": payload.rule_name,
            "rule_type": payload.rule_type,
            "executor_code": payload.executor_code,
            "status": "draft",
        },
        "rule created",
    )


@rules_router.post("/{rule_id}/versions", dependencies=[Depends(require_admin)])
def create_rule_version(rule_id: str, payload: RuleVersionCreateRequest) -> dict:
    return _ok(
        {
            "rule_id": rule_id,
            "parameters": payload.parameters,
            "scope_files": payload.scope_files,
            "priority": payload.priority,
            "stage_code": payload.stage_code,
            "dependency_rule_codes": payload.dependency_rule_codes,
            "task_override_allowed": payload.task_override_allowed,
            "status": "draft",
        },
        "rule version created",
    )


@rules_router.post("/{rule_version_id}/publish", dependencies=[Depends(require_admin)])
def publish_rule(rule_version_id: str) -> dict:
    return _ok({"rule_version_id": rule_version_id, "status": "published"})


@executors_router.get("")
def list_executors(request: Request) -> dict:
    return _ok(request.app.state.store.list_executors())


@executors_router.get("/{executor_code}")
def get_executor(executor_code: str, request: Request) -> dict:
    executors = request.app.state.store.list_executors()
    for executor in executors:
        if executor["executor_code"] == executor_code:
            return _ok(executor)
    raise HTTPException(status_code=404, detail="executor not found")


@reports_router.get("")
def list_reports(request: Request) -> dict:
    return _ok(request.app.state.store.list_reports())


@reports_router.post("")
def create_report(payload: ReportCreateRequest, request: Request) -> JSONResponse:
    report = {
        "id": "temp",
        "report_no": "SH-2026-000000-REP-V1",
        "report_type": payload.report_type,
        "conclusion": payload.conclusion,
        "status": "draft",
    }
    return JSONResponse(status_code=201, content=_ok(report, "report created"))


@issues_router.get("")
def list_issues(request: Request, round_id: str | None = None) -> dict:
    return _ok(request.app.state.store.list_issues(round_id=round_id))


@issues_router.patch("/{issue_id}")
def update_issue(issue_id: str, payload: IssueUpdateRequest, request: Request) -> dict:
    issue = request.app.state.store.update_issue(issue_id, payload.model_dump())
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    return _ok(issue)


@issues_router.post("/{issue_id}/confirm")
def confirm_issue(issue_id: str, payload: dict, request: Request) -> dict:
    issue = request.app.state.store.set_issue_status(issue_id, "confirmed", payload.get("reason"))
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    return _ok(issue)


@issues_router.post("/{issue_id}/reject")
def reject_issue(issue_id: str, payload: dict, request: Request) -> dict:
    issue = request.app.state.store.set_issue_status(issue_id, "rejected", payload.get("reason"))
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    return _ok(issue)


@settings_router.get("/models", dependencies=[Depends(require_admin)])
def list_models(request: Request) -> dict:
    return _ok(request.app.state.store.list_model_configs())


@settings_router.get("/system-parameters", dependencies=[Depends(require_admin)])
def list_system_parameters(request: Request) -> dict:
    return _ok(request.app.state.store.list_system_parameters())


@settings_router.get("/issue-categories")
def list_issue_categories() -> dict:
    return _ok(
        [
            {"code": "cross_file_consistency", "name": "跨文件一致性"},
            {"code": "standard_compliance", "name": "标准符合性"},
            {"code": "controlled_parts", "name": "受控件"},
        ]
    )


@jobs_router.get("")
def list_jobs() -> dict:
    return _ok([])


@monitoring_router.get("")
def monitoring() -> dict:
    return _ok(
        {
            "queue_waiting": 4,
            "worker_online": 2,
            "executor_failure_rate": 0.02,
            "model_failure_rate": 0.01,
        }
    )


@logs_router.get("")
def list_logs(request: Request) -> dict:
    return _ok(request.app.state.store.list_operation_logs())


def register_routers(app: FastAPI, prefix: str = "/api/v1") -> None:
    app.include_router(health_router, prefix=prefix)
    app.include_router(auth_router, prefix=prefix)
    app.include_router(users_router, prefix=prefix)
    app.include_router(tasks_router, prefix=prefix)
    app.include_router(rounds_router, prefix=prefix)
    app.include_router(standards_router, prefix=prefix)
    app.include_router(rules_router, prefix=prefix)
    app.include_router(executors_router, prefix=prefix)
    app.include_router(issues_router, prefix=prefix)
    app.include_router(reports_router, prefix=prefix)
    app.include_router(settings_router, prefix=prefix)
    app.include_router(jobs_router, prefix=prefix)
    app.include_router(monitoring_router, prefix=prefix)
    app.include_router(logs_router, prefix=prefix)
