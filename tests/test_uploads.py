"""Uploading bills at any time (BIL-01..07) and the employee-level upload store (§5.1)."""

from __future__ import annotations

import time

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from tests.fakes import PDF, FakeBackend, make_token
from tests.test_api import start
from tests.test_claim_journey import event, upload
from tourclaim_agent.main import create_app
from tourclaim_agent.state.uploads import Outcome, UploadRecord, UploadStatus, UploadStore


@pytest.fixture
def client(settings, backend: FakeBackend):
    with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
        yield test_client


@pytest.fixture
async def store():
    async with aiosqlite.connect(":memory:") as conn:
        uploads = UploadStore(conn)
        await uploads.setup()
        yield uploads


def record(document_id: int, sha: str = "h1", emp_code: str = "E1001", **extra) -> UploadRecord:
    return UploadRecord(
        document_id=document_id,
        emp_code=emp_code,
        workflow_id="wf",
        file_name=f"f{document_id}.pdf",
        file_size=10,
        sha256=sha,
        reference="agent:wf:unassigned",
        **extra,
    )


class TestUploadEndpoint:
    def test_upload_without_a_claim_starts_reading(self, client, backend):
        workflow_id = start(client)
        response = upload(client, workflow_id, ("a.pdf", PDF), ("b.pdf", PDF + b"b"))
        assert response.status_code == 200
        events = response.json()["events"]
        assert "Got 2 files: a.pdf, b.pdf. I'm reading them now" in event(events, "text")["text"]
        status = event(events, "bill_status")
        assert status["pending"] == 2
        assert [f["file_name"] for f in status["files"]] == ["a.pdf", "b.pdf"]
        assert all(d["reference"] == f"agent:{workflow_id}:unassigned" for d in backend.ocr_docs.values())

    def test_same_content_is_skipped_in_any_workflow(self, client, backend):
        upload(client, start(client), ("a.pdf", PDF))
        events = upload(client, start(client), ("copy.pdf", PDF), ("new.pdf", PDF + b"n")).json()["events"]
        assert "Skipped, already uploaded: copy.pdf" in event(events, "notice")["text"]
        assert [d["fileName"] for d in backend.ocr_docs.values()] == ["a.pdf", "new.pdf"]

    def test_repeat_inside_one_upload_is_skipped(self, client, backend):
        events = upload(client, start(client), ("a.pdf", PDF), ("a2.pdf", PDF)).json()["events"]
        assert "a2.pdf" in event(events, "notice")["text"] and len(backend.ocr_docs) == 1

    def test_only_duplicates_returns_status_without_uploading(self, client, backend):
        workflow_id = start(client)
        upload(client, workflow_id, ("a.pdf", PDF))
        events = upload(client, workflow_id, ("a.pdf", PDF)).json()["events"]
        assert [e["type"] for e in events] == ["notice", "bill_status"] and len(backend.ocr_docs) == 1

    def test_read_only_session_is_refused_before_any_ocr_call(self, client, backend):
        workflow_id = start(client)
        headers = {"Authorization": f"Bearer {make_token('E1001', IsReadOnly='True')}"}
        response = client.post(
            f"/v1/workflows/{workflow_id}/attachments", headers=headers, files=[("files", ("a.pdf", PDF, "x"))]
        )
        assert response.status_code == 403 and response.json()["error"] == "read_only"
        assert not any("Ocr" in c for c in backend.calls)

    def test_too_many_files_is_refused_whole(self, client, backend):
        files = [(f"f{i}.pdf", PDF + bytes([i])) for i in range(21)]
        response = upload(client, start(client), *files)
        assert response.status_code == 400 and "at most 20 files" in response.json()["message"]
        assert backend.ocr_docs == {}


class TestUploadStore:
    async def test_duplicate_rules(self, store):
        await store.add(record(1, "h1"))
        assert await store.already_uploaded("E1001", "h1")
        assert not await store.already_uploaded("E2002", "h1"), "other employees are never compared"
        await store.set_outcome("E1001", 1, status=UploadStatus.PROCESSED, outcome=Outcome.UNREADABLE)
        assert not await store.already_uploaded("E1001", "h1"), "an unreadable earlier copy may be uploaded again"
        await store.add(record(2, "h2"))
        await store.set_outcome("E1001", 2, status=UploadStatus.RELEASED, outcome=Outcome.NO_TOUR)
        assert not await store.already_uploaded("E1001", "h2"), "a copy given back unused may be uploaded again"
        await store.add(record(3, "h3"))
        await store.set_outcome("E1001", 3, status=UploadStatus.PROCESSED, outcome=Outcome.SAVED)
        assert await store.already_uploaded("E1001", "h3")

    async def test_outcomes_are_reported_once(self, store):
        await store.add(record(1))
        await store.set_outcome("E1001", 1, status=UploadStatus.PROCESSED, outcome=Outcome.SAVED, tour_id=5)
        assert [r.document_id for r in await store.unreported("E1001", 14)] == [1]
        await store.mark_reported("E1001", [1])
        assert await store.unreported("E1001", 14) == []
        # The same outcome again (a retried save that fails the same way) is not told twice.
        await store.set_outcome("E1001", 1, status=UploadStatus.PROCESSED, outcome=Outcome.SAVED, tour_id=5)
        assert await store.unreported("E1001", 14) == []

    async def test_old_unreported_outcomes_are_dropped(self, store):
        await store.add(record(1))
        await store.set_outcome("E1001", 1, status=UploadStatus.PROCESSED, outcome=Outcome.SAVED)
        await store.update("E1001", 1, processed_at=time.time() - 15 * 86400)
        assert await store.unreported("E1001", 14) == []

    async def test_counts(self, store):
        await store.add(record(1))
        await store.add(record(2, "h2"))
        await store.set_outcome("E1001", 2, status=UploadStatus.PROCESSED, outcome=Outcome.SAVED)
        assert await store.counts("E1001", 14) == {"pending": 1, "unreported": 1}
        assert await store.has_work("E1001") and not await store.has_work("E2002")
