"""Turns the OCR reading of a monthly Tour Plan (bill_type "tour_plan") into Within Jurisdiction tours.

Port of Tour_React src/lib/withinTourPlan.ts. The OCR answers with one flat list of journeys: `tourDetails` is the
first journey and `tourDetails.tourLegs` are all the others, for every date of the plan. The plan itself is grouped
by date, so we regroup: one date = one tour; the first journey of that date is the main journey, the rest are its legs.

The tours are the form's TourData dicts (within_form.py). Their `startDate` / `endDate` were JavaScript Dates (local
midnight); here they are the text JSON gives such a Date ('2026-09-30T18:30:00.000Z'). "Today" and the tour ids come
from the clock in js_compat (replace it with js_compat.use_runtime in tests).
"""

from __future__ import annotations

import functools
import re
from typing import Any, Final, TypedDict

from .js_compat import (
    JS_WS_CHARS,
    LOCAL_OFFSET_MS,
    MS_PER_DAY,
    UNDEFINED,
    civil_from_days,
    is_nullish,
    jget,
    js_local_date,
    js_locale_compare,
    js_number,
    js_str,
    js_trim,
    js_truthy,
    local_ymd,
    make_day,
    now_ms,
    nullish,
    utc_iso_string,
)
from .ocr_mapping import to_iso_date, to_time
from .within_form import TourData

# Same limits the tour form puts on its date inputs (TourForm.tsx).
TOUR_PLAN_MIN_DATE: Final = "2025-09-24"

WITHIN_TRAVEL_MODES: Final = (
    "Rail",
    "Public Transport",
    "Public Transport Beyond Entitled",
    "Company Provided Vehicle",
    "Personal Vehicle",
)


class TourPlanError(Exception):
    """withinTourPlan.ts:TourPlanError: a problem the employee can understand and fix (wrong file, two months in
    one plan, ...). The message is shown to the employee."""

    name: Final = "TourPlanError"


class MappedTourPlan(TypedDict):
    tours: list[TourData]
    # things we left out or could not read, for the employee to see
    warnings: list[str]


def _pad2(n: int) -> str:
    return str(n).rjust(2, "0")


def _format_ymd(y: int, m: int, d: int) -> str:
    """date-fns format(date, 'yyyy-MM-dd') of local (year, month 1-12, day): 'yyyy' is the year of the era."""
    year = y if y > 0 else 1 - y
    return f"{str(year).rjust(4, '0')}-{_pad2(m)}-{_pad2(d)}"


def _future_limit() -> str:
    """futureLimit: today + 3 months (setMonth: the day rolls over, 30 Nov -> 2 Mar), local 'yyyy-MM-dd'."""
    y, m, d = local_ymd(now_ms())
    return _format_ymd(*_set_month(y, m - 1 + 3, d))


def _set_month(year: int, month0: int, day: int) -> tuple[int, int, int]:
    """Date.prototype.setMonth on a local date (unlike `new Date(y, m, d)`, years 0-99 are kept as they are)."""
    return civil_from_days(make_day(year, month0, day))


def _trim_or_empty(value: Any) -> str:
    """`(value ?? '').trim()`: a value that is not text throws, as `trim is not a function` did."""
    value = nullish(value, "")
    if not isinstance(value, str):
        raise TypeError("trim is not a function")
    return js_trim(value)


def map_within_travel_mode(mode: Any) -> str:
    """withinTourPlan.ts:mapWithinTravelMode. Note "own" also matches inside "down" or "town" (as before)."""
    text = _trim_or_empty(mode).lower()
    if not text:
        return ""
    exact = next((m for m in WITHIN_TRAVEL_MODES if m.lower() == text), None)
    if exact:
        return exact
    if "beyond" in text:
        return "Public Transport Beyond Entitled"
    if "company" in text or "official" in text:
        return "Company Provided Vehicle"
    if "personal" in text or "own" in text:
        return "Personal Vehicle"
    if "rail" in text or "train" in text:
        return "Rail"
    if re.search(r"(public|bus|taxi|cab|auto|metro)", text):
        return "Public Transport"
    return ""


# `[\s-]` with JavaScript's whitespace; `\b` without the u flag is ASCII-only
_WS_OR_DASH = "[" + re.escape(JS_WS_CHARS + "-") + "]"
_TWO_WHEELER = re.compile(r"(two|2)" + _WS_OR_DASH + r"*wheel|bike|scooter|motorcycle")
_FOUR_WHEELER = re.compile(r"(four|4)" + _WS_OR_DASH + r"*wheel|\bcar\b", re.ASCII)


def map_vehicle_type(value: Any, mode: Any = UNDEFINED) -> str:
    """withinTourPlan.ts:mapVehicleType. The OCR sometimes keeps the type inside the mode: "Personal Vehicle (Four
    Wheeler)". "scooty" is not matched (as before)."""
    text = f"{js_str(nullish(value, ''))} {js_str(nullish(mode, ''))}".lower()
    if _TWO_WHEELER.search(text):
        return "Two Wheeler"
    if _FOUR_WHEELER.search(text):
        return "Four Wheeler"
    return ""


def _local_midnight(iso: str) -> tuple[int, int, int]:
    """toLocalDate: `new Date(y, m - 1, d)` of a 'YYYY-MM-DD', as local (year, month, day)."""
    y, m, d = (int(js_number(x)) for x in iso.split("-"))
    return js_local_date(y, m - 1, d)


def _date_json(ymd: tuple[int, int, int]) -> str:
    """What JSON gives for a Date at local midnight of that day ('2026-09-30T18:30:00.000Z' for 1 Oct 2026)."""
    y, m, d = ymd
    return utc_iso_string(make_day(y, m - 1, d) * MS_PER_DAY - LOCAL_OFFSET_MS)


class _Journey(TypedDict):
    depDate: str
    depTime: str
    from_: str
    arrDate: str
    arrTime: str
    to: str
    mode: str
    vehicle: str
    distance: float
    includeTravelCosts: bool


def _to_journey(j: Any) -> _Journey:
    """toJourney (a journey that is null throws, as reading its fields did)."""
    if is_nullish(j):
        raise TypeError("Cannot read properties of null")
    mode = map_within_travel_mode(jget(j, "modeOfTravel"))
    dep_date = to_iso_date(jget(j, "departureDate"))
    distance = js_number(jget(j, "distance"))
    return {
        "depDate": dep_date,
        "depTime": to_time(jget(j, "departureTime")),
        "from_": _trim_or_empty(jget(j, "from")),
        "arrDate": to_iso_date(jget(j, "arrivalDate")) or dep_date,
        "arrTime": to_time(jget(j, "arrivalTime")),
        "to": _trim_or_empty(jget(j, "to")),
        "mode": mode,
        "vehicle": (
            map_vehicle_type(jget(j, "personalVehicleType"), jget(j, "modeOfTravel"))
            if mode == "Personal Vehicle"
            else ""
        ),
        "distance": distance if distance > 0 else 0,
        "includeTravelCosts": jget(j, "includeTravelCosts") is not False,
    }


def _has_content(j: _Journey) -> bool:
    return bool(j["from_"] or j["to"] or j["depDate"])


def _split_purposes(purpose: Any, tour_count: int) -> list[str]:
    """splitPurposes: the purpose is one text for the whole plan, one sentence per date joined with ";"."""
    whole = _trim_or_empty(purpose)
    parts = [p for p in (js_trim(p) for p in whole.split(";")) if p]
    # One sentence per date: give each tour its own. Anything else: we can't tell which is whose, so each tour gets
    # the whole text.
    return parts if len(parts) == tour_count else [whole for _ in range(tour_count)]


def _first(items: Any) -> Any:
    """`items?.[0]`."""
    if is_nullish(items):
        return UNDEFINED
    if isinstance(items, list | str):
        return items[0] if items else UNDEFINED
    return jget(items, "0")


def _legs_of(main: Any) -> list[Any]:
    """`(main?.tourLegs ?? [])` before `.map` (anything but a list throws, as `.map is not a function` did)."""
    legs = nullish(jget(main, "tourLegs"), [])
    if not isinstance(legs, list):
        raise TypeError("tourLegs.map is not a function")
    return legs


def map_tour_plan_to_tours(result: Any) -> MappedTourPlan:
    """withinTourPlan.ts:mapTourPlanToTours. Raises TourPlanError with the text to show the employee."""
    file = _first(jget(jget(result, "meta"), "per_file_results"))
    if js_truthy(file) and not js_truthy(jget(file, "success")):
        error = jget(file, "error")
        raise TourPlanError(
            f"The tour plan could not be read{f' ({js_str(error)})' if js_truthy(error) else ''}."
            " Please upload a clear PDF or image."
        )
    bill_type = jget(file, "bill_type")
    if js_truthy(bill_type) and bill_type != "tour_plan":
        raise TourPlanError("This file does not look like a tour plan. Please upload your monthly tour plan.")

    main = jget(result, "tourDetails")
    warnings: list[str] = []
    main_journey = _to_journey(
        {
            "from": jget(main, "from"),
            "to": jget(main, "to"),
            "departureDate": jget(main, "startDate"),
            "departureTime": jget(main, "startTime"),
            "arrivalDate": jget(main, "endDate"),
            "arrivalTime": jget(main, "endTime"),
            "modeOfTravel": jget(main, "modeOfTravel"),
            "personalVehicleType": jget(main, "personalVehicleType"),
            "distance": jget(main, "distance"),
            "includeTravelCosts": jget(main, "includeTravelCosts"),
        }
    )
    all_journeys = [j for j in [main_journey, *(_to_journey(leg) for leg in _legs_of(main))] if _has_content(j)]

    if len(all_journeys) == 0:
        raise TourPlanError("No journeys were found in this file. Please upload your monthly tour plan.")

    undated = [j for j in all_journeys if not j["depDate"]]
    if len(undated) > 0:
        warnings.append(f"{len(undated)} journey(s) were skipped because their date could not be read.")

    # Stable sort: the plan is already in date order, this only guards against a mixed-up reading.
    def order(a: tuple[int, _Journey], b: tuple[int, _Journey]) -> int:
        return (
            js_locale_compare(a[1]["depDate"], b[1]["depDate"])
            or js_locale_compare(a[1]["depTime"], b[1]["depTime"])
            or a[0] - b[0]
        )

    dated = [j for _, j in sorted(enumerate(j for j in all_journeys if j["depDate"]), key=functools.cmp_to_key(order))]

    groups: dict[str, list[_Journey]] = {}
    for j in dated:
        groups[j["depDate"]] = [*groups.get(j["depDate"], []), j]

    months = list(dict.fromkeys(d[:7] for d in groups))
    if len(months) > 1:
        raise TourPlanError(
            f"This tour plan covers more than one month ({', '.join(months)}). Please upload a plan for a single month."
        )

    max_date = _future_limit()
    usable: list[tuple[str, list[_Journey]]] = []
    for date, journeys in groups.items():
        if date < TOUR_PLAN_MIN_DATE or date > max_date:
            warnings.append(
                f"Tour dated {date} was skipped: tours can only be dated from {TOUR_PLAN_MIN_DATE} up to {max_date}."
            )
            continue
        usable.append((date, journeys))
    if len(usable) == 0:
        raise TourPlanError("None of the dates in this tour plan can be claimed.")

    purposes = _split_purposes(jget(main, "purpose"), len(usable))
    stamp = now_ms()

    tours: list[TourData] = []
    for tour_idx, (_, journeys) in enumerate(usable):
        first, *rest = journeys
        start = _local_midnight(first["depDate"])

        details: dict[str, Any] = {
            "startDate": _date_json(start),
            "startTime": first["depTime"],
            "endDate": _date_json(_local_midnight(first["arrDate"])),
            "endTime": first["arrTime"],
            "modeOfTravel": first["mode"],
            "personalVehicleType": first["vehicle"],
            "distance": first["distance"],
            "from": first["from_"],
            "to": first["to"],
            "purpose": purposes[tour_idx],
            "includeTravelCosts": first["includeTravelCosts"],
            # the plan has no bills: amounts are entered by the employee
            "amountDetails": {},
            "tourLegs": [
                {
                    "id": f"leg-{stamp}-{tour_idx}-{leg_idx}",
                    "departureDate": leg["depDate"],
                    "departureTime": leg["depTime"],
                    "from": leg["from_"],
                    "arrivalDate": leg["arrDate"],
                    "arrivalTime": leg["arrTime"],
                    "to": leg["to"],
                    "modeOfTravel": leg["mode"],
                    "personalVehicleType": leg["vehicle"],
                    "distance": leg["distance"] if leg["distance"] > 0 else "",
                    "includeTravelCosts": leg["includeTravelCosts"],
                    "attendanceId": None,
                    "amountDetails": {},
                }
                for leg_idx, leg in enumerate(rest)
            ],
            # the employee ends each tour once its details are checked
            "tourEnd": False,
            "subTotal": 0,
            "attendanceId": 0,
        }
        # tours after the first stay inside the month of the first one, as "Add New Tour" does
        if tour_idx > 0:
            y, m, _d = start
            month_end = js_local_date(y, m, 0)
            details["allowedDateBounds"] = {
                "minStart": _format_ymd(*js_local_date(y, m - 1, 1)),
                "maxStart": _format_ymd(*month_end),
                "maxEnd": _format_ymd(*js_local_date(month_end[0], month_end[1] - 1, month_end[2] + 1)),
            }

        tours.append(
            {
                "id": f"tour-{stamp}-{tour_idx}",
                "tourDetails": details,  # type: ignore[typeddict-item]
                "accommodationDetails": {"entries": [], "totalAmount": 0},
                "daAllowanceDetails": {"entries": [], "subTotal": 0, "approvingAuthority": "", "uploadedFile": None},
                "declarationAccepted": False,
                "isExpanded": tour_idx == 0,
            }
        )

    modeless = len(
        [
            t
            for t in tours
            if not js_truthy(t["tourDetails"]["modeOfTravel"])
            or any(not js_truthy(leg["modeOfTravel"]) for leg in t["tourDetails"]["tourLegs"])
        ]
    )
    if modeless > 0:
        warnings.append("Some journeys have no mode of travel. Please select it before ending the tour.")

    return {"tours": tours, "warnings": warnings}
