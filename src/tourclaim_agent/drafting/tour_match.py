"""Which approved tour does each bill belong to? (BIL-30..37)

Ported from the in-app Claim Assistant (Tour_React src/lib/claimChatbot/tourMatcher.ts),
so employees get the same answers. Candidates are the employee's claimable tours, so
ownership and "approved, not yet claimed" are already given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from tourclaim_agent.domain.claim import StayRow, TransportRow
from tourclaim_agent.domain.models import TourSummary

Row = TransportRow | StayRow

# A neutral bill fits the top two tours about equally when their scores differ by less than this.
TIE_MARGIN = 0.5


def tour_window(tour: TourSummary) -> tuple[date, date] | None:
    """First to last day of the tour (BIL-30). None when the tour has no start date."""
    start = tour.start_date
    if start is None:
        return None
    end = tour.end_date or start + timedelta(days=max(0, (tour.total_days or 1) - 1))
    return start, max(start, end)


def norm(text: str | None) -> str:
    """Lower case, letters/digits/spaces only, spaces collapsed (BIL-31)."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())).strip()


def same_place(a: str | None, b: str | None) -> bool:
    """Both at least 3 characters and one contains the other ("new delhi" ~ "delhi")."""
    x, y = norm(a), norm(b)
    return len(x) >= 3 and len(y) >= 3 and (x in y or y in x)


def _clues(rows: list[Row]) -> tuple[list[date], list[str]]:
    dates: list[date] = []
    places: list[str] = []
    for row in rows:
        if isinstance(row, TransportRow):
            dates += [d for d in (row.depart_date, row.arrive_date) if d]
            places += [p for p in (norm(row.source), norm(row.destination)) if p]
        else:
            dates += [d for d in (row.check_in_date, row.check_out_date) if d]
            if norm(row.location):
                places.append(norm(row.location))
    return dates, places


@dataclass
class TourMatch:
    tour: TourSummary
    # Share of the rows' dates inside the tour window; None when the rows have no dates or the tour no window.
    date_fit: float | None
    score: float


def rank_tours(rows: list[Row], tours: list[TourSummary]) -> list[TourMatch]:
    """Score a set of rows against every tour, best first (BIL-32). score = date_fit x 2 + route_hits."""
    dates, places = _clues(rows)
    ranked = []
    for tour in tours:
        window = tour_window(tour)
        # A day either side counts too (out the night before, back the morning after), but only for a bill
        # that goes to or comes back from the tour's destination: a ticket to another city the day after
        # the tour ends is another trip, even when it leaves from the same office town.
        to_destination = not places or any(same_place(tour.destination, p) for p in places)
        inside = 0
        if window:
            start, end = window
            for d in dates:
                if start <= d <= end or (to_destination and d in (start - timedelta(days=1), end + timedelta(days=1))):
                    inside += 1
        fit = inside / len(dates) if dates and window else 0.0
        route_hits = sum(1 for p in (tour.source, tour.destination) if any(same_place(p, q) for q in places))
        ranked.append(TourMatch(tour, fit if dates and window else None, fit * 2 + route_hits))
    ranked.sort(key=lambda m: m.score, reverse=True)
    return ranked


class VerdictKind(StrEnum):
    NEUTRAL = "neutral"  # nothing tells the tours apart
    NO_TOUR = "no_tour"  # fits none of the tours
    ASSIGNED = "assigned"


@dataclass
class Verdict:
    kind: VerdictKind
    tour: TourSummary | None = None
    looks_like: TourSummary | None = None  # for NO_TOUR: a tour with some date fit, to mention


def judge(row: Row, tours: list[TourSummary]) -> Verdict:
    """Judge one row on its own against every candidate tour (BIL-33)."""
    dates, places = _clues([row])
    ranked = rank_tours([row], tours)
    if not (dates or places) or not ranked:
        return Verdict(VerdictKind.NEUTRAL)
    best = ranked[0]
    # A shared city (everyone starts from the same office town) is weak evidence: when a bill has dates,
    # they decide. Tours without a window can't be compared on dates, so they don't count here.
    windowed = [m for m in ranked if m.date_fit is not None]
    dates_rule_out = bool(windowed) and all(m.date_fit == 0 for m in windowed)
    if best.score == 0 or dates_rule_out:
        looks_like = next((m.tour for m in ranked if (m.date_fit or 0) > 0), None)
        return Verdict(VerdictKind.NO_TOUR, looks_like=looks_like)
    if len(ranked) > 1 and best.score - ranked[1].score < TIE_MARGIN:
        return Verdict(VerdictKind.NEUTRAL)
    return Verdict(VerdictKind.ASSIGNED, tour=best.tour)


@dataclass
class Triage:
    groups: dict[int, list[Row]] = field(default_factory=dict)  # tour id -> rows
    # Rows that could belong to several tours: asked about (BIL-36).
    unsure: list[Row] = field(default_factory=list)
    # Rows that fit no tour, with a tour they look like when there is one.
    no_tour: list[tuple[Row, TourSummary | None]] = field(default_factory=list)

    def tour_of(self, row: Row) -> int | None:
        return next((tid for tid, rows in self.groups.items() if any(r is row for r in rows)), None)


def triage(rows: list[Row], tours: list[TourSummary], claim_tour_id: int | None = None) -> Triage:
    """Sort the rows of one check by tour (BIL-33..35).

    A claim in progress never narrows the matching: a row that clearly fits another tour goes there.
    Neutral rows go with the single tour the other rows went to; with none, to the claim in
    progress or the only claimable tour; otherwise they are unsure.
    """
    result = Triage()
    neutral: list[Row] = []
    for row in rows:
        verdict = judge(row, tours)
        if verdict.kind is VerdictKind.ASSIGNED and verdict.tour is not None:
            result.groups.setdefault(verdict.tour.tour_id, []).append(row)
        elif verdict.kind is VerdictKind.NEUTRAL:
            neutral.append(row)
        else:
            result.no_tour.append((row, verdict.looks_like))
    if neutral:
        claimable_ids = {t.tour_id for t in tours}
        if len(result.groups) == 1:
            target: int | None = next(iter(result.groups))
        elif not result.groups and claim_tour_id in claimable_ids:
            target = claim_tour_id
        elif not result.groups and len(tours) == 1:
            target = tours[0].tour_id
        else:
            target = None
        if target is None:
            result.unsure = neutral
        else:
            result.groups.setdefault(target, []).extend(neutral)
    return result


def candidates_for(rows: list[Row], tours: list[TourSummary], limit: int = 5) -> list[TourSummary]:
    """Tours to offer for unsure rows, best score first (BIL-36)."""
    return [m.tour for m in rank_tours(rows, tours)][:limit]
