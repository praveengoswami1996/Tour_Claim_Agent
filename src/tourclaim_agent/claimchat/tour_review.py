"""Everything that stands between a claim and a complete tour, as questions for the employee, one at a time.

Port of Tour_React src/lib/claimChatbot/tourReview.ts. Inside the claim form the Claim Assistant goes through this
list before the claim is submitted: the form's own validation errors (which must be fixed), and every gap that makes
the tour incomplete (no return journey, a break in the route, days or nights nothing covers, bills not attached...),
which the employee either fixes or confirms. The AI model asks the questions and reads the answers; this module only
works out what to ask.

Review items are plain dicts with the TypeScript field names; an optional field that is undefined in the TypeScript
is left out. The `confirmed` / `deferred` sets of the review loop are Python sets of item ids.
"""

from __future__ import annotations

import functools
import math
import re
from typing import Any, Final, Literal, NotRequired, TypedDict

from .claim_assistant import (
    ClaimFormState,
    TourRules,
    find_amount_issues,
    find_missing_fields,
    is_empty_accommodation,
    is_empty_leave,
    is_empty_transport,
)
from .claim_progress import _add_day, day_text, each_day, group_days, range_text
from .js_compat import (
    LOCAL_OFFSET_MS,
    MAX_TIME_MS,
    MS_PER_DAY,
    UNDEFINED,
    jget,
    js_is_finite,
    js_len,
    js_or,
    js_str,
    js_trim,
    js_truthy,
    make_day,
    nullish,
    opt_trim,
)
from .ocr_mapping import to_iso_date
from .place_check import place_problem, same_place_both_ends
from .row_order import journey_overlaps
from .tour_matcher import _ymd, norm, same_place, tour_window

ReviewKind = Literal[
    "oddPlace",
    "noJourneys",
    "noOutbound",
    "noReturn",
    "routeBreak",
    "overlap",
    "missingField",
    "badDates",
    "uncoveredDays",
    "nightsWithoutStay",
    "stayNotOnRoute",
    "stayOutsideTravel",
    "ticketMissing",
    "hotelBillMissing",
    "amountMissing",
    "formOnly",
    "conveyanceBefore",
    "conveyanceAfter",
    "conveyanceTransfer",
    "localConveyance",
    "askLeave",
]

# How the employee can settle an item:
# - chat: give the details in the chat (an edit)
# - upload: attach the ticket or bill (or type the details)
# - form: only on the claim form (amounts, city class, limits)
# - confirm: say the claim is right as it is
Resolution = Literal["chat", "upload", "form", "confirm"]


class FormProblem(TypedDict):
    """A validation error from the claim form itself (ApproverPage's submit checks)."""

    kind: Literal["field", "cityClass", "claimedOverBilled", "overEntitlement", "noApprover", "other"]
    section: Literal["transport", "accommodation", "general"]
    # 1-based row of the section (0 = not about one row)
    row: int
    message: str


class ReviewItem(TypedDict):
    # stable while the claim does not change in that place, so an answered item is not asked again
    id: str
    kind: ReviewKind
    # the form will not accept the claim until this is fixed
    blocking: bool
    # ways to settle it, the first being the usual one
    resolutions: list[Resolution]
    # for a yes/no question: what "yes" and "no" mean
    yes: NotRequired[Literal["upload", "confirm", "details"]]
    no: NotRequired[Literal["confirm", "remove"]]
    section: NotRequired[Literal["transport", "accommodation", "leave"]]
    row: NotRequired[int]
    # for a cab or taxi question: which part of the tour it belongs to
    side: NotRequired[Literal["onward", "return", "middle"]]
    # an answer with journey details is a NEW journey (a cab, a return or onward ticket), never a change to one
    newJourney: NotRequired[bool]
    # what is wrong, as plain facts
    fact: str
    # what to ask
    question: str


class ReviewInput(TypedDict):
    state: ClaimFormState
    tour: Any
    rules: NotRequired[TourRules]
    # the form's own validation errors, when the assistant is inside the form
    formProblems: NotRequired[list[FormProblem]]
    # the employee has answered the leave question
    leaveAnswered: NotRequired[bool]


# ─── helpers (the TypeScript's local arrows) ──────────────────────────────────


def _stamp(date: Any, time: Any) -> str:
    iso = to_iso_date(date)
    return f"{iso} {js_str(js_or(time, '00:00'))}" if iso else ""


def _place(v: Any) -> str:
    """(v || '?').split(',')[0].trim()"""
    text = js_or(v, "?")
    if not isinstance(text, str):
        raise TypeError("(v || '?').split is not a function")
    return js_trim(text.split(",")[0])


def _same(a: Any, b: Any) -> bool:
    return js_truthy(a) and js_truthy(b) and same_place(norm(a), norm(b))


def _journey_name(i: int, r: Any) -> str:
    return f"journey {i + 1} ({_place(jget(r, 'source'))} → {_place(jget(r, 'destination'))})"


def _stay_name(i: int, r: Any) -> str:
    location = jget(r, "location")
    return f"stay {i + 1}{f' ({_place(location)})' if js_truthy(location) else ''}"


# modes for which a ticket or bill is normally attached
_TICKETED: Final = frozenset(("air", "rail", "public-transport", "official-beyond"))
# the long-distance legs of a tour; everything else (cab, taxi, auto, bus, metro, own or office vehicle) is local
_MAIN_MODES: Final = frozenset(("air", "rail"))


def _has(modes: frozenset[str], mode: Any) -> bool:
    """`Set.has` (a value that is not a string is never in a set of strings)."""
    return isinstance(mode, str) and mode in modes


def _is_local(mode: Any) -> bool:
    return js_truthy(mode) and mode != "select" and not _has(_MAIN_MODES, mode)


_HH_MM: Final = re.compile(r"^[0-9]{1,2}:[0-9]{2}\Z")


def _to_minutes(date: Any, time: Any) -> float | None:
    """toMinutes: `new Date(y, m - 1, d, hh, mm).getTime() / 60000` (local time), or None without a date."""
    iso = to_iso_date(date)
    if not iso:
        return None
    y, m, d = _ymd(iso)
    if js_truthy(time) and _HH_MM.match(js_str(time)):
        if not isinstance(time, str):
            raise TypeError("time.split is not a function")
        hh, mm = (int(p) for p in time.split(":"))
    else:
        hh, mm = 12, 0
    if not all(js_is_finite(v) for v in (y, m, d)):
        return math.nan
    year = math.trunc(y)
    if 0 <= year <= 99:
        year += 1900
    ms = make_day(year, math.trunc(m) - 1, math.trunc(d)) * MS_PER_DAY + (hh * 60 + mm) * 60_000 - LOCAL_OFFSET_MS
    if abs(ms) > MAX_TIME_MS:
        return math.nan
    return ms / 60_000


# a cab to or from a main journey is booked around it: up to this long before departure or after arrival
_CONVEYANCE_WINDOW_MIN: Final = 8 * 60
# two main journeys closer than this, at the same station or airport, are a connection with nothing in between
_CONNECTION_MIN: Final = 4 * 60


def _station_of(mode: Any, where: Any) -> str:
    return f"{'the airport' if mode == 'air' else 'the railway station'} in {_place(where)}"


def _length(v: Any) -> Any:
    """`(v ?? []).length` (a list or a text has a length; anything else is undefined)."""
    v = nullish(v, [])
    if isinstance(v, list):
        return len(v)
    if isinstance(v, str):
        return js_len(v)
    return UNDEFINED


def _js_minus(a: float | None, b: float | None) -> float:
    """`(a ?? Infinity) - (b ?? Infinity)` (Infinity - Infinity is NaN)."""
    x = math.inf if a is None else a
    y = math.inf if b is None else b
    if math.isinf(x) and math.isinf(y) and (x > 0) == (y > 0):
        return math.nan
    return x - y


def _or0(v: float | None) -> float:
    """`v ?? 0`"""
    return 0 if v is None else v


def _index_of(rows: list[Any], row: Any) -> int:
    """rows.indexOf(row): the same object, not an equal one."""
    return next((i for i, r in enumerate(rows) if r is row), -1)


# ─── the review ───────────────────────────────────────────────────────────────


def review_claim(inp: ReviewInput | dict[str, Any]) -> list[ReviewItem]:
    """tourReview.ts:reviewClaim: every question that stands between this claim and a complete tour, most important
    first."""
    state = inp["state"]
    tour = jget(inp, "tour")
    rules = jget(inp, "rules")
    form_problems = jget(inp, "formProblems")
    form_problems = [] if form_problems is UNDEFINED else form_problems
    leave_answered = jget(inp, "leaveAnswered")
    leave_answered = False if leave_answered is UNDEFINED else leave_answered

    items: list[dict[str, Any]] = []
    # in the order they happened (a cab typed in later is added at the end of the form, but belongs where its time
    # puts it)
    indexed = [(r, i) for i, r in enumerate(state["transportationRows"]) if not is_empty_transport(r)]
    minutes = {i: _to_minutes(jget(r, "DateOfTravelSource"), jget(r, "TimeOfTravelSource")) for r, i in indexed}

    def by_time(a: tuple[Any, int], b: tuple[Any, int]) -> float:
        diff = _js_minus(minutes[a[1]], minutes[b[1]])
        return a[1] - b[1] if math.isnan(diff) or diff == 0 else diff

    journeys = sorted(indexed, key=functools.cmp_to_key(by_time))
    stays = (
        [(r, i) for i, r in enumerate(state["accommodationRows"]) if not is_empty_accommodation(r)]
        if js_truthy(state["includeAccommodation"])
        else []
    )
    leaves = [r for r in state["leaveRows"] if not is_empty_leave(r)] if js_truthy(state["includeLeave"]) else []
    home = opt_trim(jget(tour, "source"))
    window = tour_window(tour) if js_truthy(tour) else None

    # ── 0. places that are not places ("Total Net Amount", an amount, a PNR...): they would throw every check off ──
    for r, i in journeys:

        def ask(field: str, value: Any, problem: str, i: int = i) -> None:
            is_from = field == "from"
            where = f"Where did journey {i + 1} start from?" if is_from else f"Where did journey {i + 1} end?"
            items.append(
                {
                    "id": f"oddPlace:transport:{i}:{field}:{norm(value)}",
                    "kind": "oddPlace",
                    "blocking": False,
                    "resolutions": ["chat", "confirm"],
                    "yes": "confirm",
                    "section": "transport",
                    "row": i + 1,
                    "fact": f"Journey {i + 1} has “{js_str(value)}” as its "
                    f"{'starting point (From)' if is_from else 'destination (To)'}, but {problem}. It was probably "
                    f"misread from the bill.",
                    "question": f"Journey {i + 1} has “{js_str(value)}” as its "
                    f"{'starting point' if is_from else 'destination'}, which doesn't look like a place ({problem}). "
                    f"{where}",
                }
            )

        source, destination = jget(r, "source"), jget(r, "destination")
        from_problem = place_problem(source)
        to_problem = place_problem(destination)
        if from_problem:
            ask("from", source, from_problem)
        if to_problem:
            ask("to", destination, to_problem)
        if not from_problem and not to_problem and same_place_both_ends(source, destination):
            items.append(
                {
                    "id": f"oddPlace:transport:{i}:same:{norm(source)}",
                    "kind": "oddPlace",
                    "blocking": False,
                    "resolutions": ["chat", "confirm"],
                    "yes": "confirm",
                    "section": "transport",
                    "row": i + 1,
                    "fact": f"Journey {i + 1} goes from “{js_str(source)}” to “{js_str(destination)}”, the same "
                    f"place at both ends.",
                    "question": f"Journey {i + 1} goes from {_place(source)} to {_place(destination)}, the same "
                    f"place. Where did it actually start, and where did it end?",
                }
            )
    for r, i in stays:
        location = jget(r, "location")
        problem = place_problem(location)
        if problem:
            items.append(
                {
                    "id": f"oddPlace:accommodation:{i}:{norm(location)}",
                    "kind": "oddPlace",
                    "blocking": False,
                    "resolutions": ["chat", "confirm"],
                    "yes": "confirm",
                    "section": "accommodation",
                    "row": i + 1,
                    "fact": f"Stay {i + 1} has “{js_str(location)}” as its city, but {problem}. It was probably "
                    f"misread from the bill.",
                    "question": f"Stay {i + 1} has “{js_str(location)}” as its city, which doesn't look like a place "
                    f"({problem}). Which city was the stay in?",
                }
            )

    # ── 1. the route: there and back, with nothing missing in between ──
    if len(journeys) == 0:
        items.append(
            {
                "id": "noJourneys",
                "kind": "noJourneys",
                "blocking": True,
                "resolutions": ["upload", "chat"],
                "yes": "upload",
                "fact": "The claim has no journeys yet.",
                "question": "There are no journey tickets or bills in the claim yet. Do you have them to upload? You "
                "can also tell me about the journey.",
            }
        )
    else:
        first = journeys[0][0]
        last = journeys[-1][0]
        # a round trip from a nearby station (Noida tour, flights from Delhi and back) is complete as it is
        closed_loop = len(journeys) > 1 and _same(jget(first, "source"), jget(last, "destination"))
        first_source = jget(first, "source")
        if js_truthy(home) and js_truthy(first_source) and not _same(first_source, home) and not closed_loop:
            items.append(
                {
                    "id": f"noOutbound:{norm(first_source)}",
                    "kind": "noOutbound",
                    "newJourney": True,
                    "blocking": False,
                    "resolutions": ["upload", "chat", "confirm"],
                    "yes": "upload",
                    "no": "confirm",
                    "section": "transport",
                    "row": journeys[0][1] + 1,
                    "fact": f"Your tour starts from {_place(home)}, but the first journey in the claim starts from "
                    f"{_place(first_source)}.",
                    "question": f"There is no onward journey ticket from {_place(home)} to {_place(first_source)}. "
                    f"Do you have it?",
                }
            )
        last_destination = jget(last, "destination")
        if js_truthy(last_destination) and not closed_loop and not (js_truthy(home) and _same(last_destination, home)):
            started = f", and the tour started from {_place(home)}" if js_truthy(home) else ""
            back = f" ({_place(last_destination)} → {_place(home)})" if js_truthy(home) else ""
            items.append(
                {
                    "id": f"noReturn:{norm(last_destination)}",
                    "kind": "noReturn",
                    "newJourney": True,
                    "blocking": False,
                    "resolutions": ["upload", "chat", "confirm"],
                    "yes": "upload",
                    "no": "confirm",
                    "section": "transport",
                    "fact": f"The last journey in the claim ends in {_place(last_destination)}{started}: there is "
                    f"no journey back.",
                    "question": f"There are no return journey tickets/bills uploaded{back}. Do you have them?",
                }
            )
        # the route is judged on the long-distance legs (cabs inside a city go hotel → office, then hotel → airport);
        # a tour made only of local legs (taxi or own car the whole way) is judged on all of them
        long_legs = [x for x in journeys if _has(_MAIN_MODES, jget(x[0], "ModeOfTravel"))]
        chain = long_legs if len(long_legs) > 0 else journeys
        for k, (r, i) in enumerate(chain):
            if k == 0:
                continue
            prev_r, prev_i = chain[k - 1]
            source, prev_destination = jget(r, "source"), jget(prev_r, "destination")
            if js_truthy(source) and js_truthy(prev_destination) and not _same(source, prev_destination):
                items.append(
                    {
                        "id": f"routeBreak:{k}:{norm(prev_destination)}:{norm(source)}",
                        "kind": "routeBreak",
                        "newJourney": True,
                        "blocking": False,
                        "resolutions": ["upload", "chat", "confirm"],
                        "yes": "upload",
                        "no": "confirm",
                        "section": "transport",
                        "row": i + 1,
                        "fact": f"{_journey_name(prev_i, prev_r)} ends in {_place(prev_destination)}, but "
                        f"{_journey_name(i, r)} starts from {_place(source)}.",
                        "question": f"Is a ticket missing between {_place(prev_destination)} and {_place(source)}? "
                        f"Do you have it?",
                    }
                )
        # journeys whose times overlap (any two, not only neighbours): the form marks their times in red
        rows_only = [x[0] for x in journeys]
        for o in journey_overlaps(rows_only):
            prev_r, prev_i = journeys[_index_of(rows_only, o["earlier"])]
            r, i = journeys[_index_of(rows_only, o["later"])]
            prev_arrives = _stamp(jget(prev_r, "DateOfTravelDestination"), jget(prev_r, "TimeOfTravelDestiNation"))
            departs = _stamp(jget(r, "DateOfTravelSource"), jget(r, "TimeOfTravelSource"))
            items.append(
                {
                    "id": f"overlap:{i}:{prev_i}:{departs}:{prev_arrives}",
                    "kind": "overlap",
                    "blocking": True,
                    "resolutions": ["chat", "form"],
                    "section": "transport",
                    "row": i + 1,
                    "fact": f"The times of journey {prev_i + 1} and journey {i + 1} overlap: {_journey_name(i, r)} "
                    f"departs ({departs}) before {_journey_name(prev_i, prev_r)} arrives ({prev_arrives}). Both times "
                    f"are marked in red on the claim form.",
                    "question": f"{_journey_name(i, r)} departs at {departs}, before {_journey_name(prev_i, prev_r)} "
                    f"arrives at {prev_arrives}, so their times overlap. Which time is wrong? Tell me the right "
                    f"time, or correct it on the claim form (marked in red).",
                }
            )

    # ── 2. the details the form needs ──
    for m in find_missing_fields(state):
        items.append(
            {
                "id": f"missing:{m['section']}:{m['row']}:{m['slot']}",
                "kind": "missingField",
                "blocking": True,
                "resolutions": ["chat", "form"],
                "section": m["section"],
                "row": m["row"],
                "fact": f"{'Journey' if m['section'] == 'transport' else 'Stay'} {m['row']}: the {m['label']} is "
                f"missing.",
                "question": m["question"],
            }
        )

    # ── 3. dates that can't be right ──
    min_date, max_date = jget(rules, "minDate"), jget(rules, "maxDate")

    def outside(iso: str) -> bool:
        return (js_truthy(min_date) and iso < min_date) or (js_truthy(max_date) and iso > max_date)

    window_text = f" ({range_text(min_date, max_date)})" if js_truthy(min_date) and js_truthy(max_date) else ""
    for r, i in journeys:
        dep = to_iso_date(jget(r, "DateOfTravelSource"))
        arr = to_iso_date(jget(r, "DateOfTravelDestination"))
        out = [d for d in (dep, arr) if d and outside(d)]
        if out:
            items.append(
                {
                    "id": f"badDates:transport:{i}:outside:{','.join(out)}",
                    "kind": "badDates",
                    "blocking": True,
                    "resolutions": ["chat"],
                    "section": "transport",
                    "row": i + 1,
                    "fact": f"{_journey_name(i, r)}: {day_text(out[0])} is outside your approved tour{window_text}.",
                    "question": f"{_journey_name(i, r)} is dated {day_text(out[0])}, outside your approved "
                    f"tour{window_text}. What is the right date?",
                }
            )
        a = _stamp(jget(r, "DateOfTravelSource"), jget(r, "TimeOfTravelSource"))
        b = _stamp(jget(r, "DateOfTravelDestination"), jget(r, "TimeOfTravelDestiNation"))
        if (
            a
            and b
            and b < a
            and js_truthy(jget(r, "TimeOfTravelSource"))
            and js_truthy(jget(r, "TimeOfTravelDestiNation"))
        ):
            items.append(
                {
                    "id": f"badDates:transport:{i}:backwards:{a}:{b}",
                    "kind": "badDates",
                    "blocking": True,
                    "resolutions": ["chat"],
                    "section": "transport",
                    "row": i + 1,
                    "fact": f"{_journey_name(i, r)} arrives ({b}) before it departs ({a}).",
                    "question": f"{_journey_name(i, r)} arrives before it departs. What are the right departure and "
                    f"arrival times?",
                }
            )
    for r, i in stays:
        a = _stamp(jget(r, "checkInDate"), jget(r, "checkInTime"))
        b = _stamp(jget(r, "checkOutDate"), jget(r, "checkOutTime"))
        if a and b and b < a:
            items.append(
                {
                    "id": f"badDates:accommodation:{i}:{a}:{b}",
                    "kind": "badDates",
                    "blocking": True,
                    "resolutions": ["chat"],
                    "section": "accommodation",
                    "row": i + 1,
                    "fact": f"{_stay_name(i, r)}: the check-out ({b}) is before the check-in ({a}).",
                    "question": f"For {_stay_name(i, r)}, the check-out is before the check-in. What are the right "
                    f"check-in and check-out dates and times?",
                }
            )
        out = [
            d for d in (to_iso_date(jget(r, "checkInDate")), to_iso_date(jget(r, "checkOutDate"))) if d and outside(d)
        ]
        if out:
            items.append(
                {
                    "id": f"badDates:accommodation:{i}:outside:{','.join(out)}",
                    "kind": "badDates",
                    "blocking": True,
                    "resolutions": ["chat"],
                    "section": "accommodation",
                    "row": i + 1,
                    "fact": f"{_stay_name(i, r)}: {day_text(out[0])} is outside your approved tour{window_text}.",
                    "question": f"{_stay_name(i, r)} is dated {day_text(out[0])}, outside your approved "
                    f"tour{window_text}. What are the right dates?",
                }
            )

    # ── 4. days and nights nothing accounts for ──
    first_day: Any = UNDEFINED
    last_day: Any = UNDEFINED
    if len(journeys) > 0:
        departures = sorted(d for d in (to_iso_date(jget(r, "DateOfTravelSource")) for r, _ in journeys) if d)
        arrivals = sorted(
            d
            for d in (
                js_or(to_iso_date(jget(r, "DateOfTravelDestination")), to_iso_date(jget(r, "DateOfTravelSource")))
                for r, _ in journeys
            )
            if d
        )
        first_day = departures[0] if departures else UNDEFINED
        last_day = arrivals[-1] if arrivals else UNDEFINED
        has_return_gap = any(x["kind"] == "noReturn" for x in items)
        has_outbound_gap = any(x["kind"] == "noOutbound" for x in items)

        # tour days with no journey, stay or leave (days the "no return / no onward ticket" questions already cover
        # are left out)
        if window:
            covered: set[str] = set()

            def span(a: Any, b: Any) -> None:
                f = to_iso_date(a)
                t = js_or(to_iso_date(b), f)
                if f and t and t >= f:
                    covered.update(each_day(f, t))

            for r, _ in journeys:
                span(jget(r, "DateOfTravelSource"), jget(r, "DateOfTravelDestination"))
            for r, _ in stays:
                span(jget(r, "checkInDate"), jget(r, "checkOutDate"))
            for r in leaves:
                span(jget(r, "startDate"), js_or(jget(r, "endDate"), jget(r, "startDate")))
            gaps = [
                d
                for d in each_day(window["start"], window["end"])
                if d not in covered
                and not (has_return_gap and js_truthy(last_day) and d > last_day)
                and not (has_outbound_gap and js_truthy(first_day) and d < first_day)
            ]
            if gaps:
                items.append(
                    {
                        "id": f"uncovered:{','.join(gaps)}",
                        "kind": "uncoveredDays",
                        "blocking": False,
                        "resolutions": ["upload", "chat", "confirm"],
                        "yes": "upload",
                        "no": "confirm",
                        "fact": f"Your tour runs {range_text(window['start'], window['end'])}, but nothing in the "
                        f"claim covers {group_days(gaps)}.",
                        "question": f"Nothing in the claim covers {group_days(gaps)} of your tour. Do you have "
                        f"tickets or bills for those days?",
                    }
                )

        # nights away from home with no stay, overnight journey or leave
        if js_truthy(first_day) and js_truthy(last_day) and last_day > first_day:
            nights: list[tuple[str, str]] = []
            for night in each_day(first_day, _add_day(last_day, -1)):
                next_ = _add_day(night, 1)

                def stayed_that_night(r: Any, night: str = night, next_: str = next_) -> bool:
                    inn = to_iso_date(jget(r, "checkInDate"))
                    out = to_iso_date(jget(r, "checkOutDate"))
                    return bool(inn) and bool(out) and inn <= night and out >= next_

                def travelling_that_night(r: Any, night: str = night, next_: str = next_) -> bool:
                    dep = to_iso_date(jget(r, "DateOfTravelSource"))
                    arr = to_iso_date(jget(r, "DateOfTravelDestination"))
                    return bool(dep) and bool(arr) and dep <= night and arr >= next_

                def on_leave_that_night(r: Any, night: str = night) -> bool:
                    s = to_iso_date(jget(r, "startDate"))
                    e = js_or(to_iso_date(jget(r, "endDate")), s)
                    return bool(s) and bool(e) and s <= night and e >= night

                stayed = any(stayed_that_night(r) for r, _ in stays)
                travelling = any(travelling_that_night(r) for r, _ in journeys)
                on_leave = any(on_leave_that_night(r) for r in leaves)
                # back home that night: the last journey that arrived by then ends at the starting point
                arrived = [
                    r for r, _ in journeys if js_or(to_iso_date(jget(r, "DateOfTravelDestination")), "") <= night
                ]
                where = jget(arrived[-1], "destination") if arrived else jget(journeys[0][0], "destination")
                at_home = (js_truthy(home) and _same(where, home)) or _same(where, jget(journeys[0][0], "source"))
                if not stayed and not travelling and not on_leave and not at_home:
                    nights.append((night, _place(where)))
            # one question per city run ("the nights of 6–7 Oct in Mumbai")
            runs: list[dict[str, str]] = []
            for night, city in nights:
                run = runs[-1] if runs else None
                if run is not None and run["city"] == city and _add_day(run["to"], 1) == night:
                    run["to"] = night
                else:
                    runs.append({"from": night, "to": night, "city": city})
            for run in runs:
                when = (
                    f"the night of {day_text(run['from'])}"
                    if run["from"] == run["to"]
                    else f"the nights of {range_text(run['from'], run['to'])}"
                )
                items.append(
                    {
                        "id": f"nights:{run['from']}:{run['to']}:{norm(run['city'])}",
                        "kind": "nightsWithoutStay",
                        "blocking": False,
                        "resolutions": ["upload", "chat", "confirm"],
                        "yes": "upload",
                        "no": "confirm",
                        "section": "accommodation",
                        "fact": f"There is no stay in the claim for {when} in {run['city']}.",
                        "question": f"Where did you stay on {when} in {run['city']}? Upload the hotel bill, tell me "
                        f"(hotel, guest house or own arrangement), or say if you had no stay to claim.",
                    }
                )

        # stays that don't fit the journeys
        places = [p for r, _ in journeys for p in (jget(r, "source"), jget(r, "destination")) if js_truthy(p)]
        for r, i in stays:
            location = jget(r, "location")
            if js_truthy(location) and not any(_same(p, location) for p in places):
                items.append(
                    {
                        "id": f"stayNotOnRoute:{i}:{norm(location)}",
                        "kind": "stayNotOnRoute",
                        "blocking": False,
                        "resolutions": ["confirm", "chat"],
                        "yes": "confirm",
                        "no": "remove",
                        "section": "accommodation",
                        "row": i + 1,
                        "fact": f"{_stay_name(i, r)} is in {_place(location)}, but none of the journeys go there.",
                        "question": f"{_stay_name(i, r)} is in {_place(location)}, but none of your journeys go "
                        f"there. Is this stay part of this tour?",
                    }
                )
                continue
            inn = to_iso_date(jget(r, "checkInDate"))
            out = to_iso_date(jget(r, "checkOutDate"))
            early = js_truthy(first_day) and bool(inn) and inn < _add_day(first_day, -1)
            late = js_truthy(last_day) and bool(out) and out > _add_day(last_day, 1) and not has_return_gap
            if early or late:
                travel = range_text(first_day, last_day)
                items.append(
                    {
                        "id": f"stayOutsideTravel:{i}:{inn}:{out}",
                        "kind": "stayOutsideTravel",
                        "blocking": False,
                        "resolutions": ["confirm", "chat"],
                        "yes": "confirm",
                        "no": "remove",
                        "section": "accommodation",
                        "row": i + 1,
                        "fact": f"{_stay_name(i, r)} ({range_text(js_or(inn, out), js_or(out, inn))}) falls outside "
                        f"your travel dates ({travel}).",
                        "question": f"{_stay_name(i, r)} falls outside your travel dates ({travel}). Are its dates "
                        f"right?",
                    }
                )

    # ── 4b. getting to and from the stations and airports, and getting around ──
    mains = [x for x in journeys if _has(_MAIN_MODES, jget(x[0], "ModeOfTravel"))]
    locals_ = [x for x in journeys if _is_local(jget(x[0], "ModeOfTravel"))]
    if len(mains) > 0:
        used: set[int] = set()

        def dep_of(r: Any) -> float | None:
            return _to_minutes(jget(r, "DateOfTravelSource"), jget(r, "TimeOfTravelSource"))

        def arr_of(r: Any) -> float | None:
            return _to_minutes(
                js_or(jget(r, "DateOfTravelDestination"), jget(r, "DateOfTravelSource")),
                jget(r, "TimeOfTravelDestiNation"),
            )

        def before(m: Any) -> tuple[Any, int] | None:
            """a local leg reaching the main journey's station shortly before it leaves"""
            for lr, li in locals_:
                reach = arr_of(lr)
                leave = dep_of(m)
                if reach is None or leave is None:
                    continue
                gap = leave - reach
                if (gap >= -30 and gap <= _CONVEYANCE_WINDOW_MIN) or (
                    to_iso_date(jget(lr, "DateOfTravelDestination")) == to_iso_date(jget(m, "DateOfTravelSource"))
                    and _same(jget(lr, "destination"), jget(m, "source"))
                    and gap >= -30
                ):
                    return lr, li
            return None

        def after(m: Any) -> tuple[Any, int] | None:
            """a local leg leaving the main journey's station shortly after it arrives"""
            for lr, li in locals_:
                leave = dep_of(lr)
                reached = arr_of(m)
                if leave is None or reached is None:
                    continue
                gap = leave - reached
                if (gap >= -30 and gap <= _CONVEYANCE_WINDOW_MIN) or (
                    to_iso_date(jget(lr, "DateOfTravelSource"))
                    == to_iso_date(js_or(jget(m, "DateOfTravelDestination"), jget(m, "DateOfTravelSource")))
                    and _same(jget(lr, "source"), jget(m, "destination"))
                    and gap >= -30
                ):
                    return lr, li
            return None

        first_source = jget(journeys[0][0], "source")

        def near_home(where: Any) -> bool:
            return (js_truthy(home) and _same(where, home)) or _same(where, first_source)

        def stay_starts_there(where: Any, day: str) -> bool:
            return any(
                _same(jget(r, "location"), where) and to_iso_date(jget(r, "checkInDate")) == day for r, _ in stays
            )

        def stay_ends_there(where: Any, day: str) -> bool:
            return any(
                _same(jget(r, "location"), where) and to_iso_date(jget(r, "checkOutDate")) == day for r, _ in stays
            )

        def conveyance(kind: str, key: str, row: int, fact: str, question: str, side: str) -> None:
            items.append(
                {
                    "id": f"{kind}:{key}",
                    "kind": kind,
                    "blocking": False,
                    "resolutions": ["upload", "chat", "confirm"],
                    "yes": "upload",
                    "no": "confirm",
                    "section": "transport",
                    "row": row,
                    "fact": fact,
                    "question": question,
                    "newJourney": True,
                    "side": side,
                }
            )

        def side_of(k: int) -> str:
            return "onward" if len(mains) == 1 or k == 0 else "return" if k == len(mains) - 1 else "middle"

        for k, (m, mi) in enumerate(mains):
            prev = mains[k - 1][0] if k > 0 else None
            nxt = mains[k + 1] if k + 1 < len(mains) else None
            leave_day = to_iso_date(jget(m, "DateOfTravelSource"))
            arrive_day = js_or(to_iso_date(jget(m, "DateOfTravelDestination")), leave_day)
            is_air = jget(m, "ModeOfTravel") == "air"
            mode = "flight" if is_air else "train"
            name = f"{_journey_name(mi, m)} by {'air' if is_air else 'rail'}"
            leave_text = day_text(leave_day) if leave_day else "?"
            arrive_text = day_text(arrive_day) if arrive_day else "?"

            # getting to the station or airport (unless it is the second half of a transfer, asked with the first)
            prev_gap = _or0(dep_of(m)) - _or0(arr_of(prev)) if prev is not None else math.inf
            is_transfer_leg = prev is not None and prev_gap >= 0 and prev_gap <= 12 * 60
            if not is_transfer_leg:
                b = before(m)
                if b is not None:
                    used.add(b[1])
                else:
                    source = jget(m, "source")
                    from_ = (
                        "home"
                        if k == 0 and near_home(source)
                        else "your hotel"
                        if stay_ends_there(source, leave_day)
                        else "your home, hotel or office"
                    )
                    station = _station_of(jget(m, "ModeOfTravel"), source)
                    conveyance(
                        "conveyanceBefore",
                        f"{mi}:{norm(source)}:{leave_day}",
                        mi + 1,
                        f"There is no cab, taxi or other conveyance in the claim for getting to {station} for {name} "
                        f"on {leave_text}.",
                        f"How did you get from {from_} to {station} for your {mode} on {leave_text}? If you took a "
                        f"cab, taxi or auto, do you have the bill?",
                        side_of(k),
                    )

            # after arriving: a transfer to the next main journey, or the ride onward (hotel, office, home)
            next_gap = _or0(dep_of(nxt[0])) - _or0(arr_of(m)) if nxt is not None else math.inf
            if nxt is not None and next_gap >= 0 and next_gap <= 12 * 60:
                n, ni = nxt
                connection = (
                    _same(jget(m, "destination"), jget(n, "source"))
                    and next_gap <= _CONNECTION_MIN
                    and jget(m, "ModeOfTravel") == jget(n, "ModeOfTravel")
                )
                ride = None
                for lr, li in locals_:
                    t = dep_of(lr)
                    if t is not None and t >= _or0(arr_of(m)) - 30 and t <= _or0(dep_of(n)):
                        ride = li
                        break
                if ride is not None:
                    used.add(ride)
                elif not connection:
                    next_mode = "flight" if jget(n, "ModeOfTravel") == "air" else "train"
                    conveyance(
                        "conveyanceTransfer",
                        f"{mi}:{ni}:{norm(jget(m, 'destination'))}:{norm(jget(n, 'source'))}",
                        ni + 1,
                        f"There is no cab, taxi or other conveyance in the claim between {_journey_name(mi, m)} "
                        f"arriving in {_place(jget(m, 'destination'))} and {_journey_name(ni, n)} leaving from "
                        f"{_place(jget(n, 'source'))}.",
                        f"How did you get from {_station_of(jget(m, 'ModeOfTravel'), jget(m, 'destination'))} to "
                        f"{_station_of(jget(n, 'ModeOfTravel'), jget(n, 'source'))} for your next {next_mode}? If you "
                        f"took a cab, taxi or auto, do you have the bill?",
                        "middle",
                    )
                continue
            a = after(m)
            if a is not None:
                used.add(a[1])
            else:
                destination = jget(m, "destination")
                to = (
                    "home"
                    if nxt is None and near_home(destination)
                    else "your hotel"
                    if stay_starts_there(destination, arrive_day)
                    else "your hotel, office or site"
                )
                station = _station_of(jget(m, "ModeOfTravel"), destination)
                conveyance(
                    "conveyanceAfter",
                    f"{mi}:{norm(destination)}:{arrive_day}",
                    mi + 1,
                    f"There is no cab, taxi or other conveyance in the claim for getting from {station} to "
                    f"{'home' if to == 'home' else 'the hotel or office'} after {name} on {arrive_text}.",
                    f"After your {mode} reached {_place(destination)} on {arrive_text}, how did you get to {to}? If "
                    f"you took a cab, taxi or auto, do you have the bill?",
                    side_of(k),
                )

        # getting around the destination: a visit of at least one night between arriving and leaving again
        for k, (m, _mi) in enumerate(mains):
            if k + 1 >= len(mains):
                continue
            n = mains[k + 1][0]
            in_day = js_or(to_iso_date(jget(m, "DateOfTravelDestination")), to_iso_date(jget(m, "DateOfTravelSource")))
            out_day = to_iso_date(jget(n, "DateOfTravelSource"))
            if not in_day or not out_day or out_day <= in_day:
                continue
            city = jget(m, "destination")
            if near_home(city):
                continue

            def around_then(lr: Any, li: int, in_day: str = in_day, out_day: str = out_day) -> bool:
                if li in used:
                    return False
                d = to_iso_date(jget(lr, "DateOfTravelSource"))
                return bool(d) and d >= in_day and d <= out_day

            if not any(around_then(lr, li) for lr, li in locals_):
                items.append(
                    {
                        "id": f"localConveyance:{norm(city)}:{in_day}:{out_day}",
                        "kind": "localConveyance",
                        "blocking": False,
                        "resolutions": ["upload", "chat", "confirm"],
                        "yes": "upload",
                        "no": "confirm",
                        "section": "transport",
                        "newJourney": True,
                        "fact": f"The claim has no local travel (cab, taxi, auto) in {_place(city)} between "
                        f"{range_text(in_day, out_day)}.",
                        "question": f"While you were in {_place(city)} ({range_text(in_day, out_day)}), did you take "
                        f"any cabs, taxis or autos to get around, for example from the hotel to the office or site "
                        f"and back? Do you have those bills?",
                    }
                )

    # ── 5. bills and amounts ──
    for r, i in journeys:
        if (
            not js_truthy(jget(r, "billNotRequired"))
            and _length(jget(r, "uploadedFiles")) == 0
            and _has(_TICKETED, jget(r, "ModeOfTravel"))
        ):
            items.append(
                {
                    "id": f"ticket:{i}:{norm(jget(r, 'source'))}:{norm(jget(r, 'destination'))}",
                    "kind": "ticketMissing",
                    "blocking": False,
                    "resolutions": ["upload", "confirm"],
                    "yes": "upload",
                    "no": "confirm",
                    "section": "transport",
                    "row": i + 1,
                    "fact": f"No ticket or bill is attached for {_journey_name(i, r)}.",
                    "question": f"No ticket or bill is attached for {_journey_name(i, r)}. Do you have it to upload?",
                }
            )
    for r, i in stays:
        type_ = nullish(jget(r, "selectedType"), "hotel")
        if (type_ == "hotel" or type_ == "company_paid") and _length(jget(r, "billFiles")) == 0:
            items.append(
                {
                    "id": f"hotelBill:{i}:{norm(jget(r, 'location'))}",
                    "kind": "hotelBillMissing",
                    "blocking": False,
                    "resolutions": ["upload", "confirm"],
                    "yes": "upload",
                    "no": "confirm",
                    "section": "accommodation",
                    "row": i + 1,
                    "fact": f"No hotel bill is attached for {_stay_name(i, r)}.",
                    "question": f"No hotel bill is attached for {_stay_name(i, r)}. Do you have it to upload?",
                }
            )
    for k, issue in enumerate(find_amount_issues(state)):
        items.append(
            {
                "id": f"amount:{k}:{issue}",
                "kind": "amountMissing",
                "blocking": False,
                "resolutions": ["form", "confirm"],
                "fact": issue,
                "question": f"{_ENTER_IT.sub('', issue, count=1)}. Please enter it on the claim form. Shall we carry "
                f"on with the rest meanwhile?",
            }
        )

    # ── 6. what only the form can settle (its own validation, beyond the details asked above) ──
    for p in form_problems:
        if jget(p, "kind") == "field":
            continue
        section = jget(p, "section")
        item: dict[str, Any] = {
            "id": f"form:{js_str(jget(p, 'kind'))}:{js_str(section)}:{js_str(jget(p, 'row'))}",
            "kind": "formOnly",
            "blocking": True,
            "resolutions": ["form"],
        }
        if section != "general" and section is not UNDEFINED:
            item["section"] = section
        if js_truthy(jget(p, "row")):
            item["row"] = jget(p, "row")
        message = js_str(jget(p, "message"))
        item["fact"] = f"The claim form says: {message}"
        item["question"] = (
            f"The claim form won't accept this yet: {message} Please fix it on the form and tell me when it's done."
        )
        items.append(item)

    # ── 7. leave ──
    if not js_truthy(leave_answered) and len(leaves) == 0 and len(journeys) > 0:
        items.append(
            {
                "id": "askLeave",
                "kind": "askLeave",
                "blocking": False,
                "resolutions": ["chat", "confirm"],
                "no": "confirm",
                "yes": "details",
                "section": "leave",
                "fact": "The claim has no leave entries, and the employee has not said whether they took leave "
                "during the tour.",
                "question": "Were you on leave on any day of the tour? If yes, which days?",
            }
        )

    return items  # type: ignore[return-value]


# JavaScript's `$` without the m flag: the very end of the text
_ENTER_IT: Final = re.compile(r" Enter it on the form\.?\Z")


# ─── Going through the review ─────────────────────────────────────────────────


class ReviewNext(TypedDict):
    """What comes next: {'type': 'ask', 'item', 'open'} (ask this one; the open ones, this first), {'type':
    'blocked', 'blocking'} (nothing left to ask, but the form still rejects these) or {'type': 'done'}."""

    type: Literal["ask", "blocked", "done"]
    item: NotRequired[ReviewItem]
    open: NotRequired[list[ReviewItem]]
    blocking: NotRequired[list[ReviewItem]]


def next_review_step(items: list[ReviewItem], confirmed: set[str], deferred: set[str]) -> ReviewNext:
    """tourReview.ts:nextReviewStep.

    Items the employee confirmed are not asked again; items they will fix on the form are not asked again either, but
    a blocking one keeps the claim from being submitted until the form no longer reports it.
    """
    open_ = [i for i in items if i["id"] not in confirmed and i["id"] not in deferred]
    if len(open_) > 0:
        return {"type": "ask", "item": open_[0], "open": open_}
    blocking = [i for i in items if js_truthy(jget(i, "blocking"))]
    return {"type": "blocked", "blocking": blocking} if len(blocking) > 0 else {"type": "done"}


def settle(item: ReviewItem, how: Literal["confirm", "later"], confirmed: set[str], deferred: set[str]) -> None:
    """tourReview.ts:settle: how the employee's answer settles the item (a blocking item is never confirmed away)."""
    blocking = js_truthy(jget(item, "blocking"))
    if how == "confirm" and not blocking:
        confirmed.add(item["id"])
    elif how == "later":
        (deferred if blocking else confirmed).add(item["id"])


# ─── What the AI model is told (shared by the chat and the AI evaluation) ─────


def _how_to_settle(item: ReviewItem) -> str:
    resolutions = item["resolutions"]
    ways = [
        "upload" in resolutions and "they can attach the ticket or bill with the 📎 button",
        "chat" in resolutions and "they can tell you the details in the chat",
        "form" in resolutions and "it is fixed on the claim form itself",
        "confirm" in resolutions
        and not js_truthy(jget(item, "blocking"))
        and "or they can confirm the claim is right as it is (for example they have no ticket, or there was nothing "
        "to claim)",
    ]
    return "; ".join(w for w in ways if w)


def review_question_note(item: ReviewItem, open_: list[ReviewItem], again: bool = False) -> str:
    """tourReview.ts:reviewQuestionNote: the note for one review question.

    `open_` = the items still to ask, this one first; `again` = it was just asked and is still there.
    """
    blocking = len([i for i in open_ if js_truthy(jget(i, "blocking"))])
    left = (
        "This is the last thing to check"
        if len(open_) == 1
        else f"{len(open_)} things are left to check ({blocking} of them must be fixed before submitting)"
    )
    must = "This must be fixed before the claim can be submitted. " if js_truthy(jget(item, "blocking")) else ""
    return (
        f"{'This is still not sorted out. ' if again else ''}Before the claim can be submitted, it is checked for "
        f"everything that makes the tour incomplete or that the claim form would reject. "
        f"{left}.\n"
        f"The problem: {js_str(item['fact'])}\n"
        f"{must}How it can be settled: {_how_to_settle(item)}.\n"
        f"Ask the employee exactly this question, and only this one: {js_str(item['question'])}"
    )


def review_blocked_note(blocking: list[ReviewItem]) -> str:
    """tourReview.ts:reviewBlockedNote."""
    facts = "\n".join(f"• {js_str(i['fact'])}" for i in blocking)
    return (
        "Everything else is checked, but the claim form will not accept the claim until these are fixed on the form:\n"
        f'{facts}\nAsk the employee to fix them on the form, then say "submit" again.'
    )


def review_done_note(claim_text: str, submit: bool) -> str:
    """tourReview.ts:reviewDoneNote."""
    if js_truthy(submit):
        return (
            f"The review is complete: nothing is missing and the form's checks pass. {js_str(claim_text)}\n\n"
            "By submitting, the employee declares that these details and the attached bills are genuine and correct. "
            "Ask whether to submit the claim now."
        )
    return (
        f"The review is complete: nothing is missing and the form's checks pass. {js_str(claim_text)}\n\n"
        'Tell the employee the claim is complete, and that they can say "submit" when they are ready.'
    )


def review_settled_note(item: ReviewItem, how: Literal["confirm", "later", "upload"]) -> str:
    """tourReview.ts:reviewSettledNote."""
    if how == "upload":
        return (
            "The employee has it. Ask them to attach it with the 📎 button now (PDF or photo); the check carries on "
            "once it is read. If they would rather type the details, they can."
        )
    if how == "confirm":
        return f"The employee confirmed this is right as it is: {js_str(item['fact'])} It is left as it is."
    fixed = " The claim cannot be submitted until it is fixed." if js_truthy(jget(item, "blocking")) else ""
    return f"The employee will fix this on the claim form: {js_str(item['fact'])}{fixed}"


# ─── What is missing, in one plain sentence ───────────────────────────────────


def _lower(t: str) -> str:
    return t[:1].lower() + t[1:]


def _upper(t: str) -> str:
    return t[:1].upper() + t[1:]


# JavaScript's `.`: any character but a line terminator
_DOT: Final = "[^\n\r  ]"
_BETWEEN: Final = re.compile(f"between ({_DOT}+?) and ({_DOT}+?)\\?")
_OVERLAP: Final = re.compile(r"journey ([0-9]+) and journey ([0-9]+) overlap")
_WHILE_IN: Final = re.compile(f"While you were in ({_DOT}+?) \\(")
_NIGHTS: Final = re.compile(f"on (the nights? of {_DOT}+?) in ({_DOT}+?)\\?")
_COVERS: Final = re.compile(f"covers ({_DOT}+?) of your tour")
_TICKET_FOR: Final = re.compile(f"for (journey [0-9]+ \\({_DOT}+?\\))")
_BILL_FOR: Final = re.compile(f"for (stay [0-9]+(?: \\({_DOT}+?\\))?)\\.")


def missing_in_short(items: list[ReviewItem]) -> str:
    """tourReview.ts:missingInShort: the gaps of a tour in one plain sentence the employee can't misread, e.g.
    "Missing Return journey ticket, cabs/taxis and other conveyance bills for both onward and return journeys."
    Returns '' when nothing is missing.
    """
    parts: list[str] = []

    def has(k: str) -> bool:
        return any(i["kind"] == k for i in items)

    def of(k: str) -> list[ReviewItem]:
        return [i for i in items if i["kind"] == k]

    odd = len(of("oddPlace"))
    if odd:
        parts.append(f"{'a place name' if odd == 1 else f'{odd} place names'} to correct (misread from the bill)")
    if has("noJourneys"):
        parts.append("journey tickets")
    if has("noOutbound"):
        parts.append("Onward journey ticket")
    if has("noReturn"):
        parts.append("Return journey ticket")
    for i in of("routeBreak"):
        m = _BETWEEN.search(i["question"])
        parts.append(f"ticket between {m[1]} and {m[2]}" if m else "a ticket in between")
    for i in of("overlap"):
        m = _OVERLAP.search(i["fact"])
        parts.append(
            f"correct times for journeys {m[1]} and {m[2]} (they overlap)"
            if m
            else "correct times for journeys that overlap"
        )

    # cabs, taxis and autos to and from the stations and airports: onward side, return side
    conv = [i for i in items if i["kind"] in ("conveyanceBefore", "conveyanceAfter", "conveyanceTransfer")]
    if len(conv) > 0 or has("noReturn"):
        onward = any(jget(i, "side") == "onward" for i in conv)
        # the way back needs its cabs too, even before its ticket is in the claim
        back = has("noReturn") or any(jget(i, "side") == "return" for i in conv)
        sides = (
            "for both onward and return journeys"
            if onward and back
            else "for the onward journey"
            if onward
            else "for the return journey"
            if back
            else ""
        )
        if sides:
            parts.append(f"cabs/taxis and other conveyance bills {sides}")
        transfers = [i for i in conv if jget(i, "side") == "middle"]
        if transfers:
            many = len([i for i in transfers if i["kind"] == "conveyanceTransfer"]) > 1
            parts.append(
                f"cab/taxi bills for going between "
                f"{'stations and airports' if many else 'stations and airports on the way'}"
            )
    for i in of("localConveyance"):
        m = _WHILE_IN.search(i["question"])
        parts.append(f"cab/taxi bills for local travel{f' in {m[1]}' if m and m[1] else ''}")
    for i in of("nightsWithoutStay"):
        m = _NIGHTS.search(i["question"])
        parts.append(f"hotel stay for {m[1]} in {m[2]}" if m else "a hotel stay")
    for i in of("uncoveredDays"):
        m = _COVERS.search(i["question"])
        parts.append(f"tickets or bills for {m[1] if m else 'some days of the tour'}")
    tickets = [m[1] for i in of("ticketMissing") if (m := _TICKET_FOR.search(i["question"])) and m[1]]
    if tickets:
        parts.append(f"ticket or bill file for {' and '.join(tickets)}")
    hotel_bills = [m[1] for i in of("hotelBillMissing") if (m := _BILL_FOR.search(i["question"])) and m[1]]
    if hotel_bills:
        parts.append(f"hotel bill for {' and '.join(hotel_bills)}")
    details = len(of("missingField"))
    if details:
        parts.append(f"{details} detail{'' if details == 1 else 's'} (dates, times or places) to fill in")

    if len(parts) == 0:
        return ""
    first, *rest = parts
    return f"Missing {', '.join([_upper(first), *(_lower(p) for p in rest)])}."
