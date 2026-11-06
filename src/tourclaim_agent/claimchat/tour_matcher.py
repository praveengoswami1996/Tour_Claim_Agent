"""Which approved tour a set of bills belongs to.

Port of Tour_React src/lib/claimChatbot/tourMatcher.ts. Works out the tour from what the OCR read (dates and
places), so the employee only has to say "yes, that's right" instead of choosing a tour first; and sorts a mixed
upload bill by bill (triage_rows).

Tours, matches and groups are plain dicts with the TypeScript field names. A tour's dates go through the browser's
`new Date(y, m, d)` arithmetic (an unreadable count of days gives the date "NaN-NaN-NaN", as it did there).
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal, NotRequired, TypedDict

from .claim_assistant import ClaimFormState, initial_claim_state
from .form_rows import AccommodationRow, TransportationRow
from .js_compat import (
    JS_WS_CHARS,
    LOCAL_OFFSET_MS,
    MAX_TIME_MS,
    MS_PER_DAY,
    UNDEFINED,
    civil_from_days,
    defined,
    en_in_date,
    is_nullish,
    jget,
    js_is_finite,
    js_len,
    js_number,
    js_number_to_string,
    js_or,
    js_str,
    js_trim,
    js_truthy,
    make_day,
    nullish,
)
from .ocr_mapping import file_key, to_iso_date


class ClaimableTour(TypedDict):
    tourId: int
    source: str
    destination: str
    tourStartDate: str
    statusId: int
    totalDays: int
    tantetiveEndDate: NotRequired[str]
    isAdvanceTaken: NotRequired[str | bool]


class TourMatch(TypedDict):
    tour: ClaimableTour
    # share of the bills' dates that fall inside the tour, or None when the bills have no dates
    dateFit: float | None
    # 0..4: how well the bills fit (dates count double, each place of the route counts one)
    score: float


class TourWindow(TypedDict):
    start: str
    end: str


class MatchResult(TypedDict):
    ranked: list[TourMatch]
    confident: TourMatch | None
    # False when the claim has no dates or places yet, so nothing can be compared
    hasClues: bool


# ─── the browser's local dates ──────────────────────────────────────────────────

INVALID_ISO = "NaN-NaN-NaN"


def _js_date(year: float, month0: float, day: float) -> tuple[int, int, int] | None:
    """`new Date(year, month0, day)` read back as local (y, m 1-12, d); None for an Invalid Date."""
    if not all(js_is_finite(float(v)) for v in (year, month0, day)):
        return None
    y, m0, d = math.trunc(year), math.trunc(month0), math.trunc(day)
    if 0 <= y <= 99:
        y += 1900
    days = make_day(y, m0, d)
    if abs(days * MS_PER_DAY - LOCAL_OFFSET_MS) > MAX_TIME_MS:
        return None
    return civil_from_days(days)


def _iso_of(date: tuple[int, int, int] | None) -> str:
    """`${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}` ("NaN-NaN-NaN" for an Invalid Date)."""
    if date is None:
        return INVALID_ISO
    y, m, d = date
    return f"{js_number_to_string(y)}-{m:02d}-{d:02d}"


def _ymd(iso: str) -> tuple[float, float, float]:
    """`iso.split('-').map(Number)` (missing parts are NaN)."""
    parts = [js_number(p) for p in iso.split("-")]
    parts += [math.nan] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def _add_days(iso: str, days: float) -> str:
    y, m, d = _ymd(iso)
    return _iso_of(_js_date(y, m - 1, d + days))


# ─── tours ──────────────────────────────────────────────────────────────────────


def tour_window(tour: Any) -> TourWindow | None:
    """tourMatcher.ts:tourWindow: first to last day of the tour as YYYY-MM-DD; None when it has no usable dates."""
    start = to_iso_date(jget(tour, "tourStartDate"))
    if not start:
        return None
    end = to_iso_date(jget(tour, "tantetiveEndDate"))
    if not end:
        # Math.max(0, (tour.totalDays || 1) - 1); Math.max(0, NaN) is NaN
        days = js_number(js_or(jget(tour, "totalDays"), 1)) - 1
        end = _add_days(start, days if math.isnan(days) else max(0.0, days))
    return {"start": start, "end": end}


_NOT_WORD = re.compile("[^a-z0-9" + re.escape(JS_WS_CHARS) + "]")
_SPACES = re.compile("[" + re.escape(JS_WS_CHARS) + "]+")


def norm(s: Any) -> str:
    """tourMatcher.ts:norm: lower case, letters and digits only, single spaces."""
    text = nullish(s, "")
    if not isinstance(text, str):
        raise TypeError("(s ?? '').toLowerCase is not a function")
    return js_trim(_SPACES.sub(" ", _NOT_WORD.sub(" ", text.lower())))


def same_place(a: str, b: str) -> bool:
    """tourMatcher.ts:samePlace: a place on a bill matches a tour place when one contains the other."""
    return js_len(a) >= 3 and js_len(b) >= 3 and (b in a or a in b)


def _clues_from(state: Any) -> tuple[list[str], list[str]]:
    """cluesFrom: every date and place the bills mention."""
    dates: list[str] = []
    places: list[str] = []
    for r in state["transportationRows"]:
        for d in (jget(r, "DateOfTravelSource"), jget(r, "DateOfTravelDestination")):
            if js_truthy(d):
                dates.append(to_iso_date(d))
        for p in (jget(r, "source"), jget(r, "destination")):
            if js_truthy(p):
                places.append(norm(p))
    if js_truthy(state["includeAccommodation"]):
        for r in state["accommodationRows"]:
            for d in (jget(r, "checkInDate"), jget(r, "checkOutDate")):
                if js_truthy(d):
                    dates.append(to_iso_date(d))
            if js_truthy(jget(r, "location")):
                places.append(norm(jget(r, "location")))
    return [d for d in dates if d], [p for p in places if p]


def match_tours(state: ClaimFormState, tours: list[Any]) -> MatchResult:
    """tourMatcher.ts:matchTours: all tours, best fit first, and the one to suggest when the fit is clear."""
    dates, places = _clues_from(state)
    scored: list[TourMatch] = []
    for tour in tours:
        window = tour_window(tour)
        # A day either side counts too (people travel the night before, and the form allows a day after the last
        # leg), but only for a bill that goes to or comes back from the tour's destination: a ticket to another city
        # the day after the tour ends is another trip, even when it leaves from the same office town.
        dest = norm(jget(tour, "destination"))
        to_destination = len(places) == 0 or any(same_place(dest, q) for q in places)
        inside = 0
        if window:
            before = _add_days(window["start"], -1)
            after = _add_days(window["end"], 1)
            inside = sum(
                1 for d in dates if (window["start"] <= d <= window["end"]) or (to_destination and d in (before, after))
            )
        date_fit = inside / len(dates) if dates and window else 0
        route = [norm(jget(tour, "source")), dest]
        route_hits = sum(1 for p in route if any(same_place(p, q) for q in places))
        scored.append(
            {"tour": tour, "dateFit": date_fit if dates and window else None, "score": date_fit * 2 + route_hits}
        )
    ranked = sorted(scored, key=lambda m: -m["score"])
    best = ranked[0] if ranked else None
    second = ranked[1] if len(ranked) > 1 else None
    confident = (
        best
        if best is not None and best["score"] >= 2 and (second is None or best["score"] - second["score"] >= 1)
        else None
    )
    return {"ranked": ranked, "confident": confident, "hasClues": len(dates) > 0 or len(places) > 0}


_TOUR_NUMBER = re.compile(r"\b([0-9]{2,})\b", re.ASCII)


def find_tour_by_text(text: str, tours: list[Any]) -> Any:
    """tourMatcher.ts:findTourByText: a tour the employee typed: its number ("#12", "tour 12") or a route city."""
    m = _TOUR_NUMBER.search(text)
    if m:
        by_id = next((t for t in tours if js_str(jget(t, "tourId")) == m.group(1)), None)
        if by_id is not None:
            return by_id
    t = norm(text)
    if js_len(t) < 3:
        return None
    hits = [
        tour
        for tour in tours
        if any(same_place(p, t) for p in (norm(jget(tour, "source")), norm(jget(tour, "destination"))))
    ]
    return hits[0] if len(hits) == 1 else None


# ─── Mixed uploads: which bill belongs to which tour? ─────────────────────────


class DroppedRow(TypedDict):
    """A bill that was read but cannot go into this claim."""

    label: str
    # keys (name|size) of the files behind it, so they can be uploaded again later
    files: list[str]
    # the tour it looks like it belongs to instead, when there is one
    belongsTo: NotRequired[ClaimableTour]


class RowGroup(TypedDict):
    tour: ClaimableTour
    journeys: list[TransportationRow]
    stays: list[AccommodationRow]


class NeutralRows(TypedDict):
    journeys: list[TransportationRow]
    stays: list[AccommodationRow]


class Triage(TypedDict):
    # bills that clearly belong to one tour each
    groups: list[RowGroup]
    # bills with nothing to tell them apart (they go with whichever tour is chosen)
    neutral: NeutralRows
    # bills that fit none of the tours (or not the confirmed one)
    dropped: list[DroppedRow]


def _short_day(iso: Any) -> str:
    d = to_iso_date(iso)
    if not d:
        return ""
    y, m, day = _ymd(d)
    date = _js_date(y, m - 1, day)
    return "Invalid Date" if date is None else en_in_date(*date)


def _names(files: Any) -> list[str]:
    return [file_key(f) for f in nullish(files, [])]


class _Verdict(TypedDict):
    kind: Literal["neutral", "drop", "assign"]
    tour: NotRequired[Any]


def _picked(picked: Any) -> bool:
    return not is_nullish(picked)


def _judge(probe: ClaimFormState, tours: list[Any], picked: Any) -> _Verdict:
    """judge: one bill (alone) against the tours: where does it belong?"""
    result = match_tours(probe, [picked] if _picked(picked) else tours)
    ranked = result["ranked"]
    if not result["hasClues"] or len(ranked) == 0:
        return {"kind": "neutral"}
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    # a shared city (everyone starts from the same office town) is weak evidence: when a bill has dates, they decide
    # (only when every tour has dates to compare with)
    dates_rule_out = all(m["dateFit"] is not None and m["dateFit"] == 0 for m in ranked)
    if best["score"] == 0 or dates_rule_out:
        other = UNDEFINED
        if _picked(picked):
            others = [t for t in tours if jget(t, "tourId") != jget(picked, "tourId")]
            found = next(
                (m for m in match_tours(probe, others)["ranked"] if nullish(m["dateFit"], 0) > 0 or m["score"] >= 2),
                None,
            )
            if found is not None:
                other = found["tour"]
        return {"kind": "drop", "tour": other}
    if _picked(picked):
        return {"kind": "assign", "tour": picked}
    # two tours fit about equally: this bill alone cannot say which
    if second is not None and best["score"] - second["score"] < 0.5:
        return {"kind": "neutral"}
    return {"kind": "assign", "tour": best["tour"]}


def triage_rows(mapped: dict[str, Any], tours: list[Any], picked: Any = None) -> Triage:
    """tourMatcher.ts:triageRows.

    Sorts freshly read bills one by one. With a tour already confirmed (`picked`), bills that do not fit it are
    dropped. Without one, each bill goes to the tour it fits best, so a mixed upload shows up as several groups, and
    bills that fit no tour at all are dropped.
    """
    groups: dict[Any, RowGroup] = {}
    neutral: NeutralRows = {"journeys": [], "stays": []}
    dropped: list[DroppedRow] = []

    def group_of(tour: Any) -> RowGroup:
        key = jget(tour, "tourId")
        if key not in groups:
            groups[key] = {"tour": tour, "journeys": [], "stays": []}
        return groups[key]

    for row in mapped["transportationRows"]:
        v = _judge({**initial_claim_state(), "transportationRows": [row]}, tours, picked)
        if v["kind"] == "assign":
            group_of(v["tour"])["journeys"].append(row)
        elif v["kind"] == "neutral":
            neutral["journeys"].append(row)
        else:
            source = js_str(js_or(jget(row, "source"), "?"))
            destination = js_str(js_or(jget(row, "destination"), "?"))
            on = (
                f" on {_short_day(jget(row, 'DateOfTravelSource'))}"
                if js_truthy(jget(row, "DateOfTravelSource"))
                else ""
            )
            dropped.append(
                defined(
                    label=f"ticket {source} → {destination}{on}",
                    files=_names(jget(row, "uploadedFiles")),
                    belongsTo=v["tour"],
                )  # type: ignore[arg-type]
            )
    for row in mapped["accommodationRows"]:
        v = _judge({**initial_claim_state(), "accommodationRows": [row], "includeAccommodation": True}, tours, picked)
        if v["kind"] == "assign":
            group_of(v["tour"])["stays"].append(row)
        elif v["kind"] == "neutral":
            neutral["stays"].append(row)
        else:
            days = " – ".join(
                d for d in (_short_day(jget(row, "checkInDate")), _short_day(jget(row, "checkOutDate"))) if d
            )
            location = js_str(js_or(jget(row, "location"), "?"))
            dropped.append(
                defined(
                    label=f"hotel stay in {location}{f', {days}' if days else ''}",
                    files=_names(jget(row, "billFiles")),
                    belongsTo=v["tour"],
                )  # type: ignore[arg-type]
            )
    return {"groups": list(groups.values()), "neutral": neutral, "dropped": dropped}
