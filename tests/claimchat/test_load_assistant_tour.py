"""Checks of our own for load_assistant_tour.py (loadAssistantTour.ts has no TypeScript test); expected values from
the TypeScript, run under Node with the two API responses stubbed."""

from __future__ import annotations

from tourclaim_agent.claimchat.load_assistant_tour import assistant_tour_from

CLAIM = {
    "statusCode": 200,
    "data": {
        "empTourDetails": {
            "source": "Noida",
            "destination": "Pune",
            "purposeOfTour": "Audit",
            "tentativeStartDate": "2026-10-04",
        },
        "trDetails": {"tentativeStartDate": "2026-10-05T00:00:00"},
        "tentativeTourPlans": [
            {"date": "2026-10-05", "travelMode": "Air"},
            {"date": "2026-10-07", "travelMode": "rail"},
        ],
    },
}


def test_the_plan_comes_from_the_tour_plan_when_it_has_one():
    plan = {"data": {"tentativeTourPlans": [{"date": "2026-10-09", "travelMode": "rail"}]}}
    assert assistant_tour_from(4522, CLAIM, plan) == {
        "tour": {
            "tourId": 4522,
            "source": "Noida",
            "destination": "Pune",
            "startDate": "2026-10-05T00:00:00",
            "endDate": "2026-10-09",
            "purpose": "Audit",
        },
        # the rules come from the claim request only
        "rules": {"allowAir": True, "minDate": "2026-10-05", "maxDate": "2026-10-08"},
    }


def test_without_the_tour_plan_the_claim_request_plans_are_used():
    for plan in (None, {"data": {"tentativeTourPlans": []}}, {}):
        assert assistant_tour_from(4522, CLAIM, plan)["tour"]["endDate"] == "2026-10-07"


def test_details_under_the_other_name_and_nothing_known():
    claim = {"data": {"employeeDetails": {"source": "Delhi", "tentativeStartDate": "2026-09-10"}}}
    assert assistant_tour_from(7, claim, None) == {
        "tour": {"tourId": 7, "source": "Delhi", "startDate": "2026-09-10"},
        "rules": {"allowAir": False, "minDate": "2026-09-10", "maxDate": "2026-09-11"},
    }
