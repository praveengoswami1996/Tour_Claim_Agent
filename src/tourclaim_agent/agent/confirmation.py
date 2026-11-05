"""Confirmation of irreversible operations (FRS CNF-01..06).

A confirmation is bound to a SHA-256 hash of the exact payload that will be sent. It is
single-use and expires. If anything in the payload changes after the employee confirmed,
the hashes no longer match and the operation is refused. Not yet used by any operation:
write endpoints stay disabled until their payloads are verified against UAT.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from tourclaim_agent.state.records import PendingConfirmation
from tourclaim_agent.state.registry import WorkflowRegistry


class ConfirmationError(Exception):
    """The confirmation is unknown, expired, already used, or does not match the payload."""


def payload_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ConfirmationService:
    def __init__(self, registry: WorkflowRegistry, ttl_seconds: int) -> None:
        self._registry = registry
        self._ttl = ttl_seconds

    async def request(self, workflow_id: str, operation: str, payload: Any) -> PendingConfirmation:
        pending = PendingConfirmation(
            confirmation_id=secrets.token_urlsafe(16),
            workflow_id=workflow_id,
            operation=operation,
            payload_hash=payload_hash(payload),
            expires_at=time.time() + self._ttl,
        )
        await self._registry.save_confirmation(pending)
        return pending

    async def consume(
        self, confirmation_id: str, workflow_id: str, presented_hash: str, payload: Any
    ) -> PendingConfirmation:
        """Validate and mark used. Raises ConfirmationError on any mismatch."""
        pending = await self._registry.take_confirmation(confirmation_id, workflow_id)
        if pending is None:
            raise ConfirmationError("This confirmation is unknown or was already used.")
        if time.time() > pending.expires_at:
            raise ConfirmationError("This confirmation has expired. Please review the summary again.")
        current = payload_hash(payload)
        if not secrets.compare_digest(presented_hash, pending.payload_hash) or not secrets.compare_digest(
            current, pending.payload_hash
        ):
            raise ConfirmationError("The details changed after you confirmed. Please review them again.")
        return pending
