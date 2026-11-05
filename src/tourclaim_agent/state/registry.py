"""Which workflows exist, who owns them, and pending confirmations (FRS STA-02/04, CNF).

Conversation state itself lives in the LangGraph checkpointer; this registry holds the
ownership and lifecycle data needed to list, resume and abandon workflows safely.
SQLite for now; a PostgreSQL implementation can replace it behind the same methods.
"""

from __future__ import annotations

import time
import uuid

import aiosqlite

from tourclaim_agent.state.records import PendingConfirmation, WorkflowRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_workflows (
    workflow_id TEXT PRIMARY KEY,
    emp_code    TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_workflows_owner ON agent_workflows (emp_code, status, updated_at);
CREATE TABLE IF NOT EXISTS agent_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    operation       TEXT NOT NULL,
    payload_hash    TEXT NOT NULL,
    expires_at      REAL NOT NULL
);
"""

OPEN = "open"
ABANDONED = "abandoned"


class WorkflowRegistry:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def setup(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def create(self, emp_code: str, title: str = "New conversation") -> WorkflowRecord:
        now = time.time()
        record = WorkflowRecord(uuid.uuid4().hex, emp_code, title, OPEN, now, now)
        await self._conn.execute(
            "INSERT INTO agent_workflows VALUES (?, ?, ?, ?, ?, ?)",
            (record.workflow_id, record.emp_code, record.title, record.status, now, now),
        )
        await self._conn.commit()
        return record

    async def get_owned(self, workflow_id: str, emp_code: str) -> WorkflowRecord | None:
        """Return the workflow only if it belongs to this employee and is open."""
        async with self._conn.execute(
            "SELECT * FROM agent_workflows WHERE workflow_id = ? AND emp_code = ? AND status = ?",
            (workflow_id, emp_code, OPEN),
        ) as cursor:
            row = await cursor.fetchone()
        return WorkflowRecord(*row) if row else None

    async def list_open(self, emp_code: str, limit: int = 20) -> list[WorkflowRecord]:
        async with self._conn.execute(
            "SELECT * FROM agent_workflows WHERE emp_code = ? AND status = ? ORDER BY updated_at DESC LIMIT ?",
            (emp_code, OPEN, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [WorkflowRecord(*row) for row in rows]

    async def touch(self, workflow_id: str, title: str | None = None) -> None:
        if title:
            await self._conn.execute(
                "UPDATE agent_workflows SET updated_at = ?, title = ? WHERE workflow_id = ?",
                (time.time(), title, workflow_id),
            )
        else:
            await self._conn.execute(
                "UPDATE agent_workflows SET updated_at = ? WHERE workflow_id = ?", (time.time(), workflow_id)
            )
        await self._conn.commit()

    async def abandon(self, workflow_id: str, emp_code: str) -> bool:
        cursor = await self._conn.execute(
            "UPDATE agent_workflows SET status = ?, updated_at = ? WHERE workflow_id = ? AND emp_code = ?",
            (ABANDONED, time.time(), workflow_id, emp_code),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def save_confirmation(self, pending: PendingConfirmation) -> None:
        await self._conn.execute(
            "INSERT INTO agent_confirmations VALUES (?, ?, ?, ?, ?)",
            (
                pending.confirmation_id,
                pending.workflow_id,
                pending.operation,
                pending.payload_hash,
                pending.expires_at,
            ),
        )
        await self._conn.commit()

    async def take_confirmation(self, confirmation_id: str, workflow_id: str) -> PendingConfirmation | None:
        """Fetch and delete in one step, so a confirmation can be used only once."""
        async with self._conn.execute(
            "DELETE FROM agent_confirmations WHERE confirmation_id = ? AND workflow_id = ? RETURNING *",
            (confirmation_id, workflow_id),
        ) as cursor:
            row = await cursor.fetchone()
        await self._conn.commit()
        return PendingConfirmation(*row) if row else None
