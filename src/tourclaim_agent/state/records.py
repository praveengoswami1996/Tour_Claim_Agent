from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowRecord:
    workflow_id: str
    emp_code: str
    title: str
    status: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class PendingConfirmation:
    confirmation_id: str
    workflow_id: str
    operation: str
    payload_hash: str
    expires_at: float
