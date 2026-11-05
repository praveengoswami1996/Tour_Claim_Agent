"""FastAPI application factory and lifecycle."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import aiosqlite
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from tourclaim_agent import __version__
from tourclaim_agent.agent.graph import build_graph
from tourclaim_agent.api.routes import router
from tourclaim_agent.auth.identity import IdentityMismatch, IdentityResolver
from tourclaim_agent.auth.tokens import InvalidToken, build_token_validator
from tourclaim_agent.clients.errors import (
    BackendError,
    BackendForbidden,
    BackendNotFound,
    BackendUnauthorized,
    BackendUnavailable,
)
from tourclaim_agent.config import Settings, get_settings
from tourclaim_agent.llm.factory import build_intent_extractor
from tourclaim_agent.logging_setup import configure_logging, correlation_id, new_correlation_id
from tourclaim_agent.service import (
    AgentService,
    AttachmentRejected,
    NoActiveClaim,
    ReadOnlySession,
    WorkflowNotFound,
)
from tourclaim_agent.state.registry import WorkflowRegistry

log = logging.getLogger(__name__)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "message": message})


def create_app(settings: Settings | None = None, *, http_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            db_path = settings.state_db_path
            if db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            http = await stack.enter_async_context(
                httpx.AsyncClient(timeout=settings.backend_timeout_seconds, transport=http_transport)
            )
            registry_conn = await aiosqlite.connect(db_path)
            stack.push_async_callback(registry_conn.close)
            registry = WorkflowRegistry(registry_conn)
            await registry.setup()
            # Separate connection: the checkpointer manages its own transactions.
            checkpoint_conn = await aiosqlite.connect(db_path)
            stack.push_async_callback(checkpoint_conn.close)
            checkpointer = AsyncSqliteSaver(checkpoint_conn)
            await checkpointer.setup()
            app.state.service = AgentService(
                settings=settings,
                http=http,
                validator=build_token_validator(settings, http),
                identities=IdentityResolver(settings.identity_cache_seconds),
                intents=build_intent_extractor(settings, http),
                registry=registry,
                graph=build_graph(checkpointer),
            )
            log.info("Tour Claim AI Agent %s started (%s)", __version__, settings.environment.value)
            yield

    app = FastAPI(title="Tour Claim AI Agent", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )

    @app.middleware("http")
    async def with_correlation_id(request: Request, call_next):
        token = correlation_id.set(request.headers.get("X-Correlation-ID") or new_correlation_id())
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id.get()
            return response
        finally:
            correlation_id.reset(token)

    @app.exception_handler(InvalidToken)
    async def _invalid_token(_: Request, exc: InvalidToken) -> JSONResponse:
        log.info("rejected token: %s", exc)
        return _error(401, "invalid_token", "Please sign in again.")

    @app.exception_handler(BackendUnauthorized)
    async def _session_expired(_: Request, exc: BackendUnauthorized) -> JSONResponse:
        return _error(401, "session_expired", "Your session has expired. Please sign in again.")

    @app.exception_handler(BackendForbidden)
    async def _forbidden(_: Request, exc: BackendForbidden) -> JSONResponse:
        return _error(403, "forbidden", str(exc))

    @app.exception_handler(IdentityMismatch)
    async def _mismatch(_: Request, exc: IdentityMismatch) -> JSONResponse:
        log.warning("identity mismatch between token and employee record")
        return _error(403, "identity_mismatch", "Your employee record could not be verified.")

    @app.exception_handler(ReadOnlySession)
    async def _read_only(_: Request, exc: ReadOnlySession) -> JSONResponse:
        return _error(403, "read_only", "This is a view-only session, so nothing can be changed.")

    @app.exception_handler(AttachmentRejected)
    async def _attachment(_: Request, exc: AttachmentRejected) -> JSONResponse:
        return _error(400, "attachment_rejected", str(exc))

    @app.exception_handler(NoActiveClaim)
    async def _no_claim(_: Request, exc: NoActiveClaim) -> JSONResponse:
        return _error(409, "no_active_claim", 'Start a claim first, for example "start a claim for my Mumbai tour".')

    @app.exception_handler(WorkflowNotFound)
    async def _workflow_missing(_: Request, exc: WorkflowNotFound) -> JSONResponse:
        return _error(404, "workflow_not_found", "This conversation was not found.")

    @app.exception_handler(BackendNotFound)
    async def _not_found(_: Request, exc: BackendNotFound) -> JSONResponse:
        return _error(404, "not_found", str(exc))

    @app.exception_handler(BackendError)
    async def _backend(_: Request, exc: BackendError) -> JSONResponse:
        status = 503 if isinstance(exc, BackendUnavailable) else 502
        return _error(status, "backend_error", "The Tour system is not responding. Please try again shortly.")

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(router)
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory tourclaim_agent.main:app_factory`."""
    configure_logging()
    return create_app()
