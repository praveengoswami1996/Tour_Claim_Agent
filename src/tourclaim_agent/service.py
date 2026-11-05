"""Application service: authenticates callers and runs their workflows."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath
from typing import Any

import httpx

from tourclaim_agent.agent import bill_flow
from tourclaim_agent.agent.core import AgentContext, ocr_reference
from tourclaim_agent.auth.identity import Employee, IdentityResolver
from tourclaim_agent.auth.tokens import InvalidToken, TokenClaims, TokenValidator
from tourclaim_agent.clients.allowlist import Service, write_enabled
from tourclaim_agent.clients.http import UserScopedClient
from tourclaim_agent.clients.ocr_api import OcrApiClient
from tourclaim_agent.clients.org_api import OrgApiClient
from tourclaim_agent.clients.tour_api import TourApiClient
from tourclaim_agent.config import Settings
from tourclaim_agent.llm.intent import IntentExtractor
from tourclaim_agent.state.records import WorkflowRecord
from tourclaim_agent.state.registry import WorkflowRegistry
from tourclaim_agent.state.uploads import UploadRecord, UploadStore

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 2000
NEW_TITLE = "New conversation"

# Extension → accepted leading bytes (FRS DOC-01). Content must match the extension.
_FILE_SIGNATURES = {
    ".pdf": (b"%PDF",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
}
_CONTENT_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


class WorkflowNotFound(Exception):
    """No open workflow with this ID belongs to the caller."""


class ReadOnlySession(Exception):
    """The session is read-only (impersonation), so nothing may be written (FRS IDN-05)."""


class AttachmentRejected(Exception):
    """An uploaded file failed validation. The message is safe to show to the employee."""


@dataclass(frozen=True)
class RequestUser:
    """The authenticated caller. The token lives only for the duration of the request."""

    token: str
    claims: TokenClaims
    employee: Employee


@dataclass(frozen=True)
class UploadedFile:
    file_name: str
    content: bytes


class AgentService:
    def __init__(
        self,
        *,
        settings: Settings,
        http: httpx.AsyncClient,
        validator: TokenValidator,
        identities: IdentityResolver,
        intents: IntentExtractor,
        registry: WorkflowRegistry,
        uploads: UploadStore,
        graph: Any,
    ) -> None:
        self._settings = settings
        self._http = http
        self._validator = validator
        self._identities = identities
        self._intents = intents
        self._registry = registry
        self._uploads = uploads
        self._graph = graph
        self._enabled_writes = frozenset(settings.enabled_write_endpoints)
        # Bill processing for one employee runs one request at a time (NFR-2). One instance only.
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _lock(self, emp_code: str) -> asyncio.Lock:
        return self._locks[emp_code]

    @property
    def max_upload_files(self) -> int:
        return self._settings.max_upload_files

    @property
    def max_upload_bytes(self) -> int:
        return self._settings.max_upload_bytes

    # ------------------------------------------------------------------ auth

    def _client(self, service: Service, token: str) -> UserScopedClient:
        base = self._settings.tour_api_base_url if service is Service.TOUR else self._settings.org_api_base_url
        return UserScopedClient(
            self._http,
            service=service,
            base_url=base,
            token=token,
            device_type=self._settings.device_type,
            enabled_writes=self._enabled_writes,
        )

    def tour_api(self, token: str) -> TourApiClient:
        return TourApiClient(self._client(Service.TOUR, token))

    async def authenticate(self, token: str | None) -> RequestUser:
        if not token:
            raise InvalidToken("missing bearer token")
        claims = await self._validator.validate(token)
        employee = await self._identities.resolve(claims, OrgApiClient(self._client(Service.ORG, token)))
        return RequestUser(token=token, claims=claims, employee=employee)

    def _context(self, user: RequestUser, workflow_id: str) -> AgentContext:
        return AgentContext(
            tours=self.tour_api(user.token),
            ocr=OcrApiClient(self._client(Service.TOUR, user.token)),
            employee=user.employee,
            intents=self._intents,
            today=date.today(),
            workflow_id=workflow_id,
            uploads=self._uploads,
            save_draft_enabled=write_enabled("Claim/SaveRaiseClaimDraft", self._enabled_writes)
            and not user.employee.read_only,
            auto_save_bills=self._settings.auto_save_bills,
            max_upload_files=self._settings.max_upload_files,
            max_upload_mb=self._settings.max_upload_bytes // (1024 * 1024),
            bill_report_keep_days=self._settings.bill_report_keep_days,
            bill_slow_minutes=self._settings.bill_slow_minutes,
        )

    @staticmethod
    def _config(workflow_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": workflow_id}}

    # ------------------------------------------------------------------ workflows

    async def start_session(self, user: RequestUser) -> dict[str, Any]:
        action_needed = await self.tour_api(user.token).pending_task_count(user.employee.emp_id)
        workflows = await self._registry.list_open(user.employee.emp_code)
        return {
            "employee": {
                "name": user.employee.name,
                "designation": user.employee.designation,
                "unit": user.employee.unit,
                "read_only": user.employee.read_only,
            },
            "pending": {"action_needed_count": action_needed},
            "workflows": [
                {"workflow_id": w.workflow_id, "title": w.title, "updated_at": w.updated_at} for w in workflows
            ],
        }

    async def create_workflow(self, user: RequestUser) -> WorkflowRecord:
        return await self._registry.create(user.employee.emp_code, NEW_TITLE)

    async def list_workflows(self, user: RequestUser) -> list[WorkflowRecord]:
        return await self._registry.list_open(user.employee.emp_code)

    async def abandon_workflow(self, user: RequestUser, workflow_id: str) -> None:
        if not await self._registry.abandon(workflow_id, user.employee.emp_code):
            raise WorkflowNotFound(workflow_id)

    async def _owned(self, user: RequestUser, workflow_id: str) -> WorkflowRecord:
        record = await self._registry.get_owned(workflow_id, user.employee.emp_code)
        if record is None:
            raise WorkflowNotFound(workflow_id)
        return record

    async def send_message(self, user: RequestUser, workflow_id: str, text: str) -> list[dict[str, Any]]:
        record = await self._owned(user, workflow_id)
        text = " ".join(text.split())[:MAX_MESSAGE_CHARS]
        result = await self._graph.ainvoke(
            {"messages": [{"role": "user", "text": text}], "events": [], "new_bills": []},
            self._config(workflow_id),
            context=self._context(user, workflow_id),
        )
        await self._registry.touch(workflow_id, title=text[:60] if record.title == NEW_TITLE else None)
        return list(result.get("events", []))

    # ------------------------------------------------------------------ attachments

    def _validate(self, files: list[UploadedFile]) -> None:
        if not files:
            raise AttachmentRejected("No files were received.")
        if len(files) > self._settings.max_upload_files:
            raise AttachmentRejected(f"Please upload at most {self._settings.max_upload_files} files at a time.")
        limit_mb = self._settings.max_upload_bytes // (1024 * 1024)
        for f in files:
            suffix = PurePath(f.file_name).suffix.lower()
            if suffix not in _FILE_SIGNATURES:
                raise AttachmentRejected(f"{f.file_name}: only PDF, JPG and PNG files are accepted.")
            if not f.content:
                raise AttachmentRejected(f"{f.file_name} is empty.")
            if len(f.content) > self._settings.max_upload_bytes:
                raise AttachmentRejected(f"{f.file_name} is larger than {limit_mb} MB.")
            if not f.content.startswith(_FILE_SIGNATURES[suffix]):
                raise AttachmentRejected(f"{f.file_name} does not look like a valid {suffix[1:].upper()} file.")

    async def add_attachments(
        self, user: RequestUser, workflow_id: str, files: list[UploadedFile]
    ) -> list[dict[str, Any]]:
        """Accept bills at any time (BIL-01); a claim in progress only changes the reference tag (BIL-03)."""
        await self._owned(user, workflow_id)
        if user.employee.read_only:
            raise ReadOnlySession()
        self._validate(files)
        emp_code = user.employee.emp_code
        async with self._lock(emp_code):
            snapshot = await self._graph.aget_state(self._config(workflow_id))
            claim = (snapshot.values or {}).get("claim")
            tour_id = int(claim["tour_id"]) if claim else None

            fresh: list[tuple[UploadedFile, str]] = []
            duplicates: list[str] = []
            for f in files:
                digest = hashlib.sha256(f.content).hexdigest()
                if digest in {d for _, d in fresh} or await self._uploads.already_uploaded(emp_code, digest):
                    duplicates.append(f.file_name)
                else:
                    fresh.append((f, digest))

            events: list[dict[str, Any]] = []
            if duplicates:
                events.append(
                    {
                        "type": "notice",
                        "level": "warning",
                        "text": "Skipped, already uploaded: " + ", ".join(duplicates) + ".",
                    }
                )
            if not fresh:
                return events + [await bill_flow.bill_status(self._uploads, emp_code)]

            reference = ocr_reference(workflow_id, tour_id)
            ocr = OcrApiClient(self._client(Service.TOUR, user.token))
            documents = await ocr.upload(
                [(f.file_name, f.content, _CONTENT_TYPES[PurePath(f.file_name).suffix.lower()]) for f, _ in fresh],
                reference=reference,
            )
            by_index = {d.index if d.index is not None else i: d for i, d in enumerate(documents)}
            new_bills = []
            for i, (f, digest) in enumerate(fresh):
                if i not in by_index:
                    continue
                document_id = by_index[i].document_id
                await self._uploads.add(
                    UploadRecord(
                        document_id=document_id,
                        emp_code=emp_code,
                        workflow_id=workflow_id,
                        file_name=f.file_name,
                        file_size=len(f.content),
                        sha256=digest,
                        reference=reference,
                    )
                )
                new_bills.append({"document_id": document_id, "file_name": f.file_name, "sha256": digest})
            log.info("uploaded %d bill(s) in workflow %s", len(new_bills), workflow_id)
            result = await self._graph.ainvoke(
                {
                    "messages": [{"role": "user", "text": f"[uploaded {len(new_bills)} file(s)]"}],
                    "events": [],
                    "new_bills": new_bills,
                },
                self._config(workflow_id),
                context=self._context(user, workflow_id),
            )
        await self._registry.touch(workflow_id)
        return events + list(result.get("events", []))
