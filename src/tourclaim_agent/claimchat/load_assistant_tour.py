"""A tour's details and limits for the Claim Assistant.

Port of the pure part of Tour_React src/lib/claimChatbot/loadAssistantTour.ts (fetchAssistantTour). The TypeScript
read two responses in parallel; here the caller makes the calls and passes their bodies in:

- `claim_response`: the body of GET Claim/RaiseClaimRequest/{tourId} (`{statusCode, data: {...}}`);
- `plan_response`: the body of GET TourPlan/GetTourDetails/{tourId}, or None when that call failed (the TypeScript
  caught its error and went on with null).

The claim request has no day remarks, so the plan comes from the tour plan itself when it has one.
"""

from __future__ import annotations

from typing import Any, TypedDict

from .claim_assistant import TourContext, TourRules, tour_rules_from
from .js_compat import UNDEFINED, defined, jget, js_len, js_or, nullish


class AssistantTourDetails(TypedDict):
    tour: TourContext
    rules: TourRules


def _length(v: Any) -> Any:
    """`v.length` (undefined for anything without one)."""
    if isinstance(v, list):
        return len(v)
    if isinstance(v, str):
        return js_len(v)
    return UNDEFINED


def _last(plans: Any) -> Any:
    """`plans.slice(-1)[0]`."""
    if isinstance(plans, list):
        return plans[-1] if plans else UNDEFINED
    if isinstance(plans, str):
        return plans[-1] if plans else UNDEFINED
    raise TypeError("plans.slice is not a function")


def assistant_tour_from(tour_id: int, claim_response: Any, plan_response: Any = None) -> AssistantTourDetails:
    """loadAssistantTour.ts:fetchAssistantTour, from the two responses: the tour as the chat knows it, and its rules."""
    claim_data = jget(claim_response, "data")
    # the tour's own details come under one name or the other
    details = nullish(jget(claim_data, "empTourDetails"), jget(claim_data, "employeeDetails"))
    plan_rows = nullish(jget(jget(plan_response, "data"), "tentativeTourPlans"), [])
    plans = (
        plan_rows
        if js_or(_length(plan_rows), 0)
        else js_or(jget(claim_data, "tentativeTourPlans"), js_or(jget(claim_data, "tentativeTourPlanList"), []))
    )
    tour = defined(
        tourId=tour_id,
        source=jget(details, "source"),
        destination=jget(details, "destination"),
        startDate=nullish(
            jget(jget(claim_data, "trDetails"), "tentativeStartDate"), jget(details, "tentativeStartDate")
        ),
        endDate=jget(_last(plans), "date"),
        purpose=jget(details, "purposeOfTour"),
    )
    return {"tour": tour, "rules": tour_rules_from(claim_data)}  # type: ignore[typeddict-item]
