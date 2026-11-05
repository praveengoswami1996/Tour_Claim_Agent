"""Every bill an employee uploaded through the agent, across all their workflows (BIL-05, §5.1).

The store answers: has this employee already uploaded this file (BIL-04)? Which bills are
still being read? What happened to each bill, and has the employee been told (BIL-63)?
SQLite for now; a PostgreSQL implementation can replace it behind the same methods.
Every query filters by the employee code.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, fields
from enum import StrEnum

import aiosqlite


class UploadStatus(StrEnum):
    PENDING = "pending"  # in the OCR queue
    COMPLETED_UNPROCESSED = "completed_unprocessed"  # read, not yet applied (or handed back to retry)
    AWAITING_TOUR = "awaiting_tour"  # read and claimed, waiting for the employee to name the tour
    PROCESSED = "processed"  # final outcome recorded
    RELEASED = "released"  # handed back unused for good (no tour, "none of these")
    CANCELLED = "cancelled"  # withdrawn from the OCR queue


class Outcome(StrEnum):
    """What happened to one file (BIL-61), strongest first."""

    SAVED = "saved"
    ALREADY_SAVED = "already_saved"
    READY_NOT_SAVED = "ready_not_saved"
    SAVE_FAILED = "save_failed"
    TOUR_NOT_CLAIMABLE = "tour_not_claimable"
    NO_TOUR = "no_tour"
    AWAITING_TOUR = "awaiting_tour"
    PROBLEM = "problem"
    NOTHING_FOUND = "nothing_found"
    UNREADABLE = "unreadable"
    USED_ELSEWHERE = "used_elsewhere"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    SLOW = "slow"  # still being read after TCA_BILL_SLOW_MINUTES (BIL-16)


OUTCOME_ORDER = list(Outcome)


def strongest(outcomes: list[Outcome]) -> Outcome:
    return min(outcomes, key=OUTCOME_ORDER.index)


@dataclass
class UploadRecord:
    document_id: int
    emp_code: str
    workflow_id: str
    file_name: str
    file_size: int
    sha256: str
    reference: str
    status: str = UploadStatus.PENDING
    tour_id: int | None = None
    outcome: str | None = None
    outcome_detail: str | None = None
    found: str | None = None  # what was read, e.g. "ticket Noida → Mumbai, 05 Oct 2026"
    reported: bool = True
    slow_reported: bool = False
    created_at: float = 0.0
    processed_at: float | None = None


_COLUMNS = [f.name for f in fields(UploadRecord)]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_uploads (
    document_id    INTEGER PRIMARY KEY,
    emp_code       TEXT NOT NULL,
    workflow_id    TEXT NOT NULL,
    file_name      TEXT NOT NULL,
    file_size      INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    reference      TEXT NOT NULL,
    status         TEXT NOT NULL,
    tour_id        INTEGER,
    outcome        TEXT,
    outcome_detail TEXT,
    found          TEXT,
    reported       INTEGER NOT NULL DEFAULT 1,
    slow_reported  INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    processed_at   REAL
);
CREATE INDEX IF NOT EXISTS ix_agent_uploads_owner ON agent_uploads (emp_code, status);
CREATE INDEX IF NOT EXISTS ix_agent_uploads_hash ON agent_uploads (emp_code, sha256);
"""

# An earlier upload of the same content blocks a new one unless it was never usable.
_REUSABLE_STATUSES = (UploadStatus.RELEASED.value, UploadStatus.CANCELLED.value)


def _record(row: aiosqlite.Row | tuple) -> UploadRecord:
    record = UploadRecord(*row)
    record.reported = bool(record.reported)
    record.slow_reported = bool(record.slow_reported)
    return record


class UploadStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def setup(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def _select(self, where: str, params: tuple) -> list[UploadRecord]:
        sql = f"SELECT {', '.join(_COLUMNS)} FROM agent_uploads WHERE {where} ORDER BY created_at, document_id"
        async with self._conn.execute(sql, params) as cursor:
            return [_record(row) for row in await cursor.fetchall()]

    async def add(self, record: UploadRecord) -> None:
        record.created_at = record.created_at or time.time()
        values = [getattr(record, c) for c in _COLUMNS]
        await self._conn.execute(
            f"INSERT OR REPLACE INTO agent_uploads ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
            values,
        )
        await self._conn.commit()

    async def already_uploaded(self, emp_code: str, sha256: str) -> bool:
        """BIL-04: the same content was uploaded before and is still in use (not unreadable, not given back)."""
        placeholders = ", ".join("?" * len(_REUSABLE_STATUSES))
        async with self._conn.execute(
            f"SELECT 1 FROM agent_uploads WHERE emp_code = ? AND sha256 = ? AND status NOT IN ({placeholders}) "
            "AND COALESCE(outcome, '') != ? LIMIT 1",
            (emp_code, sha256, *_REUSABLE_STATUSES, Outcome.UNREADABLE.value),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def get(self, emp_code: str, document_ids: list[int]) -> list[UploadRecord]:
        if not document_ids:
            return []
        placeholders = ", ".join("?" * len(document_ids))
        return await self._select(f"emp_code = ? AND document_id IN ({placeholders})", (emp_code, *document_ids))

    async def with_status(self, emp_code: str, *statuses: UploadStatus) -> list[UploadRecord]:
        placeholders = ", ".join("?" * len(statuses))
        return await self._select(f"emp_code = ? AND status IN ({placeholders})", (emp_code, *statuses))

    async def has_work(self, emp_code: str) -> bool:
        """Anything a check could do: bills being read or read but not yet applied."""
        async with self._conn.execute(
            "SELECT 1 FROM agent_uploads WHERE emp_code = ? AND status IN (?, ?) LIMIT 1",
            (emp_code, UploadStatus.PENDING.value, UploadStatus.COMPLETED_UNPROCESSED.value),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def update(self, emp_code: str, document_id: int, **changes: object) -> None:
        unknown = set(changes) - set(_COLUMNS)
        if unknown:
            raise ValueError(f"unknown upload columns: {unknown}")
        assignments = ", ".join(f"{k} = ?" for k in changes)
        await self._conn.execute(
            f"UPDATE agent_uploads SET {assignments} WHERE emp_code = ? AND document_id = ?",
            (*changes.values(), emp_code, document_id),
        )
        await self._conn.commit()

    async def set_outcome(
        self,
        emp_code: str,
        document_id: int,
        *,
        status: UploadStatus,
        outcome: Outcome,
        detail: str | None = None,
        found: str | None = None,
        tour_id: int | None = None,
    ) -> None:
        """Record a file's outcome. It is reported once; the same outcome again is not re-reported."""
        current = await self.get(emp_code, [document_id])
        same_as_told = bool(current) and current[0].outcome == outcome.value and current[0].reported
        await self.update(
            emp_code,
            document_id,
            status=status.value,
            outcome=outcome.value,
            outcome_detail=detail,
            found=found,
            tour_id=tour_id,
            reported=1 if same_as_told else 0,
            processed_at=time.time(),
        )

    async def unreported(self, emp_code: str, keep_days: int) -> list[UploadRecord]:
        """Outcomes not yet told to the employee (BIL-63). Older ones are dropped."""
        cutoff = time.time() - keep_days * 86400
        await self._conn.execute(
            "UPDATE agent_uploads SET reported = 1 WHERE emp_code = ? AND reported = 0 AND processed_at < ?",
            (emp_code, cutoff),
        )
        await self._conn.commit()
        return await self._select("emp_code = ? AND reported = 0", (emp_code,))

    async def mark_reported(self, emp_code: str, document_ids: list[int]) -> None:
        for document_id in document_ids:
            await self.update(emp_code, document_id, reported=1)

    async def counts(self, emp_code: str, keep_days: int) -> dict[str, int]:
        pending = len(await self.with_status(emp_code, UploadStatus.PENDING))
        unreported = len(await self.unreported(emp_code, keep_days))
        return {"pending": pending, "unreported": unreported}
