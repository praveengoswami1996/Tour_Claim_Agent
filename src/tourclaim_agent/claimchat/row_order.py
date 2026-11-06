"""Journeys and stays in date order on screen, without moving them where they are stored.

Port of Tour_React src/lib/claimChatbot/rowOrder.ts. A saved draft ties each bill file to its row by position (the
file's rowIndex), so the stored order of the rows never changes: new rows are added after the saved ones. The claim
form shows the rows sorted by date, and the Claim Assistant works on that same sorted view (so "journey 2" is the
second one on screen); its changes are put back into the stored order by row id (in_stored_order).

The TypeScript compares the date stamps with localeCompare (ICU collation, see js_compat.js_locale_compare): for
'YYYY-MM-DD HH:mm' stamps that is plain string order, but a raw date kept from a saved draft ("Select", "garbage")
sorts the ICU way.
"""

from __future__ import annotations

import functools
import re
from typing import Any, TypedDict, TypeVar

from .form_rows import RowFile
from .js_compat import jget, js_locale_compare, js_or, js_str, js_truthy, nullish
from .ocr_mapping import to_iso_date

__all__ = [
    "RowFile",
    "JourneyOverlap",
    "journeys_in_date_order",
    "journey_overlaps",
    "stays_in_date_order",
    "kept_file_ids",
    "trip_ends",
    "in_stored_order",
    "ids_of",
]

R = TypeVar("R", bound=dict[str, Any])


def _stamp_of(date: Any, time: Any) -> str:
    return f"{js_str(date)[:10]} {js_str(js_or(time, '00:00'))}" if js_truthy(date) else ""


def _in_date_order(rows: list[R], key: Any) -> list[R]:
    indexed = [(r, i, key(r)) for i, r in enumerate(rows)]

    def compare(a: tuple[R, int, str], b: tuple[R, int, str]) -> int:
        da, db = a[2], b[2]
        if not da and not db:
            return a[1] - b[1]
        if not da:
            return 1
        if not db:
            return -1
        return js_locale_compare(da, db) or a[1] - b[1]

    return [x[0] for x in sorted(indexed, key=functools.cmp_to_key(compare))]


def _journey_stamp(r: dict[str, Any]) -> str:
    return _stamp_of(jget(r, "DateOfTravelSource"), jget(r, "TimeOfTravelSource")) or _stamp_of(
        jget(r, "DateOfTravelDestination"), jget(r, "TimeOfTravelDestiNation")
    )


def journeys_in_date_order(rows: list[R]) -> list[R]:
    """rowOrder.ts:journeysInDateOrder: by departure, then arrival; rows with no date go last; ties keep order."""
    return _in_date_order(rows, _journey_stamp)


_HHMM = re.compile(r"^([0-9]{1,2}):([0-9]{2})")


def _hhmm(t: Any) -> str:
    m = _HHMM.match(js_str(nullish(t, "")))
    return f"{m[1].rjust(2, '0')}:{m[2]}" if m else ""


class JourneyOverlap(TypedDict):
    """Two journeys whose times overlap: `later` leaves before `earlier` has arrived."""

    earlier: dict[str, Any]
    later: dict[str, Any]


def journey_overlaps(rows: list[R]) -> list[JourneyOverlap]:
    """rowOrder.ts:journeyOverlaps.

    Every pair of journeys that overlap in time, in date order: a journey that leaves before an earlier one has
    arrived. The claim form marks these fields in red and the Claim Assistant asks about them, so both use this.
    On the same day both times are needed to judge; a journey arriving exactly when the next leaves is fine.
    """
    shown = journeys_in_date_order(rows)
    found: list[JourneyOverlap] = []
    for j, later in enumerate(shown):
        dep_day = to_iso_date(jget(later, "DateOfTravelSource"))
        if not dep_day:
            continue
        dep_time = _hhmm(jget(later, "TimeOfTravelSource"))
        for i in range(j):
            earlier = shown[i]
            arr_day = to_iso_date(jget(earlier, "DateOfTravelDestination"))
            if not arr_day:
                continue
            arr_time = _hhmm(jget(earlier, "TimeOfTravelDestiNation"))
            overlaps = dep_day < arr_day or (
                dep_day == arr_day and bool(dep_time) and bool(arr_time) and dep_time < arr_time
            )
            if overlaps:
                found.append({"earlier": earlier, "later": later})
    return found


def stays_in_date_order(rows: list[R]) -> list[R]:
    """rowOrder.ts:staysInDateOrder: by check-in; rows with no date go last; equal dates keep their order."""
    return _in_date_order(rows, lambda r: _stamp_of(jget(r, "checkInDate"), jget(r, "checkInTime")))


def kept_file_ids(rows: list[dict[str, Any]]) -> list[Any]:
    """rowOrder.ts:keptFileIds: the ids of the saved files the claim still holds, as the form sends them on save."""
    files = [f for r in rows for f in [*nullish(jget(r, "uploadedFiles"), []), *nullish(jget(r, "billFiles"), [])]]
    return [jget(f, "fileId") for f in files if js_truthy(jget(f, "fileId"))]


def trip_ends(rows: list[R]) -> dict[str, R | None]:
    """rowOrder.ts:tripEnds.

    The tour's first and last journey by time, wherever they sit in the list: the journey that leaves first and the
    one that arrives last. DA runs from the first's departure to the last's arrival, and the claim's Source,
    Destination and departure date come from them. {"first": None, "last": None} for no rows (TS: undefined).
    """
    dated = [
        r for r in rows if js_truthy(jget(r, "DateOfTravelSource")) or js_truthy(jget(r, "DateOfTravelDestination"))
    ]
    if not dated:
        return {"first": rows[0] if rows else None, "last": rows[-1] if rows else None}

    def arr(r: R) -> str:
        return _stamp_of(jget(r, "DateOfTravelDestination"), jget(r, "TimeOfTravelDestiNation")) or _stamp_of(
            jget(r, "DateOfTravelSource"), jget(r, "TimeOfTravelSource")
        )

    first = functools.reduce(lambda a, b: b if _journey_stamp(b) < _journey_stamp(a) else a, dated)
    last = functools.reduce(lambda a, b: b if arr(b) >= arr(a) else a, dated)
    return {"first": first, "last": last}


def in_stored_order(view: list[R], stored_ids: list[str]) -> list[R]:
    """rowOrder.ts:inStoredOrder.

    Rows of the sorted view put back in their stored order: rows already stored keep their places (matched by id, in
    the order `stored_ids` gives), and rows that are new go after them, in the order of the view.
    """
    if len(stored_ids) == 0:
        return view
    left = list(view)
    stored: list[R] = []
    for row_id in stored_ids:
        at = next((i for i, r in enumerate(left) if r["id"] == row_id), -1)
        if at >= 0:
            stored.append(left.pop(at))
    return [*stored, *left]


def ids_of(rows: list[dict[str, Any]]) -> list[str]:
    """rowOrder.ts:idsOf: the ids of a list of rows, in the order they are stored."""
    return [r["id"] for r in rows]
