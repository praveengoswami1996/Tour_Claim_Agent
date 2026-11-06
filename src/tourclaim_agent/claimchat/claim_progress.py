"""What the Claim Assistant can tell the employee at any point.

Port of Tour_React src/lib/claimChatbot/claimProgress.ts: which days of the tour the bills cover, what is done, what
is left, and what the form would flag. All plain facts from the claim so far.

`covered_days` returns the days as a list in the order a JavaScript Set would hold them (each day once).
"""

from __future__ import annotations

import re
from typing import Any, NotRequired, TypedDict

from .claim_assistant import (
    ClaimFormState,
    TourRules,
    describe_missing,
    find_form_only_issues,
    find_missing_fields,
    is_empty_accommodation,
    is_empty_leave,
    is_empty_transport,
)
from .js_compat import JS_WS, en_in_date, jget, js_or, js_str, js_truthy
from .ocr_mapping import to_iso_date
from .row_order import journey_overlaps
from .tour_matcher import _iso_of, _js_date, _ymd, norm, same_place, tour_window

_Date = tuple[int, int, int] | None


def _to_date(iso: str) -> _Date:
    y, m, d = _ymd(iso)
    return _js_date(y, m - 1, d)


def each_day(from_: str, to: str) -> list[str]:
    """claimProgress.ts:eachDay: every day from `from_` to `to` (YYYY-MM-DD, inclusive); at most 120 days."""
    days: list[str] = []
    d = _to_date(from_)
    n = 0
    while _iso_of(d) <= to and n < 120:
        days.append(_iso_of(d))
        d = None if d is None else _js_date(d[0], d[1] - 1, d[2] + 1)
        n += 1
    return days


def day_text(iso: str) -> str:
    """claimProgress.ts:dayText: '05 Oct 2026' (September is 'Sept')."""
    d = _to_date(iso)
    return "Invalid Date" if d is None else en_in_date(*d)


def range_text(a: str, b: str) -> str:
    """claimProgress.ts:rangeText: '05 Oct 2026' or '05 Oct 2026 – 07 Oct 2026'."""
    return day_text(a) if a == b else f"{day_text(a)} – {day_text(b)}"


def group_days(days: list[str]) -> str:
    """claimProgress.ts:groupDays: "11 Oct 2026 – 12 Oct 2026, 14 Oct 2026": runs of consecutive days as ranges."""
    groups: list[list[str]] = []
    for d in sorted(dict.fromkeys(days)):
        last = groups[-1] if groups else None
        if last is not None and len(each_day(last[1], d)) == 2:
            last[1] = d
        else:
            groups.append([d, d])
    return ", ".join(range_text(a, b) for a, b in groups)


def covered_days(state: ClaimFormState) -> list[str]:
    """claimProgress.ts:coveredDays: the days the employee has a ticket, a stay or a leave for (a Set, in order)."""
    days: dict[str, None] = {}

    def add_span(a: Any, b: Any) -> None:
        from_ = to_iso_date(a)
        to = to_iso_date(b)
        if from_ and to and to >= from_:
            for d in each_day(from_, to):
                days[d] = None
        else:
            for d in (from_, to):
                if d:
                    days[d] = None

    for r in state["transportationRows"]:
        if not is_empty_transport(r):
            add_span(jget(r, "DateOfTravelSource"), jget(r, "DateOfTravelDestination"))
    if js_truthy(state["includeAccommodation"]):
        for r in state["accommodationRows"]:
            if not is_empty_accommodation(r):
                add_span(jget(r, "checkInDate"), jget(r, "checkOutDate"))
    if js_truthy(state["includeLeave"]):
        for r in state["leaveRows"]:
            if not is_empty_leave(r):
                add_span(jget(r, "startDate"), js_or(jget(r, "endDate"), jget(r, "startDate")))
    return list(days)


def uncovered_days(state: ClaimFormState, tour: Any) -> list[str]:
    """claimProgress.ts:uncoveredDays: days of the tour that nothing in the claim accounts for yet."""
    window = tour_window(tour) if js_truthy(tour) else None
    if not window:
        return []
    covered = set(covered_days(state))
    if len(covered) == 0:
        return []
    return [d for d in each_day(window["start"], window["end"]) if d not in covered]


def covered_text(state: ClaimFormState) -> str:
    """claimProgress.ts:coveredText: what the claim covers, in words."""
    return group_days(covered_days(state))


# ─── Bills that do not seem to belong together ────────────────────────────────


def _add_day(iso: str, days: int) -> str:
    y, m, d = _ymd(iso)
    return _iso_of(_js_date(y, m - 1, d + days))


def find_relation_issues(state: ClaimFormState) -> list[str]:
    """claimProgress.ts:findRelationIssues.

    A hotel in a city no journey goes to, a stay outside the travel dates, or a gap between two journeys.
    """
    issues: list[str] = []
    journeys = [r for r in state["transportationRows"] if not is_empty_transport(r)]

    for i, r in enumerate(journeys):
        if i == 0:
            continue
        prev = journeys[i - 1]
        from_ = norm(jget(r, "source"))
        to = norm(jget(prev, "destination"))
        if from_ and to and not same_place(from_, to):
            issues.append(
                f"Journey {i + 1} starts from {js_str(jget(r, 'source'))}, but journey {i} ended in "
                f"{js_str(jget(prev, 'destination'))}. Is a ticket missing in between?"
            )

    if js_truthy(state["includeAccommodation"]) and len(journeys) > 0:
        places = [p for r in journeys for p in (norm(jget(r, "source")), norm(jget(r, "destination"))) if p]
        dates = sorted(
            d
            for r in journeys
            for d in (to_iso_date(jget(r, "DateOfTravelSource")), to_iso_date(jget(r, "DateOfTravelDestination")))
            if d
        )
        for i, r in enumerate(state["accommodationRows"]):
            if is_empty_accommodation(r):
                continue
            city = norm(jget(r, "location"))
            location = jget(r, "location")
            if city and not any(same_place(p, city) for p in places):
                issues.append(
                    f"Stay {i + 1} is in {js_str(location)}, but none of your journeys go there. If this isn't your "
                    f'bill for this trip, say "remove stay {i + 1}".'
                )
                continue
            first = dates[0] if dates else ""
            last = dates[-1] if dates else ""
            inn = to_iso_date(jget(r, "checkInDate"))
            out = to_iso_date(jget(r, "checkOutDate"))
            early = bool(first) and bool(inn) and inn < _add_day(first, -1)
            # a stay running past the only ticket so far is normal: the return ticket may just not be uploaded yet
            late = len(journeys) > 1 and bool(last) and bool(out) and out > _add_day(last, 1)
            if early or late:
                where = f" ({js_str(location)})" if js_truthy(location) else ""
                issues.append(
                    f"Stay {i + 1}{where} falls outside your travel dates ({range_text(first, last)}). If this isn't "
                    f'your bill for this trip, say "remove stay {i + 1}".'
                )
    return issues


# ─── Journeys whose times overlap ─────────────────────────────────────────────


def _index_of(rows: list[Any], row: Any) -> int:
    """rows.indexOf(row): the same object, not an equal one."""
    return next((i for i, r in enumerate(rows) if r is row), -1)


def find_time_overlaps(state: ClaimFormState) -> list[str]:
    """claimProgress.ts:findTimeOverlaps.

    One line per pair of journeys that overlap in time (a journey leaving before an earlier one has arrived), with
    what to do about it. Journeys are numbered as the chat shows them (the claim's rows, in date order).
    """
    rows = state["transportationRows"]

    def when(date: Any, time: Any) -> str:
        iso = to_iso_date(date)
        return " on ".join(js_str(p) for p in (time, day_text(iso) if iso else "") if js_truthy(p))

    def name(r: Any) -> str:
        source = js_str(js_or(jget(r, "source"), "?"))
        destination = js_str(js_or(jget(r, "destination"), "?"))
        return f"journey {_index_of(rows, r) + 1} ({source} → {destination})"

    lines = []
    for o in journey_overlaps([r for r in rows if not is_empty_transport(r)]):
        earlier, later = o["earlier"], o["later"]
        a = _index_of(rows, earlier) + 1
        b = _index_of(rows, later) + 1
        lines.append(
            f"The times of journey {a} and journey {b} overlap: {name(later)} departs at "
            f"{when(jget(later, 'DateOfTravelSource'), jget(later, 'TimeOfTravelSource'))}, "
            f"before {name(earlier)} arrives at "
            f"{when(jget(earlier, 'DateOfTravelDestination'), jget(earlier, 'TimeOfTravelDestiNation'))}. "
            f"Please correct journey {b}'s departure or journey {a}'s arrival in the claim (the fields are marked in "
            f"red), or tell me the right times."
        )
    return lines


# ─── Issues the form would flag (beyond the missing fields) ──────────────────


def find_claim_issues(state: ClaimFormState, rules: TourRules | None = None) -> list[str]:
    """claimProgress.ts:findClaimIssues: dates that fall outside the approved tour, or run backwards."""
    issues: list[str] = []
    min_date = jget(rules, "minDate")
    max_date = jget(rules, "maxDate")

    def outside(iso: str) -> bool:
        return (js_truthy(min_date) and iso < min_date) or (js_truthy(max_date) and iso > max_date)

    window = f" ({range_text(min_date, max_date)})" if js_truthy(min_date) and js_truthy(max_date) else ""

    def stamp(date: Any, time: Any) -> str:
        iso = to_iso_date(date)
        return f"{iso} {js_str(js_or(time, '00:00'))}" if iso else ""

    for i, r in enumerate(state["transportationRows"]):
        if is_empty_transport(r):
            continue
        source, destination = jget(r, "source"), jget(r, "destination")
        route = (
            f" ({js_str(js_or(source, '?'))} → {js_str(js_or(destination, '?'))})"
            if js_truthy(source) or js_truthy(destination)
            else ""
        )
        label = f"Journey {i + 1}{route}"
        dep = to_iso_date(jget(r, "DateOfTravelSource"))
        arr = to_iso_date(jget(r, "DateOfTravelDestination"))
        if dep and dep == arr:
            if outside(dep):
                issues.append(f"{label}: {day_text(dep)} is outside your approved tour{window}.")
        else:
            for k, d in enumerate((dep, arr)):
                if d and outside(d):
                    which = "departure" if k == 0 else "arrival"
                    issues.append(f"{label}: the {which} date {day_text(d)} is outside your approved tour{window}.")
        a = stamp(jget(r, "DateOfTravelSource"), jget(r, "TimeOfTravelSource"))
        b = stamp(jget(r, "DateOfTravelDestination"), jget(r, "TimeOfTravelDestiNation"))
        if a and b and b < a:
            issues.append(f"{label}: it arrives before it departs. Please check the dates and times.")
    issues.extend(find_time_overlaps(state))
    if js_truthy(state["includeAccommodation"]):
        for i, r in enumerate(state["accommodationRows"]):
            if is_empty_accommodation(r):
                continue
            a = stamp(jget(r, "checkInDate"), jget(r, "checkInTime"))
            b = stamp(jget(r, "checkOutDate"), jget(r, "checkOutTime"))
            if a and b and b < a:
                where = f" ({js_str(jget(r, 'location'))})" if js_truthy(jget(r, "location")) else ""
                issues.append(
                    f"Stay {i + 1}{where}: the check-out is before the check-in. Please check the dates and times."
                )
    if js_truthy(state["includeLeave"]):
        for i, r in enumerate(state["leaveRows"]):
            if is_empty_leave(r):
                continue
            for d in (jget(r, "startDate"), jget(r, "endDate")):
                iso = to_iso_date(d)
                if iso and outside(iso):
                    issues.append(f"Leave {i + 1}: {day_text(iso)} is outside your approved tour{window}.")
    return issues


# ─── Progress ─────────────────────────────────────────────────────────────────


class ProgressInput(TypedDict):
    state: ClaimFormState
    tour: Any
    rules: NotRequired[TourRules]
    # the employee has answered (or the claim already shows) the stay / leave questions
    staysAnswered: bool
    leaveAnswered: bool
    # the employee said there is nothing more to upload
    doneUploading: bool


class Progress(TypedDict):
    done: list[str]
    left: list[str]
    # what the form would flag when the claim is submitted
    heads: list[str]
    # bills that do not seem to belong together (not an error, but worth a look)
    notes: list[str]
    ready: bool


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# JavaScript's \s, as in the TypeScript regex
_BULLET = re.compile("^•" + JS_WS + "*")


def build_progress(p: ProgressInput) -> Progress:
    """claimProgress.ts:buildProgress: what is done, what is left, and what the form would flag."""
    state = p["state"]
    tour = jget(p, "tour")
    journeys = len([r for r in state["transportationRows"] if not is_empty_transport(r)])
    stays = (
        len([r for r in state["accommodationRows"] if not is_empty_accommodation(r)])
        if js_truthy(state["includeAccommodation"])
        else 0
    )
    leaves = len([r for r in state["leaveRows"] if not is_empty_leave(r)]) if js_truthy(state["includeLeave"]) else 0
    missing = find_missing_fields(state)
    done: list[str] = []
    left: list[str] = []

    if js_truthy(tour):
        source = js_str(js_or(jget(tour, "source"), "?"))
        destination = js_str(js_or(jget(tour, "destination"), "?"))
        done.append(f"Tour confirmed: {source} → {destination}")
    else:
        left.append("Confirm which tour these bills belong to")

    if journeys:
        done.append(f"{_plural(journeys, 'journey')} added")
    else:
        left.append("Add your travel details (upload a ticket, or tell me about the trip)")
    if stays:
        done.append(f"{_plural(stays, 'stay')} added")
    if leaves:
        done.append(f"{_plural(leaves, 'leave entry')} added")

    if journeys and len(missing) == 0:
        done.append("All journey and stay details are filled in")
    if missing:
        left.extend(_BULLET.sub("", line) for line in describe_missing(state, missing).split("\n"))

    if js_truthy(tour) and not js_truthy(jget(p, "doneUploading")):
        gaps = uncovered_days(state, tour)
        if gaps:
            left.append(f"Nothing yet for {group_days(gaps)}: do you have more tickets or bills?")
    if journeys and js_truthy(jget(p, "staysAnswered")):
        done.append("Stays checked")
    elif journeys:
        left.append("Tell me whether you stayed anywhere overnight")
    if journeys and js_truthy(jget(p, "leaveAnswered")):
        done.append("Leave checked")
    elif journeys:
        left.append("Tell me whether you were on leave on any day")

    heads = [*find_form_only_issues(state), *find_claim_issues(state, jget(p, "rules"))]
    return {"done": done, "left": left, "heads": heads, "notes": find_relation_issues(state), "ready": len(left) == 0}


def progress_text(p: Progress) -> str:
    """claimProgress.ts:progressText: the readable version: done, left, and what the form would flag."""
    parts = ["Here's where we are."]
    if p["done"]:
        parts.append("✅ Done\n" + "\n".join(f"• {d}" for d in p["done"]))
    if p["left"]:
        parts.append("⬜ Still to do\n" + "\n".join(f"• {item}" for item in p["left"]))
    if p["heads"]:
        parts.append("⚠️ The form will flag\n" + "\n".join(f"• {h}" for h in p["heads"]))
    if p["notes"]:
        parts.append("👀 Worth a check\n" + "\n".join(f"• {n}" for n in p["notes"]))
    if p["ready"]:
        parts.append(
            "You can fix the flagged points here or on the form, then you're good to go."
            if p["heads"]
            else 'Everything looks good. Say "submit" or "fill the form" when you\'re ready.'
        )
    else:
        parts.append("Tell me what you have, and we'll finish it together.")
    return "\n\n".join(parts)


def stay_bill_problem(row: Any, rules: TourRules | None, journeys: list[Any]) -> str | None:
    """claimProgress.ts:stayBillProblem.

    Is an uploaded accommodation bill usable for this claim? Returns what is wrong with it, or None when it is fine.
    Its check-in and check-out must be in order, inside the approved tour, and within the travel dates.
    """
    inn = to_iso_date(jget(row, "checkInDate"))
    out = to_iso_date(jget(row, "checkOutDate"))
    if not inn and not out:
        return None  # nothing readable to judge: the missing dates are asked for in the chat
    in_time, out_time = jget(row, "checkInTime"), jget(row, "checkOutTime")
    if (
        inn
        and out
        and (out < inn or (out == inn and js_truthy(in_time) and js_truthy(out_time) and out_time < in_time))
    ):
        return "the check-out date is before the check-in date"
    stay_dates = " to ".join(dict.fromkeys(range_text(d, d) for d in (inn, out) if d))
    min_date = jget(rules, "minDate")
    max_date = jget(rules, "maxDate")

    def outside_tour(d: str) -> bool:
        return bool(d) and ((js_truthy(min_date) and d < min_date) or (js_truthy(max_date) and d > max_date))

    if outside_tour(inn) or outside_tour(out):
        window = f" ({range_text(min_date, max_date)})" if js_truthy(min_date) and js_truthy(max_date) else ""
        return f"its dates ({stay_dates}) are outside your tour dates{window}"
    dates = sorted(
        d
        for r in journeys
        for d in (to_iso_date(jget(r, "DateOfTravelSource")), to_iso_date(jget(r, "DateOfTravelDestination")))
        if d
    )
    if len(dates) > 0:
        first = dates[0]
        last = dates[-1]
        # one day of room either side; a stay past the only ticket so far is fine (the return ticket may not be
        # uploaded yet)
        early = bool(inn) and inn < _add_day(first, -1)
        late = len(journeys) > 1 and bool(out) and out > _add_day(last, 1)
        if early or late:
            return f"its dates ({stay_dates}) don't fit your travel dates ({range_text(first, last)})"
    return None
