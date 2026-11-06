"""Port of Tour_React tests/claimChatbot/claimAssistant.test.ts (claimAssistant.ts only), plus checks of our own.

The claimProgress.ts tests of that file ('findClaimIssues: arrives before departs, outside tour, stay and leave outside
tour', 'stayBillProblem: bad hotel bills are refused with a reason', 'buildProgress: not ready until tour, journeys,
stays and leave are settled') are in test_claim_progress.py.
"""

from __future__ import annotations

import json
import re

import pytest

from tourclaim_agent.claimchat.claim_assistant import (
    CLAIM_EDIT_FORMAT,
    DA_RULES,
    apply_edits,
    describe_missing,
    find_amount_issues,
    find_form_only_issues,
    find_missing_fields,
    initial_claim_state,
    journey_key,
    merge_ocr_rows,
    new_accommodation_row,
    new_leave_row,
    new_transport_row,
    read_claim_edits,
    snapshot,
    summarize_journeys,
    summarize_leave,
    summarize_stays,
    tour_rules_from,
)
from tourclaim_agent.claimchat.js_compat import use_runtime

rules = {"allowAir": False, "minDate": "2026-10-05", "maxDate": "2026-10-11"}


def t(row, field, value):
    return {"section": "transport", "row": row, "field": field, "value": value}


def a(row, field, value):
    return {"section": "accommodation", "row": row, "field": field, "value": value}


def lv(row, field, value):
    return {"section": "leave", "row": row, "field": field, "value": value}


def journey(**over):
    return {
        **new_transport_row(),
        "source": "Noida",
        "destination": "Pune",
        "DateOfTravelSource": "2026-10-05",
        "TimeOfTravelSource": "10:30",
        "DateOfTravelDestination": "2026-10-05",
        "TimeOfTravelDestiNation": "16:30",
        "ModeOfTravel": "rail",
        **over,
    }


def claim():
    return {**initial_claim_state(), "transportationRows": [journey()]}


# ── applyEdits: positive ──


# TS: 'applyEdits fills a new journey into the blank row the form opens with'
def test_apply_edits_fills_a_new_journey_into_the_blank_row_the_form_opens_with():
    r = apply_edits(
        initial_claim_state(),
        [
            t(0, "from", "Noida"),
            t(0, "to", "Pune"),
            t(0, "departureDate", "2026-10-05"),
            t(0, "departureTime", "10:30 am"),
            t(0, "mode", "train"),
        ],
        rules,
    )
    assert len(r["rejected"]) == 0
    assert len(r["state"]["transportationRows"]) == 1
    row = r["state"]["transportationRows"][0]
    assert [
        row["source"],
        row["destination"],
        row["DateOfTravelSource"],
        row["TimeOfTravelSource"],
        row["ModeOfTravel"],
    ] == [
        "Noida",
        "Pune",
        "2026-10-05",
        "10:30",
        "rail",
    ]


# TS: 'applyEdits adds a stay and turns accommodation on; type change resets bill amounts'
def test_apply_edits_adds_a_stay_and_turns_accommodation_on():
    r = apply_edits(
        claim(),
        [
            a(0, "location", "Pune"),
            a(0, "checkInDate", "2026-10-05"),
            a(0, "checkOutDate", "2026-10-07"),
            a(0, "type", "company_free"),
        ],
        rules,
    )
    assert r["state"]["includeAccommodation"] is True
    assert r["state"]["accommodationRows"][0]["selectedType"] == "company_free"
    assert len(r["rejected"]) == 0


# TS: 'applyEdits removes the right entry and restores the blank row'
def test_apply_edits_removes_the_right_entry_and_restores_the_blank_row():
    s = {**initial_claim_state(), "transportationRows": [journey(), journey(source="Pune", destination="Goa")]}
    one = apply_edits(s, [t(1, "remove", "yes")], rules)
    assert len(one["state"]["transportationRows"]) == 1
    assert one["state"]["transportationRows"][0]["destination"] == "Goa"
    every = apply_edits(one["state"], [t(1, "remove", "yes")], rules)
    assert len(every["state"]["transportationRows"]) == 1
    assert every["state"]["transportationRows"][0]["source"] == ""


# ── applyEdits: negative ──

BAD = [
    (t(1, "departureDate", "2026-02-30"), r"couldn't use"),
    (t(1, "departureDate", "2026-11-20"), r"outside your approved tour"),
    (t(1, "arrivalDate", "2025-01-01"), r"outside your approved tour"),
    (t(1, "departureTime", "25:99"), r"couldn't use"),
    (t(1, "mode", "air"), r"no air travel"),
    (t(1, "mode", "teleport"), r"couldn't use"),
    (t(1, "distanceKm", "-5"), r"couldn't use"),
    (t(1, "distanceKm", "far"), r"couldn't use"),
    (t(1, "from", "   "), r"couldn't use"),
    (t(9, "from", "Delhi"), r"There is no journey 9"),
    (a(0, "checkInDate", "2026-12-25"), r"outside your approved tour"),
    (a(0, "checkInTime", "99:00"), r"couldn't use"),
    (lv(0, "startDate", "2026-12-25"), r"outside your tour"),
    (lv(0, "startDate", "not a date"), r"couldn't use"),
    ({"section": "nonsense", "row": 1, "field": "x", "value": "y"}, r"Couldn't apply"),
    (t(1, "unknownField", "x"), r"couldn't use"),
]


# TS: 'applyEdits rejects impossible, outside-tour and disallowed values and leaves the claim unchanged'
@pytest.mark.parametrize(("edit", "want"), BAD, ids=[json.dumps(e) for e, _ in BAD])
def test_apply_edits_rejects_impossible_outside_tour_and_disallowed_values(edit, want):
    before = claim()
    r = apply_edits(before, [edit], rules)
    assert len(r["applied"]) == 0
    assert re.search(want, " | ".join(r["rejected"]))
    assert r["state"]["transportationRows"][0] == before["transportationRows"][0]


# TS: 'applyEdits never removes a row that does not exist and does not mutate its input'
def test_apply_edits_never_removes_a_row_that_does_not_exist_and_does_not_mutate_its_input():
    before = claim()
    snap = json.dumps(before)
    r = apply_edits(before, [t(5, "remove", "yes"), t(1, "remove", "maybe")], rules)
    assert re.search(r"no journey 5 to remove", " ".join(r["rejected"]))
    assert len(r["state"]["transportationRows"]) == 1
    assert json.dumps(before) == snap


# ── merging bills ──


# TS: 'mergeOcrRows: duplicates are skipped and order is by date'
def test_merge_ocr_rows_duplicates_are_skipped_and_order_is_by_date():
    later = journey(
        source="Pune", destination="Noida", DateOfTravelSource="2026-10-08", DateOfTravelDestination="2026-10-08"
    )
    first = merge_ocr_rows(claim(), {"transportationRows": [later, journey()], "accommodationRows": []})
    assert first["addedJourneys"] == 1
    assert first["duplicates"] == 1
    assert [r["destination"] for r in first["state"]["transportationRows"]] == ["Pune", "Noida"]
    again = merge_ocr_rows(first["state"], {"transportationRows": [later], "accommodationRows": []})
    assert again["addedJourneys"] == 0
    assert again["duplicates"] == 1


# TS: 'mergeOcrRows: an empty OCR result leaves the claim as it was'
def test_merge_ocr_rows_an_empty_ocr_result_leaves_the_claim_as_it_was():
    r = merge_ocr_rows(claim(), {"transportationRows": [], "accommodationRows": []})
    assert r["addedJourneys"] + r["addedStays"] + r["duplicates"] == 0
    assert len(r["state"]["transportationRows"]) == 1


# ── what is missing ──


# TS: 'findMissingFields: a blank form asks for nothing; a half-filled journey asks for the rest'
def test_find_missing_fields_a_half_filled_journey_asks_for_the_rest():
    # blank row is "not started", never both (the TS assertion is a tautology)
    assert len(find_missing_fields(initial_claim_state())) == 0 + len(find_missing_fields(initial_claim_state()))
    half = {**initial_claim_state(), "transportationRows": [journey(ModeOfTravel="select", TimeOfTravelSource="")]}
    slots = sorted(m["slot"] for m in find_missing_fields(half))
    assert slots == ["departure", "mode"]
    assert find_missing_fields(claim()) == []


# TS: 'applyEdits: several new journeys in one message each get their own row; a row past the end with no place or
# date is refused'
def test_apply_edits_several_new_journeys_each_get_their_own_row():
    s = initial_claim_state()
    r = apply_edits(
        s,
        [
            t(1, "from", "Noida"),
            t(1, "to", "Mumbai"),
            t(1, "departureDate", "2026-10-05"),
            t(2, "from", "Mumbai"),
            t(2, "to", "Noida"),
            t(2, "departureDate", "2026-10-09"),
        ],
        rules,
    )
    assert len([x for x in r["state"]["transportationRows"] if x["source"]]) == 2
    assert r["state"]["transportationRows"][1]["source"] == "Mumbai"
    before = r["state"]
    bad = apply_edits(before, [t(9, "arrivalTime", "17:00")], rules)
    assert len(bad["state"]["transportationRows"]) == len(before["transportationRows"])
    assert len(bad["rejected"]) == 1


# TS: 'applyEdits: locked journeys are never written over; a new journey is still added'
def test_apply_edits_locked_journeys_are_never_written_over():
    s = apply_edits(
        initial_claim_state(),
        [t(1, "from", "Noida"), t(1, "to", "Mumbai"), t(1, "departureDate", "2026-10-05"), t(1, "mode", "air")],
        rules,
    )["state"]
    r = apply_edits(
        s,
        [
            t(1, "departureTime", "06:30"),
            t(0, "from", "Noida"),
            t(0, "to", "Noida Airport"),
            t(0, "departureDate", "2026-10-05"),
            t(0, "mode", "taxi"),
        ],
        {**rules, "allowAir": True, "lockedJourneys": [1]},
    )
    assert r["state"]["transportationRows"][0]["TimeOfTravelSource"] == ""
    assert r["state"]["transportationRows"][1]["destination"] == "Noida Airport"
    assert re.search(r"Journey 1 \(Noida → Mumbai\) was not changed", r["rejected"][0])


# ── our own: exact texts and JavaScript quirks ──


def test_row_factories_use_the_injected_clock_and_random_numbers():
    with use_runtime(now_ms=lambda: 1790000000000, random=lambda: 0.123456789):
        assert new_transport_row()["id"] == "transport-chat-1790000000000-4fzzz"
        assert new_accommodation_row()["id"] == "accom-chat-1790000000000-4fzzz"
        assert new_leave_row() == {
            "id": "leave-chat-1790000000000",
            "startDate": "",
            "endDate": "",
            "daDeductable": "Yes",
            "amount": "0",
        }
    prev = journey(ModeOfTravel="air")
    leg = new_transport_row(prev)
    assert (leg["source"], leg["DateOfTravelSource"], leg["ModeOfTravel"]) == ("Pune", "2026-10-05", "air")
    assert "invoiceDate" not in leg  # undefined in the TypeScript


def test_find_missing_fields_questions_word_for_word():
    s = {**initial_claim_state(), "transportationRows": [journey(TimeOfTravelSource="", DateOfTravelDestination="")]}
    s["includeAccommodation"] = True
    s["accommodationRows"] = [{**new_accommodation_row(), "location": "Pune", "checkInDate": "2026-09-05"}]
    missing = find_missing_fields(s)
    assert [m["question"] for m in missing] == [
        "What time did you leave for journey 1 (Noida → Pune) on 05 Oct 2026?",
        'When did you arrive for journey 1 (Noida → Pune)? ("same day 4:30 pm" works too)',
        "When did you check in for stay 1 (Pune)? Date and time, please.",
        "And when did you check out of stay 1 (Pune)?",
        "What was the hotel's name for stay 1 (Pune)?",
        "What's the invoice number on the bill for stay 1 (Pune)?",
    ]
    assert describe_missing(s, missing) == (
        "• Journey 1 (Noida → Pune): departure time, arrival date and time\n"
        "• Stay 1 (Pune): check-in date and time, check-out date and time, hotel name, invoice number"
    )
    # an untouched blank row asks for all five
    assert [m["slot"] for m in find_missing_fields(initial_claim_state())] == [
        "source",
        "destination",
        "departure",
        "arrival",
        "mode",
    ]


def test_apply_edits_messages_word_for_word():
    s = {
        **claim(),
        "accommodationRows": [{**new_accommodation_row(), "location": "Pune"}],
        "includeAccommodation": True,
    }
    r = apply_edits(
        s,
        [
            t(1, "arrivalDate", "2026-09-05"),
            t(1, "distanceKm", "120 km"),
            t(1, "mode", "flight"),
            a(1, "type", "free company lodging"),
            a(1, "type", "free"),
            a(1, "checkOutDate", "2026-10-07"),
            lv(0, "endDate", "2026-10-30"),
            lv(0, "daDeductable", "nope"),
            t(0, "remove", "yes"),
            {"section": "transport", "row": 2, "field": "remove", "value": "TRUE"},
        ],
        {"allowAir": True, "minDate": "2026-09-01", "maxDate": "2026-10-11"},
    )
    assert r["applied"] == [
        "Journey 1: arrival date 05 Sept 2026",
        "Journey 1: 120 km",
        "Journey 1: Air",
        "Stay 1: Free company lodging (bill amounts cleared)",
        "Stay 1: already Free company lodging",
        "Stay 1: check-out 07 Oct 2026",
        "Leave 1: DA deductable No",
        "Removed journey 1",
    ]
    assert r["rejected"] == [
        "Leave 1: 30 Oct 2026 is outside your tour (01 Sept 2026 – 11 Oct 2026).",
        "There is no journey 2 to remove.",
    ]
    assert r["state"]["includeLeave"] is True
    assert len(r["state"]["transportationRows"]) == 1 and r["state"]["transportationRows"][0]["source"] == ""


def test_apply_edits_only_one_bound_shows_empty_window_and_new_rows_share_one_index_map():
    r = apply_edits(claim(), [t(1, "departureDate", "2026-12-01")], {"allowAir": True, "maxDate": "2026-10-11"})
    assert r["rejected"] == ["Journey 1: 01 Dec 2026 is outside your approved tour ()."]
    # quirk: a new journey and a new stay in one message share the "taken" rows, so the stay skips blank row 1
    s = initial_claim_state()
    r = apply_edits(s, [t(0, "from", "Noida"), a(0, "location", "Pune")])
    assert [x["location"] for x in r["state"]["accommodationRows"]] == ["", "Pune"]


def test_apply_edits_on_a_row_that_is_not_a_whole_number_throws_like_the_typescript():
    two = {**claim(), "transportationRows": [journey(), journey()]}
    with pytest.raises(TypeError):
        apply_edits(two, [t(1.5, "from", "Delhi")])
    assert apply_edits(two, [t(1.5, "nothing", "x")])["rejected"] == ['Journey 1.5: couldn\'t use "x" for nothing.']
    # past the end of a one-journey claim, 1.5 is a new journey
    assert [r["source"] for r in apply_edits(claim(), [t(1.5, "from", "Delhi")])["state"]["transportationRows"]] == [
        "Noida",
        "Delhi",
    ]


def test_read_claim_edits_keeps_only_well_formed_edits_with_javascript_conversions():
    edits = read_claim_edits(
        [
            {"section": "transport", "row": "2", "field": "from", "value": 5},
            {"section": "leave", "row": None, "field": "daDeductable", "value": True},
            {"section": "stay", "row": 1, "field": "x", "value": "y"},
            {"section": "transport", "row": "2a", "field": "from", "value": "x"},
            {"section": "transport", "field": "from", "value": "x"},
            {"section": "transport", "row": 1, "field": "from", "value": None},
            {"section": "accommodation", "row": True, "field": "type", "value": {"a": 1}},
            None,
            "edit",
        ]
    )
    assert edits == [
        {"section": "transport", "row": 2, "field": "from", "value": "5"},
        {"section": "leave", "row": 0, "field": "daDeductable", "value": "true"},
        {"section": "accommodation", "row": 1, "field": "type", "value": "[object Object]"},
    ]


def test_snapshot_and_read_outs():
    s = {**claim(), "includeAccommodation": True, "includeLeave": True}
    s["transportationRows"][0]["claimedAmount"] = "1234567.5"
    s["transportationRows"][0]["uploadedFiles"] = [{"name": "t.pdf", "size": 1, "type": "application/pdf"}]
    s["accommodationRows"] = [
        {**new_accommodation_row(), "location": "Pune", "checkInDate": "2026-10-05", "claimed": "900"}
    ]
    s["leaveRows"] = [{**new_leave_row(), "startDate": "2026-10-06"}]
    with use_runtime(now_ms=lambda: 1790000000000):  # 2026-09-21 14:13 UTC
        text = snapshot(s, {"tourId": 4521, "source": "Noida", "destination": "Pune", "startDate": "2026-10-05"})
    assert text.split("\n") == [
        "Today: 2026-09-21. Approved tour #4521: Noida to Pune, 2026-10-05 to ?.",
        "IN THE CLAIM NOW: 1 journey, 1 stay.",
        "JOURNEYS",
        "T1: from=Noida to=Pune departure=2026-10-05 10:30 arrival=2026-10-05 16:30 mode=rail bills=1",
        "STAYS",
        "A1: location=Pune checkIn=2026-10-05 - checkOut=- - type=hotel hotelName=- invoiceNo=- bills=0",
        "LEAVE",
        "L1: 2026-10-06 to - deductable=Yes",
    ]
    assert snapshot(initial_claim_state()).split("\n")[1:] == [
        "IN THE CLAIM NOW: 0 journeys, 0 stays.",
        "JOURNEYS",
        "T1: from=- to=- departure=- - arrival=- - mode=select bills=0 (blank row)",
        "STAYS",
        "(no stays)",
        "LEAVE",
        "(no leave)",
    ]
    assert summarize_journeys(s) == "1. Noida → Pune · 05 Oct 2026 10:30 to 05 Oct 2026 16:30 · Rail · ₹12,34,567.50"
    assert summarize_stays(s) == "1. Pune · 05 Oct 2026 to — · Hotel · ₹900.00"
    assert summarize_leave(s) == "1. 06 Oct 2026 to 06 Oct 2026 · DA deducted"
    assert summarize_journeys(initial_claim_state()) == "No journeys yet."
    # an object-literal lookup sees Object.prototype, as the TypeScript did
    odd = {**claim(), "transportationRows": [journey(ModeOfTravel="toString")]}
    assert "· function toString() { [native code] }" in summarize_journeys(odd)


def test_amount_and_form_only_issues():
    s = claim()
    s["transportationRows"][0]["uploadedFiles"] = [{"name": "t.pdf", "size": 1, "type": "application/pdf"}]
    s["includeAccommodation"] = True
    s["accommodationRows"] = [
        {
            **new_accommodation_row(),
            "location": "Pune",
            "billed": "1000",
            "claimed": "1500.005",
            "ClaimedCGSTAmount": 90,
        },
    ]
    assert find_form_only_issues(s) == [
        "Journey 1 (Noida → Pune): amount not read. Enter it on the form.",
        "Stay 1 (Pune): the city class fills in when you pick the city on the form, so please re-select it there.",
        "Stay 1 (Pune): the claimed amount (₹1,410.01) is more than the billed amount (₹1,000.00);"
        " adjust it on the form.",
    ]
    assert find_amount_issues(s) == ["Journey 1 (Noida → Pune): amount not read. Enter it on the form."]


def test_journey_key_reads_a_saved_draft_format_as_the_same_journey():
    saved = journey(DateOfTravelSource="2026-09-10T00:00:00", TimeOfTravelSource="09:00:00")
    assert journey_key(saved) == "noida|pune|2026-09-10|09:00"
    assert journey_key(journey(source=" NOIDA ", DateOfTravelSource="10/09/2026", TimeOfTravelSource="9 am")) == (
        "noida|pune|2026-09-10|09:00"
    )


def test_tour_rules_from_the_claim_request():
    data = {
        "trDetails": {"tentativeStartDate": "2026-10-05T00:00:00"},
        "tentativeTourPlans": [
            {"date": "2026-10-09", "travelMode": "Air"},
            {"date": "31/10/2026", "travelMode": "rail"},
        ],
    }
    assert tour_rules_from(data) == {"allowAir": True, "minDate": "2026-10-05", "maxDate": "2026-11-01"}
    # an empty plan list is truthy in JavaScript: the other list is not read
    assert tour_rules_from({"tentativeTourPlans": [], "tentativeTourPlanList": [{"date": "2026-10-09"}]}) == {
        "allowAir": False
    }
    assert tour_rules_from(None) == {"allowAir": False}


def test_prompt_constants_word_for_word():
    assert "mode (one of air, rail, personal-vehicle, public-transport, official-entitled, official-beyond, " in (
        CLAIM_EDIT_FORMAT
    )
    assert CLAIM_EDIT_FORMAT.startswith('"edits" is a list of {"section","row","field","value"}, one per field.')
    assert CLAIM_EDIT_FORMAT.endswith('"row":<number>,"field":"<field>","value":"<value>"}]}')
    assert CLAIM_EDIT_FORMAT.count("\n") == 9
    assert DA_RULES.split("\n")[2] == (
        "Hours in a day: up to 6h → 30%, over 6h up to 12h → 70%, over 12h → 100% of your grade’s daily DA rate."
    )
