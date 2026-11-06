"""Checks of our own for claim_payload.py (claimPayloadBuilder.ts has no TypeScript test). Where a test states what
the old code sent, the expected value was taken from the TypeScript, run under Node; the deviations (saved rows
written back, retained file ids sent) are checked against 01_deviations.md."""

from __future__ import annotations

import json

import pytest

from tourclaim_agent.claimchat.claim_assistant import initial_claim_state, new_accommodation_row, new_transport_row
from tourclaim_agent.claimchat.claim_payload import (
    SAVED_ROW_KEY,
    assistant_draft_save,
    assistant_save_target,
    build_claim_payload,
    clean_object_for_payload,
    collect_new_files,
    js_json_stringify,
)
from tourclaim_agent.claimchat.js_compat import UNDEFINED, use_runtime


@pytest.fixture(autouse=True)
def fixed_clock():
    with use_runtime(now_ms=lambda: 1790000000000, random=lambda: 0.123456789):
        yield


def journey(**over):
    return {
        **new_transport_row(),
        "source": "Noida",
        "destination": "Pune",
        "DateOfTravelSource": "2026-10-05",
        "TimeOfTravelSource": "10:30",
        "DateOfTravelDestination": "2026-10-05",
        "TimeOfTravelDestiNation": "16:30:00",
        "ModeOfTravel": "rail",
        **over,
    }


def stay(**over):
    return {
        **new_accommodation_row(),
        "location": "Pune",
        "checkInDate": "2026-10-05",
        "checkOutDate": "2026-10-06",
        **over,
    }


NEW = {"name": "t.pdf", "size": 1, "type": "application/pdf", "documentId": 11}
SAVED = {"fileId": "f-1", "name": "old.pdf", "size": 3, "isRetained": True, "type": ""}


def dtos(payload, key):
    return json.loads(payload[key])


# ── cleanObjectForPayload and JSON.stringify ──


def test_clean_object_for_payload_drops_empty_values_keeps_zero_and_false():
    assert clean_object_for_payload(
        {"a": "", "b": None, "c": 0, "d": False, "e": {"x": ""}, "f": [], "g": ["", 1]}
    ) == {
        "c": 0,
        "d": False,
        "f": [],
        "g": [{}, 1],
    }
    assert clean_object_for_payload("") is UNDEFINED
    # Object.keys order: integer-like keys first
    assert list(clean_object_for_payload({"b": 1, "10": 2, "1": 3})) == ["1", "10", "b"]


def test_js_json_stringify_prints_numbers_as_javascript():
    assert js_json_stringify({"a": 5.0, "b": float("nan"), "c": UNDEFINED, "d": [UNDEFINED, 1e21], "e": "é\n\x01"}) == (
        '{"a":5,"b":null,"d":[null,1e+21],"e":"é\\n\\u0001"}'
    )


# ── the builder, as the old assistant used it ──


def test_a_draft_of_stays_only_keeps_the_blank_placeholder_journey():
    s = {**initial_claim_state(), "accommodationRows": [stay()], "includeAccommodation": True}
    r = assistant_draft_save(s, emp_id=101, tour_id=4522)
    assert r["draftDataText"] == (
        '{"EmpId":101,"EmpTourPlanId":4522,"LastEditedSection":"Transportation","IsPaidViaCreditCard":false,'
        '"TransportaionDetailDtos":"[{\\"Id\\":0,\\"ModeOfTravel\\":\\"select\\",\\"DistanceINKm\\":0,'
        '\\"BilledAmount\\":0,\\"ClaimedAmount\\":0,\\"SGSTAmount\\":0,\\"CGSTAmount\\":0,\\"IGSTAmount\\":0,'
        '\\"BillSGSTAmount\\":0,\\"BillCGSTAmount\\":0,\\"BillIGSTAmount\\":0,\\"TaxStructure\\":\\"SGST_CGST\\",'
        '\\"StartFileIndexCount\\":-1,\\"EndFileIndexCount\\":-1}]",'
        '"AccomodationDetailDto":"[{\\"Id\\":0,\\"Location\\":\\"Pune\\",\\"ClassOfCity\\":\\"Select\\",'
        '\\"IsOwnArrangement\\":false,\\"IsFreeAccomodation\\":false,\\"isCompanyLodginPaid\\":false,'
        '\\"HotelCity\\":\\"Pune\\",\\"DateOfCheckIn\\":\\"2026-10-05\\",\\"DateOfCheckOut\\":\\"2026-10-06\\",'
        '\\"BilledAmount\\":0,\\"ClaimedAmount\\":0,\\"BillSGSTAmount\\":0,\\"BillCGSTAmount\\":0,'
        '\\"BillIGSTAmount\\":0,\\"CGSTAmount\\":0,\\"SGSTAmount\\":0,\\"IGSTAmount\\":0,'
        '\\"TaxStructure\\":\\"SGST_CGST\\",\\"StartFileIndexCount\\":-1,\\"EndFileIndexCount\\":-1}]"}'
    )
    assert r["files"] == []


def test_journeys_trip_ends_file_ranges_and_parts_in_order():
    s = {
        **initial_claim_state(),
        "transportationRows": [
            journey(source="Pune", destination="Noida", DateOfTravelSource="2026-10-08",
                    DateOfTravelDestination="2026-10-08", uploadedFiles=[NEW, SAVED], claimedAmount="1200.50",
                    sgst="30", taxStructure="NA"),
            journey(uploadedFiles=[{**NEW, "name": "a.pdf"}, {**NEW, "name": "b.pdf"}], invoiceDate="2026-10-01"),
        ],
        "accommodationRows": [stay(billFiles=[{**NEW, "name": "h.pdf"}])],
        "includeAccommodation": False,
        "includeLeave": True,
        "leaveRows": [{"id": "l", "startDate": "2026-10-06", "endDate": "", "daDeductable": "Yes", "amount": ""}],
    }  # fmt: skip
    r = assistant_draft_save(s, emp_id=101, tour_id=4522)
    payload = r["draftData"]
    # the first departure and the last arrival by time, not by place in the list
    assert (payload["Source"], payload["Destination"], payload["TantetiveDateOfDeparture"]) == (
        "Noida",
        "Noida",
        "2026-10-05",
    )
    journeys = dtos(payload, "TransportaionDetailDtos")
    assert [(j["StartFileIndexCount"], j["EndFileIndexCount"]) for j in journeys] == [(0, 0), (1, 2)]
    assert journeys[0]["ClaimedAmount"] == 1200.5 and journeys[0]["SGSTAmount"] == 0
    assert journeys[1]["TimeOfTravelDestiNation"] == "16:30" and journeys[1]["InvoiceDate"] == "2026-10-01"
    assert "AccomodationDetailDto" not in payload  # stays are off, but their new files are still sent
    assert dtos(payload, "LeaveAvailedDto") == [
        {"Id": 0, "LeaveStartDate": "2026-10-06", "LeaveEndDate": "", "DADeductable": True, "Amount": 0}
    ]
    assert payload["IsLeaveAvailedwithbetweentour"] is True
    assert [(part, f["name"]) for part, f in r["files"]] == [
        ("transportationFiles", "t.pdf"),
        ("transportationFiles", "a.pdf"),
        ("transportationFiles", "b.pdf"),
        ("accommodationFiles", "h.pdf"),
    ]
    assert "DraftId" not in payload and "RetainedTransportationFileIds" not in payload


def test_collect_new_files_skips_saved_and_retained_files():
    rows = [journey(uploadedFiles=[NEW, SAVED, {**NEW, "isRetained": True}, {"name": "no bytes"}])]
    assert collect_new_files(rows, []) == {"transportationFiles": [NEW], "accommodationFiles": []}


def test_build_claim_payload_form_fields_and_daily_allowance():
    r = build_claim_payload(
        {
            "transportationRows": [journey()],
            "accommodationRows": [],
            "daRowsState": [{"startDate": "2026-10-05T00:00:00", "startTime": "10:30:00", "Hours": 6, "Minutes": 5,
                             "DAPercentage": 70, "DAOfTheDay": "350", "applicable": True}],
            "leaveRows": [],
            "currentTab": "DA",
            "formData": {"recipientId": 5, "claimAmount": 0, "documents": {"hotel": True}},
            "includeLeave": False,
            "includeAccommodation": False,
            "empId": 101,
            "empTourPlanId": 4522,
            "draftId": 77,
            "hasResumedDraft": True,
            "myEntitlement": {"daAmount": 500},
        }
    )  # fmt: skip
    payload = r["draftPayload"]
    assert payload["DraftId"] == 77 and payload["RecipentId"] == 5 and "ClaimAmount" not in payload
    assert payload["NoOfDays"] == 1
    assert dtos(payload, "DailyAllowanceDto") == [
        {"Id": 1, "Date": "2026-10-05", "SourceTime": "10:30", "DASlab": "500", "TotalHoursMinutes": "06:05",
         "PercentageAdmissible": 70, "Amount": 350, "IsApplicable": True, "TourNumber": 1}
    ]  # fmt: skip
    assert payload["DocumentsAttached"] == {
        "IsApprovedTourProgramme": False,
        "IsTickets_BoardingPass_Bills": False,
        "IsHotelBills": True,
    }
    assert payload["RetainedTransportationFileIds"] == [] and "RetainedAccomodationFileIds" not in payload


# ── saveDraftFromAssistant ──


def test_assistant_save_target():
    drafts = [{"empTourPlanId": 4522, "id": 77}]

    def not_called():
        raise AssertionError("not read")

    assert assistant_save_target(drafts, 4522, False, not_called) == ("exists", None)
    assert assistant_save_target(drafts, 4522, True, not_called) == ("save", 77)
    found = {"statusCode": 200, "data": {"draftId": 78}}
    assert assistant_save_target([], 4522, False, lambda: found) == ("exists", None)
    assert assistant_save_target([], 4522, True, lambda: found) == ("save", 78)
    assert assistant_save_target([], 4522, True, lambda: None) == ("save", None)


def test_deviation_2_retained_file_ids_are_sent_when_saving_into_a_draft():
    s = {
        **initial_claim_state(),
        "transportationRows": [journey(uploadedFiles=[SAVED, NEW])],
        "accommodationRows": [stay(billFiles=[{**SAVED, "fileId": "f-2"}])],
        "includeAccommodation": True,
    }
    over = assistant_draft_save(s, emp_id=101, tour_id=4522, draft_id=77, overwrite=True)["draftData"]
    assert over["DraftId"] == 77
    assert over["RetainedTransportationFileIds"] == ["f-1"]
    assert over["RetainedAccomodationFileIds"] == ["f-2"]
    new = assistant_draft_save(s, emp_id=101, tour_id=4522, draft_id=None, overwrite=True)["draftData"]
    assert "RetainedTransportationFileIds" not in new and "RetainedAccomodationFileIds" not in new


# ── deviation 1: saved rows written back as read ──

SAVED_STAY_DTO = {
    "Id": 0,
    "Location": "Pune",
    "ClassOfCity": "Y",
    "IsOwnArrangement": True,
    "IsFreeAccomodation": False,
    "isCompanyLodginPaid": False,
    "HotelCity": "Pune",
    "DateOfCheckIn": "2026-10-05",
    "TimeOfCheckIn": "12:00",
    "DateOfCheckOut": "2026-10-06",
    "BilledAmount": 0,
    "ClaimedAmount": 0,
    "TaxStructure": "NA",
    "InvoiceDate": "2026-10-06",
    "StartFileIndexCount": 0,
    "EndFileIndexCount": 0,
    "Remark": "kept",
}
# the stay as claimStateFromDraft parsed it: own arrangement lost, InvoiceDate not read
PARSED_STAY = stay(
    classOfCity="Y", checkInTime="12:00", selectedType="own_arrangement", taxStructure="NA", remarks="kept"
)


def test_a_saved_row_is_written_back_exactly_as_read():
    row = {**PARSED_STAY, SAVED_ROW_KEY: {"dto": SAVED_STAY_DTO, "row": dict(PARSED_STAY)}}
    s = {**initial_claim_state(), "accommodationRows": [row, stay(location="Goa")], "includeAccommodation": True}
    payload = assistant_draft_save(s, emp_id=101, tour_id=4522, draft_id=77, overwrite=True)["draftData"]
    saved, new = dtos(payload, "AccomodationDetailDto")
    # as read, with this save's place and files
    assert saved == {**SAVED_STAY_DTO, "StartFileIndexCount": -1, "EndFileIndexCount": -1}
    assert new["Id"] == 1 and new["Location"] == "Goa" and "_saved" not in json.dumps(payload)


def test_a_change_made_in_the_chat_is_applied_on_top_of_the_saved_row():
    edited = {**PARSED_STAY, "checkOutDate": "2026-10-07", "checkInTime": "", "remarks": "kept"}
    row = {**edited, SAVED_ROW_KEY: {"dto": SAVED_STAY_DTO, "row": dict(PARSED_STAY)}}
    s = {**initial_claim_state(), "accommodationRows": [row], "includeAccommodation": True}
    (saved,) = dtos(assistant_draft_save(s, emp_id=101, tour_id=4522)["draftData"], "AccomodationDetailDto")
    expected = {**SAVED_STAY_DTO, "DateOfCheckOut": "2026-10-07", "StartFileIndexCount": -1, "EndFileIndexCount": -1}
    del expected["TimeOfCheckIn"]
    assert saved == expected


def test_without_the_parsed_row_the_saved_row_is_sent_as_read_and_keys_keep_their_spelling():
    dto = {"id": 3, "source": "Noida", "TourNumber": 1, "isPaidUsingCard": True, "startFileIndexCount": 4}
    row = journey(**{SAVED_ROW_KEY: {"dto": dto}}, uploadedFiles=[NEW])
    s = {**initial_claim_state(), "transportationRows": [row]}
    (written,) = dtos(assistant_draft_save(s, emp_id=101, tour_id=4522)["draftData"], "TransportaionDetailDtos")
    assert written == {"id": 0, "source": "Noida", "TourNumber": 1, "isPaidUsingCard": True, "startFileIndexCount": 0,
                       "EndFileIndexCount": 0}  # fmt: skip


def test_saved_leave_rows_are_written_back_too():
    saved_leave = {"Id": 0, "LeaveStartDate": "2026-10-06T00:00:00", "LeaveEndDate": "2026-10-06T00:00:00",
                   "DADeductable": False, "Amount": 120.5}  # fmt: skip
    parsed = {"id": "leave-draft-0", "startDate": "2026-10-06", "endDate": "2026-10-06", "daDeductable": "No",
              "amount": "120.5"}  # fmt: skip
    row = {**parsed, SAVED_ROW_KEY: {"dto": saved_leave, "row": dict(parsed)}}
    s = {**initial_claim_state(), "leaveRows": [row], "includeLeave": True}
    payload = assistant_draft_save(s, emp_id=101, tour_id=4522)["draftData"]
    assert dtos(payload, "LeaveAvailedDto") == [saved_leave]
