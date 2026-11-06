"""JavaScript-semantics cases of the Within Jurisdiction port (within_tour_plan.py, within_assistant.py) that the
ported TypeScript tests do not cover. Every expected value here was checked against the original .ts under Node
(tools/parity_diff/within_run.mjs)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from importlib import resources
from typing import Any

import pytest

from tourclaim_agent.claimchat.js_compat import UNDEFINED, use_runtime
from tourclaim_agent.claimchat.within_assistant import (
    WITHIN_DA_RULES,
    WITHIN_EDIT_FORMAT,
    answer_gap,
    apply_within_edits,
    blank_tour,
    claim_totals,
    find_gaps,
    iso_of,
    progress_text,
    read_within_edits,
    snapshot,
    totals_text,
    tour_summary_rows,
)
from tourclaim_agent.claimchat.within_form import DECLARATION_TEXTS
from tourclaim_agent.claimchat.within_tour_plan import (
    TourPlanError,
    map_tour_plan_to_tours,
    map_vehicle_type,
    map_within_travel_mode,
)

NOV_6 = 1_793_934_000_000  # 2026-11-06 08:30 IST
NOV_30 = 1_795_980_600_000  # 2026-11-30 IST


@pytest.fixture(autouse=True)
def fixed_clock() -> Iterator[None]:
    with use_runtime(now_ms=lambda: NOV_6):
        yield


def journey(d: str, **extra: Any) -> dict[str, Any]:
    return {"from": "Delhi", "to": "Noida", "departureDate": d, "departureTime": "09:00", "arrivalTime": "10:00",
            "modeOfTravel": "Rail", **extra}  # fmt: skip


def plan(*legs: dict[str, Any], **main: Any) -> dict[str, Any]:
    return {"tourDetails": {"purpose": "Audit", **main, "tourLegs": list(legs)}}


def tour(**details: Any) -> dict[str, Any]:
    base = {"startDate": "2026-10-05", "startTime": "09:00", "endDate": "2026-10-05", "endTime": "10:00",
            "modeOfTravel": "Rail", "from": "Delhi", "to": "Noida", "purpose": "Audit", "includeTravelCosts": False,
            "tourLegs": [], "tourEnd": False, "subTotal": 0}  # fmt: skip
    return {"id": "tour-1", "tourDetails": {**base, **details}, "declarationAccepted": False, "isExpanded": True}


# ─── withinTourPlan ───────────────────────────────────────────────────────────


def test_travel_mode_matching_keeps_the_old_order_and_quirks():
    assert map_within_travel_mode("  public transport BEYOND entitled ") == "Public Transport Beyond Entitled"
    assert map_within_travel_mode("official car") == "Company Provided Vehicle"
    assert map_within_travel_mode("down town") == "Personal Vehicle"  # "own" inside "down"
    assert map_within_travel_mode("by train") == "Rail"
    assert map_within_travel_mode("auto rickshaw") == "Public Transport"
    assert map_within_travel_mode("flight") == ""
    assert map_within_travel_mode(None) == ""
    with pytest.raises(TypeError):
        map_within_travel_mode(5)  # (5).trim is not a function


def test_vehicle_type_matching():
    assert map_vehicle_type("two wheeler") == "Two Wheeler"  # JavaScript \s has the no-break space
    assert map_vehicle_type("", "Personal Vehicle (4-Wheeler)") == "Four Wheeler"
    assert map_vehicle_type("Car") == "Four Wheeler"
    assert map_vehicle_type("carpool") == ""
    assert map_vehicle_type("scooty") == ""


def test_plan_dates_are_the_json_of_local_midnight_and_journey_values_are_read_as_before():
    out = map_tour_plan_to_tours(
        plan(journey("5 Oct 2026", distance="1e2", includeTravelCosts=None, modeOfTravel="own two wheeler"))
    )
    (t,) = out["tours"]
    d = t["tourDetails"]
    assert d["startDate"] == "2026-10-04T18:30:00.000Z"
    assert d["endDate"] == "2026-10-04T18:30:00.000Z"  # no arrival date: the departure date
    assert d["distance"] == 100
    assert d["includeTravelCosts"] is True  # only false turns it off
    assert (d["modeOfTravel"], d["personalVehicleType"]) == ("Personal Vehicle", "Two Wheeler")
    assert t["id"] == f"tour-{NOV_6}-0"
    assert out["warnings"] == []


def test_plan_window_uses_set_month_roll_over():
    p = plan(journey("2027-03-02"), journey("2027-03-03"))
    with use_runtime(now_ms=lambda: NOV_30):  # 30 Nov + 3 months = 30 Feb = 2 Mar
        out = map_tour_plan_to_tours(p)
    assert len(out["tours"]) == 1
    assert out["warnings"] == [
        "Tour dated 2027-03-03 was skipped: tours can only be dated from 2025-09-24 up to 2027-03-02."
    ]


def test_plan_errors_and_warnings_word_for_word():
    with pytest.raises(TourPlanError) as e:
        map_tour_plan_to_tours({"meta": {"per_file_results": [{"filename": "x", "success": False, "error": "bad"}]}})
    assert str(e.value) == "The tour plan could not be read (bad). Please upload a clear PDF or image."
    with pytest.raises(TourPlanError) as e:
        map_tour_plan_to_tours(plan(journey("2025-09-20")))
    assert str(e.value) == "None of the dates in this tour plan can be claimed."
    with pytest.raises(TourPlanError) as e:
        map_tour_plan_to_tours(plan(journey("2026-10-05"), journey("2026-11-02")))
    assert str(e.value) == (
        "This tour plan covers more than one month (2026-10, 2026-11). Please upload a plan for a single month."
    )
    out = map_tour_plan_to_tours(plan(journey("garbage"), journey("2026-10-05", modeOfTravel="walk")))
    assert out["warnings"] == [
        "1 journey(s) were skipped because their date could not be read.",
        "Some journeys have no mode of travel. Please select it before ending the tour.",
    ]
    with pytest.raises(TypeError):
        map_tour_plan_to_tours(plan(journey("2026-10-05"), None))  # a null leg throws, as before


def test_plan_purposes_split_only_when_one_per_tour():
    out = map_tour_plan_to_tours(plan(journey("2026-10-05"), purpose="Site visit; "))
    assert out["tours"][0]["tourDetails"]["purpose"] == "Site visit"


# ─── withinAssistant ──────────────────────────────────────────────────────────


def test_iso_of_and_dates_shown():
    assert iso_of("2026-02-30") == "2026-02-30"  # kept as written
    assert iso_of("2026-09-30T18:30:00.000Z") == "2026-10-01"  # local (India) date
    assert iso_of("Oct 5 2026") == "2026-10-05"
    assert iso_of(0) == ""
    assert iso_of("garbage") == ""
    # date-fns month names ("Sep", not en-IN "Sept"); an impossible day rolls over
    rows = tour_summary_rows([tour(startDate="2026-09-30T18:30:00.000Z"), tour(startDate="2026-09-31")])
    assert [r["label"] for r in rows] == ["Tour 1 · 01 Oct", "Tour 2 · 01 Oct"]
    assert snapshot([tour(startDate="2026-09-05")]).startswith("Tour 1 (05 Sep 2026) purpose: Audit")


def test_snapshot_and_summary_texts():
    t = tour(modeOfTravel="Personal Vehicle", personalVehicleType="", distance="12.50", to="Sector 62, Noida")
    assert snapshot([t]) == (
        "Tour 1 (05 Oct 2026) purpose: Audit\n"
        "  leg 0: Delhi → Sector 62, Noida, 09:00 to 10:00, mode Personal Vehicle, vehicle -, 12.5 km"
    )
    assert tour_summary_rows([{**t, "tourDetails": {**t["tourDetails"], "tourEnd": True}}]) == [
        {"label": "Tour 1 · 05 Oct", "value": "Delhi → Sector 62 (Personal Vehicle) · ended"}
    ]
    assert snapshot([blank_tour()]) == "No tours yet."


def test_edits_keep_the_old_quirks():
    tours = [tour(tourLegs=[{"id": "l1", "from": "Noida", "to": "Delhi", "departureDate": "2026-10-05",
                             "departureTime": "17:00", "arrivalDate": "2026-10-05", "arrivalTime": "18:00",
                             "modeOfTravel": "Rail", "includeTravelCosts": False}])]  # fmt: skip
    out = apply_within_edits(
        tours,
        [
            {"tour": "1", "leg": 0.5, "field": "from", "value": "Agra"},  # a half leg: "applied", nothing changes
            {"tour": 1, "leg": 1, "field": "distanceKm", "value": "1,200"},
            {"tour": 1, "leg": 0, "field": "distanceKm", "value": "12 km"},  # the whole text must be a number
            {"tour": 1, "leg": 0, "field": "departureDate", "value": "2026-10-06"},
            {"tour": 1, "leg": 0, "field": "mode", "value": "own car"},
            {"tour": 1, "leg": 0, "field": "vehicleType", "value": "car"},
        ],
    )
    assert out["applied"] == [
        "Tour 1, leg 0.5: from Agra",
        "Tour 1, leg 1: distance 1200 km",
        "Tour 1: mode Personal Vehicle",
        "Tour 1: vehicle Four Wheeler",
    ]
    assert out["rejected"] == [
        'Tour 1: "12 km" isn\'t a usable distance.',
        'Tour 1: I can\'t change "departureDate" in the chat. Please use the form.',
    ]
    leg = out["tours"][0]["tourDetails"]["tourLegs"][0]
    assert (leg["from"], leg["distance"], leg["includeTravelCosts"]) == ("Noida", 1200, True)
    assert tours[0]["tourDetails"]["modeOfTravel"] == "Rail"  # the input is never changed


def test_purpose_is_cut_at_500_utf16_units():
    tours = [tour(purpose="")]
    gap = find_gaps(tours)[0]
    long = answer_gap(tours, gap, "😀" * 300)["tours"][0]["tourDetails"]["purpose"]
    assert long == "😀" * 250


def test_answers_read_as_before():
    tours = [tour(modeOfTravel="Personal Vehicle", personalVehicleType="Two Wheeler", distance=0)]
    (gap,) = find_gaps(tours)
    assert gap["question"] == "How many km was Tour 1 (05 Oct) (Delhi → Noida)?"
    assert answer_gap(tours, gap, "about 24.5km")["tours"][0]["tourDetails"]["distance"] == 24.5
    assert answer_gap(tours, gap, "2001 km")["error"] == (
        "2001 km is more than a within-jurisdiction tour can be. Please check and give the distance in km."
    )
    assert answer_gap(tours, {**gap, "kind": "junk"}, "x") is UNDEFINED  # the TypeScript switch fell through
    assert answer_gap([], UNDEFINED, "x") == {"tours": [], "error": "That tour is no longer on the form."}


def test_read_within_edits_keeps_what_the_old_filter_kept():
    kept = {"tour": None, "field": "from", "value": 0}  # Number(null) is 0: finite
    assert read_within_edits([kept, {"tour": "x", "field": "from", "value": "a"}, [1]]) == [kept]


def test_totals_sum_what_the_tours_hold():
    t = tour(amountDetails={"totalClaimedAmount": 0, "claimedAmount": "250.5"}, tourEnd=True)
    t["accommodationDetails"] = {"entries": [{"invoice": {"claimedAmountWithTax": "1234567.555"}}], "totalAmount": 0}
    t["daAllowanceDetails"] = {"entries": [{"amount": 300, "daApplicable": True}, {"amount": 300, "daApplicable": 0}],
                               "subTotal": 0, "approvingAuthority": "", "uploadedFile": None}  # fmt: skip
    assert claim_totals([t])["total"] == 250.5 + 1234567.555 + 300
    assert totals_text([t, tour()]) == (
        "Transport ₹250.50 · Accommodation ₹12,34,567.56 · DA ₹300.00 · Total ₹12,35,118.06\n"
        "DA is worked out only for ended tours (1 of 2 so far)."
    )


def test_progress_texts_word_for_word():
    ctx = {"selfApprove": False, "approverChosen": True}
    done = progress_text([tour()], ctx)
    assert done == {
        "text": "Still to do:\n• 1 tour not ended yet (1). DA and accommodation open up once a tour is ended. Every "
        'open tour can be ended now: say "end all tours".\n• Add hotel stays and travel bills on the form if you have '
        "any.",
        "ready": False,
    }
    ended = tour(tourEnd=True)
    assert progress_text([ended], ctx) == {
        "text": "Everything needed is filled in. Add hotel stays and bills on the form if you have any, then say "
        '"check & submit".',
        "ready": True,
    }
    gaps = progress_text(
        [tour(to="", startTime=""), tour(), tour(purpose="")], {"selfApprove": 0, "approverChosen": ""}
    )
    assert gaps["text"] == (
        "Still to do:\n• 3 details still missing: Tour 1, Tour 3.\n• 3 tours not ended yet (1, 2, 3). DA and "
        "accommodation open up once a tour is ended. Resolve the missing details first, then I can end them all.\n"
        "• No approving authority selected: choose one at the bottom of the form.\n"
        "• Add hotel stays and travel bills on the form if you have any."
    )


def test_model_texts_equal_the_prompts_file_and_the_declarations():
    prompts = json.loads(
        resources.files("tourclaim_agent.claimchat.ai").joinpath("prompts.json").read_text(encoding="utf-8")
    )
    assert prompts["WITHIN_EDIT_FORMAT"] == WITHIN_EDIT_FORMAT
    assert prompts["WITHIN_DA_RULES"] == WITHIN_DA_RULES
    assert len(DECLARATION_TEXTS) == 7
    assert DECLARATION_TEXTS[4] == "Tour destination is more than 8 km from my place of work"
