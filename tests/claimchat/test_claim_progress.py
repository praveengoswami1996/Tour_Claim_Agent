"""Port of the claimProgress tests of Tour_React tests/claimChatbot/claimAssistant.test.ts (findClaimIssues,
stayBillProblem, buildProgress) and timeOverlap.test.ts ('the chat tells about the overlap and how to fix it'), plus
checks of our own (expected texts taken from the TypeScript, run under Node).

Not ported here: timeOverlap.test.ts 'the review asks about the overlap as a must-fix...' needs tourReview.ts.
"""

from __future__ import annotations

import re

from tourclaim_agent.claimchat.claim_assistant import initial_claim_state, new_accommodation_row, new_transport_row
from tourclaim_agent.claimchat.claim_progress import (
    build_progress,
    covered_days,
    covered_text,
    day_text,
    each_day,
    find_claim_issues,
    find_relation_issues,
    find_time_overlaps,
    group_days,
    progress_text,
    range_text,
    stay_bill_problem,
    uncovered_days,
)

rules = {"allowAir": False, "minDate": "2026-10-05", "maxDate": "2026-10-11"}


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


# ── claimAssistant.test.ts ──


# TS: 'findClaimIssues: arrives before departs, outside tour, stay and leave outside tour'
def test_find_claim_issues_arrives_before_departs_outside_tour_stay_and_leave_outside_tour():
    s = {
        **initial_claim_state(),
        "transportationRows": [
            journey(DateOfTravelDestination="2026-10-04", TimeOfTravelDestiNation="09:00"),
            journey(DateOfTravelSource="2026-12-01", DateOfTravelDestination="2026-12-01"),
        ],
        "includeAccommodation": True,
        "accommodationRows": [
            {
                **new_accommodation_row(),
                "location": "Pune",
                "checkInDate": "2026-10-07",
                "checkInTime": "12:00",
                "checkOutDate": "2026-10-06",
                "checkOutTime": "10:00",
            }
        ],
    }
    text = "\n".join(find_claim_issues(s, rules))
    assert re.search(r"arrives before it departs", text)
    assert re.search(r"outside your approved tour", text)
    assert re.search(r"check-out is before the check-in", text)
    assert find_claim_issues(claim(), rules) == []


# TS: 'stayBillProblem: bad hotel bills are refused with a reason'
def test_stay_bill_problem_bad_hotel_bills_are_refused_with_a_reason():
    travel = [
        {"DateOfTravelSource": "2026-10-05", "DateOfTravelDestination": "2026-10-05"},
        {"DateOfTravelSource": "2026-10-08", "DateOfTravelDestination": "2026-10-08"},
    ]
    assert stay_bill_problem({"checkInDate": "2026-10-05", "checkOutDate": "2026-10-08"}, rules, travel) is None
    assert re.search(
        r"before the check-in",
        stay_bill_problem({"checkInDate": "2026-10-08", "checkOutDate": "2026-10-06"}, rules, travel) or "",
    )
    assert re.search(
        r"outside your tour dates",
        stay_bill_problem({"checkInDate": "2026-12-01", "checkOutDate": "2026-12-03"}, rules, travel) or "",
    )
    assert re.search(
        r"travel dates",
        stay_bill_problem({"checkInDate": "2026-10-05", "checkOutDate": "2026-10-20"}, {"allowAir": True}, travel)
        or "",
    )
    assert stay_bill_problem({}, rules, travel) is None


# TS: 'buildProgress: not ready until tour, journeys, stays and leave are settled'
def test_build_progress_not_ready_until_tour_journeys_stays_and_leave_are_settled():
    base = {
        "state": claim(),
        "tour": None,
        "rules": rules,
        "staysAnswered": False,
        "leaveAnswered": False,
        "doneUploading": True,
    }
    assert build_progress(base)["ready"] is False
    tour = {
        "tourId": 1,
        "source": "Noida",
        "destination": "Pune",
        "tourStartDate": "2026-10-05",
        "statusId": 1,
        "totalDays": 7,
    }
    done = build_progress({**base, "tour": tour, "staysAnswered": True, "leaveAnswered": True})
    assert done["ready"] is True
    assert len(done["heads"]) == 0
    assert build_progress({**base, "state": initial_claim_state(), "tour": tour})["ready"] is False


# ── timeOverlap.test.ts ──


def j(from_, to, dep, dep_t, arr, arr_t, mode):
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


def ambala_trip():
    """the four bills of the Ambala ⇄ New Delhi trip, in date order as the chat shows them"""
    return [
        j("AMBALA CANT JN", "NEW DELHI", "2026-08-24", "07:38", "2026-08-24", "10:15", "air"),
        j("New Delhi Railway Station", "Noida Corporate Office", "2026-08-24", "10:15", "2026-08-24", "11:33",
          "public-transport"),
        j("Noida Corporate Office", "New Delhi Railway Station", "2026-08-24", "17:00", "2026-08-24", "19:00",
          "public-transport"),
        j("NEW DELHI (NDLS)", "AMBALA CANT JN (UMB)", "2026-08-24", "17:15", "2026-08-24", "19:50", "air"),
    ]  # fmt: skip


# TS: timeOverlap.test.ts 'the chat tells about the overlap and how to fix it'
def test_the_chat_tells_about_the_overlap_and_how_to_fix_it():
    issues = find_time_overlaps({**initial_claim_state(), "transportationRows": ambala_trip()})
    assert len(issues) == 1
    assert re.search(r"journey 3 and journey 4 overlap", issues[0])
    assert re.search(r"departs at 17:15", issues[0])
    assert re.search(r"arrives at 19:00", issues[0])
    assert re.search(r"correct .* in the claim", issues[0])
    assert any(
        re.search(r"overlap", i)
        for i in find_claim_issues({**initial_claim_state(), "transportationRows": ambala_trip()})
    )


# ── our own: exact texts ──


def test_days_ranges_and_groups():
    assert day_text("2026-09-05") == "05 Sept 2026"
    assert range_text("2026-10-05", "2026-10-07") == "05 Oct 2026 – 07 Oct 2026"
    assert group_days(["2026-10-14", "2026-10-11", "2026-10-12", "2026-10-11"]) == (
        "11 Oct 2026 – 12 Oct 2026, 14 Oct 2026"
    )
    assert each_day("2026-12-30", "2027-01-01") == ["2026-12-30", "2026-12-31", "2027-01-01"]
    assert len(each_day("2026-01-01", "2027-01-01")) == 120  # a misread year is capped
    assert each_day("2026-10-05", "2026-10-04") == []


def test_covered_and_uncovered_days():
    s = {
        **claim(),
        "includeLeave": True,
        "leaveRows": [{"id": "l", "startDate": "2026-10-07", "endDate": "", "daDeductable": "Yes", "amount": "0"}],
    }
    assert covered_days(s) == ["2026-10-05", "2026-10-07"]
    assert covered_text(s) == "05 Oct 2026, 07 Oct 2026"
    tour = {"tourStartDate": "2026-10-05", "tantetiveEndDate": "2026-10-08"}
    assert uncovered_days(s, tour) == ["2026-10-06", "2026-10-08"]
    assert uncovered_days(initial_claim_state(), tour) == []


def test_find_time_overlaps_word_for_word():
    issues = find_time_overlaps({**initial_claim_state(), "transportationRows": ambala_trip()})
    assert issues == [
        "The times of journey 3 and journey 4 overlap: journey 4 (NEW DELHI (NDLS) → AMBALA CANT JN (UMB)) departs at "
        "17:15 on 24 Aug 2026, before journey 3 (Noida Corporate Office → New Delhi Railway Station) arrives at 19:00 "
        "on 24 Aug 2026. Please correct journey 4's departure or journey 3's arrival in the claim (the fields are "
        "marked in red), or tell me the right times."
    ]


def test_find_claim_issues_word_for_word():
    s = {
        **initial_claim_state(),
        "transportationRows": [
            journey(DateOfTravelSource="2026-10-04", DateOfTravelDestination="2026-10-12"),
            journey(source="", destination="", DateOfTravelSource="2026-12-01", DateOfTravelDestination="2026-12-01"),
        ],
        "includeLeave": True,
        "leaveRows": [{"id": "l", "startDate": "2026-10-20", "endDate": "", "daDeductable": "Yes", "amount": "0"}],
    }
    assert find_claim_issues(s, rules) == [
        "Journey 1 (Noida → Pune): the departure date 04 Oct 2026 is outside your approved tour (05 Oct 2026 – 11 Oct "
        "2026).",
        "Journey 1 (Noida → Pune): the arrival date 12 Oct 2026 is outside your approved tour (05 Oct 2026 – 11 Oct "
        "2026).",
        "Journey 2: 01 Dec 2026 is outside your approved tour (05 Oct 2026 – 11 Oct 2026).",
        "Leave 1: 20 Oct 2026 is outside your approved tour (05 Oct 2026 – 11 Oct 2026).",
    ]


def test_find_relation_issues_word_for_word():
    s = {
        **initial_claim_state(),
        "transportationRows": [journey(), journey(source="Mumbai", destination="Noida", DateOfTravelSource="2026-10-08",
                                                  DateOfTravelDestination="2026-10-08")],
        "includeAccommodation": True,
        "accommodationRows": [
            {**new_accommodation_row(), "location": "Goa", "checkInDate": "2026-10-05"},
            {**new_accommodation_row(), "location": "Pune", "checkInDate": "2026-10-01", "checkOutDate": "2026-10-03"},
        ],
    }  # fmt: skip
    assert find_relation_issues(s) == [
        "Journey 2 starts from Mumbai, but journey 1 ended in Pune. Is a ticket missing in between?",
        "Stay 1 is in Goa, but none of your journeys go there. If this isn't your bill for this trip, say \"remove "
        'stay 1".',
        "Stay 2 (Pune) falls outside your travel dates (05 Oct 2026 – 08 Oct 2026). If this isn't your bill for this "
        'trip, say "remove stay 2".',
    ]


def test_progress_text_word_for_word():
    tour = {"tourId": 1, "source": "Noida", "destination": "Pune", "tourStartDate": "2026-10-05", "totalDays": 3}
    s = {**initial_claim_state(), "transportationRows": [journey(TimeOfTravelSource="", ModeOfTravel="select")]}
    p = build_progress(
        {
            "state": s,
            "tour": tour,
            "rules": rules,
            "staysAnswered": True,
            "leaveAnswered": False,
            "doneUploading": False,
        }
    )
    assert progress_text(p) == "\n\n".join(
        [
            "Here's where we are.",
            "✅ Done\n• Tour confirmed: Noida → Pune\n• 1 journey added\n• Stays checked",
            "⬜ Still to do\n• Journey 1 (Noida → Pune): departure time, mode of travel\n"
            "• Nothing yet for 06 Oct 2026 – 07 Oct 2026: do you have more tickets or bills?\n"
            "• Tell me whether you were on leave on any day",
            "Tell me what you have, and we'll finish it together.",
        ]
    )
    ready = build_progress(
        {"state": claim(), "tour": tour, "staysAnswered": True, "leaveAnswered": True, "doneUploading": True}
    )
    assert progress_text(ready).endswith('Everything looks good. Say "submit" or "fill the form" when you\'re ready.')


def test_stay_bill_problem_word_for_word():
    travel = [
        {"DateOfTravelSource": "2026-10-05", "DateOfTravelDestination": "2026-10-05"},
        {"DateOfTravelSource": "2026-10-08", "DateOfTravelDestination": "2026-10-08"},
    ]
    assert (
        stay_bill_problem({"checkInDate": "2026-12-01", "checkOutDate": "2026-12-01"}, rules, travel)
        == "its dates (01 Dec 2026) are outside your tour dates (05 Oct 2026 – 11 Oct 2026)"
    )
    assert (
        stay_bill_problem({"checkInDate": "2026-10-01", "checkOutDate": "2026-10-06"}, None, travel)
        == "its dates (01 Oct 2026 to 06 Oct 2026) don't fit your travel dates (05 Oct 2026 – 08 Oct 2026)"
    )
    same_day = {
        "checkInDate": "2026-10-06",
        "checkOutDate": "2026-10-06",
        "checkInTime": "12:00",
        "checkOutTime": "09:00",
    }
    assert stay_bill_problem(same_day, rules, travel) == "the check-out date is before the check-in date"
