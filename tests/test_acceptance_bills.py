"""Acceptance scenarios 1-14 of bill_upload_auto_draft_requirements.md §9, against the fake backend."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes import PDF, FakeBackend, make_token, tour
from tests.test_api import auth, say, start, texts
from tests.test_claim_journey import event, upload
from tourclaim_agent.drafting.bill_pipeline import AIR_NOT_IN_PLAN
from tourclaim_agent.main import create_app

A, B, C = 601, 602, 603


def plan(source: str, destination: str, out_day: str, back_day: str, mode: str = "Train") -> dict[str, Any]:
    us = lambda d: f"{d[5:7]}/{d[8:10]}/{d[:4]} 00:00:00"  # noqa: E731 - .NET en-US dates
    return {
        "empTourDetails": {
            "purposeOfTour": "Inspection",
            "source": source,
            "destination": destination,
            "tentativeStartDate": us(out_day),
            "noOfDays": str(int(back_day[8:10]) - int(out_day[8:10]) + 1),
        },
        "tentativeTourPlanList": [
            {"date": us(out_day), "from": source, "to": destination, "travelMode": mode},
            {"date": us(back_day), "from": destination, "to": source, "travelMode": mode},
        ],
        "transportationList": [],
        "accommodationList": [],
    }


def ticket(source: str, destination: str | None, day: str | None, mode: str = "Train", amount: int = 1200):
    head: dict[str, Any] = {"from": source, "modeOfTravel": mode, "amountDetails": {"billedAmount": amount}}
    if destination:
        head["to"] = destination
    if day:
        head |= {"startDate": day, "startTime": "08:00", "endDate": day, "endTime": "16:00"}
    head["amountDetails"]["invoiceNo"] = f"INV-{source[:3]}-{day}"
    return {"tourDetails": head}


def hotel(location: str, check_in: str, check_out: str, *, in_time: str = "12:00", out_time: str = "11:00"):
    return {
        "accommodationDetails": {
            "entries": [
                {
                    "location": location,
                    "hotelName": f"{location} Residency",
                    "checkInDate": check_in,
                    "checkInTime": in_time,
                    "checkOutDate": check_out,
                    "checkOutTime": out_time,
                    "invoice": {"billedAmount": 6000, "invoiceNo": f"H-{check_in}"},
                }
            ]
        }
    }


@pytest.fixture
def backend() -> FakeBackend:
    fake = FakeBackend()
    fake.outside_tours[1001] = [
        tour(A, 1001, kind_dest="Mumbai", status_id=2, status="Tour Approved", start="2026-10-05", days=4),
        tour(B, 1001, kind_dest="Pune", status_id=2, status="Tour Approved", start="2026-10-20", source="Delhi"),
    ]
    fake.prefills = {
        A: plan("Noida", "Mumbai", "2026-10-05", "2026-10-08"),
        B: plan("Delhi", "Pune", "2026-10-20", "2026-10-22"),
        C: plan("Noida", "Mumbai", "2026-10-06", "2026-10-09"),
    }
    return fake


@pytest.fixture
def saving(settings):
    return settings.model_copy(update={"enabled_write_endpoints": ["Claim/SaveRaiseClaimDraft"]})


@pytest.fixture
def client(saving, backend):
    with TestClient(create_app(saving, http_transport=backend.transport())) as test_client:
        yield test_client


def check(client: TestClient, workflow_id: str) -> list[dict]:
    response = client.post(f"/v1/workflows/{workflow_id}/bills/check?stream=false", headers=auth())
    assert response.status_code == 200, response.text
    return response.json()["events"]


def report(events: list[dict]) -> dict[str, tuple[str, int | None]]:
    lines = event(events, "bill_report")["files"]
    return {line["file_name"]: (line["outcome"], line["tour_id"]) for line in lines}


def saved_rows(request: dict, field: str = "TransportaionDetailDtos") -> list[dict]:
    return json.loads(request["draft"][field])


def scenario_1(client, backend) -> tuple[str, list[dict]]:
    workflow_id = start(client)
    response = upload(client, workflow_id, ("train.pdf", PDF + b"1"), ("hotel.pdf", PDF + b"2"))
    assert response.status_code == 200
    backend.finish_ocr("train.pdf", ticket("Noida", "Mumbai", "2026-10-05"))
    backend.finish_ocr("hotel.pdf", hotel("Mumbai", "2026-10-05", "2026-10-08"))
    return workflow_id, check(client, workflow_id)


def test_1_upload_with_no_claim_one_tour_fits(client, backend):
    workflow_id = start(client)
    events = upload(client, workflow_id, ("train.pdf", PDF + b"1"), ("hotel.pdf", PDF + b"2")).json()["events"]
    assert "I'm reading them now" in texts(events) and event(events, "bill_status")["pending"] == 2
    backend.finish_ocr("train.pdf", ticket("Noida", "Mumbai", "2026-10-05"))
    backend.finish_ocr("hotel.pdf", hotel("Mumbai", "2026-10-05", "2026-10-08"))
    events = check(client, workflow_id)
    assert report(events) == {"train.pdf": ("saved", A), "hotel.pdf": ("saved", A)}
    assert event(events, "draft_saved")["tour_id"] == A
    [request] = backend.saved_requests
    assert request["draft"]["DraftId"] is None and request["draft"]["EmpTourPlanId"] == A
    assert request["files"] == [("transportationFiles", "train.pdf"), ("accommodationFiles", "hotel.pdf")]
    assert event(events, "bill_report")["tours"][0]["coverage"] == (
        "These bills add 05–08 Oct 2026. Every day of the tour is now covered."
    )


def test_2_mixed_upload_goes_to_two_tours(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("train.pdf", PDF + b"1"), ("hotel.pdf", PDF + b"2"), ("pune.pdf", PDF + b"3"))
    backend.finish_ocr("train.pdf", ticket("Noida", "Mumbai", "2026-10-05"))
    backend.finish_ocr("hotel.pdf", hotel("Mumbai", "2026-10-05", "2026-10-08"))
    backend.finish_ocr("pune.pdf", ticket("Delhi", "Pune", "2026-10-20"))
    events = check(client, workflow_id)
    assert report(events) == {"train.pdf": ("saved", A), "hotel.pdf": ("saved", A), "pune.pdf": ("saved", B)}
    assert [t["tour_id"] for t in event(events, "bill_report")["tours"]] == [A, B]
    assert {e["tour_id"] for e in events if e["type"] == "draft_saved"} == {A, B}


EXISTING_A = {
    "draftId": 778,
    "empTourPlanId": A,
    "draftData": {
        "PurposeOfTour": "Inspection",
        "Remarks": "typed in the claim form",
        "TransportaionDetailDtos": json.dumps(
            [
                {
                    "Id": 11,
                    "DateOfTravelSource": "2026-10-05T00:00:00",
                    "TimeOfTravelSource": "08:00:00",
                    "Source": "Noida",
                    "Destination": "Mumbai",
                    "ModeOfTravel": "Train",
                    "BilledAmount": 1200,
                    "ClaimedAmount": 1200,
                    "InvoiceNumber": "INV-Noi-2026-10-05",
                    "Remark": "outbound",
                }
            ]
        ),
        "AccomodationDetailDto": "[]",
        "LeaveAvailedDto": json.dumps([{"LeaveStartDate": "2026-10-06", "LeaveEndDate": "2026-10-06"}]),
        "transportationFileReferences": [{"fileId": 55, "originalFileName": "outbound.pdf", "rowIndex": 0}],
        "accomodationFileReferences": [],
    },
}


def test_3_existing_draft_gets_the_return_ticket_after_the_saved_row(client, backend):
    backend.drafts[A] = json.loads(json.dumps(EXISTING_A))
    workflow_id = start(client)
    upload(client, workflow_id, ("return.pdf", PDF + b"r"))
    backend.finish_ocr("return.pdf", ticket("Mumbai", "Noida", "2026-10-08"))
    events = check(client, workflow_id)
    assert report(events) == {"return.pdf": ("saved", A)}
    [request] = backend.saved_requests
    draft = request["draft"]
    assert draft["DraftId"] == 778 and draft["RetainedTransportationFileIds"] == [55]
    rows = saved_rows(request)
    assert [(r["Source"], r["StartFileIndexCount"], r["EndFileIndexCount"]) for r in rows] == [
        ("Noida", -1, -1),
        ("Mumbai", 0, 0),
    ]
    original = json.loads(EXISTING_A["draftData"]["TransportaionDetailDtos"])[0]
    assert {k: rows[0][k] for k in original} == original, "the saved row is unchanged"
    assert draft["LeaveAvailedDto"] == EXISTING_A["draftData"]["LeaveAvailedDto"]
    assert draft["Remarks"] == "typed in the claim form"
    assert request["files"] == [("transportationFiles", "return.pdf")]


def test_4_already_saved(client, backend):
    workflow_id, _ = scenario_1(client, backend)
    events = upload(client, workflow_id, ("train-copy.pdf", PDF + b"1")).json()["events"]
    assert "Skipped, already uploaded: train-copy.pdf" in texts(events)
    upload(client, workflow_id, ("train-scan.jpg", b"\xff\xd8\xffscan"))
    backend.finish_ocr("train-scan.jpg", ticket("Noida", "Mumbai", "2026-10-05"))
    events = check(client, workflow_id)
    assert report(events) == {"train-scan.jpg": ("already_saved", A)}
    assert len(backend.saved_requests) == 1, "the draft was not changed"


class TestScenario5CantTell:
    @pytest.fixture(autouse=True)
    def second_mumbai_tour(self, backend):
        backend.outside_tours[1001].append(
            tour(C, 1001, kind_dest="Mumbai", status_id=2, status="Tour Approved", start="2026-10-06", days=4)
        )

    def ask(self, client, backend) -> tuple[str, dict]:
        workflow_id = start(client)
        upload(client, workflow_id, ("taxi.pdf", PDF + b"taxi"))
        backend.finish_ocr("taxi.pdf", ticket("Mumbai", "Mumbai Airport", None, mode="Taxi", amount=450))
        events = check(client, workflow_id)
        assert report(events) == {"taxi.pdf": ("awaiting_tour", None)}
        return workflow_id, event(events, "choices")

    def test_asks_which_tour_and_saves_into_the_chosen_one(self, client, backend):
        workflow_id, choices = self.ask(client, backend)
        assert choices["prompt"] == "Which tour are these bills for?"
        assert [o["tour_id"] for o in choices["options"]][:2] == [A, C]
        assert choices["options"][-1]["label"] == "None of these"
        assert backend.saved_requests == []
        events = say(client, workflow_id, "2")
        assert report(events) == {"taxi.pdf": ("saved", C)}
        assert backend.saved_requests[0]["draft"]["EmpTourPlanId"] == C

    def test_none_of_these_releases_the_bill(self, client, backend):
        workflow_id, choices = self.ask(client, backend)
        events = say(client, workflow_id, str(choices["options"][-1]["number"]))
        assert report(events) == {"taxi.pdf": ("no_tour", None)}
        assert backend.released == [next(iter(backend.ocr_docs))] and backend.saved_requests == []

    def test_the_question_stays_open_across_other_messages(self, client, backend):
        workflow_id, _ = self.ask(client, backend)
        say(client, workflow_id, "show my tours")
        assert report(say(client, workflow_id, "2")) == {"taxi.pdf": ("saved", C)}


def test_6_fits_no_tour(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("old.pdf", PDF + b"old"))
    backend.finish_ocr("old.pdf", ticket("Noida", "Mumbai", "2026-07-05"))
    events = check(client, workflow_id)
    assert report(events) == {"old.pdf": ("no_tour", None)}
    assert backend.released == list(backend.ocr_docs) and backend.saved_requests == []


def test_7_draft_that_cant_be_read_is_not_written_and_is_retried(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("train.pdf", PDF + b"1"))
    backend.finish_ocr("train.pdf", ticket("Noida", "Mumbai", "2026-10-05"))
    backend.fail_next("Claim/GetRaiseClaimDraft", 500)
    events = check(client, workflow_id)
    assert report(events) == {"train.pdf": ("save_failed", A)}
    assert backend.saved_requests == [] and backend.released == list(backend.ocr_docs)
    events = check(client, workflow_id)
    assert report(events) == {"train.pdf": ("saved", A)}


def test_8_result_taken_by_another_device(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("train.pdf", PDF + b"1"))
    backend.finish_ocr("train.pdf", ticket("Noida", "Mumbai", "2026-10-05"))
    backend.taken_elsewhere.add(next(iter(backend.ocr_docs)))
    assert report(check(client, workflow_id)) == {"train.pdf": ("used_elsewhere", None)}
    assert backend.saved_requests == []


def test_9_saving_off_keeps_the_claim_in_the_workflow(settings, backend):
    with TestClient(create_app(settings, http_transport=backend.transport())) as client:
        workflow_id = start(client)
        upload(client, workflow_id, ("train.pdf", PDF + b"1"), ("hotel.pdf", PDF + b"2"))
        backend.finish_ocr("train.pdf", ticket("Noida", "Mumbai", "2026-10-05"))
        backend.finish_ocr("hotel.pdf", hotel("Mumbai", "2026-10-05", "2026-10-08"))
        events = check(client, workflow_id)
        assert report(events) == {"train.pdf": ("ready_not_saved", A), "hotel.pdf": ("ready_not_saved", A)}
        assert "Saving drafts from the chat is switched off" in texts(events)
        assert not any("SaveRaiseClaimDraft" in c for c in backend.calls)
        assert backend.released == []
        summary = event(say(client, workflow_id, "review my claim"), "claim_summary")
        assert summary["tour_id"] == A and summary["journeys"][0]["bills"] == ["train.pdf"]


def test_10_closed_the_chat(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("train.pdf", PDF + b"1"), ("hotel.pdf", PDF + b"2"))
    backend.finish_ocr("train.pdf", ticket("Noida", "Mumbai", "2026-10-05"))
    backend.finish_ocr("hotel.pdf", hotel("Mumbai", "2026-10-05", "2026-10-08"))
    assert client.post("/v1/sessions", headers=auth()).json()["bills"] == {"pending": 0, "unreported": 2}
    later = start(client)  # a new conversation the next day
    assert set(report(check(client, later))) == {"train.pdf", "hotel.pdf"}
    assert [e["type"] for e in check(client, later)] == ["bill_status"]
    assert client.post("/v1/sessions", headers=auth()).json()["bills"] == {"pending": 0, "unreported": 0}


def test_11_read_only_token(client, backend):
    workflow_id = start(client)
    headers = {"Authorization": f"Bearer {make_token('E1001', IsReadOnly='True')}"}
    response = client.post(
        f"/v1/workflows/{workflow_id}/attachments", headers=headers, files=[("files", ("a.pdf", PDF, "x"))]
    )
    assert response.status_code == 403 and response.json()["error"] == "read_only"
    assert not any("Ocr" in c for c in backend.calls)


def test_12_air_not_in_plan(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("flight.pdf", PDF + b"f"))
    backend.finish_ocr("flight.pdf", ticket("Delhi", "Pune", "2026-10-20", mode="Flight", amount=5400))
    events = check(client, workflow_id)
    line = event(events, "bill_report")["files"][0]
    assert (line["outcome"], line["tour_id"], line["detail"]) == ("saved", B, AIR_NOT_IN_PLAN)
    [request] = backend.saved_requests
    assert saved_rows(request)[0]["ModeOfTravel"] == ""


def test_13_bad_stay_is_not_added(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("hotel.pdf", PDF + b"h"))
    backend.finish_ocr("hotel.pdf", hotel("Mumbai", "2026-10-07", "2026-10-06"))
    line = event(check(client, workflow_id), "bill_report")["files"][0]
    assert (line["outcome"], line["detail"]) == ("problem", "the check-out date is before the check-in date")
    assert backend.saved_requests == []


def test_14_two_tabs_process_each_bill_once(client, backend):
    workflow_id = start(client)
    upload(client, workflow_id, ("train.pdf", PDF + b"1"), ("hotel.pdf", PDF + b"2"))
    backend.finish_ocr("train.pdf", ticket("Noida", "Mumbai", "2026-10-05"))
    backend.finish_ocr("hotel.pdf", hotel("Mumbai", "2026-10-05", "2026-10-08"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: check(client, workflow_id), range(2)))
    lines = [line for events in results for e in events if e["type"] == "bill_report" for line in e["files"]]
    assert sorted(line["file_name"] for line in lines) == ["hotel.pdf", "train.pdf"]
    assert len(backend.saved_requests) == 1
