"""Journey J1 end to end through the HTTP interface, with the backend faked."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FLIGHT_BACK, FLIGHT_OUT, HOTEL, PDF, FakeBackend, make_token
from tests.test_api import auth, say, start, texts
from tourclaim_agent.main import create_app


@pytest.fixture
def save_enabled(settings):
    return settings.model_copy(update={"enabled_write_endpoints": ["Claim/SaveRaiseClaimDraft"]})


@pytest.fixture
def client(settings, backend: FakeBackend):
    with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
        yield test_client


@pytest.fixture
def saving_client(save_enabled, backend: FakeBackend):
    with TestClient(create_app(save_enabled, http_transport=backend.transport())) as test_client:
        yield test_client


def upload(client: TestClient, workflow_id: str, *files: tuple[str, bytes], emp_code: str = "E1001"):
    return client.post(
        f"/v1/workflows/{workflow_id}/attachments?stream=false",
        headers=auth(emp_code),
        files=[("files", (name, content, "application/octet-stream")) for name, content in files],
    )


def event(events: list[dict], kind: str) -> dict:
    return next(e for e in events if e["type"] == kind)


def writes(backend: FakeBackend) -> list[str]:
    return [c for c in backend.calls if c.startswith(("POST", "PUT", "DELETE")) and "/Ocr/" not in c]


def prepared(client: TestClient, backend: FakeBackend) -> str:
    """Start the Mumbai claim and have both bills read."""
    workflow_id = start(client)
    say(client, workflow_id, "start a claim for my Mumbai tour")
    assert upload(client, workflow_id, ("out.pdf", PDF), ("hotel.pdf", PDF + b"2")).status_code == 200
    backend.finish_ocr("out.pdf", FLIGHT_OUT)
    backend.finish_ocr("hotel.pdf", HOTEL)
    return workflow_id


class TestStart:
    def test_start_seeds_rows_from_the_approved_plan(self, client):
        events = say(client, start(client), "start a claim for my Mumbai tour")
        summary = event(events, "claim_summary")
        assert summary["tour_id"] == 503
        assert [j["route"] for j in summary["journeys"]] == ["Noida → Mumbai", "Mumbai → Noida"]
        assert event(events, "upload_request")["max_files"] == 20
        assert not summary["save_enabled"]

    def test_only_approved_tours_can_be_claimed(self, client):
        events = say(client, start(client), "start a claim for tour 501")
        assert "can only be started on an approved tour" in texts(events)

    def test_other_employees_tour_is_refused(self, client, backend):
        events = say(client, start(client), "raise claim for tour 777")
        assert "couldn't find tour 777" in texts(events)
        assert not any("777" in c for c in backend.calls)

    def test_existing_draft_is_not_overwritten(self, client, backend):
        backend.drafts[503] = {"draftId": 42, "empTourPlanId": 503}
        events = say(client, start(client), "start a claim for my Mumbai tour")
        assert "already have a saved draft" in texts(events)
        assert event(events, "draft_saved")["draft_id"] == 42


class TestBills:
    def test_upload_tagged_with_the_claim_in_progress(self, client, backend):
        workflow_id = start(client)
        say(client, workflow_id, "start a claim for my Mumbai tour")
        assert upload(client, workflow_id, ("out.pdf", PDF)).status_code == 200
        assert [d["reference"] for d in backend.ocr_docs.values()] == [f"agent:{workflow_id}:tour-503"]

    @pytest.mark.parametrize(
        ("name", "content", "reason"),
        [("bill.exe", PDF, "only PDF"), ("bill.pdf", b"MZ\x90\x00", "valid PDF"), ("bill.pdf", b"", "empty")],
    )
    def test_bad_files_rejected(self, client, name, content, reason):
        workflow_id = start(client)
        say(client, workflow_id, "start a claim for my Mumbai tour")
        response = upload(client, workflow_id, (name, content))
        assert response.status_code == 400 and reason in response.json()["message"]

    def test_bills_are_read_matched_and_checked(self, client, backend):
        workflow_id = prepared(client, backend)
        assert all(d["reference"] == f"agent:{workflow_id}:tour-503" for d in backend.ocr_docs.values())
        events = say(client, workflow_id, "review my claim")
        summary = event(events, "claim_summary")
        assert summary["journeys"][0]["bills"] == ["out.pdf"]
        assert summary["stays"][0]["place"] == "Harbour Inn"
        assert summary["total_claimed"] == "₹15,500.00"
        missing = [f for f in summary["findings"] if f["code"] == "MISSING_BILL"]
        assert len(missing) == 1 and "Mumbai → Noida" in missing[0]["message"]
        assert summary["bills_pending"] == 0

    def test_duplicate_upload_skipped(self, client, backend):
        workflow_id = prepared(client, backend)
        events = upload(client, workflow_id, ("again.pdf", PDF)).json()["events"]
        assert "already uploaded" in events[0]["text"] and len(backend.ocr_docs) == 2

    def test_result_taken_elsewhere_is_not_applied(self, client, backend):
        workflow_id = start(client)
        say(client, workflow_id, "start a claim for my Mumbai tour")
        upload(client, workflow_id, ("out.pdf", PDF))
        backend.finish_ocr("out.pdf", FLIGHT_OUT)
        backend.taken_elsewhere.add(next(iter(backend.ocr_docs)))
        summary = event(say(client, workflow_id, "review my claim"), "claim_summary")
        assert summary["journeys"][0]["bills"] == []

    def test_pending_bills_reported(self, client, backend):
        workflow_id = start(client)
        say(client, workflow_id, "start a claim for my Mumbai tour")
        upload(client, workflow_id, ("out.pdf", PDF))
        events = say(client, workflow_id, "review my claim")
        assert "still reading 1 bill" in texts(events)


class TestSave:
    def test_save_is_off_by_default(self, client, backend):
        workflow_id = prepared(client, backend)
        events = say(client, workflow_id, "save draft")
        assert "isn't switched on yet" in texts(events)
        assert writes(backend) == []

    def test_read_only_session_cannot_upload(self, client, backend):
        workflow_id = start(client)
        say(client, workflow_id, "start a claim for my Mumbai tour")
        headers = {"Authorization": f"Bearer {make_token('E1001', IsReadOnly='True')}"}
        # A new token for the same employee; identity is re-resolved from the cache.
        response = client.post(
            f"/v1/workflows/{workflow_id}/attachments", headers=headers, files=[("files", ("a.pdf", PDF, "x"))]
        )
        assert response.status_code == 403 and response.json()["error"] == "read_only"

    def test_save_draft_sends_exact_payload_and_files(self, saving_client, backend):
        workflow_id = prepared(saving_client, backend)
        events = say(saving_client, workflow_id, "save draft")
        saved = event(events, "draft_saved")
        assert saved["draft_id"] == 9001
        assert saved["open_in_form"] == "/raise-claim-approver?tourId=503&isDraft=true&statusId=2&totalDays=3"
        assert "Nothing has been submitted" in texts(events)

        request = backend.saved_requests[0]
        assert request["files"] == [("transportationFiles", "out.pdf"), ("accommodationFiles", "hotel.pdf")]
        rows = json.loads(request["draft"]["TransportaionDetailDtos"])
        assert [(r["StartFileIndexCount"], r["EndFileIndexCount"]) for r in rows] == [(0, 0), (-1, -1)]
        assert request["draft"]["EmpId"] == 1001 and request["draft"]["DraftId"] is None
        # Never submitted: no submit endpoint was called.
        assert not any("Submit" in c for c in backend.calls)

    def test_save_after_automatic_save_sends_nothing_again(self, saving_client, backend):
        workflow_id = prepared(saving_client, backend)
        say(saving_client, workflow_id, "save draft")
        events = say(saving_client, workflow_id, "save draft")
        assert len(backend.saved_requests) == 1, "the bills were saved once, automatically"
        assert event(events, "draft_saved")["draft_id"] == 9001

    def test_a_later_bill_is_added_to_the_same_draft_and_saved_files_are_kept(self, saving_client, backend):
        workflow_id = prepared(saving_client, backend)
        say(saving_client, workflow_id, "review my claim")
        first = backend.saved_requests[0]["draft"]
        saved_files = [r["fileId"] for r in backend.drafts[503]["draftData"]["transportationFileReferences"]]
        assert upload(saving_client, workflow_id, ("back.pdf", PDF + b"back")).status_code == 200
        backend.finish_ocr("back.pdf", FLIGHT_BACK)
        events = saving_client.post(f"/v1/workflows/{workflow_id}/bills/check?stream=false", headers=auth()).json()
        second = backend.saved_requests[1]["draft"]
        assert second["DraftId"] == 9001 and first["DraftId"] is None
        assert second["RetainedTransportationFileIds"] == saved_files
        assert backend.saved_requests[1]["files"] == [("transportationFiles", "back.pdf")]
        rows = json.loads(second["TransportaionDetailDtos"])
        assert len(rows) == 2, "the planned return leg was filled in place, not added again"
        assert [(r["StartFileIndexCount"], r["EndFileIndexCount"]) for r in rows] == [(-1, -1), (0, 0)]
        assert event(events["events"], "bill_report")["files"][0]["outcome"] == "saved"

    def test_blocking_problem_prevents_save(self, saving_client, backend):
        backend.prefills[503] = {
            **backend.prefills[503],
            "tentativeTourPlanList": [{"date": "", "from": "Noida", "to": "Mumbai", "travelMode": "Flight"}],
        }
        workflow_id = start(saving_client)
        say(saving_client, workflow_id, "start a claim for my Mumbai tour")
        events = say(saving_client, workflow_id, "save draft")
        assert "Not saved yet" in texts(events) and backend.saved_requests == []

    def test_bills_are_added_to_a_draft_made_elsewhere_meanwhile(self, saving_client, backend):
        workflow_id = prepared(saving_client, backend)
        backend.drafts[503] = {
            "draftId": 55,
            "empTourPlanId": 503,
            "draftData": {"LeaveAvailedDto": "[]", "remarks": "from the form"},
        }
        events = say(saving_client, workflow_id, "save draft")
        assert event(events, "draft_saved")["draft_id"] == 55
        [request] = backend.saved_requests
        assert request["draft"]["DraftId"] == 55 and request["draft"]["remarks"] == "from the form"


class TestCancel:
    def test_cancel_hands_back_ocr_results(self, client, backend):
        workflow_id = prepared(client, backend)
        say(client, workflow_id, "review my claim")
        upload(client, workflow_id, ("late.pdf", PDF + b"late"))
        events = say(client, workflow_id, "cancel this claim")
        assert "stopped preparing the claim for Tour 503" in texts(events)
        late_id = max(backend.ocr_docs)
        assert backend.cancelled == [late_id]
        assert sorted(backend.released) == sorted(d for d in backend.ocr_docs if d != late_id)
        events = say(client, workflow_id, "review my claim")
        assert "not preparing a claim" in texts(events)
