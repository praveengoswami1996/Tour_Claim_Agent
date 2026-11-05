from __future__ import annotations

import time

import aiosqlite
import pytest

from tourclaim_agent.agent.confirmation import ConfirmationError, ConfirmationService, payload_hash
from tourclaim_agent.state.registry import WorkflowRegistry


@pytest.fixture
async def registry():
    conn = await aiosqlite.connect(":memory:")
    reg = WorkflowRegistry(conn)
    await reg.setup()
    yield reg
    await conn.close()


PAYLOAD = {"draftId": 7, "ClaimAmount": 1250.5, "rows": [{"Source": "Noida", "Destination": "Mumbai"}]}


def test_hash_ignores_key_order():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})


async def test_confirmed_payload_is_accepted_once(registry):
    service = ConfirmationService(registry, ttl_seconds=60)
    pending = await service.request("wf1", "submit_claim", PAYLOAD)
    await service.consume(pending.confirmation_id, "wf1", pending.payload_hash, PAYLOAD)
    with pytest.raises(ConfirmationError, match="already used"):
        await service.consume(pending.confirmation_id, "wf1", pending.payload_hash, PAYLOAD)


async def test_changed_payload_is_refused(registry):
    service = ConfirmationService(registry, ttl_seconds=60)
    pending = await service.request("wf1", "submit_claim", PAYLOAD)
    changed = {**PAYLOAD, "ClaimAmount": 9999}
    with pytest.raises(ConfirmationError, match="changed"):
        await service.consume(pending.confirmation_id, "wf1", pending.payload_hash, changed)


async def test_other_workflow_cannot_use_confirmation(registry):
    service = ConfirmationService(registry, ttl_seconds=60)
    pending = await service.request("wf1", "submit_claim", PAYLOAD)
    with pytest.raises(ConfirmationError):
        await service.consume(pending.confirmation_id, "wf2", pending.payload_hash, PAYLOAD)


async def test_expired_confirmation_is_refused(registry, monkeypatch):
    service = ConfirmationService(registry, ttl_seconds=60)
    pending = await service.request("wf1", "submit_claim", PAYLOAD)
    monkeypatch.setattr(time, "time", lambda: pending.expires_at + 1)
    with pytest.raises(ConfirmationError, match="expired"):
        await service.consume(pending.confirmation_id, "wf1", pending.payload_hash, PAYLOAD)
