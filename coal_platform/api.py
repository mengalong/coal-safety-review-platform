from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from coal_platform.auth import access_token_expires_at, create_access_token, require_admin, require_user
from coal_platform.config import get_settings
from coal_platform.document_parser import MAX_FILE_BYTES, DocumentParseError, render_pdf_region
from coal_platform.executor_runtime import process_queue_job, run_rule_execution
from coal_platform.model_gateway import ModelGatewayError
from coal_platform.report_renderer import render_docx, render_pdf
from coal_platform.request_context import get_trace_id
from coal_platform.rule_engine import FIXED_AUDIT_STAGES, RuleConfigurationError
from coal_platform.schemas import (
    BasicInfoPayload,
    DynamicItemDecisionRequest,
    ExecutionAttemptRequest,
    IssueCategoryRequest,
    IssueUpdateRequest,
    LocalRerunRequest,
    LoginRequest,
    LoginResponse,
    ManualIssueCreateRequest,
    ModelConfigRequest,
    ModelConfigUpdateRequest,
    ParsedBlockUpdateRequest,
    ParseReviewRequest,
    PasswordChangeRequest,
    ReportCreateRequest,
    ReportTemplateRequest,
    RoundCreateRequest,
    RoundRuleAssemblyRequest,
    RuleCreateRequest,
    RulePackCreateRequest,
    RulePackUpdateRequest,
    RuleTestRunRequest,
    RuleVersionCreateRequest,
    StandardCreateRequest,
    StandardParseRevisionCreateRequest,
    StandardRelationPayload,
    StandardVersionAbolishRequest,
    StandardVersionCreateRequest,
    SystemParameterRequest,
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


def _dispatch_job_if_enabled(request: Request, job_id: str | None) -> dict[str, str] | None:
    if not job_id or not get_settings().dispatch_jobs:
        return None
    try:
        from coal_platform.worker import dispatch_queue_job

        return {"celery_task_id": dispatch_queue_job(job_id)}
    except Exception as exc:  # noqa: BLE001 - dispatch failure must remain visible on the queue job
        request.app.state.store.update_queue_job(
            job_id, {"status": "failed", "error": {"code": "JOB_DISPATCH_FAILED", "message": str(exc)}}
        )
        return {"dispatch_error": str(exc)}


def _queue_file_parse(request: Request, task_id: str, file_id: str) -> dict | None:
    job = request.app.state.store.create_task_file_parse_job(task_id, file_id, _operation_context(request))
    if not job:
        return None
    if not job.pop("reused", False):
        dispatch = _dispatch_job_if_enabled(request, job["id"])
        if dispatch:
            job.update(dispatch)
    return job


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


@auth_router.patch("/password", dependencies=[Depends(require_user)])
def change_password(payload: PasswordChangeRequest, request: Request) -> dict:
    changed = request.app.state.store.change_password(
        request.state.current_user["id"],
        payload.current_password,
        payload.new_password,
        _operation_context(request),
    )
    if not changed:
        raise HTTPException(status_code=400, detail="current password is incorrect or new password is unchanged")
    return _ok(True, "password changed; all sessions revoked")


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
    try:
        for upload in files:
            content = await upload.read()
            if len(content) > MAX_FILE_BYTES:
                raise HTTPException(status_code=413, detail=f"file exceeds {MAX_FILE_BYTES} byte upload limit")
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
    except Exception:
        for item in file_records:
            await run_in_threadpool(request.app.state.object_storage.delete, item["storage_key"])
        raise
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
    jobs = [job for item in created or [] if (job := _queue_file_parse(request, task_id, item["id"]))]
    return _ok({"files": created, "parse_jobs": jobs})


@tasks_router.patch("/{task_id}/files/{file_id}")
async def update_file_metadata(task_id: str, file_id: str, request: Request) -> dict:
    store = request.app.state.store
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    payload = await request.json()
    payload.update(_operation_context(request))
    updated = store.update_task_file(task_id, file_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="file not found")
    return _ok(updated)


@tasks_router.put("/{task_id}/files/{file_id}")
async def replace_file(task_id: str, file_id: str, request: Request, file: Annotated[UploadFile, File()]) -> dict:
    store = request.app.state.store
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    old_file = next((item for item in task.get("files", []) if item.get("id") == file_id), None)
    if not old_file or old_file.get("status") == "deleted":
        raise HTTPException(status_code=404, detail="file not found")
    content = await file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"file exceeds {MAX_FILE_BYTES} byte upload limit")
    file_name = Path(file.filename or "unnamed").name
    storage_key = f"tasks/{task_id}/{uuid4().hex}/{file_name}"
    await run_in_threadpool(request.app.state.object_storage.put, storage_key, content, file.content_type)
    payload = {
        "file_name": file_name, "file_type": Path(file_name).suffix.lower().lstrip(".") or "other",
        "content_type": file.content_type, "file_size": len(content), "sha256": sha256(content).hexdigest(),
        "storage_key": storage_key, **_operation_context(request),
    }
    try:
        updated = store.replace_task_file(task_id, file_id, payload)
    except ValueError as exc:
        await run_in_threadpool(request.app.state.object_storage.delete, storage_key)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated:
        await run_in_threadpool(request.app.state.object_storage.delete, storage_key)
        raise HTTPException(status_code=404, detail="file not found")
    old_key = old_file.get("storage_key")
    if old_key and old_key != storage_key:
        await run_in_threadpool(request.app.state.object_storage.delete, old_key)
    for asset in (old_file.get("parse_summary") or {}).get("page_assets", []):
        if asset.get("thumbnail_storage_key"):
            await run_in_threadpool(request.app.state.object_storage.delete, asset["thumbnail_storage_key"])
    parse_job = _queue_file_parse(request, task_id, file_id)
    if parse_job:
        updated["parse_job"] = parse_job
    return _ok(updated)


@tasks_router.delete("/{task_id}/files/{file_id}")
async def delete_file(task_id: str, file_id: str, request: Request) -> dict:
    store = request.app.state.store
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    old_file = next((item for item in task.get("files", []) if item.get("id") == file_id), None)
    deleted = store.delete_task_file(task_id, file_id, _operation_context(request))
    if not deleted:
        raise HTTPException(status_code=404, detail="file not found")
    for asset in (old_file or {}).get("parse_summary", {}).get("page_assets", []):
        if asset.get("thumbnail_storage_key"):
            await run_in_threadpool(request.app.state.object_storage.delete, asset["thumbnail_storage_key"])
    return _ok(deleted)


@tasks_router.post("/{task_id}/files/{file_id}/retry-parse")
def retry_file_parse(task_id: str, file_id: str, request: Request) -> dict:
    store = request.app.state.store
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    retried = store.retry_task_file_parse(task_id, file_id, _operation_context(request))
    if not retried:
        raise HTTPException(status_code=404, detail="file not found")
    parse_job = _queue_file_parse(request, task_id, file_id)
    if parse_job:
        retried["parse_job"] = parse_job
    return _ok(retried)


@tasks_router.get("/{task_id}/files/{file_id}/blocks")
def list_file_blocks(task_id: str, file_id: str, request: Request) -> dict:
    store = request.app.state.store
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    blocks = store.list_task_file_blocks(task_id, file_id)
    if blocks is None:
        raise HTTPException(status_code=404, detail="file not found")
    return _ok(blocks)


@tasks_router.patch("/{task_id}/files/{file_id}/blocks/{block_id}")
def update_file_block(task_id: str, file_id: str, block_id: str, payload: ParsedBlockUpdateRequest, request: Request) -> dict:
    task = request.app.state.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    data = payload.model_dump(exclude_unset=True)
    data.update(_operation_context(request))
    block = request.app.state.store.update_task_file_block(task_id, file_id, block_id, data)
    if not block:
        raise HTTPException(status_code=409, detail="parsed block is not editable")
    return _ok(block, "parsed block revised")


@tasks_router.post("/{task_id}/files/{file_id}/parse-review")
def review_file_parse(task_id: str, file_id: str, payload: ParseReviewRequest, request: Request) -> dict:
    task = request.app.state.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    data = payload.model_dump()
    data.update(_operation_context(request))
    reviewed = request.app.state.store.review_task_file_parse(task_id, file_id, data)
    if not reviewed:
        raise HTTPException(status_code=409, detail="file parse is not reviewable")
    if payload.decision == "reparse":
        retried = request.app.state.store.retry_task_file_parse(task_id, file_id, _operation_context(request))
        job = _queue_file_parse(request, task_id, file_id)
        if retried and job:
            reviewed = {**retried, "parse_job": job}
    return _ok(reviewed, "parse review recorded")


@tasks_router.get("/{task_id}/files/{file_id}/pages")
def list_file_pages(task_id: str, file_id: str, request: Request) -> dict:
    store = request.app.state.store
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    pages = store.list_task_file_pages(task_id, file_id)
    if pages is None:
        raise HTTPException(status_code=404, detail="file not found")
    return _ok(pages)


@tasks_router.get("/{task_id}/files/{file_id}/pages/{page_no}/thumbnail")
def get_file_thumbnail(task_id: str, file_id: str, page_no: int, request: Request) -> Response:
    task = request.app.state.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    page_assets = request.app.state.store.list_task_file_pages(task_id, file_id)
    asset = next((item for item in page_assets or [] if item.get("page_no") == page_no), None)
    if not asset or not asset.get("thumbnail_storage_key"):
        raise HTTPException(status_code=404, detail="page thumbnail not found")
    content = request.app.state.object_storage.get(asset["thumbnail_storage_key"])
    if content is None:
        raise HTTPException(status_code=404, detail="page thumbnail object not found")
    return Response(content=content, media_type="image/png")


@tasks_router.get("/{task_id}/files/{file_id}/pages/{page_no}/region")
def get_file_region(
    task_id: str,
    file_id: str,
    page_no: int,
    request: Request,
    x: float = Query(ge=0),
    y: float = Query(ge=0),
    width: float = Query(gt=0),
    height: float = Query(gt=0),
) -> Response:
    task = request.app.state.store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    page_assets = request.app.state.store.list_task_file_pages(task_id, file_id)
    asset = next((item for item in page_assets or [] if item.get("page_no") == page_no), None)
    if not asset:
        raise HTTPException(status_code=404, detail="page not found")
    file_item = next((item for item in task.get("files", []) if item.get("id") == file_id), None)
    is_pdf = bool(
        file_item
        and (
            Path(file_item.get("file_name", "")).suffix.lower() == ".pdf"
            or file_item.get("content_type") == "application/pdf"
        )
    )
    if not is_pdf:
        raise HTTPException(status_code=409, detail="region evidence is only available for PDF files")
    content = request.app.state.object_storage.get(file_item["storage_key"])
    if content is None:
        raise HTTPException(status_code=404, detail="source PDF object not found")
    try:
        region = render_pdf_region(content, page_no, {"x": x, "y": y, "width": width, "height": height})
    except DocumentParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=region, media_type="image/png")


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


def _decide_dynamic_item(round_id: str, item_id: str, decision: str, payload: DynamicItemDecisionRequest, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    data = payload.model_dump()
    data.update(_operation_context(request))
    try:
        item = request.app.state.store.decide_dynamic_item(round_id, item_id, decision, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="dynamic item not found")
    return _ok(item, "dynamic item updated")


@rounds_router.post("/{round_id}/dynamic-items/{item_id}/confirm")
def confirm_dynamic_item(round_id: str, item_id: str, payload: DynamicItemDecisionRequest, request: Request) -> dict:
    return _decide_dynamic_item(round_id, item_id, "applicable", payload, request)


@rounds_router.post("/{round_id}/dynamic-items/{item_id}/exclude")
def exclude_dynamic_item(round_id: str, item_id: str, payload: DynamicItemDecisionRequest, request: Request) -> dict:
    return _decide_dynamic_item(round_id, item_id, "not_applicable", payload, request)


@rounds_router.post("/{round_id}/dynamic-items/{item_id}/manual")
def manual_dynamic_item(round_id: str, item_id: str, payload: DynamicItemDecisionRequest, request: Request) -> dict:
    return _decide_dynamic_item(round_id, item_id, "manual_review", payload, request)


@rounds_router.post("/{round_id}/coverage/check")
def check_coverage(round_id: str, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    result = request.app.state.store.check_round_publishability(round_id)
    return _ok(result or {"round_id": round_id, "can_publish": False, "blockers": []})


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
    dispatch = _dispatch_job_if_enabled(request, run.get("job_id"))
    if dispatch:
        run["dispatch"] = dispatch
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


@rounds_router.get("/{round_id}/audit/progress")
def get_audit_progress(round_id: str, request: Request) -> dict:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    return _ok(request.app.state.store.get_audit_progress(round_id))


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


@rule_executions_router.post("/{execution_id}/run", dependencies=[Depends(require_admin)])
def run_execution(execution_id: str, request: Request) -> dict:
    result = run_rule_execution(request.app.state.store, execution_id)
    if not result:
        raise HTTPException(status_code=404, detail="rule execution not found")
    return _ok(result, "rule execution completed")


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


@rule_versions_router.post("/{rule_version_id}/disable", dependencies=[Depends(require_admin)])
def disable_rule(rule_version_id: str, request: Request) -> dict:
    version = request.app.state.store.disable_rule_version(rule_version_id, _operation_context(request))
    if not version:
        raise HTTPException(status_code=404, detail="rule version not found or archived")
    return _ok(version, "rule version disabled")


@rule_versions_router.post("/{rule_version_id}/copy", dependencies=[Depends(require_admin)])
def copy_rule(rule_version_id: str, request: Request) -> JSONResponse:
    version = request.app.state.store.copy_rule_version(rule_version_id, _operation_context(request))
    if not version:
        raise HTTPException(status_code=404, detail="rule version not found")
    return JSONResponse(status_code=201, content=_ok(version, "rule version copied"))


@rule_versions_router.post("/{rule_version_id}/test-runs", dependencies=[Depends(require_admin)])
def create_rule_test_run(
    rule_version_id: str, payload: RuleTestRunRequest, request: Request
) -> JSONResponse:
    data = payload.model_dump()
    data.update(_operation_context(request))
    try:
        job = request.app.state.store.create_rule_test_run(rule_version_id, data)
    except RuleConfigurationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    if not job:
        raise HTTPException(status_code=404, detail="rule version not found")
    dispatch = _dispatch_job_if_enabled(request, job.get("id"))
    if dispatch:
        job["dispatch"] = dispatch
    return JSONResponse(status_code=202, content=_ok(job, "rule test run queued"))


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
    items = request.app.state.store.list_reports()
    if _current_user(request)["role"] != "admin":
        visible = []
        for item in items:
            if not item.get("round_id"):
                continue
            round_item = request.app.state.store.get_round(item["round_id"])
            task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
            if task and task.get("owner_user_id") == _current_user(request)["id"]:
                visible.append(item)
        items = visible
    return _ok(items)


@reports_router.get("/{report_id}")
def get_report(report_id: str, request: Request) -> dict:
    report = request.app.state.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    round_item = request.app.state.store.get_round(report.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if not task:
        raise HTTPException(status_code=404, detail="report round not found")
    _ensure_task_access(request, task)
    return _ok(report)


@reports_router.get("/{report_id}/artifacts")
def list_report_artifacts(report_id: str, request: Request) -> dict:
    report = request.app.state.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    round_item = request.app.state.store.get_round(report.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if not task:
        raise HTTPException(status_code=404, detail="report round not found")
    _ensure_task_access(request, task)
    artifacts = []
    for artifact_type, key, content_type in (
        ("word", report.get("word_object_key"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("pdf", report.get("pdf_object_key"), "application/pdf"),
    ):
        if key:
            artifacts.append({
                "artifact_type": artifact_type,
                "file_name": key.rsplit("/", 1)[-1],
                "object_key": key,
                "content_type": content_type,
            })
    return _ok(artifacts)


@reports_router.get("/{report_id}/artifacts/{artifact_type}/download")
async def download_report_artifact(report_id: str, artifact_type: str, request: Request) -> Response:
    report = request.app.state.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    if report.get("status") != "published":
        raise HTTPException(status_code=409, detail="report is not published")
    round_item = request.app.state.store.get_round(report.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if not task:
        raise HTTPException(status_code=404, detail="report round not found")
    _ensure_task_access(request, task)
    artifact = {
        "word": (report.get("word_object_key"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "pdf": (report.get("pdf_object_key"), "application/pdf"),
    }.get(artifact_type)
    if not artifact or not artifact[0]:
        raise HTTPException(status_code=404, detail="report artifact not found")
    content = await run_in_threadpool(request.app.state.object_storage.get, artifact[0])
    if content is None:
        raise HTTPException(status_code=404, detail="report artifact is not available")
    file_name = artifact[0].rsplit("/", 1)[-1]
    return Response(
        content=content,
        media_type=artifact[1],
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@reports_router.get("/{report_id}/preview")
def preview_report(report_id: str, request: Request) -> dict:
    report = request.app.state.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    round_item = request.app.state.store.get_round(report.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if not task:
        raise HTTPException(status_code=404, detail="report round not found")
    _ensure_task_access(request, task)
    return _ok({"report_no": report["report_no"], "report_type": report["report_type"], "version_no": report["version_no"], "status": report["status"], "content": report.get("content_snapshot") or {}})


@reports_router.post("")
def create_report(payload: ReportCreateRequest, request: Request) -> JSONResponse:
    round_item = request.app.state.store.get_round(payload.round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    _ensure_task_access(request, task)
    data = payload.model_dump()
    data.update(_operation_context(request))
    report = request.app.state.store.create_report(data)
    if not report:
        raise HTTPException(status_code=404, detail="round not found")
    return JSONResponse(status_code=201, content=_ok(report, "report created"))


@reports_router.post("/{report_id}/publish")
async def publish_report(report_id: str, payload: dict, request: Request) -> dict:
    report = request.app.state.store.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    round_item = request.app.state.store.get_round(report.get("round_id", ""))
    task = request.app.state.store.get_task(round_item.get("task_id", "")) if round_item else None
    if not task:
        raise HTTPException(status_code=404, detail="report round not found")
    _ensure_task_access(request, task)
    data = dict(payload)
    data.update(_operation_context(request))
    try:
        published = request.app.state.store.publish_report(report_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=exc.args[0]) from exc
    if published:
        content = published.get("content_snapshot") or {}
        awaitable = (
            ("word", f"reports/{published['report_no']}.docx", render_docx(content), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("pdf", f"reports/{published['report_no']}.pdf", render_pdf(content), "application/pdf"),
        )
        for _artifact_type, key, binary, media_type in awaitable:
            await run_in_threadpool(request.app.state.object_storage.put, key, binary, media_type)
    return _ok(published, "report published")


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


@rounds_router.post("/{round_id}/issues")
def create_manual_issue(round_id: str, payload: ManualIssueCreateRequest, request: Request) -> JSONResponse:
    round_item = request.app.state.store.get_round(round_id)
    if not round_item:
        raise HTTPException(status_code=404, detail="round not found")
    task = request.app.state.store.get_task(round_item.get("task_id", ""))
    if task:
        _ensure_task_access(request, task)
    data = payload.model_dump()
    data.update(_operation_context(request))
    issue = request.app.state.store.create_manual_issue(round_id, data)
    if not issue:
        raise HTTPException(status_code=404, detail="round not found")
    return JSONResponse(status_code=201, content=_ok(issue, "manual issue created"))


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


@issues_router.post("/{issue_id}/close")
def close_issue(issue_id: str, payload: dict, request: Request) -> dict:
    issue = request.app.state.store.get_issue(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    _ensure_issue_access(request, issue)
    issue = request.app.state.store.set_issue_status(
        issue_id, "closed", payload.get("reason"), _operation_context(request)
    )
    if not issue:
        raise HTTPException(status_code=404, detail="issue not found")
    return _ok(issue)


@settings_router.get("/models", dependencies=[Depends(require_admin)])
def list_models(request: Request) -> dict:
    return _ok(request.app.state.store.list_model_configs())


@settings_router.post("/models", dependencies=[Depends(require_admin)])
def create_model(payload: ModelConfigRequest, request: Request) -> JSONResponse:
    data = payload.model_dump()
    data.update(_operation_context(request))
    model = request.app.state.store.create_model_config(data)
    if not model:
        raise HTTPException(status_code=409, detail="model configuration already exists")
    return JSONResponse(status_code=201, content=_ok(model, "model configuration created"))


@settings_router.patch("/models/{config_id}", dependencies=[Depends(require_admin)])
def update_model(config_id: str, payload: ModelConfigUpdateRequest, request: Request) -> dict:
    data = payload.model_dump(exclude_unset=True)
    data.update(_operation_context(request))
    model = request.app.state.store.update_model_config(config_id, data)
    if not model:
        raise HTTPException(status_code=404, detail="model configuration not found")
    return _ok(model)


@settings_router.post("/models/{config_id}/test", dependencies=[Depends(require_admin)])
def test_model_connection(config_id: str, request: Request) -> dict:
    try:
        result = request.app.state.model_gateway.test_connection(config_id, trace_id=get_trace_id())
    except ModelGatewayError as exc:
        status_code = 404 if exc.code == "MODEL_CONFIG_NOT_FOUND" else 409 if exc.code == "MODEL_CONFIG_DISABLED" else 502
        raise HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)}) from exc
    return _ok(result, "model connection succeeded")


@settings_router.get("/model-call-logs", dependencies=[Depends(require_admin)])
def list_model_call_logs(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return _ok(request.app.state.store.list_model_call_logs(limit))


@settings_router.get("/system-parameters", dependencies=[Depends(require_admin)])
def list_system_parameters(request: Request) -> dict:
    return _ok(request.app.state.store.list_system_parameters())


@settings_router.put("/system-parameters/{param_key}", dependencies=[Depends(require_admin)])
def upsert_system_parameter(param_key: str, payload: SystemParameterRequest, request: Request) -> dict:
    data = payload.model_dump()
    data["scope"] = "global"
    data.update(_operation_context(request))
    return _ok(request.app.state.store.upsert_config_entry(param_key, data))


@settings_router.get("/issue-categories")
def list_issue_categories(request: Request) -> dict:
    return _ok([item["param_value"] | {"status": item["status"]} for item in request.app.state.store.list_config_entries("issue_category")])


@settings_router.post("/issue-categories", dependencies=[Depends(require_admin)])
def upsert_issue_category(payload: IssueCategoryRequest, request: Request) -> JSONResponse:
    data = {"scope": "issue_category", "status": payload.status, "param_value": payload.model_dump(exclude={"status"}), **_operation_context(request)}
    item = request.app.state.store.upsert_config_entry(f"issue_category:{payload.code}", data)
    return JSONResponse(status_code=201, content=_ok(item["param_value"] | {"status": item["status"]}))


@settings_router.get("/report-templates")
def list_report_templates(request: Request) -> dict:
    return _ok([item["param_value"] | {"status": item["status"]} for item in request.app.state.store.list_config_entries("report_template")])


@settings_router.post("/report-templates", dependencies=[Depends(require_admin)])
def upsert_report_template(payload: ReportTemplateRequest, request: Request) -> JSONResponse:
    data = {"scope": "report_template", "status": payload.status, "param_value": payload.model_dump(exclude={"status"}), **_operation_context(request)}
    item = request.app.state.store.upsert_config_entry(f"report_template:{payload.template_code}", data)
    return JSONResponse(status_code=201, content=_ok(item["param_value"] | {"status": item["status"]}))


@settings_router.get("/audit-stages")
def list_audit_stages() -> dict:
    return _ok(list(FIXED_AUDIT_STAGES))


@jobs_router.get("")
def list_jobs(request: Request) -> dict:
    return _ok(request.app.state.store.list_queue_jobs())


@jobs_router.post("/{job_id}/run")
def run_job(job_id: str, request: Request) -> dict:
    try:
        result = process_queue_job(
            request.app.state.store,
            job_id,
            request.app.state.object_storage,
            request.app.state.ocr_backend,
            request.app.state.ocr_dpi,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="queue job not found")
    return _ok(result, "queue job completed")


@jobs_router.post("/{job_id}/retry")
def retry_job(job_id: str, request: Request) -> dict:
    result = request.app.state.store.retry_queue_job(job_id, _operation_context(request))
    if not result:
        raise HTTPException(status_code=409, detail="queue job is not retryable")
    return _ok(result, "queue job requeued")


@jobs_router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    store = request.app.state.store
    if not store.get_queue_job(job_id):
        raise HTTPException(status_code=404, detail="queue job not found")
    result = store.cancel_queue_job(job_id, _operation_context(request))
    if not result:
        raise HTTPException(status_code=409, detail="queue job is not cancelable")
    return _ok(result, "queue job canceled")


@monitoring_router.get("")
def monitoring(request: Request) -> dict:
    jobs = request.app.state.store.list_queue_jobs()
    total = len(jobs)
    failed = sum(item.get("status") == "failed" for item in jobs)
    ocr_backend = request.app.state.ocr_backend
    return _ok({"queue_waiting": sum(item.get("status") in {"queued", "pending"} for item in jobs), "queue_running": sum(item.get("status") == "running" for item in jobs), "queue_failed": failed, "worker_online": 1 if get_settings().dispatch_jobs else 0, "job_failure_rate": round(failed / total, 4) if total else 0.0, "alerts_new": len(request.app.state.store.list_alerts()), "ocr_engine": ocr_backend.engine_name if ocr_backend else "disabled"})


@monitoring_router.get("/alerts")
def list_alerts(request: Request) -> dict:
    return _ok(request.app.state.store.list_alerts())


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
