"""Brain of the Claim Assistant on the Within Jurisdiction claim form.

Port of Tour_React src/lib/claimChatbot/withinAssistant.ts. Unlike the Raise Claim assistant, the claim lives in the
form (the list of tours), and the assistant reads and writes it through a bridge. The employee uploads their monthly
Tour Plan; every date becomes a tour (see within_tour_plan.py). What the plan cannot give (distance for a personal
vehicle, a missing mode...) is asked here, one question at a time. The AI model reads what the employee types and
writes every reply: it never works out an amount (DA and totals are calculated by the form), and every edit is
validated here first.

The tours are the form's TourData dicts (within_form.py), never changed in place: every change returns new dicts.
Reading a field of a tour, leg or entry that is null throws TypeError, as the TypeScript did.
"""

from __future__ import annotations

import math
import re
from typing import Any, Final, NotRequired, TypedDict

from .js_compat import (
    MAX_TIME_MS,
    UNDEFINED,
    en_in_money,
    is_nullish,
    jget,
    js_is_finite,
    js_len,
    js_local_date,
    js_number,
    js_or,
    js_str,
    js_trim,
    js_truthy,
    local_ymd,
    now_ms,
    nullish,
    opt_trim,
    v8_date_parse,
)
from .ocr_mapping import to_time
from .within_form import TourData
from .within_tour_plan import WITHIN_TRAVEL_MODES, map_vehicle_type, map_within_travel_mode

Tour = TourData

# ─── Small helpers ────────────────────────────────────────────────────────────

_ISO_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
# date-fns en-US short month names ('MMM')
_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _prop(obj: Any, key: str) -> Any:
    """`obj.key`: throws on null / undefined, as JavaScript does; UNDEFINED when there is no such key."""
    if is_nullish(obj):
        raise TypeError(f"Cannot read properties of {'null' if obj is None else 'undefined'} (reading '{key}')")
    return jget(obj, key)


def _at(items: list[Any], index: Any) -> Any:
    """`items[index]` for a number index: UNDEFINED unless it is a whole number inside the list."""
    if isinstance(index, bool) or not isinstance(index, int | float):
        return UNDEFINED
    if index != index or index in (math.inf, -math.inf) or index != int(index):
        return UNDEFINED
    i = int(index)
    return items[i] if 0 <= i < len(items) else UNDEFINED


def _list(value: Any, what: str) -> list[Any]:
    """A value about to be `.map` / `.forEach`ed: anything but a list throws, as `x.map is not a function` did."""
    if not isinstance(value, list):
        raise TypeError(f"{what} is not a function")
    return value


def _length(value: Any) -> Any:
    """`value.length` (for a list or a string; UNDEFINED otherwise)."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str):
        return js_len(value)
    return jget(value, "length")


def _len_of(value: Any) -> Any:
    """`value.length` (throws on null / undefined)."""
    if is_nullish(value):
        raise TypeError("Cannot read properties of undefined (reading 'length')")
    return _length(value)


def _length_opt(value: Any) -> Any:
    """`value?.length`."""
    return UNDEFINED if is_nullish(value) else _length(value)


def _gt(a: float, b: Any) -> bool:
    """`a > b` for a number a and a length that may be undefined (then false)."""
    return isinstance(b, int | float) and a > b


def _gt0(value: Any) -> bool:
    """`value > 0` for a number or undefined."""
    return isinstance(value, int | float) and value > 0


def _strict_eq(a: Any, b: Any) -> bool:
    """`a === b` for the plain values a tour holds."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return a is b


def _js_slice(value: str, end: int) -> str:
    """`value.slice(0, end)`: counted in UTF-16 units (a character outside the BMP may be cut in half)."""
    if js_len(value) <= end:
        return value
    units = value.encode("utf-16-le", "surrogatepass")[: 2 * end]
    return units.decode("utf-16-le", "surrogatepass")


def _has_two_letters(value: str) -> bool:
    """`/\\p{L}{2}/u.test(value)`: two letters in a row."""
    run = 0
    for ch in value:
        run = run + 1 if ch.isalpha() else 0
        if run >= 2:
            return True
    return False


def _format_ymd(y: int, m: int, d: int) -> str:
    """date-fns format(date, 'yyyy-MM-dd'): 'yyyy' is the year of the era."""
    return f"{_era_year(y)}-{m:02d}-{d:02d}"


def _era_year(y: int) -> str:
    return str(y if y > 0 else 1 - y).rjust(4, "0")


def _date_ms(value: Any) -> float | None:
    """`new Date(value).getTime()` (None for an Invalid Date): text through the browser's parser, a number as ms."""
    if isinstance(value, str):
        return v8_date_parse(value)
    if isinstance(value, bool | int | float):
        ms = js_number(value)
        if not js_is_finite(ms) or abs(ms) > MAX_TIME_MS:
            return None
        return float(math.trunc(ms))
    return v8_date_parse(js_str(value))


def iso_of(value: Any) -> str:
    """withinAssistant.ts:isoOf: any date the form holds (a Date as JSON gives it, 'YYYY-MM-DD', or an ISO string)
    -> local 'YYYY-MM-DD', or ''. A 'YYYY-MM-DD' text is returned as it is, even '2026-02-30'."""
    if not js_truthy(value):
        return ""
    if isinstance(value, str) and _ISO_DAY.match(value):
        return value
    ms = _date_ms(value)
    if ms is None:
        return ""
    return _format_ymd(*local_ymd(ms))


def _local_day(iso: str) -> tuple[int, int, int]:
    """`new Date(y, m - 1, d)` of an isoOf text (an impossible day rolls over: 30 Feb -> 2 Mar)."""
    y, m, d = (js_number(x) for x in iso.split("-"))
    return js_local_date(int(y), int(m) - 1, int(d))


def _show_date(value: Any) -> str:
    """showDate: date-fns 'dd MMM yyyy' ('01 Sep 2026'), or '—'."""
    iso = iso_of(value)
    if not iso:
        return "—"
    y, m, d = _local_day(iso)
    return f"{d:02d} {_MONTHS[m - 1]} {_era_year(y)}"


def _short_date(value: Any) -> str:
    """shortDate: date-fns 'dd MMM' ('01 Sep'), or ''."""
    iso = iso_of(value)
    if not iso:
        return ""
    _, m, d = _local_day(iso)
    return f"{d:02d} {_MONTHS[m - 1]}"


def _place(v: Any) -> str:
    """place: the text before the first comma ("Sector 62, Noida" -> "Sector 62")."""
    text = js_or(v, "?")
    if not isinstance(text, str):
        raise TypeError("split is not a function")
    return js_trim(text.split(",")[0])


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _num(v: Any) -> float:
    """num: `Number(v) || 0`."""
    n = js_number(v)
    return n if js_truthy(n) else 0


# ─── A tour as a list of journeys (leg 0 is the main journey) ─────────────────


# leg: 0 = the main journey, 1.. = tour legs ("from" is a Python keyword, hence the functional syntax)
Journey = TypedDict(
    "Journey",
    {
        "tourNo": Any,
        "leg": int,
        "from": str,
        "to": str,
        "depDate": str,
        "depTime": str,
        "arrDate": str,
        "arrTime": str,
        "mode": str,
        "vehicle": str,
        "distance": float,
    },
)


def journeys_of(tour: Tour, tour_no: Any) -> list[Journey]:
    """withinAssistant.ts:journeysOf: the main journey (leg 0) and the legs (1..), as the questions see them."""
    t = _prop(tour, "tourDetails")
    main: Journey = {
        "tourNo": tour_no,
        "leg": 0,
        "from": js_or(_prop(t, "from"), ""),
        "to": js_or(_prop(t, "to"), ""),
        "depDate": iso_of(_prop(t, "startDate")),
        "depTime": js_or(_prop(t, "startTime"), ""),
        "arrDate": iso_of(_prop(t, "endDate")),
        "arrTime": js_or(_prop(t, "endTime"), ""),
        "mode": js_or(_prop(t, "modeOfTravel"), ""),
        "vehicle": js_or(_prop(t, "personalVehicleType"), ""),
        "distance": _num(_prop(t, "distance")),
    }
    legs: list[Journey] = [
        {
            "tourNo": tour_no,
            "leg": i + 1,
            "from": js_or(_prop(leg, "from"), ""),
            "to": js_or(_prop(leg, "to"), ""),
            "depDate": iso_of(_prop(leg, "departureDate")),
            "depTime": js_or(_prop(leg, "departureTime"), ""),
            "arrDate": iso_of(_prop(leg, "arrivalDate")),
            "arrTime": js_or(_prop(leg, "arrivalTime"), ""),
            "mode": js_or(_prop(leg, "modeOfTravel"), ""),
            "vehicle": js_or(_prop(leg, "personalVehicleType"), ""),
            "distance": _num(_prop(leg, "distance")),
        }
        for i, leg in enumerate(_list(nullish(_prop(t, "tourLegs"), []), "tourLegs.map"))
    ]
    return [main, *legs]


# Journey fields set_journey_field can change, with the main-journey and leg keys they write
_MAIN_KEYS: Final = {
    "from": "from",
    "to": "to",
    "depTime": "startTime",
    "arrTime": "endTime",
    "mode": "modeOfTravel",
    "vehicle": "personalVehicleType",
}
_LEG_KEYS: Final = {
    "from": "from",
    "to": "to",
    "depTime": "departureTime",
    "arrTime": "arrivalTime",
    "mode": "modeOfTravel",
    "vehicle": "personalVehicleType",
}


def _spread(value: Any) -> dict[str, Any]:
    """`{ ...value }`: a copy of an object; nothing for null, undefined or a plain value."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list | str):
        return {str(i): v for i, v in enumerate(value)}
    return {}


def set_journey_field(tour: Tour, leg: Any, field: str, value: Any) -> Tour:
    """withinAssistant.ts:setJourneyField: a copy of the tour with one field of one journey changed (the tour it
    came from is never touched). Setting the distance also turns "Add Travel Costs" on (the distance box sits in it).
    """
    t = _spread(_prop(tour, "tourDetails"))
    if _strict_eq(leg, 0):
        if field in _MAIN_KEYS:
            t[_MAIN_KEYS[field]] = js_str(value)
        if field == "distance":
            t["distance"] = js_number(value)
            # the distance box sits inside "Add Travel Costs", so it has to be on
            t["includeTravelCosts"] = True
    else:
        legs = _list(_prop(t, "tourLegs"), "tourLegs.map")

        def changed(i: int, item: Any) -> Any:
            if not _strict_eq(i, js_number(leg) - 1):
                return item
            nxt = _spread(item)
            if field in _LEG_KEYS:
                nxt[_LEG_KEYS[field]] = js_str(value)
            if field == "distance":
                nxt["distance"] = js_number(value)
                nxt["includeTravelCosts"] = True
            return nxt

        t["tourLegs"] = [changed(i, item) for i, item in enumerate(legs)]
    return {**tour, "tourDetails": t}  # type: ignore[typeddict-item]


def _journey_label(j: Any) -> str:
    leg = f", leg {js_str(j['leg'])}" if j["leg"] > 0 else ""
    day = f" ({_short_date(j['depDate'])})" if j["depDate"] else ""
    return f"Tour {js_str(j['tourNo'])}{leg}{day}"


def _route(j: Any) -> str:
    return f"{_place(j['from'])} → {_place(j['to'])}"


# ─── What the form has ────────────────────────────────────────────────────────


def _filled(value: Any) -> bool:
    """`!!value?.trim()`."""
    return js_truthy(opt_trim(value))


def has_tour_data(tours: list[Tour]) -> bool:
    """withinAssistant.ts:hasTourData: has the employee put anything into the tours (the form always opens with one
    empty tour)? DA entries do not count here (the form's own hasMeaningfulData counts them)."""
    for t in tours:
        d = _prop(t, "tourDetails")
        if (
            js_truthy(_prop(d, "startDate"))
            or js_truthy(_prop(d, "endDate"))
            or _filled(_prop(d, "from"))
            or _filled(_prop(d, "to"))
            or _filled(_prop(d, "purpose"))
            or _filled(_prop(d, "modeOfTravel"))
            or _gt0(_len_of(_prop(d, "tourLegs")))
            or _gt0(nullish(_length_opt(jget(_prop(t, "accommodationDetails"), "entries")), 0))
        ):
            return True
    return False


def blank_tour() -> Tour:
    """withinAssistant.ts:blankTour: an empty tour, as the form opens with (startDate / endDate undefined: left out)."""
    return {
        "id": f"tour-{now_ms()}",
        "tourDetails": {  # type: ignore[typeddict-item]
            "startTime": "",
            "endTime": "",
            "modeOfTravel": "",
            "from": "",
            "to": "",
            "purpose": "",
            "includeTravelCosts": False,
            "tourLegs": [],
            "tourEnd": False,
            "subTotal": 0,
        },
        "accommodationDetails": {"entries": [], "totalAmount": 0},
        "declarationAccepted": False,
        "isExpanded": True,
    }


def _trim(value: Any) -> str:
    """`value.trim()` on a journey field (a value that is not text throws)."""
    if not isinstance(value, str):
        raise TypeError("trim is not a function")
    return js_trim(value)


def tour_discrepancies(tours: list[Tour]) -> list[str]:
    """withinAssistant.ts:tourDiscrepancies: everything that stops the open tours from being ended, one line each.

    The rules are the form's own End Tour check (TourForm: isTourReadyToEndUI + no same-minute journey), so "no
    lines" means the End Tour box can be ticked.
    """
    out: list[str] = []
    for i, tour in enumerate(tours):
        details = _prop(tour, "tourDetails")
        if js_truthy(_prop(details, "tourEnd")):
            continue
        no = i + 1
        if not _filled(_prop(details, "purpose")):
            out.append(f"Tour {no}: the purpose is missing")
        for j in journeys_of(tour, no):
            leg = f", leg {j['leg']}" if j["leg"] > 0 else ""
            where = f"Tour {no}{leg}{f' ({_route(j)})' if js_truthy(j['from']) or js_truthy(j['to']) else ''}"
            pv = j["mode"] == "Personal Vehicle"
            checks = [
                (not j["depDate"], "start date"),
                (not js_truthy(j["depTime"]), "start time"),
                (not _trim(j["from"]), "from place"),
                (not js_truthy(j["mode"]), "mode of travel"),
                (not j["arrDate"], "end date"),
                (not js_truthy(j["arrTime"]), "end time"),
                (not _trim(j["to"]), "to place"),
                (pv and not js_truthy(j["vehicle"]), "vehicle type"),
                (pv and not (j["distance"] > 0), "distance (km)"),
            ]
            missing = [label for flag, label in checks if flag]
            if len(missing) > 0:
                out.append(f"{where}: {', '.join(missing)} missing")
            elif j["depDate"] == j["arrDate"] and _strict_eq(j["depTime"], j["arrTime"]):
                out.append(f"{where}: the start and end time are the same")
    return out


def tour_ready_to_end(tour: Tour) -> bool:
    """withinAssistant.ts:tourReadyToEnd: no discrepancies once End Tour is unticked."""
    unended: Any = {**tour, "tourDetails": {**_spread(_prop(tour, "tourDetails")), "tourEnd": False}}
    return len(tour_discrepancies([unended])) == 0


# ─── Gaps: what the employee is asked, one at a time ──────────────────────────


class Gap(TypedDict):
    tourId: Any
    tourNo: int
    leg: int
    # 'purpose' | 'from' | 'to' | 'depTime' | 'arrTime' | 'mode' | 'vehicle' | 'distance'
    kind: str
    question: str


def find_gaps(tours: list[Tour]) -> list[Gap]:
    """withinAssistant.ts:findGaps: by tour, the purpose, then per journey (main first). Ended tours are skipped and
    dates are never asked (a missing date shows only in tour_discrepancies)."""
    gaps: list[Gap] = []
    for i, tour in enumerate(tours):
        details = _prop(tour, "tourDetails")
        if js_truthy(_prop(details, "tourEnd")):
            continue
        tour_no = i + 1
        tour_id = _prop(tour, "id")

        def gap(leg: int, kind: str, question: str, tour_id: Any = tour_id, tour_no: int = tour_no) -> Gap:
            return {"tourId": tour_id, "tourNo": tour_no, "leg": leg, "kind": kind, "question": question}

        if not _filled(_prop(details, "purpose")):
            gaps.append(gap(0, "purpose", f"What is the purpose of Tour {tour_no}?"))
        for j in journeys_of(tour, tour_no):
            label = _journey_label(j)
            leg = j["leg"]
            if not _trim(j["from"]):
                gaps.append(gap(leg, "from", f"Where did {label} start from?"))
            if not _trim(j["to"]):
                gaps.append(gap(leg, "to", f"Where did {label} end?"))
            if not js_truthy(j["depTime"]):
                gaps.append(gap(leg, "depTime", f"What time did {label} ({_route(j)}) depart? For example 09:30."))
            if not js_truthy(j["arrTime"]):
                gaps.append(gap(leg, "arrTime", f"What time did {label} ({_route(j)}) arrive? For example 11:00."))
            if not js_truthy(j["mode"]):
                gaps.append(gap(leg, "mode", f"How did you travel for {label} ({_route(j)})?"))
            if j["mode"] == "Personal Vehicle" and not js_truthy(j["vehicle"]):
                gaps.append(
                    gap(
                        leg,
                        "vehicle",
                        f"Was the personal vehicle for {label} ({_route(j)}) a two-wheeler or a four-wheeler?",
                    )
                )
            if j["mode"] == "Personal Vehicle" and j["distance"] <= 0:
                gaps.append(gap(leg, "distance", f"How many km was {label} ({_route(j)})?"))
    return gaps


class AnswerResult(TypedDict):
    tours: list[Tour]
    # set when the answer could not be used: what to tell the employee
    error: NotRequired[str]


MAX_DISTANCE_KM: Final = 2000

_FIRST_NUMBER = re.compile(r"([0-9]+(?:\.[0-9]+)?)")


def answer_gap(tours: list[Tour], gap: Gap, text: str) -> AnswerResult:
    """withinAssistant.ts:answerGap: reads the employee's reply to a gap and writes it into the tours (without
    touching the old ones). A gap of an unknown kind gives UNDEFINED, as the TypeScript switch fell through."""
    # gap.tourId is read for each tour (so no tours means no error even for a broken gap)
    index = next((i for i, t in enumerate(tours) if _strict_eq(_prop(t, "id"), _prop(gap, "tourId"))), -1)
    if index < 0:
        return {"tours": tours, "error": "That tour is no longer on the form."}
    tour = tours[index]

    def put(nxt: Tour) -> AnswerResult:
        return {"tours": [nxt if i == index else t for i, t in enumerate(tours)]}

    value = _trim(text)
    kind = _prop(gap, "kind")
    leg = _prop(gap, "leg")

    if kind == "purpose":
        if js_len(value) < 3:
            return {"tours": tours, "error": "Please describe the purpose in a few words."}
        return put({**tour, "tourDetails": {**_spread(_prop(tour, "tourDetails")), "purpose": _js_slice(value, 500)}})  # type: ignore[typeddict-item]
    if kind in ("from", "to"):
        if not _has_two_letters(value) or js_len(value) > 80:
            return {"tours": tours, "error": 'Please give a place name, like "Gurugram".'}
        return put(set_journey_field(tour, leg, kind, value))
    if kind in ("depTime", "arrTime"):
        time = to_time(value)
        if not time:
            return {"tours": tours, "error": "I couldn't read that time. Please give it like 09:30 or 4:15 pm."}
        return put(set_journey_field(tour, leg, kind, time))
    if kind == "mode":
        mode = map_within_travel_mode(value)
        if not mode:
            return {
                "tours": tours,
                "error": f"I didn't recognise that. Choose one of: {', '.join(WITHIN_TRAVEL_MODES)}.",
            }
        nxt = set_journey_field(tour, leg, "mode", mode)
        # "personal vehicle, two wheeler" in one go
        vehicle = map_vehicle_type(value) if mode == "Personal Vehicle" else ""
        if vehicle:
            nxt = set_journey_field(nxt, leg, "vehicle", vehicle)
        return put(nxt)
    if kind == "vehicle":
        vehicle = map_vehicle_type(value)
        if not vehicle:
            return {"tours": tours, "error": 'Please say "two wheeler" or "four wheeler".'}
        return put(set_journey_field(tour, leg, "vehicle", vehicle))
    if kind == "distance":
        m = _FIRST_NUMBER.search(value.replace(",", ""))
        km = js_number(m[1]) if m else math.nan
        if not js_is_finite(km) or km <= 0:
            return {"tours": tours, "error": 'Please give the distance in km, like "24".'}
        if km > MAX_DISTANCE_KM:
            return {
                "tours": tours,
                "error": f"{js_str(km)} km is more than a within-jurisdiction tour can be. Please check and give the "
                "distance in km.",
            }
        return put(set_journey_field(tour, leg, "distance", km))
    return UNDEFINED  # type: ignore[return-value]


# ─── Typed corrections from the chat model ────────────────────────────────────


class WithinEdit(TypedDict):
    tour: int
    # 0 = main journey
    leg: int
    # 'purpose' | 'from' | 'to' | 'departureTime' | 'arrivalTime' | 'mode' | 'vehicleType' | 'distanceKm'
    field: str
    value: str | int | float


class ApplyResult(TypedDict):
    tours: list[Tour]
    applied: list[str]
    rejected: list[str]


def apply_within_edits(tours_in: list[Tour], edits: list[WithinEdit]) -> ApplyResult:
    """withinAssistant.ts:applyWithinEdits: applied one after the other (a later edit sees the earlier ones).

    Changing the mode away from Personal Vehicle keeps the vehicle type and distance (as before).
    """
    tours = tours_in
    applied: list[str] = []
    rejected: list[str] = []

    for edit in edits:
        edit_tour = _prop(edit, "tour")
        index = js_number(edit_tour) - 1
        leg_n = js_number(_prop(edit, "leg"))
        leg = leg_n if js_truthy(leg_n) else 0
        tour = _at(tours, index)
        where = f"Tour {js_str(edit_tour)}{f', leg {js_str(leg)}' if leg > 0 else ''}"
        if not js_truthy(tour):
            rejected.append(f"Tour {js_str(edit_tour)} doesn't exist.")
            continue
        details = _prop(tour, "tourDetails")
        if js_truthy(_prop(details, "tourEnd")):
            rejected.append(f"{where} is already ended. Untick End Tour on the form to change it.")
            continue
        if leg < 0 or _gt(leg, _len_of(_prop(details, "tourLegs"))):
            rejected.append(f"{where} doesn't exist.")
            continue
        value = js_trim(js_str(nullish(_prop(edit, "value"), "")))
        field = _prop(edit, "field")
        idx = int(index)

        def replace(nxt: Tour, note: str) -> None:
            nonlocal tours
            tours = [nxt if i == idx else t for i, t in enumerate(tours)]  # noqa: B023 (used at once)
            applied.append(f"{where}: {note}")  # noqa: B023 (used at once)

        if field == "purpose":
            if js_len(value) < 3:
                rejected.append(f"{where}: the purpose is too short.")
            else:
                replace({**tour, "tourDetails": {**_spread(details), "purpose": _js_slice(value, 500)}}, "purpose set")
        elif field in ("from", "to"):
            if not _has_two_letters(value) or js_len(value) > 80:
                rejected.append(f'{where}: "{value}" isn\'t a place name.')
            else:
                replace(set_journey_field(tour, leg, field, value), f"{'from' if field == 'from' else 'to'} {value}")
        elif field in ("departureTime", "arrivalTime"):
            time = to_time(value)
            if not time:
                rejected.append(f'{where}: I couldn\'t read the time "{value}".')
            else:
                replace(
                    set_journey_field(tour, leg, "depTime" if field == "departureTime" else "arrTime", time),
                    f"{'departure' if field == 'departureTime' else 'arrival'} {time}",
                )
        elif field == "mode":
            mode = map_within_travel_mode(value)
            if not mode:
                rejected.append(f'{where}: "{value}" isn\'t a travel mode I know.')
            else:
                replace(set_journey_field(tour, leg, "mode", mode), f"mode {mode}")
        elif field == "vehicleType":
            vehicle = map_vehicle_type(value)
            journey_mode = _prop(_at(journeys_of(tour, edit_tour), leg), "mode")
            if not vehicle:
                rejected.append(f"{where}: please say two wheeler or four wheeler.")
            elif journey_mode != "Personal Vehicle":
                rejected.append(f"{where}: a vehicle type only applies to a personal vehicle.")
            else:
                replace(set_journey_field(tour, leg, "vehicle", vehicle), f"vehicle {vehicle}")
        elif field == "distanceKm":
            km = js_number(value.replace(",", ""))
            if not js_is_finite(km) or km <= 0 or km > MAX_DISTANCE_KM:
                rejected.append(f'{where}: "{value}" isn\'t a usable distance.')
            else:
                replace(set_journey_field(tour, leg, "distance", km), f"distance {js_str(km)} km")
        else:
            rejected.append(f'{where}: I can\'t change "{js_str(field)}" in the chat. Please use the form.')
    return {"tours": tours, "applied": applied, "rejected": rejected}


# ─── Showing the claim ────────────────────────────────────────────────────────


def tour_summary_rows(tours: list[Tour]) -> list[dict[str, str]]:
    """withinAssistant.ts:tourSummaryRows: one "Tour 1 · 01 Oct" / "Delhi → Ghaziabad (mode)" row per tour, for the
    inset box in a message."""
    rows: list[dict[str, str]] = []
    for i, tour in enumerate(tours):
        js = journeys_of(tour, i + 1)
        text = ", then ".join(_route(j) + _mode_text(j) for j in js)
        details = _prop(tour, "tourDetails")
        rows.append(
            {
                "label": f"Tour {i + 1} · {_short_date(_prop(details, 'startDate')) or '—'}",
                "value": f"{text}{' · ended' if js_truthy(_prop(details, 'tourEnd')) else ''}",
            }
        )
    return rows


def _mode_text(j: Journey) -> str:
    if not js_truthy(j["mode"]):
        return ""
    vehicle = f", {js_str(j['vehicle'])}" if js_truthy(j["vehicle"]) else ""
    return f" ({js_str(j['mode'])}{vehicle})"


class ClaimTotals(TypedDict):
    transport: float
    accommodation: float
    da: float
    total: float


def _for_each(value: Any) -> list[Any]:
    """`value?.forEach(...)`: nothing for null / undefined; anything but a list throws."""
    if is_nullish(value):
        return []
    return _list(value, "forEach")


def claim_totals(tours: list[Tour]) -> ClaimTotals:
    """withinAssistant.ts:claimTotals: the sums of what the tours hold now (transport claimed, accommodation claimed
    with tax, DA of applicable days). The form fills these values in (DA only for ended tours); nothing is worked out
    here."""
    transport: float = 0
    accommodation: float = 0
    da: float = 0

    def claimed(a: Any) -> float:
        return _num(jget(a, "totalClaimedAmount")) or _num(jget(a, "claimedAmount"))

    for tour in tours:
        d = _prop(tour, "tourDetails")
        transport += claimed(_prop(d, "amountDetails"))
        for leg in _list(_prop(d, "tourLegs"), "tourLegs.forEach"):
            transport += claimed(_prop(leg, "amountDetails"))
        for e in _for_each(jget(_prop(tour, "accommodationDetails"), "entries")):
            invoice = _prop(e, "invoice")
            accommodation += _num(jget(invoice, "claimedAmountWithTax")) or _num(jget(invoice, "claimedAmount"))
        for e in _for_each(jget(_prop(tour, "daAllowanceDetails"), "entries")):
            if js_truthy(_prop(e, "daApplicable")):
                da += _num(_prop(e, "amount"))
    return {"transport": transport, "accommodation": accommodation, "da": da, "total": transport + accommodation + da}


def _inr(n: float) -> str:
    return f"₹{en_in_money(n)}"


def totals_text(tours: list[Tour]) -> str:
    """withinAssistant.ts:totalsText: the claim's totals in one line, and how many tours have their DA so far."""
    t = claim_totals(tours)
    ended = len([x for x in tours if js_truthy(_prop(_prop(x, "tourDetails"), "tourEnd"))])
    return (
        f"Transport {_inr(t['transport'])} · Accommodation {_inr(t['accommodation'])} · DA {_inr(t['da'])}"
        f" · Total {_inr(t['total'])}"
    ) + (f"\nDA is worked out only for ended tours ({ended} of {len(tours)} so far)." if ended < len(tours) else "")


class ProgressContext(TypedDict):
    selfApprove: bool
    approverChosen: bool


class Progress(TypedDict):
    text: str
    ready: bool


def progress_text(tours: list[Tour], ctx: ProgressContext) -> Progress:
    """withinAssistant.ts:progressText: what is still to do before the claim can be submitted (the form's check()
    is not asked here)."""
    if not has_tour_data(tours):
        return {"text": "There are no tours yet. Upload your monthly tour plan and I will create them.", "ready": False}
    lines: list[str] = []
    gaps = find_gaps(tours)
    if len(gaps) > 0:
        tour_names = dict.fromkeys(f"Tour {g['tourNo']}" for g in gaps)
        lines.append(f"• {_plural(len(gaps), 'detail')} still missing: {', '.join(tour_names)}.")
    open_nos = [i + 1 for i, t in enumerate(tours) if not js_truthy(_prop(_prop(t, "tourDetails"), "tourEnd"))]
    if len(open_nos) > 0:
        all_ready = len(tour_discrepancies(tours)) == 0
        lines.append(
            f"• {_plural(len(open_nos), 'tour')} not ended yet ({', '.join(str(n) for n in open_nos)}). DA and "
            "accommodation open up once a tour is ended."
            + (
                ' Every open tour can be ended now: say "end all tours".'
                if all_ready
                else " Resolve the missing details first, then I can end them all."
            )
        )
    if not js_truthy(_prop(ctx, "selfApprove")) and not js_truthy(_prop(ctx, "approverChosen")):
        lines.append("• No approving authority selected: choose one at the bottom of the form.")
    ready = len(lines) == 0
    if ready:
        lines.append(
            'Everything needed is filled in. Add hotel stays and bills on the form if you have any, then say "check '
            '& submit".'
        )
    else:
        lines.append("• Add hotel stays and travel bills on the form if you have any.")
    return {"text": lines[0] if ready else "Still to do:\n" + "\n".join(lines), "ready": ready}


WITHIN_DA_RULES: Final = "\n".join(
    [
        "DA is worked out per calendar day of each tour, from the first departure to the last arrival.",
        "The first day counts from departure to 23:59, full days in between count 24 hours, and the last day counts "
        "from 00:00 to arrival.",
        "Hours in a day: up to 6h → 30%, over 6h up to 12h → 70%, over 12h → 100% of your grade’s daily DA rate.",
        "On a day with free company lodging you get 25% of that.",
        "If you were promoted during the month, days before the promotion date use your previous grade’s rate.",
        'DA is calculated once a tour is ended, and you can untick "DA Applicable" for a day.',
    ]
)

# ─── What the AI model needs ──────────────────────────────────────────────────

# How the AI model lists tour edits ("edit" action); apply_within_edits validates every one.
WITHIN_EDIT_FORMAT: Final = (
    '"edits" is a list of {"tour","leg","field","value"}, one per field; add an edit for EVERY value the employee '
    "states.\n"
    "- tour = tour number, leg = 0 for the main journey or the leg number.\n"
    "- fields: purpose, from, to, departureTime, arrivalTime (HH:mm 24-hour), mode (one of "
    f"{', '.join(WITHIN_TRAVEL_MODES)}), vehicleType (Two Wheeler or Four Wheeler, only for Personal Vehicle), "
    "distanceKm (a number).\n"
    "Dates cannot be changed, tours or legs cannot be added or deleted, and amounts, hotel stays and bills are "
    "entered on the form. Ended tours cannot be changed.\n"
    "Never an amount (fare, cost, rupees): a cost is not a distance. Convert times exactly (8 pm = 20:00).\n"
    'Shape: {"action":"edit","args":{},"edits":[{"tour":<number>,"leg":<number>,"field":"<field>","value":<value>}]}'
)


def read_within_edits(items: list[Any]) -> list[WithinEdit]:
    """withinAssistant.ts:readWithinEdits: the edits the model listed, keeping only well-formed ones (never
    guessed): an object with a numeric `tour`, a text `field` and a value that is not null."""
    return [
        e
        for e in items
        if isinstance(e, dict | list)
        and js_is_finite(js_number(jget(e, "tour")))
        and isinstance(jget(e, "field"), str)
        and not is_nullish(jget(e, "value"))
    ]


def snapshot(tours: list[Tour]) -> str:
    """withinAssistant.ts:snapshot: the claim in a few lines for the model (CURRENT TOURS)."""
    if not has_tour_data(tours):
        return "No tours yet."
    blocks: list[str] = []
    for i, tour in enumerate(tours):
        d = _prop(tour, "tourDetails")
        purpose = js_or(opt_trim(_prop(d, "purpose")), "-")
        head = (
            f"Tour {i + 1} ({_show_date(_prop(d, 'startDate'))}){' [ended]' if js_truthy(_prop(d, 'tourEnd')) else ''}"
            f" purpose: {js_str(purpose)}"
        )
        legs = "\n".join(
            f"  leg {j['leg']}: {js_str(js_or(j['from'], '-'))} → {js_str(js_or(j['to'], '-'))},"
            f" {js_str(js_or(j['depTime'], '-'))} to {js_str(js_or(j['arrTime'], '-'))},"
            f" mode {js_str(js_or(j['mode'], '-'))}"
            + (
                f", vehicle {js_str(js_or(j['vehicle'], '-'))}, "
                + (f"{js_str(j['distance'])} km" if j["distance"] > 0 else "distance -")
                if j["mode"] == "Personal Vehicle"
                else ""
            )
            for j in journeys_of(tour, i + 1)
        )
        blocks.append(f"{head}\n{legs}")
    return "\n".join(blocks)


__all__ = [
    "MAX_DISTANCE_KM",
    "WITHIN_DA_RULES",
    "WITHIN_EDIT_FORMAT",
    "AnswerResult",
    "ApplyResult",
    "ClaimTotals",
    "Gap",
    "Journey",
    "Progress",
    "ProgressContext",
    "WithinEdit",
    "answer_gap",
    "apply_within_edits",
    "blank_tour",
    "claim_totals",
    "find_gaps",
    "has_tour_data",
    "iso_of",
    "journeys_of",
    "progress_text",
    "read_within_edits",
    "set_journey_field",
    "snapshot",
    "totals_text",
    "tour_discrepancies",
    "tour_ready_to_end",
    "tour_summary_rows",
]
