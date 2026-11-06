"""Port of Tour_React tests/claimChatbot/savedClaim.test.ts (all but the readOutcome test, which is in
test_bill_outcome.py) and savedDraftsGreeting.test.ts, plus checks of our own: the `_saved` rows of deviation 1 and
JavaScript details (expected values taken from the TypeScript, run under Node)."""

from __future__ import annotations

import copy
import json
import re

from tourclaim_agent.claimchat.claim_assistant import (
    apply_edits,
    initial_claim_state,
    merge_ocr_rows,
    new_accommodation_row,
    new_transport_row,
)
from tourclaim_agent.claimchat.claim_payload import SAVED_ROW_KEY, assistant_draft_save
from tourclaim_agent.claimchat.claim_progress import group_days, uncovered_days
from tourclaim_agent.claimchat.js_compat import use_runtime
from tourclaim_agent.claimchat.row_order import (
    ids_of,
    in_stored_order,
    journeys_in_date_order,
    kept_file_ids,
    trip_ends,
)
from tourclaim_agent.claimchat.saved_claim import (
    add_to_saved,
    bill_days,
    claim_state_from_draft,
    compare_with_saved,
    draft_for_model,
    rows_count,
    saved_claim_text,
    saved_draft_summary,
    saved_drafts_greeting_note,
)

# ── savedClaim.test.ts ──

tour = {
    "tourId": 7,
    "source": "Noida",
    "destination": "Pune",
    "tourStartDate": "2026-09-10",
    "tantetiveEndDate": "2026-09-12",
    "statusId": 2,
    "totalDays": 3,
}


def saved_journey(date):
    """a saved journey as the server returns it: dates with a time part, times with seconds"""
    return {
        "DateOfTravelSource": f"{date}T00:00:00",
        "TimeOfTravelSource": "09:00:00",
        "Source": "Noida",
        "ModeOfTravel": "rail",
        "DateOfTravelDestination": f"{date}T00:00:00",
        "TimeOfTravelDestiNation": "18:00:00",
        "Destination": "Pune",
        "BilledAmount": 500,
    }


# the saved draft of the example: bills for 10 Sep and 12 Sep, none for 11 Sep
draft = {
    "draftId": 1,
    "draftData": {
        "TransportaionDetailDtos": json.dumps([saved_journey("2026-09-10"), saved_journey("2026-09-12")]),
        "AccomodationDetailDto": "[]",
        "transportationFileReferences": [
            {
                "fileId": "f-10",
                "originalFileName": "ticket-10.pdf",
                "fileSize": 100,
                "rowIndex": 0,
                "tempFilePath": "temp-uploads/draft-1/a.pdf",
            },
            {
                "fileId": "f-12",
                "originalFileName": "ticket-12.pdf",
                "fileSize": 120,
                "rowIndex": 1,
                "tempFilePath": "temp-uploads/draft-1/b.pdf",
            },
        ],  # fmt: skip
        "accomodationFileReferences": [],
    },
}


def bill_journey(date, file):
    """a journey as the OCR reads it from a bill"""
    return {
        **new_transport_row(),
        "DateOfTravelSource": date,
        "TimeOfTravelSource": "09:00",
        "source": "Noida",
        "DateOfTravelDestination": date,
        "TimeOfTravelDestiNation": "18:00",
        "destination": "Pune",
        "ModeOfTravel": "rail",
        "uploadedFiles": [{"name": file, "size": 10, "type": "application/pdf", "documentId": 1}],
    }


def _first_file_id(r):
    files = r["uploadedFiles"]
    return files[0].get("fileId") if files else None


# TS: 'claimStateFromDraft: dates and times as the chat keeps them, and each row keeps its saved files'
def test_claim_state_from_draft_dates_times_and_saved_files():
    saved = claim_state_from_draft(draft)
    assert len(saved["transportationRows"]) == 2
    assert saved["transportationRows"][0]["DateOfTravelSource"] == "2026-09-10"
    assert saved["transportationRows"][0]["TimeOfTravelSource"] == "09:00"
    assert [
        [[f["fileId"], f["isRetained"], f["tempFilePath"]] for f in r["uploadedFiles"]]
        for r in saved["transportationRows"]
    ] == [[["f-10", True, "temp-uploads/draft-1/a.pdf"]], [["f-12", True, "temp-uploads/draft-1/b.pdf"]]]


# TS: 'the 10 to 12 Sep example: an 11 Sep bill is new, a second copy of the 10 Sep journey is already saved'
def test_the_10_to_12_sep_example():
    saved = claim_state_from_draft(draft)
    cmp = compare_with_saved(
        saved,
        {
            "transportationRows": [bill_journey("2026-09-11", "ticket-11.pdf"), bill_journey("2026-09-10", "scan.jpg")],
            "accommodationRows": [],
        },
    )
    added, already_saved = cmp["added"], cmp["alreadySaved"]
    assert [r["DateOfTravelSource"] for r in added["transportationRows"]] == ["2026-09-11"]
    assert [r["DateOfTravelSource"] for r in already_saved["transportationRows"]] == ["2026-09-10"]

    merged = add_to_saved(saved, added)
    # stored: the saved rows keep their places (the server ties their files to them by position); the new one after
    assert [r["DateOfTravelSource"] for r in merged["transportationRows"]] == ["2026-09-10", "2026-09-12", "2026-09-11"]
    assert [_first_file_id(r) for r in merged["transportationRows"]] == ["f-10", "f-12", None]
    # shown: by date
    assert [r["DateOfTravelSource"] for r in journeys_in_date_order(merged["transportationRows"])] == [
        "2026-09-10",
        "2026-09-11",
        "2026-09-12",
    ]
    assert uncovered_days(merged, tour) == []

    text = saved_claim_text(
        {"claim": "Noida → Pune", "saved": saved, "added": added, "alreadySaved": already_saved, "merged": merged,
         "tour": tour}
    )  # fmt: skip
    assert re.search(f"already has {group_days(['2026-09-10', '2026-09-12'])}", text)
    assert re.search(f"These bills add {group_days(['2026-09-11'])}", text)
    assert re.search(r"1 journey .* is already in your saved claim", text)
    assert re.search(r"Every day of the tour is now covered", text)


# TS: 'savedClaimText: a day still missing is named'
def test_saved_claim_text_a_day_still_missing_is_named():
    saved = claim_state_from_draft(draft)
    none = {"transportationRows": [], "accommodationRows": []}
    text = saved_claim_text(
        {"claim": "Noida → Pune", "saved": saved, "added": none, "alreadySaved": none, "merged": saved, "tour": tour}
    )
    assert re.search(f"Still no bills for {group_days(['2026-09-11'])}", text)


# TS: 'mergeOcrRows: rows holding saved files keep their places; a claim without them stays in date order'
def test_merge_ocr_rows_rows_holding_saved_files_keep_their_places():
    saved = claim_state_from_draft(draft)
    with_saved = merge_ocr_rows(
        saved, {"transportationRows": [bill_journey("2026-09-11", "b.pdf")], "accommodationRows": []}
    )
    assert [r["DateOfTravelSource"] for r in with_saved["state"]["transportationRows"]] == [
        "2026-09-10",
        "2026-09-12",
        "2026-09-11",
    ]
    fresh = merge_ocr_rows(
        {**saved, "transportationRows": [{**r, "uploadedFiles": []} for r in saved["transportationRows"]]},
        {"transportationRows": [bill_journey("2026-09-11", "b.pdf")], "accommodationRows": []},
    )
    assert [r["DateOfTravelSource"] for r in fresh["state"]["transportationRows"]] == [
        "2026-09-10",
        "2026-09-11",
        "2026-09-12",
    ]


# TS: 'mergeOcrRows: a saved journey in another date and time format is not added twice'
def test_merge_ocr_rows_a_saved_journey_in_another_format_is_not_added_twice():
    saved = claim_state_from_draft(draft)
    r = merge_ocr_rows(saved, {"transportationRows": [bill_journey("2026-09-10", "copy.pdf")], "accommodationRows": []})
    assert r["addedJourneys"] == 0
    assert r["duplicates"] == 1


# ── shown by date, stored in the saved order: the 10 / 12 Sep draft and an 11 Sep bill ──


def scenario():
    saved = claim_state_from_draft(draft)
    stored = merge_ocr_rows(
        saved, {"transportationRows": [bill_journey("2026-09-11", "ticket-11.pdf")], "accommodationRows": []}
    )["state"]
    return {
        "saved": saved,
        "stored": stored,
        "storedIds": ids_of(stored["transportationRows"]),
        "shown": journeys_in_date_order(stored["transportationRows"]),
    }


# TS: 'shown by date (10, 11, 12 Sep) while stored as saved (10, 12, 11 Sep)'
def test_shown_by_date_while_stored_as_saved():
    sc = scenario()
    assert [r["DateOfTravelSource"] for r in sc["stored"]["transportationRows"]] == [
        "2026-09-10",
        "2026-09-12",
        "2026-09-11",
    ]
    assert [r["DateOfTravelSource"] for r in sc["shown"]] == ["2026-09-10", "2026-09-11", "2026-09-12"]


# TS: 'putting the shown rows back gives exactly the stored order, so every saved bill stays on its row'
def test_putting_the_shown_rows_back_gives_the_stored_order():
    sc = scenario()
    back = in_stored_order(sc["shown"], sc["storedIds"])
    assert ids_of(back) == sc["storedIds"]
    assert [_first_file_id(r) for r in back] == [_first_file_id(r) for r in sc["stored"]["transportationRows"]]
    assert kept_file_ids(back) == ["f-10", "f-12"]


# TS: '"journey 2 arrives at 8 pm" changes the second journey on screen (11 Sep), and it is stored in its own row'
def test_an_edit_of_the_second_journey_on_screen_is_stored_in_its_own_row():
    sc = scenario()
    edited = apply_edits(
        {**claim_state_from_draft(draft), "transportationRows": sc["shown"]},
        [{"section": "transport", "row": 2, "field": "arrivalTime", "value": "20:00"}],
    )["state"]
    back = in_stored_order(edited["transportationRows"], sc["storedIds"])
    # stored row 3 is the 11 Sep journey: it has the new time; the saved rows are untouched
    assert back[2]["DateOfTravelSource"] == "2026-09-11"
    assert back[2]["TimeOfTravelDestiNation"] == "20:00"
    assert [back[0]["TimeOfTravelDestiNation"], back[1]["TimeOfTravelDestiNation"]] == ["18:00", "18:00"]


# TS: 'a journey added in the chat goes after the stored ones; a removed one simply drops out'
def test_a_journey_added_in_the_chat_goes_after_the_stored_ones():
    sc = scenario()
    added = [*sc["shown"], {**bill_journey("2026-09-09", "cab.pdf"), "id": "transport-chat-new"}]
    back = in_stored_order(journeys_in_date_order(added), sc["storedIds"])
    assert ids_of(back) == [*sc["storedIds"], "transport-chat-new"]
    removed = in_stored_order([r for r in sc["shown"] if r["DateOfTravelSource"] != "2026-09-11"], sc["storedIds"])
    assert [r["DateOfTravelSource"] for r in removed] == ["2026-09-10", "2026-09-12"]
    # nothing stored yet: the view order is kept
    assert ids_of(in_stored_order(sc["shown"], [])) == ids_of(sc["shown"])


# TS: 'DA and the claim summary use the first departure and the last arrival by time, wherever they are stored'
def test_trip_ends_by_time_wherever_stored():
    sc = scenario()
    ends = trip_ends(sc["stored"]["transportationRows"])
    assert ends["first"]["DateOfTravelSource"] == "2026-09-10"
    assert ends["last"]["DateOfTravelDestination"] == "2026-09-12"
    assert trip_ends([]) == {"first": None, "last": None}


# ── savedDraftsGreeting.test.ts ──

tour_m = {
    "tourId": 4521,
    "source": "Noida",
    "destination": "Mumbai",
    "tourStartDate": "2026-10-05",
    "tantetiveEndDate": "2026-10-09",
    "totalDays": 5,
    "statusId": 2,
}


def j(from_, to, d, d2=None):
    return {
        **new_transport_row(),
        "source": from_,
        "destination": to,
        "DateOfTravelSource": d,
        "TimeOfTravelSource": "10:00",
        "DateOfTravelDestination": d2 or d,
        "TimeOfTravelDestiNation": "12:00",
        "ModeOfTravel": "air",
    }


def claim(journeys, stays=()):
    return {
        **initial_claim_state(),
        "transportationRows": list(journeys) if journeys else initial_claim_state()["transportationRows"],
        "accommodationRows": [
            {
                **new_accommodation_row(),
                "checkInTime": "14:00",
                "checkOutTime": "11:00",
                "hotelName": "H",
                "invoiceNo": "I",
                **x,
            }
            for x in stays
        ]  # fmt: skip
        if stays
        else initial_claim_state()["accommodationRows"],
        "includeAccommodation": len(stays) > 0,
    }


MUMBAI_STAY = {"location": "Mumbai", "checkInDate": "2026-10-05", "checkOutDate": "2026-10-09"}


# TS: 'summary: what a one-way draft holds, and that the return journey is still missing'
def test_summary_of_a_one_way_draft():
    s = saved_draft_summary(
        tour_m,
        claim([j("Noida", "Mumbai", "2026-10-05")], [MUMBAI_STAY]),
        {"savedInChat": True, "lastSaved": "2026-10-27T10:00:00"},
    )
    assert s["tourId"] == 4521
    assert s["route"] == "Noida → Mumbai"
    assert s["journeys"] == ["Noida → Mumbai, 05 Oct 2026"]
    assert s["stays"] == ["Mumbai, 05 Oct 2026 – 09 Oct 2026"]
    assert re.search(r"05 Oct 2026 – 09 Oct 2026", s["daysCovered"])
    assert any(re.search(r"no journey back", m, re.IGNORECASE) for m in s["stillMissing"])
    assert s["savedInChat"] is True
    assert s["lastSaved"] == "27 Oct 2026"


# TS: 'summary: a complete draft has nothing missing; an empty draft says it has no journeys'
def test_summary_complete_and_empty_drafts():
    def cab(from_, to, d, t1, t2):
        return {**j(from_, to, d), "TimeOfTravelSource": t1, "TimeOfTravelDestiNation": t2,
                "ModeOfTravel": "public-transport", "uploadedFiles": [{"name": "cab.pdf"}]}  # fmt: skip

    def with_bills(r):
        return {**r, "uploadedFiles": [{"name": "ticket.pdf"}]}

    full = saved_draft_summary(
        tour_m,
        claim(
            [
                with_bills(j("Noida", "Mumbai", "2026-10-05")),
                with_bills(j("Mumbai", "Noida", "2026-10-09")),
                cab("Noida", "Noida Airport", "2026-10-05", "08:00", "09:30"),
                cab("Mumbai Airport", "Mumbai Hotel", "2026-10-05", "12:30", "13:30"),
                cab("Mumbai Hotel", "Mumbai Office", "2026-10-07", "09:00", "09:30"),
                cab("Mumbai Hotel", "Mumbai Airport", "2026-10-09", "08:00", "09:30"),
                cab("Noida Airport", "Noida", "2026-10-09", "12:30", "13:30"),
            ],
            [MUMBAI_STAY],
        ),
    )
    assert [m for m in full["stillMissing"] if not re.search(r"hotel bill", m, re.IGNORECASE)] == []
    assert re.search(r"^Missing Hotel bill for stay 1|^Nothing is missing\.$", full["missing"])
    assert full["savedInChat"] is False
    empty = saved_draft_summary(tour_m, initial_claim_state())
    assert empty["journeys"] == []
    assert empty["daysCovered"] == ""
    assert any(re.search(r"no journeys", m, re.IGNORECASE) for m in empty["stillMissing"])


# TS: 'greeting note: drafts saved in an earlier chat are said to be from the previous conversation'
def test_greeting_note_drafts_saved_in_an_earlier_chat():
    s = saved_draft_summary(tour_m, claim([j("Noida", "Mumbai", "2026-10-05")]), {"savedInChat": True})
    note = saved_drafts_greeting_note([s], 2)
    assert re.search(r"a saved draft", note)
    assert re.search(r"from our previous conversation", note)
    assert re.search(r"📎", note)
    assert re.search(r"other 2 approved tours", note)
    assert re.search(r"asking what they would like to do", note)


# TS: 'greeting note: drafts saved from the form are never said to come from a chat'
def test_greeting_note_drafts_saved_from_the_form():
    s = saved_draft_summary(tour_m, claim([j("Noida", "Mumbai", "2026-10-05")]), {"savedInChat": False})
    note = saved_drafts_greeting_note([s, {**s, "tourId": 4522}], 0)
    assert re.search(r"2 saved drafts", note)
    assert re.search(r"do not say they came from a conversation with you", note)
    assert not re.search(r"other \d+ approved", note)


# TS: 'greeting note: a mix says which ones the chat saved'
def test_greeting_note_a_mix():
    a = saved_draft_summary(tour_m, claim([j("Noida", "Mumbai", "2026-10-05")]), {"savedInChat": True})
    b = {**a, "tourId": 4522, "savedInChat": False}
    assert re.search(
        r"Say which of them were saved in an earlier conversation with you", saved_drafts_greeting_note([a, b], 1)
    )


# TS: 'summary: a draft with no cab bills says which ones are missing'
def test_summary_a_draft_with_no_cab_bills():
    summary = saved_draft_summary(
        tour_m,
        claim([j("Noida", "Mumbai", "2026-10-05"), j("Mumbai", "Noida", "2026-10-09")], [MUMBAI_STAY]),
    )
    assert any(re.search(r"getting to the airport in Noida", m) for m in summary["stillMissing"])
    assert any(re.search(r"local travel \(cab, taxi, auto\) in Mumbai", m) for m in summary["stillMissing"])


# ── our own ──


def test_draft_for_model_leaves_out_the_long_notes():
    s = saved_draft_summary(tour_m, claim([j("Noida", "Mumbai", "2026-10-05")]))
    model = draft_for_model(s)
    assert "stillMissing" not in model
    assert model == {k: v for k, v in s.items() if k != "stillMissing"}
    assert "lastSaved" not in s  # undefined in the TypeScript: left out


def test_rows_count_and_bill_days():
    rows = {"transportationRows": [j("Noida", "Mumbai", "2026-10-05", "2026-10-06")], "accommodationRows": []}
    assert rows_count(rows) == 1
    assert bill_days(rows) == ["2026-10-05", "2026-10-06"]


def test_saved_claim_text_with_bills_on_days_already_saved():
    saved = claim_state_from_draft(draft)
    added = {"transportationRows": [bill_journey("2026-09-10", "x.pdf")], "accommodationRows": []}
    none = {"transportationRows": [], "accommodationRows": []}
    text = saved_claim_text({"claim": "T", "saved": saved, "added": added, "alreadySaved": none,
                             "merged": add_to_saved(saved, added), "tour": tour})  # fmt: skip
    assert text == (
        "📁 Your saved claim for T already has 10 Sept 2026, 12 Sept 2026.\n"
        "These bills add 1 journey on days the saved claim already has.\n"
        "Still no bills for 11 Sept 2026."
    )


# a draft as the server may send it: other casings, a stay, a leave entry, odd values
ODD_DRAFT = {
    "DRAFTDATA": {
        "transportationDetailDtos": [
            {"source": "Delhi", "destination": "Agra", "dateOfTravelSource": "16/01/2026",
             "timeOfTravelSource": "9.05 pm", "departureModeofTravel": "air", "DistanceINKm": "12.50",
             "BilledAmount": "abc", "IGSTAmount": 3, "InvoiceDate": "2026-01-16", "isPaidUsingCard": 1, "Remark": None},
            {"TaxStructure": "NA", "SGSTAmount": 9, "BilledAmount": 0, "InvoiceDate": "garbage"},
        ],
        "AccommodationDetailDto": json.dumps([
            {"Location": "Agra", "DateOfCheckIn": "2026-01-16T00:00:00", "TimeOfCheckIn": "14:00:00",
             "IsFreeAccomodation": True, "TaxStructure": "IGST", "ClassOfCity": None, "InvoiceDate": "2026-01-17"},
        ]),
        "LeaveAvailedDto": '[{"LeaveStartDate": "2026-01-17T00:00:00", "DADeductable": 0, "Amount": "12"}, 5]',
        "transportationFileReferences": json.dumps([
            {"FileId": "f-1", "displayName": "a.pdf", "RowIndex": "1", "fileSize": "7"},
            {"fileId": "f-2", "rowIndex": None},
        ]),
    }
}  # fmt: skip


def test_claim_state_from_draft_reads_any_casing_and_odd_values():
    with use_runtime(now_ms=lambda: 1790000000000, random=lambda: 0.123456789):
        s = claim_state_from_draft(ODD_DRAFT)
    t0, t1 = s["transportationRows"]
    assert (t0["source"], t0["destination"], t0["DateOfTravelSource"], t0["TimeOfTravelSource"]) == (
        "Delhi", "Agra", "2026-01-16", "21:05")  # fmt: skip
    assert (t0["ModeOfTravel"], t0["distanceKm"], t0["travelCost"], t0["taxStructure"]) == ("air", "12.5", "0", "IGST")
    assert (t0["invoiceDate"], t0["isPaidUsingCard"], t0["remarks"], t0["includeTravelCosts"]) == (
        "2026-01-16", True, "", False)  # fmt: skip
    assert [f["fileId"] for f in t1["uploadedFiles"]] == ["f-1"]
    assert t1["uploadedFiles"][0] == {"fileId": "f-1", "name": "a.pdf", "size": 7, "isRetained": True, "type": "",
                                      "uploadDate": "1970-01-01T00:00:00.000Z"}  # fmt: skip
    assert [f["fileId"] for f in t0["uploadedFiles"]] == ["f-2"]
    assert (t1["taxStructure"], t1["sgst"], t1["ModeOfTravel"], t1["invoiceDate"]) == (
        "NA",
        "0",
        "select",
        "NaN-NaN-NaN",
    )
    (a,) = s["accommodationRows"]
    assert (a["selectedType"], a["taxStructure"], a["classOfCity"], a["checkInTime"]) == (
        "company_free", "IGST", "Select", "14:00")  # fmt: skip
    assert "invoiceDate" not in a or a["invoiceDate"] == ""  # a stay's InvoiceDate is not read (old behaviour)
    leave, odd = s["leaveRows"]
    assert (leave["startDate"], leave["endDate"], leave["daDeductable"], leave["amount"]) == (
        "2026-01-17",
        "",
        "No",
        "12",
    )
    assert (odd["id"], odd["startDate"], odd["amount"]) == ("leave-draft-1", "", "0")
    assert s["includeAccommodation"] is True and s["includeLeave"] is True


def test_a_draft_that_is_not_json_has_no_rows():
    s = claim_state_from_draft({"draftData": {"TransportaionDetailDtos": "{not json", "LeaveAvailedDto": "NaN"}})
    assert [r["id"].split("-")[0] for r in s["transportationRows"]] == ["transport"]
    assert SAVED_ROW_KEY not in s["transportationRows"][0]
    assert s["includeAccommodation"] is False and s["includeLeave"] is False


# ── deviation 1: every row read from a saved item carries it ──


def _strip(rows):
    return [{k: v for k, v in r.items() if k != SAVED_ROW_KEY} for r in rows]


def test_every_saved_row_carries_the_item_it_was_read_from():
    s = claim_state_from_draft(ODD_DRAFT)
    data = ODD_DRAFT["DRAFTDATA"]
    assert [r[SAVED_ROW_KEY]["dto"] for r in s["transportationRows"]] == data["transportationDetailDtos"]
    assert [r[SAVED_ROW_KEY]["dto"] for r in s["accommodationRows"]] == json.loads(data["AccommodationDetailDto"])
    assert [r[SAVED_ROW_KEY]["dto"] for r in s["leaveRows"]] == json.loads(data["LeaveAvailedDto"])
    # the parsed row is a copy of the row as it was read, without `_saved`
    for section in ("transportationRows", "accommodationRows", "leaveRows"):
        for r, plain in zip(s[section], _strip(s[section]), strict=True):
            assert r[SAVED_ROW_KEY]["row"] == plain
            assert r[SAVED_ROW_KEY]["row"] is not plain
    # copies: changing the row or the draft later does not change what was kept
    s["transportationRows"][0]["source"] = "Changed"
    assert s["transportationRows"][0][SAVED_ROW_KEY]["row"]["source"] == "Delhi"
    assert s["transportationRows"][0][SAVED_ROW_KEY]["dto"] is not data["transportationDetailDtos"][0]


def test_saved_rows_go_back_to_the_server_exactly_as_read():
    """the old code turned this own-arrangement stay into a hotel stay and dropped its InvoiceDate (deviation 1)"""
    stay = {"Id": 0, "Location": "Pune", "IsOwnArrangement": "Yes", "HotelCity": "Pune", "DateOfCheckIn": "2026-10-05",
            "DateOfCheckOut": "2026-10-06", "InvoiceDate": "2026-10-06", "TaxStructure": "NA",
            "StartFileIndexCount": -1, "EndFileIndexCount": -1}  # fmt: skip
    journey = saved_journey("2026-10-05")
    leave = {"LeaveStartDate": "2026-10-06T00:00:00", "LeaveEndDate": "2026-10-06T00:00:00", "DADeductable": True}
    saved = claim_state_from_draft({"draftData": {"TransportaionDetailDtos": [journey], "AccomodationDetailDto": [stay],
                                                  "LeaveAvailedDto": [leave]}})  # fmt: skip
    payload = assistant_draft_save(saved, emp_id=1, tour_id=7, draft_id=3, overwrite=True)["draftData"]
    assert json.loads(payload["AccomodationDetailDto"]) == [stay]
    assert json.loads(payload["TransportaionDetailDtos"]) == [{**journey, "Id": 0, "StartFileIndexCount": -1,
                                                                "EndFileIndexCount": -1}]  # fmt: skip
    assert json.loads(payload["LeaveAvailedDto"]) == [{**leave, "Id": 0}]
    # a change made in the chat is still sent
    edited = apply_edits(saved, [{"section": "transport", "row": 1, "field": "departureTime", "value": "07:15"}])
    payload = assistant_draft_save(edited["state"], emp_id=1, tour_id=7)["draftData"]
    assert json.loads(payload["TransportaionDetailDtos"])[0]["TimeOfTravelSource"] == "07:15"


def test_saved_rows_change_no_comparison_or_text():
    saved = claim_state_from_draft(draft)
    plain = {**saved, "transportationRows": _strip(saved["transportationRows"])}
    incoming = {"transportationRows": [bill_journey("2026-09-10", "a.pdf"), bill_journey("2026-09-11", "b.pdf")],
                "accommodationRows": []}  # fmt: skip
    a, b = compare_with_saved(saved, incoming), compare_with_saved(plain, incoming)
    assert a == b
    merged = add_to_saved(saved, a["added"])
    assert _strip(merged["transportationRows"]) == add_to_saved(plain, b["added"])["transportationRows"]
    args = {"claim": "T", "added": a["added"], "alreadySaved": a["alreadySaved"], "tour": tour}
    assert saved_claim_text({**args, "saved": saved, "merged": merged}) == saved_claim_text(
        {**args, "saved": plain, "merged": add_to_saved(plain, b["added"])}
    )
    assert saved_draft_summary(tour, saved) == saved_draft_summary(tour, copy.deepcopy(plain))
