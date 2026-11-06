"""Port of Tour_React tests/claimChatbot/withinAssistant.test.ts (covers withinTourPlan.ts and withinAssistant.ts).

The TypeScript test ran on the real clock; here the clock is fixed (6 Nov 2026), so the October plan stays inside
the tour plan date window whenever the tests run.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator
from typing import Any

import pytest

from tourclaim_agent.claimchat.js_compat import use_runtime
from tourclaim_agent.claimchat.within_assistant import (
    answer_gap,
    apply_within_edits,
    blank_tour,
    find_gaps,
    has_tour_data,
    journeys_of,
    progress_text,
    read_within_edits,
    tour_discrepancies,
    tour_ready_to_end,
)
from tourclaim_agent.claimchat.within_tour_plan import TourPlanError, map_tour_plan_to_tours

NOW_MS = 1_793_934_000_000  # 2026-11-06 08:30 IST


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    with use_runtime(now_ms=lambda: NOW_MS):
        yield


def leg(from_: str, to: str, d: str, dep: str, arr: str, mode: str, vehicle: str = "") -> dict[str, Any]:
    return {
        "from": from_,
        "to": to,
        "departureDate": d,
        "departureTime": dep,
        "arrivalDate": d,
        "arrivalTime": arr,
        "modeOfTravel": mode,
        "personalVehicleType": vehicle,
        "distance": 0,
        "includeTravelCosts": True,
    }


def plan() -> dict[str, Any]:
    """The October plan: 1 Oct (1 journey), 2 Oct (2), 3 Oct (3), 4 Oct (2)."""
    return {
        "meta": {"per_file_results": [{"filename": "Oct_Tour_Plan.pdf", "success": True, "bill_type": "tour_plan"}]},
        "tourDetails": {
            "purpose": "Inspect; Review; Audit; Meeting",
            "startDate": "2026-10-01",
            "startTime": "09:00",
            "from": "Delhi",
            "endDate": "2026-10-01",
            "endTime": "11:00",
            "to": "Ghaziabad",
            "modeOfTravel": "Company Provided Vehicle",
            "personalVehicleType": "",
            "distance": 0,
            "includeTravelCosts": True,
            "tourLegs": [
                leg("Delhi", "Faridabad", "2026-10-02", "08:30", "10:00", "Rail"),
                leg("Faridabad", "Palwal", "2026-10-02", "14:00", "15:15", "Public Transport"),
                leg("Delhi", "Noida", "2026-10-03", "07:45", "09:15", "Personal Vehicle", "Four Wheeler"),
                leg("Noida", "Greater Noida", "2026-10-03", "13:00", "14:00", "Personal Vehicle", "Four Wheeler"),
                leg("Greater Noida", "Dadri", "2026-10-03", "16:30", "17:15", "Personal Vehicle", "Four Wheeler"),
                leg("Delhi", "Gurugram", "2026-10-04", "09:30", "11:00", "Personal Vehicle", "Two Wheeler"),
                leg("Gurugram", "Manesar", "2026-10-04", "14:30", "15:30", "Personal Vehicle", "Two Wheeler"),
            ],
        },
    }


# TS: 'tour plan: one date = one tour, first journey is the main, the rest are legs'
def test_tour_plan_one_date_one_tour():
    out = map_tour_plan_to_tours(plan())
    tours = out["tours"]
    assert out["warnings"] == []
    assert len(tours) == 4
    assert [len(t["tourDetails"]["tourLegs"]) for t in tours] == [0, 1, 2, 1]
    assert [f"{t['tourDetails']['from']}>{t['tourDetails']['to']}" for t in tours] == [
        "Delhi>Ghaziabad",
        "Delhi>Faridabad",
        "Delhi>Noida",
        "Delhi>Gurugram",
    ]
    assert [t["tourDetails"]["purpose"] for t in tours] == ["Inspect", "Review", "Audit", "Meeting"]
    assert tours[2]["tourDetails"]["modeOfTravel"] == "Personal Vehicle"
    assert tours[2]["tourDetails"]["personalVehicleType"] == "Four Wheeler"
    assert tours[3]["tourDetails"]["tourLegs"][0]["personalVehicleType"] == "Two Wheeler"
    assert all(not t["tourDetails"]["tourEnd"] for t in tours)
    # tours after the first stay inside the month
    assert "allowedDateBounds" not in tours[0]["tourDetails"]
    assert tours[1]["tourDetails"]["allowedDateBounds"] == {
        "minStart": "2026-10-01",
        "maxStart": "2026-10-31",
        "maxEnd": "2026-11-01",
    }


# TS: 'tour plan: purposes that do not split per date are kept whole on every tour'
def test_tour_plan_purposes_that_do_not_split_are_kept_whole():
    p = plan()
    p["tourDetails"]["purpose"] = "One long purpose for the whole month"
    tours = map_tour_plan_to_tours(p)["tours"]
    assert all(t["tourDetails"]["purpose"] == "One long purpose for the whole month" for t in tours)


# TS: 'tour plan: a wrong file, two months, or no journeys are turned away'
def test_tour_plan_wrong_file_two_months_or_no_journeys_are_turned_away():
    ticket = plan()
    ticket["meta"]["per_file_results"][0]["bill_type"] = "ticket"
    with pytest.raises(TourPlanError):
        map_tour_plan_to_tours(ticket)

    two_months = plan()
    two_months["tourDetails"]["tourLegs"] = [leg("Delhi", "Noida", "2026-11-02", "08:00", "09:00", "Rail")]
    with pytest.raises(TourPlanError, match="more than one month"):
        map_tour_plan_to_tours(two_months)

    with pytest.raises(TourPlanError):
        map_tour_plan_to_tours({"tourDetails": {"tourLegs": []}})
    with pytest.raises(TourPlanError):
        map_tour_plan_to_tours({"meta": {"per_file_results": [{"filename": "x", "success": False, "error": "bad"}]}})


# TS: 'gaps: only personal-vehicle distance is missing after a plan is read, and answers fill it'
def test_gaps_only_personal_vehicle_distance_and_answers_fill_it():
    tours = map_tour_plan_to_tours(plan())["tours"]
    gaps = find_gaps(tours)
    assert [f"{g['tourNo']}.{g['leg']}.{g['kind']}" for g in gaps] == [
        "3.0.distance",
        "3.1.distance",
        "3.2.distance",
        "4.0.distance",
        "4.1.distance",
    ]
    assert not tour_ready_to_end(tours[2])

    for gap in gaps:
        result = answer_gap(tours, gap, "12 km")
        assert "error" not in result
        tours = result["tours"]
    assert len(find_gaps(tours)) == 0
    assert all(tour_ready_to_end(t) for t in tours)
    assert journeys_of(tours[2], 3)[2]["distance"] == 12
    assert tours[2]["tourDetails"]["includeTravelCosts"] is True


# TS: 'answers: bad values are refused, good ones are read'
def test_answers_bad_values_refused_good_ones_read():
    tours = map_tour_plan_to_tours(plan())["tours"]
    distance = find_gaps(tours)[0]
    assert answer_gap(tours, distance, "a lot").get("error")
    assert answer_gap(tours, distance, "0").get("error")
    assert answer_gap(tours, distance, "99999").get("error")
    assert "error" not in answer_gap(tours, distance, "24.5")
    # the old tours are never touched
    assert tours[2]["tourDetails"]["distance"] == 0

    no_mode = copy.deepcopy(plan())
    no_mode["tourDetails"]["tourLegs"][0]["modeOfTravel"] = ""
    t2 = map_tour_plan_to_tours(no_mode)["tours"]
    mode_gap = next(g for g in find_gaps(t2) if g["kind"] == "mode")
    assert answer_gap(t2, mode_gap, "helicopter").get("error")
    assert journeys_of(answer_gap(t2, mode_gap, "by train")["tours"][1], 2)[0]["mode"] == "Rail"


# TS: 'edits: validated, and ended tours are never changed'
def test_edits_validated_and_ended_tours_never_changed():
    tours = map_tour_plan_to_tours(plan())["tours"]
    out = apply_within_edits(
        tours,
        [
            {"tour": 3, "leg": 1, "field": "distanceKm", "value": 24},
            {"tour": 3, "leg": 0, "field": "arrivalTime", "value": "9.30 pm"},
            {"tour": 1, "leg": 0, "field": "vehicleType", "value": "Two Wheeler"},  # company vehicle: refused
            {"tour": 9, "leg": 0, "field": "mode", "value": "Rail"},  # no such tour
            {"tour": 2, "leg": 5, "field": "mode", "value": "Rail"},  # no such leg
            {"tour": 2, "leg": 0, "field": "distanceKm", "value": -3},
        ],
    )
    assert len(out["applied"]) == 2
    assert len(out["rejected"]) == 4
    assert journeys_of(out["tours"][2], 3)[1]["distance"] == 24
    assert journeys_of(out["tours"][2], 3)[0]["arrTime"] == "21:30"

    ended = [{**t, "tourDetails": {**t["tourDetails"], "tourEnd": True}} if i == 0 else t for i, t in enumerate(tours)]
    rejected = apply_within_edits(ended, [{"tour": 1, "leg": 0, "field": "mode", "value": "Rail"}])["rejected"]
    assert re.search("already ended", rejected[0])


# TS: 'model edits: well-formed ones are kept, junk is dropped'
def test_model_edits_well_formed_kept_junk_dropped():
    assert read_within_edits(
        [{"tour": 3, "leg": 1, "field": "distanceKm", "value": 24}, {"nonsense": True}, None, "x"]
    ) == [{"tour": 3, "leg": 1, "field": "distanceKm", "value": 24}]
    assert len(read_within_edits([])) == 0


# TS: 'progress: blank form, open tours and the approver are reported'
def test_progress_blank_form_open_tours_and_approver():
    assert has_tour_data([blank_tour()]) is False
    assert re.search(
        "no tours yet", progress_text([blank_tour()], {"selfApprove": False, "approverChosen": False})["text"], re.I
    )
    tours = map_tour_plan_to_tours(plan())["tours"]
    p = progress_text(tours, {"selfApprove": False, "approverChosen": False})
    assert p["ready"] is False
    assert re.search("approving authority", p["text"])
    assert re.search("not ended", progress_text(tours, {"selfApprove": True, "approverChosen": False})["text"])


# TS: 'discrepancies: nothing may be ended while any tour has a gap; none once all are complete'
def test_discrepancies_until_all_complete():
    tours = map_tour_plan_to_tours(plan())["tours"]
    before = tour_discrepancies(tours)
    assert len(before) == 5
    assert re.fullmatch(r"Tour 3 \(Delhi → Noida\): distance \(km\) missing", before[0])
    assert re.match(r"Tour 3, leg 1 \(Noida → Greater Noida\)", before[1])

    # a missing purpose and a same-minute journey are reported too
    odd = [
        {
            **t,
            "tourDetails": {
                **t["tourDetails"],
                "purpose": "",
                "endTime": t["tourDetails"]["startTime"],
                "endDate": t["tourDetails"]["startDate"],
            },
        }
        if i == 0
        else t
        for i, t in enumerate(tours)
    ]
    lines = tour_discrepancies(odd)
    assert "Tour 1: the purpose is missing" in lines
    assert any(re.search(r"Tour 1 .*start and end time are the same", line) for line in lines)

    for gap in find_gaps(tours):
        tours = answer_gap(tours, gap, "10")["tours"]
    assert tour_discrepancies(tours) == []
    # tours that are already ended are not reported again
    assert tour_discrepancies([{**t, "tourDetails": {**t["tourDetails"], "tourEnd": True}} for t in tours]) == []
