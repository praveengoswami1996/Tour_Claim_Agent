"""Match how the employee referred to a tour against their own tours (FRS CONV-02).

Only the employee's own tour list is searched, so a match is always a tour they own.
When the reference is ambiguous the caller must ask the employee to choose.
"""

from __future__ import annotations

import re

from tourclaim_agent.domain.models import TourSummary
from tourclaim_agent.llm.intent import TourReference


def _words(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 3}


def _place_matches(tour: TourSummary, place: str) -> bool:
    wanted = _words(place)
    if not wanted:
        return False
    return bool(wanted & (_words(tour.source) | _words(tour.destination)))


def resolve_tours(tours: list[TourSummary], ref: TourReference) -> list[TourSummary]:
    """Return the candidate tours for a reference, best first. Input order is newest first."""
    if ref.tour_id is not None:
        return [t for t in tours if t.tour_id == ref.tour_id]

    candidates = tours
    if ref.place:
        candidates = [t for t in candidates if _place_matches(t, ref.place)]
    if ref.month or ref.year:
        candidates = [
            t
            for t in candidates
            if (day := t.start_date or t.end_date or t.request_date) is not None
            and (ref.month is None or day.month == ref.month)
            and (ref.year is None or day.year == ref.year)
        ]
    if ref.latest and candidates:
        return candidates[:1]
    return candidates


def has_reference(ref: TourReference) -> bool:
    return any((ref.tour_id is not None, ref.place, ref.month, ref.year, ref.latest))


def tour_label(tour: TourSummary) -> str:
    when = tour.start_date.strftime("%d %b %Y") if tour.start_date else "date not recorded"
    return f"Tour {tour.tour_id}: {tour.route}, {when} ({tour.status or tour.status_info.backend_name})"
