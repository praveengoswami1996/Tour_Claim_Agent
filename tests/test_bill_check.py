"""POST bills/check and processing at the start of each message (BIL-10..17, BIL-60..64)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FLIGHT_OUT, HOTEL, PDF, FakeBackend
from tests.test_api import auth, say, start, texts
from tests.test_claim_journey import event, upload
from tourclaim_agent.main import create_app


@pytest.fixture
def saving(settings):
    return settings.model_copy(update={"enabled_write_endpoints": ["Claim/SaveRaiseClaimDraft"]})


@pytest.fixture
def client(saving, backend: FakeBackend):
    with TestClient(create_app(saving, http_transport=backend.transport())) as test_client:
        yield test_client


def check(client: TestClient, workflow_id: str) -> list[dict]:
    response = client.post(f"/v1/workflows/{workflow_id}/bills/check?stream=false", headers=auth())
    assert response.status_code == 200, response.text
    return response.json()["events"]


def kinds(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def test_check_with_nothing_to_do_returns_just_the_status(client, backend):
    events = check(client, start(client))
    assert events == [{"type": "bill_status", "pending": 0, "files": []}]
    assert not any("Ocr" in c for c in backend.calls), "no OCR call when nothing is waiting"


def test_check_while_still_reading(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("out.pdf", PDF))
    assert check(client, workflow_id)[-1]["pending"] == 1


def test_finished_bills_are_saved_and_reported_once(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("out.pdf", PDF), ("hotel.pdf", PDF + b"h"))
    backend.finish_ocr("out.pdf", FLIGHT_OUT)
    backend.finish_ocr("hotel.pdf", HOTEL)
    events = check(client, workflow_id)
    assert kinds(events)[:3] == ["text", "bill_report", "draft_saved"] and events[-1]["type"] == "bill_status"
    report = event(events, "bill_report")
    assert [(f["file_name"], f["outcome"], f["tour_id"]) for f in report["files"]] == [
        ("out.pdf", "saved", 503),
        ("hotel.pdf", "saved", 503),
    ]
    assert report["files"][0]["found"] == "ticket Delhi (Noida) → Mumbai, 20 Aug 2026"
    assert report["files"][1]["found"] == "hotel stay in Mumbai, 20–22 Aug 2026"
    assert report["tours"][0]["draft_id"] == 9001 and report["tours"][0]["dates"] == "20–22 Aug 2026"
    assert "I read 2 bills. 2 are saved in your draft for Tour 503 (Noida → Mumbai, 20–22 Aug 2026)." in texts(events)
    assert check(client, workflow_id) == [{"type": "bill_status", "pending": 0, "files": []}]


def test_message_turn_processes_bills_first(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("out.pdf", PDF))
    backend.finish_ocr("out.pdf", FLIGHT_OUT)
    events = say(client, workflow_id, "show my tours")
    assert kinds(events).index("bill_report") < kinds(events).index("tour_list")


def test_sessions_counts_pending_and_unreported(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("out.pdf", PDF), ("hotel.pdf", PDF + b"h"))
    assert client.post("/v1/sessions", headers=auth()).json()["bills"] == {"pending": 2, "unreported": 0}


def test_unreadable_bill_can_be_read_again(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("scan.png", b"\x89PNG\r\n\x1a\nscan"))
    backend.finish_ocr("scan.png", None, failed=True)
    line = event(check(client, workflow_id), "bill_report")["files"][0]
    assert line["outcome"] == "unreadable" and line["detail"] == "could not read"
    response = client.post(
        f"/v1/workflows/{workflow_id}/bills/{line['retry_document_id']}/retry?stream=false", headers=auth()
    )
    assert response.status_code == 200 and backend.retried == [line["retry_document_id"]]
    assert event(response.json()["events"], "bill_status")["pending"] == 1


def test_slow_bill_is_reported_once_and_can_be_removed(client, backend, monkeypatch):
    workflow_id = start(client)
    upload(client, workflow_id, ("out.pdf", PDF))
    import tourclaim_agent.drafting.bill_pipeline as pipeline

    real = pipeline.time.time
    monkeypatch.setattr(pipeline.time, "time", lambda: real() + 16 * 60)
    line = event(check(client, workflow_id), "bill_report")["files"][0]
    assert line["outcome"] == "slow" and line["cancel_document_id"]
    assert "bill_report" not in kinds(check(client, workflow_id)), "reported once"
    response = client.post(f"/v1/workflows/{workflow_id}/bills/{line['cancel_document_id']}/cancel", headers=auth())
    assert response.status_code == 200 and backend.cancelled == [line["cancel_document_id"]]


def test_bill_of_another_employee_is_not_found(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("out.pdf", PDF))
    document_id = next(iter(backend.ocr_docs))
    other = start(client, "E2002")
    response = client.post(f"/v1/workflows/{other}/bills/{document_id}/retry", headers=auth("E2002"))
    assert response.status_code == 404 and backend.retried == []
