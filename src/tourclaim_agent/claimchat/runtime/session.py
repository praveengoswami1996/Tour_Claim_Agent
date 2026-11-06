"""One chat = one ChatSession: everything the old React component held, as plain JSON, kept per workflow.

Runtime design §1 (Project-Context/agent/old_assistant_parity/02_runtime_design.md). The old `ClaimChatbot` /
`WithinClaimChatbot` kept their state in `useState`/`useRef` for as long as the component was mounted. Here that
state is a JSON document per workflow, loaded at the start of every request (a turn) and saved at its end:

- `state`: the flow's own values (step, pending, picked, askYN, bills, sentHashes, ...), owned by the flow;
- `narrator`: the useNarrator part (queue, heard text, failed reply, retry, conversation id, shown replies);
- `ocr`: the bill list as last read, ids hidden here, files uploaded in this session, results taken and not settled;
- `page`: what the page held for the component (tours, drafts, the loadTour cache);
- `messages`: the bubbles (the old `messages` state), for the model's recent chat;
- `chipActions`: what each chip id on screen does (the chips' onClick, as named continuations).

Callbacks the old state held (`askYN.yes()`, a reply's `onShown`) are **named continuations**: `[name, {args}]`,
resolved through the flow's continuation table, so they survive serialisation.

Next to the sessions: staged file bytes (`chat_files`, per workflow and key) and the employee's "localStorage"
(`chat_local_storage`, per employee and key: kept bill reports, drafts saved by the chat). SQLite now; PostgreSQL
later behind the same methods.

A session not touched for `idle_seconds` (15 minutes) counts as a closed tab: what it still holds of the OCR queue is
given back (the old `pagehide` / `recoverOcrClaims`), and the conversation is gone (404, the frontend starts a new one).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from tourclaim_agent.claimchat.js_compat import UNDEFINED

log = logging.getLogger(__name__)

OPEN = "open"
ABANDONED = "abandoned"
EXPIRED = "expired"

# a session not touched for this long counts as a closed tab (runtime design §7)
IDLE_SECONDS = 15 * 60
# the bubbles kept for the model's recent chat (the old chat kept all of them in memory)
MAX_MESSAGES = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    workflow_id TEXT PRIMARY KEY,
    emp_code    TEXT NOT NULL,
    mode        TEXT NOT NULL,
    status      TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_sessions_owner ON chat_sessions (emp_code, status, updated_at);
CREATE TABLE IF NOT EXISTS chat_files (
    workflow_id TEXT NOT NULL,
    file_key    TEXT NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    data        BLOB NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (workflow_id, file_key)
);
CREATE TABLE IF NOT EXISTS chat_local_storage (
    emp_code   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (emp_code, key)
);
"""

Continuation = list[Any]
"""A named continuation: [name, {args}] (JSON). See ChatFlow.continuations."""


def cont(name: str, **args: Any) -> Continuation:
    """A callback the session can keep: run later with Turn.run()."""
    return [name, args]


def to_jsonable(value: Any) -> Any:
    """The value as JSON.stringify keeps it: `undefined` keys left out of objects, `undefined` in arrays is null."""
    if value is UNDEFINED:
        return None
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items() if v is not UNDEFINED}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, set | frozenset):
        return [to_jsonable(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, bytes | bytearray):
        raise TypeError("file bytes do not go in the session: keep them in the file store")
    return value


def _new_data(params: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        "params": params,
        "state": state,
        "active": False,
        "form": None,
        "narrator": {
            "events": [],
            "said": None,
            "failed": None,
            "retry": None,
            "unavailable": False,
            "conversationId": None,
            "shown": [],
        },
        "ocr": {"documents": [], "loaded": False, "error": None, "hidden": [], "local": [], "claims": {}},
        "page": {},
        "messages": [],
        "nextId": 1,
        "chipActions": {},
        "outbox": [],
    }


@dataclass
class ChatSession:
    """One conversation (workflow) in one mode: the whole state of the old mounted chat component."""

    workflow_id: str
    emp_code: str
    mode: str
    status: str
    created_at: float
    updated_at: float
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, emp_code: str, mode: str, params: dict[str, Any], state: dict[str, Any]) -> ChatSession:
        now = time.time()
        return cls(uuid.uuid4().hex, emp_code, mode, OPEN, now, now, _new_data(params, state))

    # ── parts ──
    @property
    def params(self) -> dict[str, Any]:
        """What the chat was created with: {tourId?, draftId?} (the component's props)."""
        return self.data["params"]

    @property
    def state(self) -> dict[str, Any]:
        """The flow's own state (owned by the flow)."""
        return self.data["state"]

    @property
    def narrator(self) -> dict[str, Any]:
        return self.data["narrator"]

    @property
    def ocr(self) -> dict[str, Any]:
        return self.data["ocr"]

    @property
    def page(self) -> dict[str, Any]:
        return self.data["page"]

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.data["messages"]

    @property
    def active(self) -> bool:
        """The chat window is open (the old `active` prop)."""
        return bool(self.data.get("active"))

    @active.setter
    def active(self, value: bool) -> None:
        self.data["active"] = bool(value)

    @property
    def form(self) -> Any:
        """The open form's state as the bridge's read() returns it (form modes), or None."""
        return self.data.get("form")

    @form.setter
    def form(self, value: Any) -> None:
        self.data["form"] = value

    def new_id(self) -> str:
        """A new bubble id (the old newId())."""
        n = int(self.data.get("nextId") or 1)
        self.data["nextId"] = n + 1
        return f"m{n}"

    def add_message(self, message: dict[str, Any]) -> dict[str, Any]:
        self.messages.append(message)
        del self.messages[:-MAX_MESSAGES]
        return message

    def held_document_ids(self) -> list[int]:
        """OCR results this chat took (MarkAsRead) and has not settled or given back."""
        return [int(i) for i in (self.ocr.get("claims") or {})]

    def dumps(self) -> str:
        return json.dumps(to_jsonable(self.data), ensure_ascii=False)


class EmployeeStorage(MutableMapping[str, str]):
    """The employee's "localStorage" for the chat (kept bill reports, drafts saved by the chat), key -> text.

    Loaded at the start of a turn and written back (only the keys that changed) at its end. Shared by all the
    employee's chats, as localStorage was shared by the browser's tabs.
    """

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})
        self._changed: set[str] = set()

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __setitem__(self, key: str, value: str) -> None:
        self._values[key] = str(value)
        self._changed.add(key)

    def __delitem__(self, key: str) -> None:
        del self._values[key]
        self._changed.add(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def changes(self) -> dict[str, str | None]:
        """{key: new text, or None when removed} since loading."""
        return {k: self._values.get(k) for k in self._changed}

    def mark_saved(self) -> None:
        self._changed.clear()


@dataclass(frozen=True)
class StoredFile:
    key: str
    name: str
    type: str
    size: int
    data: bytes


class SessionStore:
    """SQLite persistence of chat sessions, their staged file bytes and the employees' chat storage."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def setup(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    # ── sessions ──
    async def create(self, session: ChatSession) -> None:
        await self._conn.execute(
            "INSERT INTO chat_sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session.workflow_id,
                session.emp_code,
                session.mode,
                session.status,
                session.dumps(),
                session.created_at,
                session.updated_at,
            ),
        )
        await self._conn.commit()

    @staticmethod
    def _row(row: Any) -> ChatSession:
        workflow_id, emp_code, mode, status, state, created_at, updated_at = row
        return ChatSession(workflow_id, emp_code, mode, status, created_at, updated_at, json.loads(state))

    async def get(self, workflow_id: str) -> ChatSession | None:
        async with self._conn.execute("SELECT * FROM chat_sessions WHERE workflow_id = ?", (workflow_id,)) as cur:
            row = await cur.fetchone()
        return self._row(row) if row else None

    async def exists(self, workflow_id: str) -> bool:
        """Whether this workflow is a chat session (any status): the 0.4 routes, not the 0.3 ones."""
        async with self._conn.execute("SELECT 1 FROM chat_sessions WHERE workflow_id = ?", (workflow_id,)) as cur:
            return await cur.fetchone() is not None

    async def get_owned(self, workflow_id: str, emp_code: str) -> ChatSession | None:
        """The session, only if it belongs to this employee and is open."""
        session = await self.get(workflow_id)
        if session is None or session.emp_code != emp_code or session.status != OPEN:
            return None
        return session

    async def save(self, session: ChatSession, *, touch: bool = True) -> None:
        if touch:
            session.updated_at = time.time()
        await self._conn.execute(
            "UPDATE chat_sessions SET status = ?, state = ?, updated_at = ? WHERE workflow_id = ?",
            (session.status, session.dumps(), session.updated_at, session.workflow_id),
        )
        await self._conn.commit()

    async def stale(self, emp_code: str, older_than: float) -> list[ChatSession]:
        """The employee's open sessions not touched since `older_than` (epoch seconds)."""
        async with self._conn.execute(
            "SELECT * FROM chat_sessions WHERE emp_code = ? AND status = ? AND updated_at < ?",
            (emp_code, OPEN, older_than),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row(r) for r in rows]

    # ── staged file bytes ──
    async def put_file(self, workflow_id: str, key: str, name: str, type_: str, data: bytes) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO chat_files VALUES (?, ?, ?, ?, ?, ?, ?)",
            (workflow_id, key, name, type_, len(data), data, time.time()),
        )
        await self._conn.commit()

    async def get_file(self, workflow_id: str, key: str) -> StoredFile | None:
        async with self._conn.execute(
            "SELECT file_key, name, type, size, data FROM chat_files WHERE workflow_id = ? AND file_key = ?",
            (workflow_id, key),
        ) as cur:
            row = await cur.fetchone()
        return StoredFile(row[0], row[1], row[2], row[3], bytes(row[4])) if row else None

    async def delete_files(self, workflow_id: str, keys: list[str] | None = None) -> None:
        """Deletes these files of the workflow (all of them when `keys` is None)."""
        if keys is None:
            await self._conn.execute("DELETE FROM chat_files WHERE workflow_id = ?", (workflow_id,))
        else:
            await self._conn.executemany(
                "DELETE FROM chat_files WHERE workflow_id = ? AND file_key = ?", [(workflow_id, k) for k in keys]
            )
        await self._conn.commit()

    async def file_keys(self, workflow_id: str) -> list[str]:
        async with self._conn.execute(
            "SELECT file_key FROM chat_files WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    # ── the employee's chat storage ("localStorage") ──
    async def load_storage(self, emp_code: str) -> EmployeeStorage:
        async with self._conn.execute(
            "SELECT key, value FROM chat_local_storage WHERE emp_code = ?", (emp_code,)
        ) as cur:
            rows = await cur.fetchall()
        return EmployeeStorage({k: v for k, v in rows})

    async def save_storage(self, emp_code: str, storage: EmployeeStorage) -> None:
        changes = storage.changes()
        if not changes:
            return
        now = time.time()
        for key, value in changes.items():
            if value is None:
                await self._conn.execute(
                    "DELETE FROM chat_local_storage WHERE emp_code = ? AND key = ?", (emp_code, key)
                )
            else:
                await self._conn.execute(
                    "INSERT OR REPLACE INTO chat_local_storage VALUES (?, ?, ?, ?)", (emp_code, key, value, now)
                )
        await self._conn.commit()
        storage.mark_saved()


class WorkflowFiles:
    """The staged file bytes of one chat (uploaded but not yet sent to the OCR, files read in this session)."""

    def __init__(self, store: SessionStore, workflow_id: str) -> None:
        self._store = store
        self._workflow_id = workflow_id

    async def put(self, key: str, name: str, type_: str, data: bytes) -> None:
        await self._store.put_file(self._workflow_id, key, name, type_, data)

    async def get(self, key: str) -> StoredFile | None:
        return await self._store.get_file(self._workflow_id, key)

    async def delete(self, *keys: str) -> None:
        if keys:
            await self._store.delete_files(self._workflow_id, list(keys))

    async def keys(self) -> list[str]:
        return await self._store.file_keys(self._workflow_id)


class Locks:
    """Per workflow, turns run one at a time; per employee, taking OCR results runs one at a time (design §2).

    In memory: the agent runs as one process (as the 0.3 agent's per-employee locks).
    """

    def __init__(self) -> None:
        self._turns: dict[str, asyncio.Lock] = {}
        self._ocr: dict[str, asyncio.Lock] = {}

    def turn(self, workflow_id: str) -> asyncio.Lock:
        return self._turns.setdefault(workflow_id, asyncio.Lock())

    def ocr(self, emp_code: str) -> asyncio.Lock:
        return self._ocr.setdefault(emp_code, asyncio.Lock())


ReleaseHeld = Callable[[ChatSession], Awaitable[bool]]


async def expire_idle_sessions(
    store: SessionStore,
    locks: Locks,
    emp_code: str,
    release: ReleaseHeld,
    *,
    idle_seconds: float = IDLE_SECONDS,
    now: float | None = None,
) -> list[str]:
    """Closes the employee's sessions not touched for `idle_seconds` (a closed tab), giving back what they hold.

    `release(session)` gives back the session's taken OCR results (POST Ocr/Release with the caller's token) and
    returns whether it did; a session whose results could not be given back stays as it is and is tried again on
    the employee's next request (the old recoverOcrClaims, tried again on the next load). Sessions with a turn
    running are left alone. Returns the ids of the sessions closed.
    """
    now = time.time() if now is None else now
    closed: list[str] = []
    for session in await store.stale(emp_code, now - idle_seconds):
        if locks.turn(session.workflow_id).locked():
            continue
        if session.held_document_ids():
            try:
                released = await release(session)
            except Exception:  # noqa: BLE001 - tried again on the next request
                log.warning("could not give back the OCR results of an idle chat", exc_info=True)
                released = False
            if not released:
                continue
        session.status = EXPIRED
        await store.save(session, touch=False)
        await store.delete_files(session.workflow_id)
        closed.append(session.workflow_id)
    return closed
