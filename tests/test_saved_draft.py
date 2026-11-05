"""Reading a saved draft and writing it back (§5.2, BIL-42..44).

The first cases are ported from Tour_React tests/claimChatbot/savedClaim.test.ts. The round-trip
test shows that saved rows, their files, leave rows and remarks come back unchanged.
"""

from __future__ import annotations

import json
from datetime import date

from tests.fakes import FLIGHT_OUT
from tourclaim_agent.domain.claim import Bill, ExistingDraft, Origin
from tourclaim_agent.domain.models import TourSummary
from tourclaim_agent.drafting.merge import merge_bill
from tourclaim_agent.drafting.ocr_mapping import extract_bill
from tourclaim_agent.drafting.payload import build_draft_request
from tourclaim_agent.drafting.saved_draft import saved_counts, workspace_from_draft

TOUR = TourSummary(
    tourId=7, source="Noida", destination="Pune", tourStartDate="2026-09-10", tantetiveEndDate="2026-09-12", totalDays=3
)


def saved_journey(day: str) -> dict:
    """A saved journey as the server returns it: dates with a time part, times with seconds."""
    return {
        "DateOfTravelSource": f"{day}T00:00:00",
        "TimeOfTravelSource": "09:00:00",
        "Source": "Noida",
        "ModeOfTravel": "rail",
        "DateOfTravelDestination": f"{day}T00:00:00",
        "TimeOfTravelDestiNation": "18:00:00",
        "Destination": "Pune",
        "BilledAmount": 500,
        "ClaimedAmount": 500,
        "TaxStructure": "NA",
        "Remark": f"remark for {day}",
    }


# The saved draft of the React example: bills for 10 Sep and 12 Sep, none for 11 Sep.
DRAFT = {
    "draftId": 1,
    "lastSavedDate": "2026-09-20T10:00:00",
    "draftData": {
        "TransportaionDetailDtos": json.dumps([saved_journey("2026-09-10"), saved_journey("2026-09-12")]),
        "AccomodationDetailDto": "[]",
        "transportationFileReferences": [
            {"fileId": "f-10", "originalFileName": "ticket-10.pdf", "fileSize": 100, "rowIndex": 0},
            {"fileId": "f-12", "originalFileName": "ticket-12.pdf", "fileSize": 120, "rowIndex": 1},
        ],
        "accomodationFileReferences": [],
    },
}


def bill_journey(day: str, time: str = "9:00") -> dict:
    """An OCR result for a Noida → Pune train ticket."""
    return {
        "tourDetails": {
            "startDate": day,
            "startTime": time,
            "endDate": day,
            "endTime": "18:00",
            "from": "Noida",
            "to": "Pune",
            "modeOfTravel": "rail",
            "amountDetails": {"billedAmount": 450, "invoiceNo": f"R-{day}"},
        }
    }


def parsed(raw: dict = DRAFT):
    draft = ExistingDraft.from_backend(raw)
    assert draft is not None
    return workspace_from_draft(7, draft, TOUR)


class TestPortedFromReact:
    def test_dates_and_times_as_the_chat_keeps_them_and_rows_keep_their_files(self):
        ws = parsed()
        assert [r.depart_date for r in ws.transport] == [date(2026, 9, 10), date(2026, 9, 12)]
        assert ws.transport[0].depart_time == "09:00"
        assert [[f.file_id for f in r.saved_files] for r in ws.transport] == [["f-10"], ["f-12"]]
        assert all(r.origin is Origin.SAVED for r in ws.transport)
        assert ws.draft_id == 1

    def test_new_day_is_added_after_the_saved_rows_and_a_second_copy_is_already_saved(self):
        ws = parsed()
        new = merge_bill(
            ws, Bill(document_id=11, file_name="ticket-11.pdf", sha256="a"), extract_bill(bill_journey("2026-09-11"))
        )
        copy = merge_bill(
            ws, Bill(document_id=12, file_name="scan.jpg", sha256="b"), extract_bill(bill_journey("2026-09-10"))
        )
        assert [r.kind for r in new] == ["added"] and [r.kind for r in copy] == ["already_saved"]
        # Stored: the saved rows keep their places; the new one goes after them.
        assert [r.depart_date.day for r in ws.transport] == [10, 12, 11]
        assert [r.saved_files[0].file_id if r.saved_files else None for r in ws.transport] == ["f-10", "f-12", None]

    def test_a_saved_journey_in_another_date_and_time_format_is_not_added_twice(self):
        ws = parsed()
        [result] = merge_bill(
            ws, Bill(document_id=5, file_name="copy.pdf", sha256="c"), extract_bill(bill_journey("10-09-2026", "09:00"))
        )
        assert result.kind == "already_saved" and len(ws.transport) == 2


class TestRoundTrip:
    DRAFT_WITH_EVERYTHING = {
        "draftId": 778,
        "empTourPlanId": 7,
        "draftData": {
            "purposeOfTour": "Inspection",
            "recipentId": 4321,
            "claimAmount": 1700,
            "remarks": "entered in the claim form",
            "transportaionDetailDtos": [
                {**saved_journey("2026-09-10"), "InvoiceNumber": "R-1", "Id": 31},
            ],
            "accomodationDetailDto": json.dumps(
                [
                    {
                        "location": "Pune",
                        "hotelName": "Deccan Stay",
                        "dateOfCheckIn": "2026-09-10",
                        "dateOfCheckOut": "2026-09-12",
                        "billedAmount": 1200,
                        "claimedAmount": 1200,
                        "taxStructure": "SGST_CGST",
                        "billCGSTAmount": 30,
                        "billSGSTAmount": 30,
                        "Id": 41,
                    }
                ]
            ),
            "leaveAvailedDto": json.dumps(
                [{"LeaveStartDate": "2026-09-11", "LeaveEndDate": "2026-09-11", "DADeductable": True}]
            ),
            "dailyAllowanceDto": [{"Date": "2026-09-10", "Amount": 800}],
            "isLeaveAvailedwithbetweentour": True,
            "documentsAttached": {
                "IsApprovedTourProgramme": True,
                "IsTickets_BoardingPass_Bills": True,
                "IsHotelBills": True,
            },
            "transportationFileReferences": [{"fileId": 501, "originalFileName": "out.pdf", "rowIndex": 0}],
            "accomodationFileReferences": json.dumps([{"fileId": 502, "displayName": "hotel.pdf", "rowIndex": 0}]),
        },
    }

    def test_saved_rows_files_leave_and_remarks_come_back_unchanged(self):
        source = self.DRAFT_WITH_EVERYTHING
        ws = parsed(source)
        merge_bill(
            ws, Bill(document_id=900, file_name="return.pdf", sha256="r"), extract_bill(bill_journey("2026-09-12"))
        )
        request = build_draft_request(ws, emp_id=1001)
        draft = request.draft_data
        saved_data = source["draftData"]

        assert draft["DraftId"] == 778 and draft["EmpTourPlanId"] == 7
        assert draft["RetainedTransportationFileIds"] == [501]
        assert draft["RetainedAccomodationFileIds"] == [502]
        assert request.transport_file_ids == [900] and request.stay_file_ids == []

        journeys = json.loads(draft["TransportaionDetailDtos"])
        saved_row = {**saved_data["transportaionDetailDtos"][0], "StartFileIndexCount": -1, "EndFileIndexCount": -1}
        assert journeys[0] == saved_row, "the saved journey is written back exactly as read"
        assert (journeys[1]["StartFileIndexCount"], journeys[1]["EndFileIndexCount"]) == (0, 0)
        assert journeys[1]["DateOfTravelSource"] == "2026-09-12"

        [stay] = json.loads(draft["AccomodationDetailDto"])
        assert stay["Location"] == "Pune" and stay["BillCGSTAmount"] == 30 and stay["Id"] == 41
        assert (stay["StartFileIndexCount"], stay["EndFileIndexCount"]) == (-1, -1)
        assert "location" not in stay, "written back under the API's own field names"

        assert draft["LeaveAvailedDto"] == saved_data["leaveAvailedDto"]
        assert json.loads(draft["DailyAllowanceDto"]) == saved_data["dailyAllowanceDto"]
        assert draft["IsLeaveAvailedwithbetweentour"] is True
        assert draft["remarks"] == "entered in the claim form"
        assert draft["RecipentId"] == 4321 and draft["PurposeOfTour"] == "Inspection"
        assert draft["DocumentsAttached"]["IsApprovedTourProgramme"] is True
        assert draft["ClaimAmount"] == 1700 + 450, "the saved total plus the new row"
        assert not any(k.lower().endswith("filereferences") for k in draft), "file references are never sent back"
        assert not any(k.lower() == "transportaiondetaildtos" and k != "TransportaionDetailDtos" for k in draft)

    def test_reading_the_written_draft_gives_the_same_saved_rows(self):
        ws = parsed(self.DRAFT_WITH_EVERYTHING)
        written = build_draft_request(ws, emp_id=1001).draft_data
        again = workspace_from_draft(7, ExistingDraft(draft_id=778, data=written), TOUR)
        assert [(r.source, r.destination, r.depart_date, r.money) for r in again.transport] == [
            (r.source, r.destination, r.depart_date, r.money) for r in ws.transport
        ]
        assert [(s.location, s.check_in_date, s.money) for s in again.stays] == [
            (s.location, s.check_in_date, s.money) for s in ws.stays
        ]

    def test_flat_draft_and_counts(self):
        flat = {"draftId": 3, **DRAFT["draftData"]}
        draft = ExistingDraft.from_backend(flat)
        assert draft.draft_id == 3 and saved_counts(draft) == (2, 0)

    def test_draft_without_an_id_is_not_a_draft(self):
        assert ExistingDraft.from_backend({"draftData": {}}) is None

    def test_new_claim_payload_is_unchanged(
        self,
    ):
        """A workspace that never came from a saved draft sends no retained lists, as before."""
        from tests.test_drafting import workspace

        ws = workspace()
        merge_bill(ws, Bill(document_id=1, file_name="out.pdf", sha256="x"), extract_bill(FLIGHT_OUT))
        draft = build_draft_request(ws, emp_id=1001).draft_data
        assert "RetainedTransportationFileIds" not in draft and draft["DraftId"] is None
