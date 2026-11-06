"""HTTP interface of the Claim Assistant chats: the 0.4 contract.

Project-Context/frontend/agent_0.4_frontend_requirements.md §4.

Every call carries the employee's SSO token. Replies are an SSE stream (`event: agent_event`, `data: <event JSON>`,
then `event: done`) so replies appear while the model writes them; `?stream=false` returns `{"events": [...]}`.

Paths shared with the 0.3 conversations are told apart by the request: `POST /sessions` and `POST /workflows` with a
`mode` are chats, without one they are 0.3; `DELETE /workflows/{id}`, `/messages` and `/attachments` go to the chat
runtime when the workflow is a chat, else to the 0.3 handlers (api/routes.py).

Errors: 401 `invalid_token` / `session_expired`, 404 `workflow_not_found` (unknown, abandoned or expired chat: the
frontend starts a new one and calls /open), 404 `bridge_call_not_found`, 400 `unknown_mode`, 503 `backend_error`.
A failure after the response started arrives as an `error` event before `done`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from tourclaim_agent.api import routes
from tourclaim_agent.claimchat.runtime.turn import (
    UNSET,
    Channel,
    ChatRuntime,
    ChatUser,
    IncomingFile,
    TurnFailed,
    TurnInput,
    _End,
    _Pause,
)
from tourclaim_agent.clients.errors import BackendUnauthorized
from tourclaim_agent.service import AgentService, RequestUser

router = APIRouter(prefix="/v1")

ServiceDep = Annotated[AgentService, Depends(routes.get_service)]
UserDep = Annotated[RequestUser, Depends(routes.current_user)]
StreamDep = Annotated[bool, Query()]

# a browser sends a File with no type as application/octet-stream: the old checks saw type ''
_UNTYPED = {"application/octet-stream", ""}
# how many files one attachment call may carry (the chat itself refuses more than 20 per read, as before)
MAX_FILES_PER_CALL = 200


def get_chat(request: Request) -> ChatRuntime:
    return request.app.state.chat


ChatDep = Annotated[ChatRuntime, Depends(get_chat)]


def chat_user(request: Request, user: RequestUser) -> ChatUser:
    settings = request.app.state.settings
    name = (user.claims.name or user.employee.name or "").strip()
    origin = request.headers.get("origin") or settings.chat_app_origin
    return ChatUser(token=user.token, employee=user.employee, name=name, origin=origin)


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise RequestValidationError([{"type": "json_invalid", "loc": ("body",), "msg": "Invalid JSON"}]) from exc
    if not isinstance(body, dict):
        raise RequestValidationError([{"type": "dict_type", "loc": ("body",), "msg": "An object is expected"}])
    return body


def _params(body: dict[str, Any]) -> dict[str, Any]:
    """The component's props: {tourId?, draftId?}."""
    return {k: body[k] for k in ("tourId", "draftId") if body.get(k) is not None}


def _turn_input(entry: str, body: dict[str, Any], **extra: Any) -> TurnInput:
    active = body.get("active")
    return TurnInput(
        entry=entry,
        active=bool(active) if active is not None else None,
        form=body.get("form", UNSET),
        **extra,
    )


def _sse(event: dict[str, Any]) -> str:
    return f"event: agent_event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def respond(channel: Channel, stream: bool) -> StreamingResponse | dict[str, Any]:
    """The events of a turn as SSE (or JSON). Waits for the first event, so a turn that fails before it sent
    anything is an HTTP error (401, 404, 503) rather than a broken stream."""
    first = await channel.get()
    if isinstance(first, TurnFailed):
        raise first.error

    if not stream:
        events: list[dict[str, Any]] = []
        item = first
        while not isinstance(item, _Pause | _End):
            if isinstance(item, TurnFailed):
                if not events:
                    raise item.error
                events.append(_error_event(item.error))
                break
            events.append(item)
            item = await channel.get()
        return {"events": events}

    async def sse() -> AsyncIterator[str]:
        item = first
        while not isinstance(item, _Pause | _End):
            if isinstance(item, TurnFailed):
                yield _sse(_error_event(item.error))
                break
            yield _sse(item)
            item = await channel.get()
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


def _error_event(error: BaseException) -> dict[str, Any]:
    if isinstance(error, BackendUnauthorized):
        return {
            "type": "error",
            "error": "session_expired",
            "message": "Your session has expired. Please sign in again.",
        }
    return {"type": "error", "error": "turn_failed", "message": "Something went wrong. Please try again."}


# ── conversations ──


@router.post("/sessions", response_model=None)
async def start_session(request: Request, user: UserDep, service: ServiceDep, chat: ChatDep) -> Any:
    """With a mode: the launcher's state (`status`, `bills`) before the chat window is opened. Without: 0.3."""
    body = await _json_body(request)
    if body.get("mode"):
        return await chat.launcher(chat_user(request, user), str(body["mode"]), _params(body))
    return await routes.start_session(user, service)


@router.post("/workflows", status_code=201, response_model=None)
async def create_workflow(request: Request, user: UserDep, service: ServiceDep, chat: ChatDep) -> Any:
    """With a mode: a new chat `{workflow_id}`. Without: a 0.3 conversation."""
    body = await _json_body(request)
    if body.get("mode"):
        session = await chat.create(chat_user(request, user), str(body["mode"]), _params(body))
        return JSONResponse(status_code=201, content={"workflow_id": session.workflow_id, "mode": session.mode})
    record = await routes.create_workflow(user, service)
    return JSONResponse(status_code=201, content=record.model_dump())


@router.delete("/workflows/{workflow_id}", status_code=204, response_model=None)
async def abandon_workflow(
    workflow_id: str, request: Request, user: UserDep, service: ServiceDep, chat: ChatDep
) -> Response:
    """Start over: the chat is closed (what it took from the bill queue is given back)."""
    if await chat.store.exists(workflow_id):
        await chat.abandon(chat_user(request, user), workflow_id)
    else:
        await routes.abandon_workflow(workflow_id, user, service)
    return Response(status_code=204)


# ── turns ──


async def _turn(
    request: Request, chat: ChatRuntime, user: RequestUser, workflow_id: str, data: TurnInput, stream: bool
) -> StreamingResponse | dict[str, Any]:
    channel = await chat.start_turn(chat_user(request, user), workflow_id, data)
    return await respond(channel, stream)


@router.post("/workflows/{workflow_id}/open", response_model=None)
async def open_chat(
    workflow_id: str, request: Request, user: UserDep, chat: ChatDep, stream: StreamDep = True
) -> StreamingResponse | dict[str, Any]:
    """The window was opened: the greeting, or the replies held back while it was closed."""
    return await _turn(request, chat, user, workflow_id, _turn_input("open", await _json_body(request)), stream)


@router.post("/workflows/{workflow_id}/messages", response_model=None)
async def send_message(
    workflow_id: str, request: Request, user: UserDep, service: ServiceDep, chat: ChatDep, stream: StreamDep = True
) -> StreamingResponse | dict[str, Any]:
    """A typed message `{text, active, form?}` (0.3: `{text}`)."""
    body = await _json_body(request)
    if not await chat.store.exists(workflow_id):
        try:
            message = routes.MessageIn.model_validate(body)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc
        return await routes.send_message(workflow_id, message, user, service, stream)
    text = body.get("text")
    data = _turn_input("message", body, text=text if isinstance(text, str) else "")
    return await _turn(request, chat, user, workflow_id, data, stream)


@router.post("/workflows/{workflow_id}/actions", response_model=None)
async def tap_chip(
    workflow_id: str, request: Request, user: UserDep, chat: ChatDep, stream: StreamDep = True
) -> StreamingResponse | dict[str, Any]:
    """A chip tap `{chip, active, form?}` (also the ✕ of a staged file, with the pill's `removeChip`)."""
    body = await _json_body(request)
    data = _turn_input("chip", body, chip=str(body.get("chip") or ""))
    return await _turn(request, chat, user, workflow_id, data, stream)


@router.post("/workflows/{workflow_id}/attachments", response_model=None)
async def add_attachments(
    workflow_id: str, request: Request, user: UserDep, service: ServiceDep, chat: ChatDep, stream: StreamDep = True
) -> StreamingResponse | dict[str, Any]:
    """Bills or a tour plan: multipart `files` (or `files[]`), `active`, `form` (JSON text)."""
    form = await request.form(max_files=MAX_FILES_PER_CALL + 1, max_part_size=16 * 1024 * 1024)
    uploads = [f for f in [*form.getlist("files"), *form.getlist("files[]")] if isinstance(f, UploadFile)]
    if not await chat.store.exists(workflow_id):
        if not uploads:
            raise RequestValidationError([{"type": "missing", "loc": ("body", "files"), "msg": "Field required"}])
        return await routes.add_attachments(workflow_id, uploads, user, service, stream)
    limit = service.max_upload_bytes
    files: list[IncomingFile] = []
    for upload in uploads[:MAX_FILES_PER_CALL]:
        content = await upload.read(limit + 1)
        name = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        type_ = (upload.content_type or "").split(";", 1)[0].strip().lower()
        size = upload.size if upload.size is not None else len(content)
        files.append(IncomingFile(name=name, type="" if type_ in _UNTYPED else type_, size=size, data=content))
    body: dict[str, Any] = {}
    active = form.get("active")
    if isinstance(active, str):
        body["active"] = active.strip().lower() in ("true", "1", "yes")
    raw_form = form.get("form")
    if isinstance(raw_form, str) and raw_form.strip():
        try:
            body["form"] = json.loads(raw_form)
        except ValueError as exc:
            raise RequestValidationError(
                [{"type": "json_invalid", "loc": ("body", "form"), "msg": "Invalid JSON"}]
            ) from exc
    return await _turn(request, chat, user, workflow_id, _turn_input("files", body, files=files), stream)


@router.post("/workflows/{workflow_id}/poll", response_model=None)
async def poll(
    workflow_id: str, request: Request, user: UserDep, chat: ChatDep, stream: StreamDep = True
) -> StreamingResponse | dict[str, Any]:
    """Bills (or a tour plan) being read: every 20 s while `bills.inFlight` is not empty (60 s after an error)."""
    return await _turn(request, chat, user, workflow_id, _turn_input("poll", await _json_body(request)), stream)


@router.post("/workflows/{workflow_id}/retry", response_model=None)
async def retry(
    workflow_id: str, request: Request, user: UserDep, chat: ChatDep, stream: StreamDep = True
) -> StreamingResponse | dict[str, Any]:
    """ "Try again" after the system notice."""
    return await _turn(request, chat, user, workflow_id, _turn_input("retry", await _json_body(request)), stream)


@router.post("/workflows/{workflow_id}/bridge/{call_id}", response_model=None)
async def bridge_answer(
    workflow_id: str, call_id: str, request: Request, user: UserDep, chat: ChatDep, stream: StreamDep = True
) -> StreamingResponse | dict[str, Any]:
    """The answer to a `bridge_call` `{ok, result?, error?, form?}`; the rest of the turn follows."""
    body = await _json_body(request)
    error = body.get("error")
    channel = await chat.answer_bridge(
        chat_user(request, user),
        workflow_id,
        call_id,
        ok=bool(body.get("ok")),
        result=body.get("result"),
        error=error if isinstance(error, str) else None,
        form=body.get("form", UNSET),
    )
    return await respond(channel, stream)
