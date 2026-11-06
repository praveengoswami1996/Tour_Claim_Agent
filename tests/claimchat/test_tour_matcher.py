"""Port of Tour_React tests/claimChatbot/tourMatch.test.ts and the tourMatcher tests of staysOnly.test.ts, plus checks
of our own (the expected values of those were taken from the TypeScript, run under Node)."""

from __future__ import annotations

from tests.claimchat.test_ocr_mapping import BILLS, RESULT
from tourclaim_agent.claimchat.claim_assistant import initial_claim_state, new_accommodation_row, new_transport_row
from tourclaim_agent.claimchat.ocr_mapping import attach_unmatched, map_ocr_to_rows
from tourclaim_agent.claimchat.tour_matcher import (
    find_tour_by_text,
    match_tours,
    norm,
    same_place,
    tour_window,
    triage_rows,
)

# ── tourMatch.test.ts ──

# the tour from the report: New Delhi → Pune, 16–17 Jan 2026
PUNE = {
    "tourId": 5,
    "source": "New Delhi, Delhi",
    "destination": "Pune",
    "tourStartDate": "2026-01-16",
    "tantetiveEndDate": "2026-01-17",
    "statusId": 2,
    "totalDays": 2,
}


def ticket(from_: str, to: str, date: str) -> dict:
    return {
        **new_transport_row(),
        "source": from_,
        "destination": to,
        "DateOfTravelSource": date,
        "DateOfTravelDestination": date,
        "uploadedFiles": [{"name": f"{to}-{date}.pdf", "size": 1, "type": "application/pdf"}],
    }


def stay(location: str, check_in: str, check_out: str) -> dict:
    return {
        **new_accommodation_row(),
        "location": location,
        "checkInDate": check_in,
        "checkOutDate": check_out,
        "billFiles": [],
    }


# TS: 'a ticket to another city the day after the tour ends is not this tour, even from the same office town'
def test_a_ticket_to_another_city_the_day_after_the_tour_ends_is_not_this_tour():
    t = triage_rows(
        {"transportationRows": [ticket("New Delhi (DEL)", "Jaipur (JAI)", "2026-01-18")], "accommodationRows": []},
        [PUNE],
    )
    assert len(t["groups"]) == 0
    assert len(t["dropped"]) == 1


# TS: 'a day either side still counts for the trip itself: out the night before, back the morning after'
def test_a_day_either_side_still_counts_for_the_trip_itself():
    out = triage_rows(
        {"transportationRows": [ticket("New Delhi (DEL)", "Pune (PNQ)", "2026-01-15")], "accommodationRows": []},
        [PUNE],
    )
    assert [g["tour"]["tourId"] for g in out["groups"]] == [5]
    back = triage_rows(
        {"transportationRows": [ticket("Pune (PNQ)", "New Delhi (DEL)", "2026-01-18")], "accommodationRows": []},
        [PUNE],
    )
    assert [g["tour"]["tourId"] for g in back["groups"]] == [5]


# TS: 'a bill inside the tour dates fits, even to another city (tours can have several stops)'
def test_a_bill_inside_the_tour_dates_fits_even_to_another_city():
    t = triage_rows({"transportationRows": [ticket("Pune", "Mumbai", "2026-01-17")], "accommodationRows": []}, [PUNE])
    assert [g["tour"]["tourId"] for g in t["groups"]] == [5]


# TS: 'a hotel in another city starting the day after the tour ends is not this tour'
def test_a_hotel_in_another_city_starting_the_day_after_the_tour_ends_is_not_this_tour():
    t = triage_rows(
        {"transportationRows": [], "accommodationRows": [stay("Jaipur, Rajasthan", "2026-01-18", "2026-01-20")]},
        [PUNE],
    )
    assert len(t["groups"]) == 0


# ── staysOnly.test.ts ──

# approved tours: 10–12 Sep 2026 and 18–20 Jan 2026 (no tour in Nov 2025)
SEP_TOUR = {
    "tourId": 1,
    "source": "Noida",
    "destination": "Delhi",
    "tourStartDate": "2026-09-10",
    "tantetiveEndDate": "2026-09-12",
    "statusId": 2,
    "totalDays": 3,
}
JAN_TOUR = {
    "tourId": 2,
    "source": "Noida",
    "destination": "Jaipur",
    "tourStartDate": "2026-01-18",
    "tantetiveEndDate": "2026-01-20",
    "statusId": 2,
    "totalDays": 3,
}


# TS: staysOnly.test.ts 'hotel bills only: each stay goes to the tour its dates fit; a stay no tour fits is left out'
def test_hotel_bills_only_each_stay_goes_to_the_tour_its_dates_fit():
    mapped = map_ocr_to_rows(RESULT, BILLS)
    triage = triage_rows(mapped, [SEP_TOUR, JAN_TOUR])
    by_tour = {g["tour"]["tourId"]: [r["billFiles"][0]["name"] for r in g["stays"]] for g in triage["groups"]}
    assert sorted(by_tour[1]) == ["ACC3_10SEP2026_12SEP2026.jpeg", "ACC_10SEP2026_12SEP2026.jpeg"]
    assert sorted(by_tour[2]) == ["ACC2_18JAN2026_20JAN2026.jpeg", "ACC_18JAN2026_20JAN2026.jpeg"]
    assert [d["files"] for d in triage["dropped"]] == [["ACC_06NOV2025_07NOV2025.jpeg|1"]]


# TS: staysOnly.test.ts 'attachUnmatched: once the claim is known, the empty bill goes to its first stay'
def test_attach_unmatched_once_the_claim_is_known_the_empty_bill_goes_to_its_first_stay():
    mapped = map_ocr_to_rows(RESULT, BILLS)
    sep_stays = triage_rows(mapped, [SEP_TOUR])["groups"][0]["stays"]
    r = attach_unmatched({"transportationRows": [], "accommodationRows": sep_stays}, mapped["unmatchedFiles"], BILLS)
    assert r["attached"] == ["ACC2_10SEP2026_12SEP2026.jpeg"]
    assert len(r["accommodationRows"][0]["billFiles"]) == 2
    # nothing to attach to: the bill stays unattached
    empty = attach_unmatched({"transportationRows": [], "accommodationRows": []}, mapped["unmatchedFiles"], BILLS)
    assert empty["attached"] == []


# ── our own ──


def test_tour_window_from_the_end_date_or_the_number_of_days():
    assert tour_window(PUNE) == {"start": "2026-01-16", "end": "2026-01-17"}
    assert tour_window({"tourStartDate": "30/12/2026", "totalDays": 3}) == {"start": "2026-12-30", "end": "2027-01-01"}
    assert tour_window({"tourStartDate": "2026-01-16", "totalDays": 0}) == {"start": "2026-01-16", "end": "2026-01-16"}
    # (totalDays || 1) - 1 with text that is no number: Math.max(0, NaN) gives an Invalid Date, as in the browser
    assert tour_window({"tourStartDate": "2026-01-16", "totalDays": "abc"}) == {
        "start": "2026-01-16",
        "end": "NaN-NaN-NaN",
    }
    assert tour_window({"tourStartDate": "garbage", "totalDays": 3}) is None


def test_norm_and_same_place():
    assert norm("  New Delhi (NDLS) ") == "new delhi ndls"
    assert norm(None) == ""
    assert same_place("new delhi", "delhi")
    assert not same_place("ny", "new york")


def test_match_tours_confident_only_with_a_clear_lead():
    claim = {**initial_claim_state(), "transportationRows": [ticket("New Delhi", "Pune", "2026-01-16")]}
    other = {**PUNE, "tourId": 6, "destination": "Goa", "tourStartDate": "2026-03-01", "tantetiveEndDate": "2026-03-02"}
    r = match_tours(claim, [other, PUNE])
    assert [m["tour"]["tourId"] for m in r["ranked"]] == [5, 6]
    assert r["ranked"][0]["dateFit"] == 1 and r["ranked"][0]["score"] == 4
    assert r["confident"] is r["ranked"][0]
    assert r["hasClues"] is True
    twin = {**PUNE, "tourId": 7}
    assert match_tours(claim, [PUNE, twin])["confident"] is None
    blank = match_tours(initial_claim_state(), [PUNE])
    assert blank["hasClues"] is False and blank["ranked"][0]["dateFit"] is None


def test_find_tour_by_text_by_number_or_a_single_route_city():
    tours = [PUNE, {**PUNE, "tourId": 45, "destination": "Jaipur"}]
    assert find_tour_by_text("tour 45 please", tours)["tourId"] == 45
    assert find_tour_by_text("jaipur", tours)["tourId"] == 45
    assert find_tour_by_text("delhi", tours) is None  # both start there
    assert find_tour_by_text("go", tours) is None


def test_triage_labels_and_the_tour_a_dropped_bill_belongs_to():
    jan = {**JAN_TOUR, "source": "Delhi"}
    t = triage_rows(
        {
            "transportationRows": [ticket("Delhi", "Jaipur", "2026-01-18")],
            "accommodationRows": [stay("Jaipur", "2026-09-30", "")],
        },
        [SEP_TOUR, jan],
        SEP_TOUR,
    )
    assert t["groups"] == []
    assert t["dropped"][0]["label"] == "ticket Delhi → Jaipur on 18 Jan 2026"
    assert t["dropped"][0]["files"] == ["Jaipur-2026-01-18.pdf|1"]
    assert t["dropped"][0]["belongsTo"] is jan
    assert t["dropped"][1] == {"label": "hotel stay in Jaipur, 30 Sept 2026", "files": []}
