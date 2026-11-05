"""Unit tests: OCR mapping, merging bills into rows, checks and the draft payload contract."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from tests.fakes import FLIGHT_OUT, HOTEL, MUMBAI_PREFILL
from tourclaim_agent.domain.claim import Bill, ClaimPrefill, ClaimWorkspace, Origin, TaxStructure
from tourclaim_agent.drafting.analysis import Severity, analyse, blocking
from tourclaim_agent.drafting.merge import merge_bill
from tourclaim_agent.drafting.ocr_mapping import extract_bill
from tourclaim_agent.drafting.payload import build_draft_request


def workspace() -> ClaimWorkspace:
    return ClaimWorkspace.from_prefill(503, ClaimPrefill.model_validate(MUMBAI_PREFILL))


def bill(doc_id: int, name: str) -> Bill:
    return Bill(document_id=doc_id, file_name=name, sha256=f"sha-{doc_id}", status="Completed")


def codes(ws: ClaimWorkspace) -> set[str]:
    return {f.code for f in analyse(ws)}


class TestPrefill:
    def test_dotnet_dates_and_plan_legs(self):
        ws = workspace()
        assert ws.tour_start == date(2026, 8, 20) and ws.tour_end == date(2026, 8, 22)
        assert [(r.source, r.destination, r.depart_date) for r in ws.transport] == [
            ("Noida", "Mumbai", date(2026, 8, 20)),
            ("Mumbai", "Noida", date(2026, 8, 22)),
        ]
        assert all(r.origin is Origin.PLAN and not r.bill_ids for r in ws.transport)


class TestOcrMapping:
    def test_flight(self):
        extract = extract_bill(FLIGHT_OUT)
        journey = extract.journeys[0]
        assert extract.ok and journey.depart_time == "07:15"
        assert journey.money.billed == Decimal("6500.00") and journey.money.tax_structure is TaxStructure.IGST

    def test_hotel_with_dash_dates(self):
        stay = extract_bill(HOTEL).stays[0]
        assert stay.check_in_date == date(2026, 8, 20) and stay.check_out_date == date(2026, 8, 22)

    def test_failed_or_garbage_results(self):
        failed = {"meta": {"per_file_results": [{"success": False, "error": "blurred"}]}}
        assert not extract_bill(failed).ok and extract_bill(failed).error == "blurred"
        assert not extract_bill("not json").ok
        assert not extract_bill({"tourDetails": {"amountDetails": "x"}}).ok


class TestMerge:
    def test_bill_matches_planned_leg(self):
        ws = workspace()
        out = bill(1, "out.pdf")
        merge_bill(ws, out, extract_bill(FLIGHT_OUT))
        first = ws.transport[0]
        assert first.bill_ids == [1] and first.money.billed == Decimal("6500.00")
        assert first.origin is Origin.PLAN  # matched, not added
        assert len(ws.transport) == 2 and out.used_by == [first.key]

    def test_unmatched_journey_becomes_new_row(self):
        ws = workspace()
        other = {"tourDetails": {"startDate": "2026-08-21", "from": "Mumbai", "to": "Pune", "modeOfTravel": "Taxi"}}
        merge_bill(ws, bill(2, "taxi.pdf"), extract_bill(other))
        assert len(ws.transport) == 3
        assert [r.destination for r in ws.transport] == ["Mumbai", "Pune", "Noida"]  # sorted by date

    def test_multi_leg_ticket_counts_amount_once(self):
        ws = workspace()
        round_trip = {
            "tourDetails": {
                "startDate": "2026-08-20",
                "from": "Noida",
                "to": "Mumbai",
                "modeOfTravel": "Flight",
                "amountDetails": {"billedAmount": 12000, "invoiceNo": "RT-1"},
                "tourLegs": [{"departureDate": "2026-08-22", "from": "Mumbai", "to": "Noida"}],
            }
        }
        merge_bill(ws, bill(3, "rt.pdf"), extract_bill(round_trip))
        assert [r.bill_ids for r in ws.transport] == [[3], [3]]
        assert [r.money.billed for r in ws.transport] == [Decimal("12000.00"), None]

    def test_unreadable_bill(self):
        ws = workspace()
        b = bill(4, "blurry.jpg")
        ws.bills.append(b)
        merge_bill(ws, b, extract_bill({"meta": {"per_file_results": [{"success": False}]}}))
        assert b.kind == "unreadable" and "UNREADABLE_BILL" in codes(ws)


class TestAnalysis:
    def test_plan_only_claim_lists_missing_bills_and_da_note(self):
        found = codes(workspace())
        assert {"MISSING_BILL", "DA_IN_FORM"} <= found
        assert not blocking(analyse(workspace()))

    def test_complete_claim_has_no_warnings(self):
        ws = workspace()
        merge_bill(ws, bill(1, "out.pdf"), extract_bill(FLIGHT_OUT))
        back = json.loads(json.dumps(FLIGHT_OUT).replace("2026-08-20", "2026-08-22"))
        back["tourDetails"].update({"from": "Mumbai", "to": "Noida"})
        back["tourDetails"]["amountDetails"]["invoiceNo"] = "AI-456"
        merge_bill(ws, bill(2, "back.pdf"), extract_bill(back))
        merge_bill(ws, bill(3, "hotel.pdf"), extract_bill(HOTEL))
        severities = {f.severity for f in analyse(ws)}
        assert severities == {Severity.INFO}

    def test_claimed_above_billed_blocks(self):
        ws = workspace()
        merge_bill(ws, bill(1, "out.pdf"), extract_bill(FLIGHT_OUT))
        ws.transport[0].money.claimed = Decimal("7000")
        assert "CLAIMED_ABOVE_BILLED" in {f.code for f in blocking(analyse(ws))}

    def test_broken_chain_and_dates_outside_tour(self):
        ws = workspace()
        ws.transport[1].source = "Pune"
        ws.transport[1].depart_date = ws.transport[1].arrive_date = date(2026, 8, 25)
        found = codes(ws)
        assert {"BROKEN_CHAIN", "AFTER_TOUR"} <= found

    def test_missing_date_blocks(self):
        ws = workspace()
        ws.transport[0].depart_date = None
        assert "MISSING_DATE" in {f.code for f in blocking(analyse(ws))}

    def test_duplicate_invoice(self):
        ws = workspace()
        merge_bill(ws, bill(1, "out.pdf"), extract_bill(FLIGHT_OUT))
        merge_bill(ws, bill(2, "copy.pdf"), extract_bill(FLIGHT_OUT))
        assert "DUPLICATE_INVOICE" in codes(ws)


class TestPayload:
    def test_matches_backend_contract(self):
        ws = workspace()
        merge_bill(ws, bill(11, "out.pdf"), extract_bill(FLIGHT_OUT))
        merge_bill(ws, bill(12, "hotel.pdf"), extract_bill(HOTEL))
        request = build_draft_request(ws, emp_id=1001)
        draft = request.draft_data

        assert draft["EmpId"] == 1001 and draft["EmpTourPlanId"] == 503 and draft["RecipentId"] is None
        assert draft["TantetiveDateOfDeparture"] == "2026-08-20" and draft["NoOfDays"] == 3
        assert draft["ClaimAmount"] == 15500.0
        assert draft["DailyAllowanceDto"] == "[]" and draft["LeaveAvailedDto"] == "[]"
        assert draft["DocumentsAttached"] == {
            "IsApprovedTourProgramme": None,
            "IsTickets_BoardingPass_Bills": True,
            "IsHotelBills": True,
        }

        transport = json.loads(draft["TransportaionDetailDtos"])  # a JSON string, as the API expects
        assert transport[0]["TimeOfTravelDestiNation"] == "09:30"
        assert (transport[0]["StartFileIndexCount"], transport[0]["EndFileIndexCount"]) == (0, 0)
        assert (transport[1]["StartFileIndexCount"], transport[1]["EndFileIndexCount"]) == (-1, -1)
        assert transport[0]["TaxStructure"] == "IGST" and transport[0]["IGSTAmount"] == 310.0
        assert transport[1]["TaxStructure"] == "NA"

        stays = json.loads(draft["AccomodationDetailDto"])
        assert stays[0]["DateOfCheckIn"] == "2026-08-20" and stays[0]["HotelName"] == "Harbour Inn"
        assert (stays[0]["StartFileIndexCount"], stays[0]["EndFileIndexCount"]) == (0, 0)
        assert request.transport_file_ids == [11] and request.stay_file_ids == [12]

    def test_shared_ticket_is_sent_for_each_row(self):
        ws = workspace()
        ws.transport[0].bill_ids = [5]
        ws.transport[1].bill_ids = [5, 6]
        request = build_draft_request(ws, emp_id=1)
        rows = json.loads(request.draft_data["TransportaionDetailDtos"])
        assert [(r["StartFileIndexCount"], r["EndFileIndexCount"]) for r in rows] == [(0, 0), (1, 2)]
        assert request.transport_file_ids == [5, 5, 6]

    def test_claimed_tax_zero_when_claiming_less_than_billed(self):
        ws = workspace()
        merge_bill(ws, bill(1, "out.pdf"), extract_bill(FLIGHT_OUT))
        ws.transport[0].money.claimed = Decimal("5000")
        row = json.loads(build_draft_request(ws, 1).draft_data["TransportaionDetailDtos"])[0]
        assert row["BillIGSTAmount"] == 310.0 and row["IGSTAmount"] == 0.0
