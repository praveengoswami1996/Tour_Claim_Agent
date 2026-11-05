"""HTTP interface for the React app (FRS §8)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tourclaim_agent.service import AgentService, AttachmentRejected, RequestUser, UploadedFile

router = APIRouter(prefix="/v1")


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class WorkflowOut(BaseModel):
    workflow_id: str
    title: str
    updated_at: float


def get_service(request: Request) -> AgentService:
    return request.app.state.service


def _bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def current_user(request: Request, service: Annotated[AgentService, Depends(get_service)]) -> RequestUser:
    return await service.authenticate(_bearer(request))


ServiceDep = Annotated[AgentService, Depends(get_service)]
UserDep = Annotated[RequestUser, Depends(current_user)]


@router.post("/sessions")
async def start_session(user: UserDep, service: ServiceDep) -> dict[str, Any]:
    return await service.start_session(user)


@router.post("/workflows", status_code=201)
async def create_workflow(user: UserDep, service: ServiceDep) -> WorkflowOut:
    record = await service.create_workflow(user)
    return WorkflowOut(workflow_id=record.workflow_id, title=record.title, updated_at=record.updated_at)


@router.get("/workflows")
async def list_workflows(user: UserDep, service: ServiceDep) -> list[WorkflowOut]:
    return [
        WorkflowOut(workflow_id=r.workflow_id, title=r.title, updated_at=r.updated_at)
        for r in await service.list_workflows(user)
    ]


@router.delete("/workflows/{workflow_id}", status_code=204)
async def abandon_workflow(workflow_id: str, user: UserDep, service: ServiceDep) -> None:
    await service.abandon_workflow(user, workflow_id)


def _respond(events: list[dict[str, Any]], stream: bool) -> StreamingResponse | dict[str, Any]:
    if not stream:
        return {"events": events}

    async def sse() -> AsyncIterator[str]:
        for event in events:
            yield f"event: agent_event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.post("/workflows/{workflow_id}/messages", response_model=None)
async def send_message(
    workflow_id: str,
    message: MessageIn,
    user: UserDep,
    service: ServiceDep,
    stream: Annotated[bool, Query()] = True,
) -> StreamingResponse | dict[str, Any]:
    # The workflow runs before the response starts, so auth and backend errors still
    # map to proper HTTP status codes instead of failing mid-stream.
    return _respond(await service.send_message(user, workflow_id, message.text), stream)


@router.post("/workflows/{workflow_id}/attachments", response_model=None)
async def add_attachments(
    workflow_id: str,
    files: list[UploadFile],
    user: UserDep,
    service: ServiceDep,
    stream: Annotated[bool, Query()] = True,
) -> StreamingResponse | dict[str, Any]:
    """Upload bills for the claim being prepared. They go to the Tour system's OCR queue."""
    limit = service.max_upload_bytes
    received: list[UploadedFile] = []
    for upload in files[: service.max_upload_files + 1]:
        content = await upload.read(limit + 1)
        name = (upload.filename or "file").replace("\\", "/").rsplit("/", 1)[-1][:120]
        received.append(UploadedFile(file_name=name, content=content))
    if len(files) > service.max_upload_files:
        raise AttachmentRejected(f"Please upload at most {service.max_upload_files} files at a time.")
    return _respond(await service.add_attachments(user, workflow_id, received), stream)
