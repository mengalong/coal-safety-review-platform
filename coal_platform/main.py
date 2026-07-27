from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from coal_platform import __version__
from coal_platform.api import register_routers
from coal_platform.config import get_settings
from coal_platform.database import SessionLocal
from coal_platform.request_context import get_trace_id, trace_id_context
from coal_platform.sqlalchemy_store import SqlAlchemyStore
from coal_platform.storage import ObjectStorage, build_object_storage
from coal_platform.store import DemoStore
from coal_platform.store_protocol import PlatformStore


def _error_response(
    code: str,
    message: str,
    detail: object,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "detail": detail, "trace_id": get_trace_id()},
        headers=headers,
    )


def create_app(
    store: PlatformStore | None = None,
    object_storage: ObjectStorage | None = None,
) -> FastAPI:
    settings = get_settings()
    active_store = store
    if active_store is None:
        active_store = SqlAlchemyStore(SessionLocal) if settings.store_backend == "database" else DemoStore.seed()
    active_storage = object_storage or build_object_storage(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        active_store.initialize(seed_demo_data=settings.seed_demo_data)
        active_storage.initialize()
        yield

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_trace_id(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid4().hex
        token = trace_id_context.set(trace_id)
        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            trace_id_context.reset(token)

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        code = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "RESOURCE_NOT_FOUND",
            409: "CONFLICT",
        }.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return _error_response(code, message, exc.detail, exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response("VALIDATION_ERROR", "request validation failed", exc.errors(), 422)

    app.state.store = active_store
    app.state.object_storage = active_storage
    register_routers(app, prefix=settings.api_v1_prefix)
    return app


app = create_app()
