"""Port of the rowOrder.ts tests of Tour_React tests/claimChatbot/timeOverlap.test.ts.

Not ported here (they need claimProgress.ts / tourReview.ts, next phase): 'the chat tells about the overlap and how to
fix it', 'the review asks about the overlap as a must-fix, and the short summary names it'. The rowOrder tests of
savedClaim.test.ts build their claim with claimStateFromDraft (savedClaim.ts) and move with that module.
"""

from __future__ import annotations

from tourclaim_agent.claimchat.claim_assistant import new_transport_row
from tourclaim_agent.claimchat.row_order import (
    ids_of,
    in_stored_order,
    journey_overlaps,
    journeys_in_date_order,
    kept_file_ids,
    stays_in_date_order,
    trip_ends,
)


def j(from_: str, to: str, dep: str, dep_t: str, arr: str, arr_t: str, mode: str) -> dict:
    return {
        **new_transport_row(),
        "source": from_,
        "destination": to,
        "DateOfTravelSource": dep,
        "TimeOfTravelSource": dep_t,
        "DateOfTravelDestination": arr,
        "TimeOfTravelDestiNation": arr_t,
        "ModeOfTravel": mode,
    }


# the four bills of the Ambala ⇄ New Delhi trip, in date order as the chat shows them
def ambala_trip() -> list[dict]:
    return [
        j("AMBALA CANT JN", "NEW DELHI", "2026-08-24", "07:38", "2026-08-24", "10:15", "air"),
        j(
            "New Delhi Railway Station",
            "Noida Corporate Office",
            "2026-08-24",
            "10:15",
            "2026-08-24",
            "11:33",
            "public-transport",
        ),
        j(
            "Noida Corporate Office",
            "New Delhi Railway Station",
            "2026-08-24",
            "17:00",
            "2026-08-24",
            "19:00",
            "public-transport",
        ),
        j("NEW DELHI (NDLS)", "AMBALA CANT JN (UMB)", "2026-08-24", "17:15", "2026-08-24", "19:50", "air"),
    ]


# TS: 'journeyOverlaps: the cab arriving at 19:00 and the train leaving at 17:15 overlap'
def test_journey_overlaps_the_cab_arriving_at_19_and_the_train_leaving_at_17_15_overlap():
    rows = ambala_trip()
    found = journey_overlaps(rows)
    assert len(found) == 1
    assert found[0]["earlier"] is rows[2]
    assert found[0]["later"] is rows[3]


# TS: 'journeyOverlaps: arriving exactly when the next leaves is fine; missing times are not judged'
def test_journey_overlaps_arriving_when_the_next_leaves_is_fine_missing_times_not_judged():
    assert len(journey_overlaps(ambala_trip()[:2])) == 0
    no_time = [
        j("A", "B", "2026-08-24", "10:00", "2026-08-24", "", "rail"),
        j("B", "C", "2026-08-24", "11:00", "2026-08-24", "12:00", "rail"),
    ]
    assert len(journey_overlaps(no_time)) == 0


# TS: 'journeyOverlaps: not only neighbours (a long journey covering two later ones)'
def test_journey_overlaps_not_only_neighbours():
    rows = [
        j("A", "B", "2026-08-24", "08:00", "2026-08-25", "08:00", "rail"),
        j("B", "C", "2026-08-24", "20:00", "2026-08-24", "21:00", "public-transport"),
        j("C", "D", "2026-08-24", "22:00", "2026-08-24", "23:00", "public-transport"),
    ]
    assert len(journey_overlaps(rows)) == 2


# ── our own ──


def row(id_: str, dep: str = "", dep_t: str = "", arr: str = "", arr_t: str = "", **extra) -> dict:
    return {
        "id": id_,
        "DateOfTravelSource": dep,
        "TimeOfTravelSource": dep_t,
        "DateOfTravelDestination": arr,
        "TimeOfTravelDestiNation": arr_t,
        **extra,
    }


def test_journeys_in_date_order_falls_back_to_arrival_puts_undated_last_and_is_stable():
    rows = [
        row("none"),
        row("12", "2026-09-12"),
        row("arr-11", arr="2026-09-11", arr_t="08:00"),
        row("10b", "2026-09-10T00:00:00", "09:00"),
        row("10a", "2026-09-10", "09:00"),
        row("none2"),
    ]
    assert ids_of(journeys_in_date_order(rows)) == ["10b", "10a", "arr-11", "12", "none", "none2"]


def test_date_order_on_raw_saved_dates_follows_localecompare():
    # raw dates kept from a saved draft sort the ICU way: "garbage" before "Select" (plain string order: the reverse)
    rows = [row("S", "Select", "09:00"), row("g", "garbage", "18:00"), row("d", "05/10/2026")]
    assert ids_of(journeys_in_date_order(rows)) == ["d", "g", "S"]
    stays = [{"id": "b", "checkInDate": "b"}, {"id": "A", "checkInDate": "A"}, {"id": "x", "checkInDate": ""}]
    assert ids_of(stays_in_date_order(stays)) == ["A", "b", "x"]


def test_trip_ends_first_wins_ties_on_departure_last_wins_ties_on_arrival():
    rows = [row("a", "2026-09-10", "09:00", "2026-09-12"), row("b", "2026-09-10", "09:00", "2026-09-12"), row("c")]
    ends = trip_ends(rows)
    assert (ends["first"]["id"], ends["last"]["id"]) == ("a", "b")
    assert trip_ends([row("x"), row("y")]) == {"first": rows[2] | {"id": "x"}, "last": rows[2] | {"id": "y"}}
    assert trip_ends([]) == {"first": None, "last": None}


def test_in_stored_order_and_kept_file_ids():
    view = [row("new"), row("s2"), row("s1")]
    assert ids_of(in_stored_order(view, ["s1", "gone", "s2"])) == ["s1", "s2", "new"]
    assert in_stored_order(view, []) is view
    files = [
        {"uploadedFiles": [{"name": "a", "fileId": "f-1"}, {"name": "b"}]},
        {"billFiles": [{"name": "c", "fileId": ""}, {"name": "d", "fileId": "f-2"}]},
    ]
    assert kept_file_ids(files) == ["f-1", "f-2"]
