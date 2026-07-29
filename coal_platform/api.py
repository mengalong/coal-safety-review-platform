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
from coal_platform.rule_engine import FIXED_AUDIT_STAGES, RuleConfigurationError
from coal_platform.schemas import (
    BasicInfoPayload,
    ExecutionAttemptRequest,
    IssueUpdateRequest,
    LocalRerunRequest,
    LoginRequest,
    LoginResponse,
    ReportCreateRequest,
    RoundCreateRequest,
    RoundRuleAssemblyRequest,
    RuleCreateRequest,
    RulePackCreateRequest,
    RulePackUpdateRequest,
    RuleVersionCreateRequest,
    StandardCreateRequest,
    StandardParseRevisionCreateRequest,
    StandardRelationPayload,
    StandardVersionAbolishRequest,
    StandardVersionCreateRequest,
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
standard_versions_router = APIRouter(prefix="/standard-versions", tags=["standard-versions"], dependencies=[Depends(require_user)])
standard_parse_revisions_router = APIRouter(
    prefix="/standard-parse-revisions", tags=["standard-parse-revisions"], dependencies=[Depends(require_user)]
)
rules_router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[Depends(require_user)])
rule_versions_router = APIRouter(prefix="/rule-versions", tags=["rule-versions"], dependencies=[Depends(require_user)])
rule_executions_router = APIRouter(prefix="/rule-executions", tags=["rule-executions"], dependencies=[Depends(require_user)])
rule_packs_router = APIRouter(prefix="/rule-packs", tags=["rule-packs"], dependencies=[Depends(require_user)])
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


def _ensure_issue_access(request: Request, issue: dict) -> None:
    round_item = request.app.state.store.get_round(issue.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if not task:
        raise HTTPException(status_code=404, detail="issue round not found")
    _ensure_task_access(request, task)


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
    data = dict(payload)
    data.update(_operation_context(request))
    item = request.app.state.store.add_standard_to_round(round_id, data)
    if not item:
        raise HTTPException(status_code=404, detail="round not found")
    return JSONResponse(status_code=201, content=_ok(item, "round standard selected"))


@rounds_router.get("/{round_id}/standards")
def list_round_standards(round_id: str, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    items = request.app.state.store.list_round_standards(round_id)
    if items is None:
        raise HTTPException(status_code=404, detail="round not found")
    return _ok(items)


@rounds_router.post("/{round_id}/standards/{round_standard_id}/confirm")
def confirm_round_standard(round_id: str, round_standard_id: str, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    item = request.app.state.store.confirm_round_standard(
        round_id,
        round_standard_id,
        _operation_context(request),
    )
    if not item:
        raise HTTPException(status_code=404, detail="round standard not found")
    return _ok(item, "round standard confirmed")


@rounds_router.get("/{round_id}/rules")
def list_round_rules(round_id: str, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    rules = request.app.state.store.list_round_rules(round_id)
    if rules is None:
        raise HTTPException(status_code=404, detail="round not found")
    return _ok(rules)


@rounds_router.post("/{round_id}/rules/assemble")
def assemble_round_rules(round_id: str, payload: RoundRuleAssemblyRequest, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    data = payload.model_dump()
    data.update(_operation_context(request))
    try:
        snapshot = request.app.state.store.assemble_round_rules(round_id, data)
    except RuleConfigurationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if not snapshot:
        raise HTTPException(status_code=404, detail="round not found")
    return _ok(snapshot, "round rules assembled")


@rounds_router.get("/{round_id}/dynamic-items")
def list_dynamic_items(round_id: str, request: Request) -> dict:
    item = request.app.state.store.get_round(round_id)
    if not item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    return _ok(request.app.state.store.list_dynamic_items(round_id) or [])


@rounds_router.get("/{round_id}/coverage")
def list_coverage(round_id: str, request: Request) -> dict:
    item = request.app.state.store.get_round(round_id)
    if not item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    coverage = request.app.state.store.list_coverage(round_id)
    return _ok(coverage or {"round_id": round_id, "summary": {}, "items": []})


@rounds_router.post("/{round_id}/audit/start")
def start_audit(round_id: str, request: Request) -> JSONResponse:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    run = request.app.state.store.start_audit(round_id, _operation_context(request))
    if not run:
        raise HTTPException(status_code=404, detail="round not found")
    return JSONResponse(status_code=202, content=_ok(run, "audit queued"))


@rounds_router.post("/{round_id}/audit/local-rerun")
def local_rerun(round_id: str, payload: LocalRerunRequest, request: Request) -> JSONResponse:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    data = payload.model_dump()
    data.update(_operation_context(request))
    try:
        result = request.app.state.store.local_rerun(round_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="round not found")
    return JSONResponse(status_code=202, content=_ok(result, "local rerun queued"))


@rounds_router.get("/{round_id}/audit-runs")
def list_audit_runs(round_id: str, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    return _ok(request.app.state.store.list_audit_runs(round_id) or [])


@rounds_router.get("/{round_id}/rule-executions")
def list_rule_executions(round_id: str, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    return _ok(request.app.state.store.list_rule_executions(round_id) or [])


@rule_executions_router.get("/{execution_id}")
def get_rule_execution(execution_id: str, request: Request) -> dict:
    item = request.app.state.store.get_rule_execution(execution_id)
    if not item:
        raise HTTPException(status_code=404, detail="rule execution not found")
    round_item = request.app.state.store.get_round(item.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if task:
        _ensure_task_access(request, task)
    return _ok(item)


@rule_executions_router.get("/{execution_id}/attempts")
def list_execution_attempts(execution_id: str, request: Request) -> dict:
    item = request.app.state.store.get_rule_execution(execution_id)
    if not item:
        raise HTTPException(status_code=404, detail="rule execution not found")
    round_item = request.app.state.store.get_round(item.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if task:
        _ensure_task_access(request, task)
    return _ok(request.app.state.store.list_execution_attempts(execution_id) or [])


@rule_executions_router.post("/{execution_id}/attempts")
def record_execution_attempt(
    execution_id: str, payload: ExecutionAttemptRequest, request: Request
) -> dict:
    item = request.app.state.store.get_rule_execution(execution_id)
    if not item:
        raise HTTPException(status_code=404, detail="rule execution not found")
    round_item = request.app.state.store.get_round(item.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if task:
        _ensure_task_access(request, task)
    data = payload.model_dump()
    data.update(_operation_context(request))
    try:
        result = request.app.state.store.record_execution_attempt(execution_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="rule execution not found")
    return _ok(result, "execution attempt recorded")


@rule_executions_router.post("/{execution_id}/retry")
def retry_rule_execution(execution_id: str, request: Request) -> dict:
    item = request.app.state.store.get_rule_execution(execution_id)
    if not item:
        raise HTTPException(status_code=404, detail="rule execution not found")
    round_item = request.app.state.store.get_round(item.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if task:
        _ensure_task_access(request, task)
    try:
        result = request.app.state.store.retry_rule_execution(execution_id, _operation_context(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="rule execution not found")
    return _ok(result, "rule execution queued for retry")


@standards_router.get("")
def list_standards(request: Request) -> dict:
    return _ok(request.app.state.store.list_standards())


@standards_router.get("/search")
def search_standards(request: Request, q: str = "") -> dict:
    query = q.strip().lower()
    items = request.app.state.store.list_standards()
    if query:
        items = [
            item
            for item in items
            if query in item.get("standard_code", "").lower()
            or query in item.get("standard_name", "").lower()
            or query in (item.get("scope_text") or "").lower()
        ]
    return _ok(items)


@standards_router.post("", dependencies=[Depends(require_admin)])
def create_standard(payload: StandardCreateRequest, request: Request) -> JSONResponse:
    data = payload.model_dump(mode="json")
    data.update(_operation_context(request))
    standard = request.app.state.store.create_standard(data)
    if not standard:
        raise HTTPException(status_code=409, detail="standard code already exists")
    return JSONResponse(status_code=201, content=_ok(standard, "standard created"))


@standards_router.post("/{standard_id}/versions", dependencies=[Depends(require_admin)])
def create_standard_version(standard_id: str, payload: StandardVersionCreateRequest, request: Request) -> JSONResponse:
    data = payload.model_dump()
    data.update(_operation_context(request))
    if not request.app.state.store.get_standard(standard_id):
        raise HTTPException(status_code=404, detail="standard not found")
    version = request.app.state.store.create_standard_version(standard_id, data)
    if not version:
        raise HTTPException(status_code=409, detail="standard version already exists")
    return JSONResponse(status_code=201, content=_ok(version, "standard version created"))


@standards_router.get("/{standard_id}")
def get_standard(standard_id: str, request: Request) -> dict:
    standard = request.app.state.store.get_standard(standard_id)
    if not standard:
        raise HTTPException(status_code=404, detail="standard not found")
    return _ok(standard)


@standards_router.get("/{standard_id}/versions")
def list_standard_versions(standard_id: str, request: Request) -> dict:
    if not request.app.state.store.get_standard(standard_id):
        raise HTTPException(status_code=404, detail="standard not found")
    return _ok(request.app.state.store.list_standard_versions(standard_id))


@standard_versions_router.get("/{standard_version_id}/parse-revisions")
def list_standard_parse_revisions(standard_version_id: str, request: Request) -> dict:
    revisions = request.app.state.store.list_standard_parse_revisions(standard_version_id)
    if revisions is None:
        raise HTTPException(status_code=404, detail="standard version not found")
    return _ok(revisions)


@standard_versions_router.post("/{standard_version_id}/parse-revisions", dependencies=[Depends(require_admin)])
def create_standard_parse_revision(
    standard_version_id: str, payload: StandardParseRevisionCreateRequest, request: Request
) -> JSONResponse:
    data = payload.model_dump()
    data.update(_operation_context(request))
    revision = request.app.state.store.create_standard_parse_revision(standard_version_id, data)
    if not revision:
        raise HTTPException(status_code=404, detail="standard version not found")
    return JSONResponse(status_code=201, content=_ok(revision, "standard parse revision created"))


@standard_versions_router.get("/{standard_version_id}/compare/{other_version_id}")
def compare_standard_versions(standard_version_id: str, other_version_id: str, request: Request) -> dict:
    comparison = request.app.state.store.compare_standard_versions(standard_version_id, other_version_id)
    if not comparison:
        raise HTTPException(status_code=404, detail="standard version not found")
    return _ok(comparison)


@standard_versions_router.get("/{standard_version_id}")
def get_standard_version(standard_version_id: str, request: Request) -> dict:
    version = request.app.state.store.get_standard_version(standard_version_id)
    if not version:
        raise HTTPException(status_code=404, detail="standard version not found")
    return _ok(version)


@standard_versions_router.post("/{standard_version_id}/publish", dependencies=[Depends(require_admin)])
def publish_standard_version(standard_version_id: str, request: Request) -> dict:
    version = request.app.state.store.publish_standard_version(
        standard_version_id,
        _operation_context(request),
    )
    if not version:
        raise HTTPException(status_code=404, detail="standard version not found")
    return _ok(version, "standard version published")


@standard_versions_router.post("/{standard_version_id}/abolish", dependencies=[Depends(require_admin)])
def abolish_standard_version(
    standard_version_id: str, payload: StandardVersionAbolishRequest, request: Request
) -> dict:
    data = payload.model_dump()
    data.update(_operation_context(request))
    version = request.app.state.store.abolish_standard_version(standard_version_id, data)
    if not version:
        raise HTTPException(status_code=404, detail="standard version or superseding version not found")
    return _ok(version, "standard version abolished")


@standard_versions_router.get("/{standard_version_id}/clauses")
def list_standard_clauses(standard_version_id: str, request: Request) -> dict:
    clauses = request.app.state.store.list_standard_clauses(standard_version_id)
    if clauses is None:
        raise HTTPException(status_code=404, detail="standard version not found")
    return _ok(clauses)


@standard_parse_revisions_router.post("/{revision_id}/publish", dependencies=[Depends(require_admin)])
def publish_standard_parse_revision(revision_id: str, request: Request) -> dict:
    revision = request.app.state.store.publish_standard_parse_revision(revision_id, _operation_context(request))
    if not revision:
        raise HTTPException(status_code=404, detail="standard parse revision not found")
    return _ok(revision, "standard parse revision published")


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
def create_rule(payload: RuleCreateRequest, request: Request) -> JSONResponse:
    data = payload.model_dump()
    data.update(_operation_context(request))
    rule = request.app.state.store.create_rule(data)
    if not rule:
        raise HTTPException(status_code=409, detail="rule code or executor already exists")
    return JSONResponse(status_code=201, content=_ok(rule, "rule created"))


@rules_router.get("/{rule_id}")
def get_rule(rule_id: str, request: Request) -> dict:
    rule = request.app.state.store.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="rule not found")
    return _ok(rule)


@rules_router.post("/{rule_id}/versions", dependencies=[Depends(require_admin)])
def create_rule_version(rule_id: str, payload: RuleVersionCreateRequest, request: Request) -> JSONResponse:
    data = payload.model_dump()
    data.update(_operation_context(request))
    try:
        version = request.app.state.store.create_rule_version(rule_id, data)
    except RuleConfigurationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if not version:
        raise HTTPException(status_code=404, detail="rule or executor version not found")
    return JSONResponse(status_code=201, content=_ok(version, "rule version created"))


@rules_router.get("/{rule_id}/versions")
def list_rule_versions(rule_id: str, request: Request) -> dict:
    versions = request.app.state.store.list_rule_versions(rule_id)
    if versions is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return _ok(versions)


@rule_versions_router.get("/{rule_version_id}")
def get_rule_version(rule_version_id: str, request: Request) -> dict:
    version = request.app.state.store.get_rule_version(rule_version_id)
    if not version:
        raise HTTPException(status_code=404, detail="rule version not found")
    return _ok(version)


@rule_versions_router.post("/{rule_version_id}/validate", dependencies=[Depends(require_admin)])
def validate_rule_version(rule_version_id: str, request: Request) -> dict:
    result = request.app.state.store.validate_rule_version(rule_version_id)
    if not result:
        raise HTTPException(status_code=404, detail="rule version not found")
    return _ok(result)


@rule_versions_router.post("/{rule_version_id}/publish", dependencies=[Depends(require_admin)])
@rules_router.post("/{rule_version_id}/publish", dependencies=[Depends(require_admin)])
def publish_rule(rule_version_id: str, request: Request) -> dict:
    try:
        version = request.app.state.store.publish_rule_version(rule_version_id, _operation_context(request))
    except RuleConfigurationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if not version:
        raise HTTPException(status_code=404, detail="rule version not found")
    return _ok(version, "rule version published")


@rule_packs_router.get("")
def list_rule_packs(request: Request) -> dict:
    return _ok(request.app.state.store.list_rule_packs())


@rule_packs_router.post("", dependencies=[Depends(require_admin)])
def create_rule_pack(payload: RulePackCreateRequest, request: Request) -> JSONResponse:
    data = payload.model_dump()
    data.update(_operation_context(request))
    try:
        pack = request.app.state.store.create_rule_pack(data)
    except RuleConfigurationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if not pack:
        raise HTTPException(status_code=409, detail="rule pack code already exists")
    return JSONResponse(status_code=201, content=_ok(pack, "rule pack created"))


@rule_packs_router.patch("/{pack_id}", dependencies=[Depends(require_admin)])
def update_rule_pack(pack_id: str, payload: RulePackUpdateRequest, request: Request) -> dict:
    data = payload.model_dump(exclude_unset=True)
    data.update(_operation_context(request))
    try:
        pack = request.app.state.store.update_rule_pack(pack_id, data)
    except RuleConfigurationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if not pack:
        raise HTTPException(status_code=404, detail="rule pack not found")
    return _ok(pack, "rule pack updated")


@executors_router.get("")
def list_executors(request: Request) -> dict:
    return _ok(request.app.state.store.list_executors())


@executors_router.get("/{executor_code}/versions")
def list_executor_versions(executor_code: str, request: Request) -> dict:
    versions = request.app.state.store.list_executor_versions(executor_code)
    if versions is None:
        raise HTTPException(status_code=404, detail="executor not found")
    return _ok(versions)


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
    if round_id:
        round_item = request.app.state.store.get_round(round_id)
        if not round_item:
            raise HTTPException(status_code=404, detail="round not found")
        task = request.app.state.store.get_task(round_item.get("task_id", ""))
        if task:
            _ensure_task_access(request, task)
    items = request.app.state.store.list_issues(round_id=round_id)
    if not round_id and _current_user(request)["role"] != "admin":
        visible = []
        for item in items:
            round_item = request.app.state.store.get_round(item.get("round_id", ""))
            task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
            if task and task.get("owner_user_id") == _current_user(request)["id"]:
                visible.append(item)
        items = visible
    return _ok(items)


@issues_router.patch("/{issue_id}")
def update_issue(issue_id: str, payload: IssueUpdateRequest, request: Request) -> dict:
    issue = request.app.state.store.get_issue(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    _ensure_issue_access(request, issue)
    data = payload.model_dump()
    data.update(_operation_context(request))
    issue = request.app.state.store.update_issue(issue_id, data)
    return _ok(issue)


@issues_router.post("/{issue_id}/confirm")
def confirm_issue(issue_id: str, payload: dict, request: Request) -> dict:
    issue = request.app.state.store.get_issue(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    _ensure_issue_access(request, issue)
    issue = request.app.state.store.set_issue_status(
        issue_id, "confirmed", payload.get("reason"), _operation_context(request)
    )
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    return _ok(issue)


@issues_router.post("/{issue_id}/reject")
def reject_issue(issue_id: str, payload: dict, request: Request) -> dict:
    issue = request.app.state.store.get_issue(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    _ensure_issue_access(request, issue)
    issue = request.app.state.store.set_issue_status(
        issue_id, "rejected", payload.get("reason"), _operation_context(request)
    )
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


@settings_router.get("/audit-stages")
def list_audit_stages() -> dict:
    return _ok(list(FIXED_AUDIT_STAGES))


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
    app.include_router(standard_versions_router, prefix=prefix)
    app.include_router(standard_parse_revisions_router, prefix=prefix)
    app.include_router(rules_router, prefix=prefix)
    app.include_router(rule_versions_router, prefix=prefix)
    app.include_router(rule_executions_router, prefix=prefix)
    app.include_router(rule_packs_router, prefix=prefix)
    app.include_router(executors_router, prefix=prefix)
    app.include_router(issues_router, prefix=prefix)
    app.include_router(reports_router, prefix=prefix)
    app.include_router(settings_router, prefix=prefix)
    app.include_router(jobs_router, prefix=prefix)
    app.include_router(monitoring_router, prefix=prefix)
    app.include_router(logs_router, prefix=prefix)
