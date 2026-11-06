"""Brain of the Raise Claim assistant (outside jurisdiction).

Port of Tour_React src/lib/claimChatbot/claimAssistant.ts. The assistant builds the claim in the chat first (the
claim state lives in the chat, not in the form) and hands it to the Raise Claim page when the employee asks for it.
The AI model reads what the employee types and writes every reply; the edits it lists are validated here
(apply_edits) before they touch the claim. It never works out an amount: DA and totals are calculated by the form.

The claim state and its rows are plain dicts with the TypeScript field names (see form_rows.py). Row ids come from
the clock and random numbers in js_compat (replace them with js_compat.use_runtime for fixed ids).
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Callable
from typing import Any, Final, Literal, NotRequired, TypedDict

from .form_rows import AccommodationRow, LeaveRow, TransportationRow
from .js_compat import (
    UNDEFINED,
    en_in_date,
    en_in_money,
    is_nullish,
    jget,
    js_fraction_to_radix,
    js_int,
    js_is_finite,
    js_local_date,
    js_lookup,
    js_number,
    js_number_to_string,
    js_or,
    js_parse_float,
    js_random,
    js_str,
    js_trim,
    js_truthy,
    local_ymd,
    now_ms,
    nullish,
    utc_iso_string,
    v8_date_parse,
)
from .ocr_mapping import TRAVEL_MODES, accommodation_type_fields, map_travel_mode, to_iso_date, to_time
from .row_order import journeys_in_date_order, stays_in_date_order


class ClaimFormState(TypedDict):
    transportationRows: list[TransportationRow]
    accommodationRows: list[AccommodationRow]
    leaveRows: list[LeaveRow]
    includeAccommodation: bool
    includeLeave: bool


class TourContext(TypedDict):
    tourId: str | int
    source: NotRequired[str]
    destination: NotRequired[str]
    startDate: NotRequired[str]
    endDate: NotRequired[str]
    purpose: NotRequired[str]


class TourRules(TypedDict):
    """Limits from the approved tour that the form itself enforces (see TransportationSection)."""

    allowAir: bool
    # journeys and leave must fall within [minDate, maxDate] (YYYY-MM-DD; maxDate already has the form's +1 day)
    minDate: NotRequired[str]
    maxDate: NotRequired[str]
    # the existing journeys (1-based) that these edits may not change: a ride being asked about is a new journey
    lockedJourneys: NotRequired[list[int]]


def _pad2(n: int) -> str:
    return str(n).rjust(2, "0")


def tour_rules_from(claim_data: Any) -> TourRules:
    """claimAssistant.ts:tourRulesFrom: same sources as TransportationSection's minISO/maxISO and its air rule."""
    plans = js_or(jget(claim_data, "tentativeTourPlans"), js_or(jget(claim_data, "tentativeTourPlanList"), []))
    start = nullish(
        jget(jget(claim_data, "trDetails"), "tentativeStartDate"),
        jget(jget(claim_data, "employeeDetails"), "tentativeStartDate"),
    )
    dates = sorted(d for d in (to_iso_date(v) for v in [start, *(jget(p, "date") for p in plans)]) if d)
    allow_air = any(js_str(nullish(jget(p, "travelMode"), "")).lower() == "air" for p in plans)
    if len(dates) == 0:
        return {"allowAir": allow_air}
    y, m, d = (int(js_number(x)) for x in dates[-1].split("-"))
    max_y, max_m, max_d = js_local_date(y, m - 1, d + 1)
    return {"allowAir": allow_air, "minDate": dates[0], "maxDate": f"{max_y}-{_pad2(max_m)}-{_pad2(max_d)}"}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _inr(n: Any) -> str:
    value = js_number(n)
    if math.isnan(value) or value == 0:
        value = 0.0
    return f"₹{en_in_money(value)}"


def _show_date(iso: Any) -> str:
    """showDate: '05 Oct 2026' (September is 'Sept'), or '—'."""
    if not js_truthy(iso):
        return "—"
    ms = v8_date_parse(f"{to_iso_date(js_str(iso))}T00:00:00")
    if ms is None:
        return "—"
    return en_in_date(*local_ymd(ms))


MODE_LABELS: Final = {
    "air": "Air",
    "rail": "Rail",
    "personal-vehicle": "Personal vehicle",
    "public-transport": "Public transport",
    "official-entitled": "Official vehicle (entitled)",
    "official-beyond": "Official vehicle (beyond entitlement)",
    "official-dedicated": "Official vehicle (dedicated)",
}

ACCOM_LABELS: Final = {
    "hotel": "Hotel",
    "own_arrangement": "Own arrangement",
    "company_free": "Free company lodging",
    "company_paid": "Company lodging (paid)",
}


def _blank(v: Any) -> bool:
    return is_nullish(v) or js_trim(js_str(v)) == "" or v == "select" or v == "Select"


def _at(rows: list[Any], index: Any) -> Any:
    """rows[index] as JavaScript reads it: UNDEFINED outside the list or for a non-integer index."""
    if isinstance(index, bool) or not isinstance(index, int | float) or not js_is_finite(float(index)):
        return UNDEFINED
    if index != int(index) or not 0 <= index < len(rows):
        return UNDEFINED
    return rows[int(index)]


def _route(r: Any) -> str:
    """' (Noida → Pune)' when the row has a place, else ''."""
    if js_truthy(jget(r, "source")) or js_truthy(jget(r, "destination")):
        return f" ({js_str(js_or(jget(r, 'source'), '?'))} → {js_str(js_or(jget(r, 'destination'), '?'))})"
    return ""


def _in_city(r: Any) -> str:
    return f" ({js_str(jget(r, 'location'))})" if js_truthy(jget(r, "location")) else ""


def is_empty_transport(r: Any) -> bool:
    """claimAssistant.ts:isEmptyTransport: a transport row the employee has not started (the form opens with one)."""
    return (
        _blank(jget(r, "source"))
        and _blank(jget(r, "destination"))
        and _blank(jget(r, "DateOfTravelSource"))
        and _blank(jget(r, "DateOfTravelDestination"))
    )


def is_empty_accommodation(r: Any) -> bool:
    """claimAssistant.ts:isEmptyAccommodation."""
    return _blank(jget(r, "location")) and _blank(jget(r, "checkInDate")) and _blank(jget(r, "checkOutDate"))


def is_empty_leave(r: Any) -> bool:
    """claimAssistant.ts:isEmptyLeave."""
    return _blank(jget(r, "startDate")) and _blank(jget(r, "endDate"))


def _random_suffix() -> str:
    """Math.random().toString(36).slice(2, 7)"""
    return js_fraction_to_radix(js_random(), 36)[2:7]


def new_transport_row(prev: Any = None) -> TransportationRow:
    """claimAssistant.ts:newTransportRow: a blank journey; a new leg starts where the previous one ended."""
    prev = UNDEFINED if prev is None else prev
    prev_mode = jget(prev, "ModeOfTravel")
    return {
        "id": f"transport-chat-{now_ms()}-{_random_suffix()}",
        "DateOfTravelSource": nullish(jget(prev, "DateOfTravelDestination"), ""),
        "TimeOfTravelSource": "",
        # a new leg starts where the previous one ended, as the form's "Add leg" does
        "source": nullish(jget(prev, "destination"), ""),
        "ModeOfTravel": prev_mode if js_truthy(prev_mode) and prev_mode != "select" else "select",
        "DateOfTravelDestination": "",
        "TimeOfTravelDestiNation": "",
        "destination": "",
        "distanceKm": "",
        "invoiceNo": "",
        # invoiceDate: undefined in the TypeScript (left out, as JSON leaves it out)
        "travelCost": "0",
        "totalBilledAmount": "0",
        "claimedAmount": "0",
        "cgst": "0",
        "igst": "0",
        "sgst": "0",
        "BillSGSTAmount": "0",
        "BillCGSTAmount": "0",
        "BillIGSTAmount": "0",
        "taxStructure": "SGST_CGST",
        "billNotRequired": False,
        "uploadedFiles": [],
        "includeTravelCosts": False,
        "isPaidUsingCard": False,
        "remarks": "",
        "financeRemark": "",
    }


def new_accommodation_row() -> AccommodationRow:
    """claimAssistant.ts:newAccommodationRow: a blank stay."""
    return {
        "id": f"accom-chat-{now_ms()}-{_random_suffix()}",
        "checkInDate": "",
        "checkInTime": "",
        "checkOutDate": "",
        "checkOutTime": "",
        "location": "",
        "classOfCity": "Select",
        "ownArrangement": "No",
        "freeProvidedByCompany": "No",
        "companyPaid": "No",
        "selectedType": "hotel",
        "hotelName": "",
        "billed": "0",
        "tax": "0",
        "totalBilled": "0",
        "claimed": "0",
        "billFiles": [],
        "taxType": "SGST_CGST",
        "taxStructure": "SGST_CGST",
        "invoiceNo": "",
        "invoiceDate": "",
        "ClaimedIGSTAmount": 0,
        "ClaimedSGSTAmount": 0,
        "ClaimedCGSTAmount": 0,
        "BillSGSTAmount": "0",
        "BillCGSTAmount": "0",
        "BillIGSTAmount": "0",
        "remarks": "",
        "financeRemark": "",
        "isPaidUsingCard": False,
    }


def new_leave_row() -> LeaveRow:
    """claimAssistant.ts:newLeaveRow: a blank leave entry."""
    return {"id": f"leave-chat-{now_ms()}", "startDate": "", "endDate": "", "daDeductable": "Yes", "amount": "0"}


def initial_claim_state() -> ClaimFormState:
    """claimAssistant.ts:initialClaimState: an empty claim, with the blank rows the form itself opens with."""
    return {
        "transportationRows": [new_transport_row()],
        "accommodationRows": [new_accommodation_row()],
        "leaveRows": [new_leave_row()],
        "includeAccommodation": False,
        "includeLeave": False,
    }


# ─── Keeping what was already read ────────────────────────────────────────────


def _key(*parts: Any) -> str:
    return "|".join(js_trim(js_str(nullish(p, ""))).lower() for p in parts)


def journey_key(r: Any) -> str:
    """claimAssistant.ts:journeyKey: one journey, whatever format its dates and times came in."""
    return _key(
        jget(r, "source"),
        jget(r, "destination"),
        js_or(to_iso_date(jget(r, "DateOfTravelSource")), jget(r, "DateOfTravelSource")),
        js_or(to_time(jget(r, "TimeOfTravelSource")), jget(r, "TimeOfTravelSource")),
    )


def stay_key(r: Any) -> str:
    """claimAssistant.ts:stayKey: one stay (a saved draft keeps "2026-09-10T00:00:00")."""
    return _key(
        jget(r, "location"),
        js_or(to_iso_date(jget(r, "checkInDate")), jget(r, "checkInDate")),
        js_or(to_iso_date(jget(r, "checkOutDate")), jget(r, "checkOutDate")),
    )


def _holds_saved_files(rows: list[Any]) -> bool:
    """Rows holding a saved draft's files: the server ties them to the rows by position, so they keep their places."""
    return any(
        js_truthy(jget(f, "fileId"))
        for r in rows
        for f in [*nullish(jget(r, "uploadedFiles"), []), *nullish(jget(r, "billFiles"), [])]
    )


class MergeResult(TypedDict):
    state: ClaimFormState
    addedJourneys: int
    addedStays: int
    duplicates: int


def merge_ocr_rows(state: ClaimFormState, mapped: dict[str, Any]) -> MergeResult:
    """claimAssistant.ts:mergeOcrRows.

    Adds rows the OCR read from new bills to the claim, keeping everything already there (the OCR only ever returns
    what is in the files just uploaded). A ticket or stay that is already in the claim is not added twice. Journeys
    and stays stay in date order, except in a claim that holds a saved draft's files: its rows keep their places
    (the server ties the files to them by position) and the new ones go after them. The claim form and the Claim
    Assistant show them sorted by date anyway (row_order.py).
    """
    duplicates = 0

    journeys = [r for r in state["transportationRows"] if not is_empty_transport(r)]
    seen_journeys = {journey_key(r) for r in journeys}
    new_journeys = []
    for r in mapped["transportationRows"]:
        k = journey_key(r)
        if k in seen_journeys:
            duplicates += 1
            continue
        seen_journeys.add(k)
        new_journeys.append(r)
    all_journeys = (
        [*journeys, *journeys_in_date_order(new_journeys)]
        if _holds_saved_files(journeys)
        else journeys_in_date_order([*journeys, *new_journeys])
    )

    stays = (
        [r for r in state["accommodationRows"] if not is_empty_accommodation(r)]
        if state["includeAccommodation"]
        else []
    )
    seen_stays = {stay_key(r) for r in stays}
    new_stays = []
    for r in mapped["accommodationRows"]:
        k = stay_key(r)
        if k in seen_stays:
            duplicates += 1
            continue
        seen_stays.add(k)
        new_stays.append(r)
    all_stays = (
        [*stays, *stays_in_date_order(new_stays)]
        if _holds_saved_files(stays)
        else stays_in_date_order([*stays, *new_stays])
    )

    return {
        "state": {
            **state,
            "transportationRows": all_journeys if all_journeys else state["transportationRows"],
            "accommodationRows": all_stays if all_stays else state["accommodationRows"],
            "includeAccommodation": True if len(all_stays) > 0 else state["includeAccommodation"],
        },
        "addedJourneys": len(new_journeys),
        "addedStays": len(new_stays),
        "duplicates": duplicates,
    }


# ─── What is still missing ────────────────────────────────────────────────────

Slot = Literal[
    "source", "destination", "departure", "arrival", "mode", "location", "checkIn", "checkOut", "hotelName", "invoiceNo"
]


class MissingField(TypedDict):
    section: Literal["transport", "accommodation"]
    row: int  # 1-based, as shown to the employee
    slot: Slot
    # what the assistant asks
    question: str
    # short name, for listing what is missing ("departure time")
    label: str


def find_missing_fields(state: ClaimFormState) -> list[MissingField]:
    """claimAssistant.ts:findMissingFields: the form's own submit checks that the employee can answer in words."""
    missing: list[MissingField] = []
    for i, r in enumerate(state["transportationRows"]):
        row = i + 1
        leg = f"journey {row}{_route(r)}"

        def add(slot: Slot, label: str, question: str, row: int = row) -> None:
            missing.append({"section": "transport", "row": row, "slot": slot, "label": label, "question": question})

        if _blank(jget(r, "source")):
            add("source", "starting point", f"Where did {leg} start from?")
        if _blank(jget(r, "destination")):
            add("destination", "destination", f"And where did {leg} end?")
        if _blank(jget(r, "DateOfTravelSource")) or _blank(jget(r, "TimeOfTravelSource")):
            no_date = _blank(jget(r, "DateOfTravelSource"))
            add(
                "departure",
                "departure date and time" if no_date else "departure time",
                f'When did you leave for {leg}? A date and time works, like "5 Oct, 10:30 am".'
                if no_date
                else f"What time did you leave for {leg} on {_show_date(jget(r, 'DateOfTravelSource'))}?",
            )
        if _blank(jget(r, "DateOfTravelDestination")) or _blank(jget(r, "TimeOfTravelDestiNation")):
            no_date = _blank(jget(r, "DateOfTravelDestination"))
            add(
                "arrival",
                "arrival date and time" if no_date else "arrival time",
                f'When did you arrive for {leg}? ("same day 4:30 pm" works too)'
                if no_date
                else f"What time did you arrive for {leg} on {_show_date(jget(r, 'DateOfTravelDestination'))}?",
            )
        if _blank(jget(r, "ModeOfTravel")):
            add(
                "mode",
                "mode of travel",
                f"How did you travel for {leg}: air, rail, bus/taxi, or a personal or official vehicle?",
            )
    if js_truthy(state["includeAccommodation"]):
        for i, r in enumerate(state["accommodationRows"]):
            if is_empty_accommodation(r):
                continue
            row = i + 1
            stay = f"stay {row}{_in_city(r)}"

            def add_stay(slot: Slot, label: str, question: str, row: int = row) -> None:
                missing.append(
                    {"section": "accommodation", "row": row, "slot": slot, "label": label, "question": question}
                )

            if _blank(jget(r, "location")):
                add_stay("location", "city", f"Which city was {stay} in?")
            if _blank(jget(r, "checkInDate")) or _blank(jget(r, "checkInTime")):
                add_stay(
                    "checkIn", "check-in date and time", f"When did you check in for {stay}? Date and time, please."
                )
            if _blank(jget(r, "checkOutDate")) or _blank(jget(r, "checkOutTime")):
                add_stay("checkOut", "check-out date and time", f"And when did you check out of {stay}?")
            type_ = nullish(jget(r, "selectedType"), "hotel")
            if type_ == "hotel" and _blank(jget(r, "hotelName")):
                add_stay("hotelName", "hotel name", f"What was the hotel's name for {stay}?")
            if type_ in ("hotel", "company_paid") and _blank(jget(r, "invoiceNo")):
                add_stay("invoiceNo", "invoice number", f"What's the invoice number on the bill for {stay}?")
    return missing


def find_amount_issues(state: ClaimFormState) -> list[str]:
    """claimAssistant.ts:findAmountIssues: bills attached but no amount read from them (a heads-up only)."""
    issues: list[str] = []
    for i, r in enumerate(state["transportationRows"]):
        if is_empty_transport(r) or js_truthy(jget(r, "billNotRequired")) or len(r["uploadedFiles"]) == 0:
            continue
        if not js_number(jget(r, "claimedAmount")) > 0:
            issues.append(f"Journey {i + 1}{_route(r)}: amount not read. Enter it on the form.")
    if js_truthy(state["includeAccommodation"]):
        for i, r in enumerate(state["accommodationRows"]):
            if is_empty_accommodation(r):
                continue
            type_ = nullish(jget(r, "selectedType"), "hotel")
            if type_ in ("hotel", "company_paid") and not js_number(jget(r, "claimed")) > 0:
                issues.append(f"Stay {i + 1}{_in_city(r)}: amount not read. Enter it on the form.")
    return issues


def _num_or_zero(v: Any) -> float:
    """Number(v) || 0"""
    n = js_number(v)
    return 0.0 if math.isnan(n) or n == 0 else n


def find_form_only_issues(state: ClaimFormState) -> list[str]:
    """claimAssistant.ts:findFormOnlyIssues: what the form will flag that can only be settled on the form itself."""
    issues = find_amount_issues(state)
    if not js_truthy(state["includeAccommodation"]):
        return issues
    for i, r in enumerate(state["accommodationRows"]):
        if is_empty_accommodation(r):
            continue
        stay = f"Stay {i + 1}{_in_city(r)}"
        if not _blank(jget(r, "location")) and (_blank(jget(r, "classOfCity")) or jget(r, "classOfCity") == "Select"):
            issues.append(
                f"{stay}: the city class fills in when you pick the city on the form, so please re-select it there."
            )
        type_ = nullish(jget(r, "selectedType"), "hotel")
        if type_ in ("hotel", "company_paid"):
            billed = _num_or_zero(jget(r, "billed"))
            tax = (
                js_number(js_or(jget(r, "ClaimedIGSTAmount"), 0))
                + js_number(js_or(jget(r, "ClaimedCGSTAmount"), 0))
                + js_number(js_or(jget(r, "ClaimedSGSTAmount"), 0))
            )
            claimed_base = _num_or_zero(jget(r, "claimed")) - tax
            if claimed_base > billed:
                issues.append(
                    f"{stay}: the claimed amount ({_inr(claimed_base)}) is more than the billed amount"
                    f" ({_inr(billed)}); adjust it on the form."
                )
    return issues


def describe_missing(state: ClaimFormState, missing: list[MissingField]) -> str:
    """claimAssistant.ts:describeMissing: "• Journey 1 (Noida → Pune): departure time, mode of travel" lines."""
    groups: dict[str, list[str]] = {}
    for m in missing:
        key = f"{js_str(m['section'])}:{js_str(m['row'])}"
        groups[key] = [*groups.get(key, []), m["label"]]
    lines = []
    for key, labels in groups.items():
        section, row_text = key.split(":")[:2]
        row = js_int(js_number(row_text))
        if section == "transport":
            r = _at(state["transportationRows"], row - 1)
            lines.append(f"• Journey {js_str(row)}{_route(r)}: {', '.join(labels)}")
        else:
            a = _at(state["accommodationRows"], row - 1)
            lines.append(f"• Stay {js_str(row)}{_in_city(a)}: {', '.join(labels)}")
    return "\n".join(lines)


# ─── Read-outs ────────────────────────────────────────────────────────────────


def summarize_journeys(state: ClaimFormState) -> str:
    """claimAssistant.ts:summarizeJourneys."""
    rows = [r for r in state["transportationRows"] if not is_empty_transport(r)]
    if len(rows) == 0:
        return "No journeys yet."
    lines = []
    for i, r in enumerate(rows):
        amount = js_number(jget(r, "claimedAmount"))
        mode = js_str(nullish(js_lookup(MODE_LABELS, jget(r, "ModeOfTravel")), "mode not set"))
        lines.append(
            f"{i + 1}. {js_str(js_or(jget(r, 'source'), '?'))} → {js_str(js_or(jget(r, 'destination'), '?'))}"
            f" · {_show_date(jget(r, 'DateOfTravelSource'))} {js_str(js_or(jget(r, 'TimeOfTravelSource'), '--:--'))}"
            f" to {_show_date(jget(r, 'DateOfTravelDestination'))}"
            f" {js_str(js_or(jget(r, 'TimeOfTravelDestiNation'), '--:--'))}"
            f" · {mode}{f' · {_inr(amount)}' if js_truthy(amount) else ''}"
        )
    return "\n".join(lines)


def summarize_stays(state: ClaimFormState) -> str:
    """claimAssistant.ts:summarizeStays."""
    if not js_truthy(state["includeAccommodation"]):
        return "No stays added."
    rows = [r for r in state["accommodationRows"] if not is_empty_accommodation(r)]
    if len(rows) == 0:
        return "No stays added."
    lines = []
    for i, r in enumerate(rows):
        claimed = js_number(jget(r, "claimed"))
        label = js_str(nullish(js_lookup(ACCOM_LABELS, nullish(jget(r, "selectedType"), "hotel")), "Hotel"))
        lines.append(
            f"{i + 1}. {js_str(js_or(jget(r, 'location'), '?'))} · {_show_date(jget(r, 'checkInDate'))}"
            f" to {_show_date(jget(r, 'checkOutDate'))} · {label}{f' · {_inr(claimed)}' if js_truthy(claimed) else ''}"
        )
    return "\n".join(lines)


def summarize_leave(state: ClaimFormState) -> str:
    """claimAssistant.ts:summarizeLeave."""
    if not js_truthy(state["includeLeave"]):
        return "No leave."
    rows = [r for r in state["leaveRows"] if not is_empty_leave(r)]
    if len(rows) == 0:
        return "No leave."
    return "\n".join(
        f"{i + 1}. {_show_date(jget(r, 'startDate'))} to {_show_date(js_or(jget(r, 'endDate'), jget(r, 'startDate')))}"
        f" · DA {'deducted' if jget(r, 'daDeductable') == 'Yes' else 'not deducted'}"
        for i, r in enumerate(rows)
    )


DA_RULES: Final = "\n".join(
    [
        "DA is worked out per calendar day from your first departure to your last arrival.",
        "The first day counts from departure to 23:59, full days in between count 24 hours, and the last day counts "
        "from 00:00 to arrival.",
        "Hours in a day: up to 6h → 30%, over 6h up to 12h → 70%, over 12h → 100% of your grade’s daily DA rate.",
        "On a day with free company lodging you get 25% of that.",
        "If you were promoted during the tour, days before the promotion date use your previous grade’s rate.",
        'Leave days marked "DA deductable" are subtracted from the claim.',
    ]
)

# ─── LLM: message -> edits ────────────────────────────────────────────────────


class ClaimEdit(TypedDict):
    section: Literal["transport", "accommodation", "leave"]
    # 1-based row; 0 = add a new row
    row: int | float
    field: str
    value: str


def snapshot(state: ClaimFormState, tour: TourContext | None = None) -> str:
    """claimAssistant.ts:snapshot: the claim in a few lines, for the AI model ("Today" is the UTC date)."""
    # bills=N: the ticket or bill files attached to that row (on the form or in the saved draft)
    t = [
        f"T{i + 1}: from={js_str(js_or(jget(r, 'source'), '-'))} to={js_str(js_or(jget(r, 'destination'), '-'))}"
        f" departure={js_str(js_or(jget(r, 'DateOfTravelSource'), '-'))}"
        f" {js_str(js_or(jget(r, 'TimeOfTravelSource'), '-'))}"
        f" arrival={js_str(js_or(jget(r, 'DateOfTravelDestination'), '-'))}"
        f" {js_str(js_or(jget(r, 'TimeOfTravelDestiNation'), '-'))}"
        f" mode={js_str(js_or(jget(r, 'ModeOfTravel'), '-'))} bills={len(nullish(jget(r, 'uploadedFiles'), []))}"
        f"{' (blank row)' if is_empty_transport(r) else ''}"
        for i, r in enumerate(state["transportationRows"])
    ]
    a = (
        [
            f"A{i + 1}: location={js_str(js_or(jget(r, 'location'), '-'))}"
            f" checkIn={js_str(js_or(jget(r, 'checkInDate'), '-'))} {js_str(js_or(jget(r, 'checkInTime'), '-'))}"
            f" checkOut={js_str(js_or(jget(r, 'checkOutDate'), '-'))} {js_str(js_or(jget(r, 'checkOutTime'), '-'))}"
            f" type={js_str(js_or(jget(r, 'selectedType'), 'hotel'))}"
            f" hotelName={js_str(js_or(jget(r, 'hotelName'), '-'))}"
            f" invoiceNo={js_str(js_or(jget(r, 'invoiceNo'), '-'))}"
            f" bills={len(nullish(jget(r, 'billFiles'), []))}{' (blank row)' if is_empty_accommodation(r) else ''}"
            for i, r in enumerate(state["accommodationRows"])
        ]
        if js_truthy(state["includeAccommodation"])
        else ["(no stays)"]
    )
    filled = len([r for r in state["transportationRows"] if not is_empty_transport(r)])
    stays = (
        len([r for r in state["accommodationRows"] if not is_empty_accommodation(r)])
        if js_truthy(state["includeAccommodation"])
        else 0
    )
    leave = (
        [
            f"L{i + 1}: {js_str(js_or(jget(r, 'startDate'), '-'))} to {js_str(js_or(jget(r, 'endDate'), '-'))}"
            f" deductable={js_str(jget(r, 'daDeductable'))}"
            for i, r in enumerate(state["leaveRows"])
        ]
        if js_truthy(state["includeLeave"])
        else ["(no leave)"]
    )
    today = to_iso_date(utc_iso_string(now_ms()))
    if js_truthy(tour):
        head = (
            f"Today: {today}. Approved tour #{js_str(jget(tour, 'tourId'))}:"
            f" {js_str(nullish(jget(tour, 'source'), '?'))}"
            f" to {js_str(nullish(jget(tour, 'destination'), '?'))}, {js_str(nullish(jget(tour, 'startDate'), '?'))}"
            f" to {js_str(nullish(jget(tour, 'endDate'), '?'))}."
        )
    else:
        head = f"Today: {today}. The tour is not known yet; it is worked out from the bills."
    return "\n".join(
        [
            head,
            f"IN THE CLAIM NOW: {filled} journey{'' if filled == 1 else 's'}, {stays} stay{'' if stays == 1 else 's'}.",
            "JOURNEYS",
            *t,
            "STAYS",
            *a,
            "LEAVE",
            *leave,
        ]
    )


# How the AI model lists claim edits ("edit" action); apply_edits validates every one.
CLAIM_EDIT_FORMAT: Final = (
    '"edits" is a list of {"section","row","field","value"}, one per field. One message often covers several '
    "journeys: add an edit for EVERY value mentioned, and give a value with no journey number to the journey whose "
    'field is still "-".\n'
    '- section "transport", row = journey number (a new journey: 0, or for several new journeys the numbers after the '
    "last one, e.g. 4 and 5 when the claim has 3; a blank T row counts as a journey to fill); fields from, to, "
    f"departureDate, departureTime, arrivalDate, arrivalTime, mode (one of {', '.join(TRAVEL_MODES)}), distanceKm.\n"
    "  mode: train/rail/express = rail; flight/plane/air = air; bus, taxi, cab, auto, metro = public-transport; own "
    "car/bike/scooter = personal-vehicle; office/company vehicle = official-entitled.\n"
    '- section "accommodation", row = stay number (0 = add a new stay: a stay in a city the claim does not have yet is '
    "a new stay); fields location, checkInDate, checkInTime, checkOutDate, checkOutTime, hotelName, invoiceNo, type "
    "(hotel, own_arrangement, company_free = free company guest house/lodging, company_paid).\n"
    '- section "leave", row = leave number (0 = add); fields startDate, endDate, daDeductable (Yes/No). Only when the '
    "employee clearly says they were on leave.\n"
    '- to delete a wrong or duplicate journey, stay or leave entry: field "remove", value "yes" (with its section and '
    "row number).\n"
    "Dates as YYYY-MM-DD (use the tour's year and month when the employee gives only a day), times as HH:mm 24-hour "
    "(8 pm = 20:00, 9.15 in the morning = 09:15, noon = 12:00). Convert exactly what the employee wrote; never reuse a "
    "value from the claim or from this format.\n"
    "When the employee gives a date with the time, list the date too. An arrival time with no date is on the "
    'journey\'s departure date ("next day" = the day after).\n'
    "Never an amount (fare, cost, rupees): those are not edits, and never a distance taken from an amount.\n"
    'Shape: {"action":"edit","args":{},"edits":[{"section":"<section>","row":<number>,"field":"<field>",'
    '"value":"<value>"}]}'
)

_SECTIONS: Final = ("transport", "accommodation", "leave")


def read_claim_edits(items: list[Any]) -> list[ClaimEdit]:
    """claimAssistant.ts:readClaimEdits: the edits the model listed, keeping only well-formed ones (never guessed)."""
    return [
        {**e, "row": js_int(js_number(jget(e, "row"))), "value": js_str(e["value"])}  # type: ignore[typeddict-item]
        for e in items
        if isinstance(e, dict)
        and jget(e, "section") in _SECTIONS
        and js_is_finite(js_number(jget(e, "row")))
        and isinstance(jget(e, "field"), str)
        and not is_nullish(jget(e, "value"))
    ]


# ─── Apply edits (validated) ──────────────────────────────────────────────────


class ApplyResult(TypedDict):
    state: ClaimFormState
    applied: list[str]
    rejected: list[str]


_CREATES = re.compile(r"(from|to|departureDate|arrivalDate|location|checkInDate|checkOutDate|startDate|endDate)")
_YES = re.compile(r"(y|yes|true)", re.IGNORECASE | re.ASCII)


def _row_of(rows: list[Any], index: Any) -> Any:
    """rows[index] for a field write: JavaScript threw a TypeError writing to an undefined row."""
    r = _at(rows, index)
    if r is UNDEFINED:
        raise TypeError(f"Cannot set properties of undefined (reading row {js_str(index)})")
    return r


def _same_value_zero_in(value: float, items: list[Any]) -> bool:
    """items.includes(value)"""
    for x in items:
        n = x if isinstance(x, int | float) and not isinstance(x, bool) else None
        if n is not None and (n == value or (math.isnan(n) and math.isnan(value))):
            return True
    return False


def apply_edits(input_state: ClaimFormState, edits: list[Any], rules: TourRules | None = None) -> ApplyResult:
    """claimAssistant.ts:applyEdits: the model's edits, validated, applied to a copy of the claim.

    New entries: "row 0" edits in one message all go to the same new row, and rows numbered after the last one
    ("journey 4" and "journey 5" when the claim has 3) each become their own new row, in order. A number past the
    end only creates a row when its edits say where or when (from, to, a date, a place): "journey 9 arrives at
    5 pm" on a 3-journey claim is refused, not turned into a new journey. Removals come last, bottom up.
    """
    rules_: Any = {"allowAir": True} if rules is None else rules
    min_date, max_date = jget(rules_, "minDate"), jget(rules_, "maxDate")

    def outside_tour(iso: str) -> bool:
        return (js_truthy(min_date) and iso < min_date) or (js_truthy(max_date) and iso > max_date)

    window_text = (
        f"{_show_date(min_date)} – {_show_date(max_date)}" if js_truthy(min_date) and js_truthy(max_date) else ""
    )
    state: Any = {
        **input_state,
        "transportationRows": [{**r} for r in input_state["transportationRows"]],
        "accommodationRows": [{**r} for r in input_state["accommodationRows"]],
        "leaveRows": [{**r} for r in input_state["leaveRows"]],
    }
    applied: list[str] = []
    rejected: list[str] = []
    new_row_index: dict[str, int] = {}
    length_at_start = {
        "transport": len(input_state["transportationRows"]),
        "accommodation": len(input_state["accommodationRows"]),
        "leave": len(input_state["leaveRows"]),
    }

    def edit_key(section: Any, row: float) -> str:
        return f"{js_str(section)}:{js_number_to_string(row)}"

    creatable = {
        edit_key(jget(e, "section"), js_number(jget(e, "row")))
        for e in edits
        if jget(e, "section") in length_at_start
        and js_number(jget(e, "row")) > length_at_start[jget(e, "section")]
        and _CREATES.fullmatch(js_str(jget(e, "field")))
    }

    def resolve_row(
        section: str, rows: list[Any], row: Any, is_empty: Callable[[Any], bool], create: Callable[[], Any]
    ) -> Any:
        start = length_at_start[section]
        if 1 <= row <= start:
            return row - 1
        is_new = row == 0 or (start < row <= start + 5 and edit_key(section, row) in creatable)
        if not is_new:
            return -1
        key = edit_key(section, row)
        if key in new_row_index:
            return new_row_index[key]
        # reuse the blank row the form opens with before adding another
        taken = set(new_row_index.values())
        blank_idx = next((i for i, r in enumerate(rows) if is_empty(r) and i not in taken), -1)
        if blank_idx >= 0:
            idx = blank_idx
        else:
            rows.append(create())
            idx = len(rows) - 1
        new_row_index[key] = idx
        return idx

    def section_rows(source: Any, section: Any) -> list[Any]:
        if section == "transport":
            return source["transportationRows"]
        if section == "accommodation":
            return source["accommodationRows"]
        return source["leaveRows"]

    def section_name(section: Any) -> str:
        return "journey" if section == "transport" else "stay" if section == "accommodation" else "leave entry"

    removals: list[dict[str, Any]] = []

    for e in edits:
        section, field = jget(e, "section"), jget(e, "field")
        value = js_trim(js_str(nullish(jget(e, "value"), "")))
        row = js_int(js_number(jget(e, "row")))
        locked = jget(rules_, "lockedJourneys")
        if section == "transport" and not is_nullish(locked) and _same_value_zero_in(row, locked):
            r = _at(input_state["transportationRows"], row - 1)
            route = f" ({js_str(js_or(jget(r, 'source'), '?'))} → {js_str(js_or(jget(r, 'destination'), '?'))})"
            rejected.append(
                f"Journey {js_str(row)}{route if js_truthy(r) else ''} was not changed: the ride asked about is added "
                "as a new journey, not written over an existing one."
            )
            continue

        # deleting an entry comes last (below), so row numbers in the other edits still mean what the employee saw
        if field == "remove":
            if not _YES.fullmatch(value):
                continue
            # "delete the stay" with no number: the section's only entry
            if row == 0:
                is_empty = (
                    is_empty_transport
                    if section == "transport"
                    else is_empty_accommodation
                    if section == "accommodation"
                    else is_empty_leave
                )
                every = section_rows(input_state, section)
                rows = [r for r in every if not is_empty(r)]
                if len(rows) != 1:
                    rejected.append(f"Which {section_name(section)} should go? Please give its number.")
                    continue
                removals.append({**e, "row": next(i for i, r in enumerate(every) if r is rows[0]) + 1})
                continue
            removals.append(e)
            continue

        if section == "transport":
            transport_rows = state["transportationRows"]
            idx = resolve_row(
                "transport",
                transport_rows,
                row,
                is_empty_transport,
                lambda rows=transport_rows: new_transport_row(_at(rows, len(rows) - 1)),
            )
            if idx < 0:
                rejected.append(f"There is no journey {js_str(row)}.")
                continue
            label = f"Journey {js_str(idx + 1)}"
            if field in ("from", "to"):
                if value:
                    _row_of(transport_rows, idx)["source" if field == "from" else "destination"] = value
                    applied.append(f"{label}: {field} {value}")
                    continue
            elif field in ("departureDate", "arrivalDate"):
                d = to_iso_date(value)
                if d:
                    if outside_tour(d):
                        rejected.append(f"{label}: {_show_date(d)} is outside your approved tour ({window_text}).")
                        continue
                    target = "DateOfTravelSource" if field == "departureDate" else "DateOfTravelDestination"
                    _row_of(transport_rows, idx)[target] = d
                    applied.append(
                        f"{label}: {'departure' if field == 'departureDate' else 'arrival'} date {_show_date(d)}"
                    )
                    continue
            elif field in ("departureTime", "arrivalTime"):
                t = to_time(value)
                if t:
                    target = "TimeOfTravelSource" if field == "departureTime" else "TimeOfTravelDestiNation"
                    _row_of(transport_rows, idx)[target] = t
                    applied.append(f"{label}: {'departure' if field == 'departureTime' else 'arrival'} time {t}")
                    continue
            elif field == "mode":
                m = map_travel_mode(value)
                if m != "select":
                    if m == "air" and not js_truthy(jget(rules_, "allowAir")):
                        rejected.append(f"{label}: your approved tour has no air travel, so air can't be claimed.")
                        continue
                    _row_of(transport_rows, idx)["ModeOfTravel"] = m
                    applied.append(f"{label}: {MODE_LABELS[m]}")
                    continue
            elif field == "distanceKm":
                km = js_parse_float(value)
                if km > 0:
                    _row_of(transport_rows, idx)["distanceKm"] = js_number_to_string(km)
                    applied.append(f"{label}: {js_number_to_string(km)} km")
                    continue
            rejected.append(f'{label}: couldn\'t use "{value}" for {js_str(field)}.')
            continue

        if section == "accommodation":
            stay_rows = state["accommodationRows"]
            idx = resolve_row("accommodation", stay_rows, row, is_empty_accommodation, new_accommodation_row)
            if idx < 0:
                rejected.append(f"There is no stay {js_str(row)}.")
                continue
            state["includeAccommodation"] = True
            label = f"Stay {js_str(idx + 1)}"
            if field in ("location", "hotelName", "invoiceNo"):
                if value:
                    _row_of(stay_rows, idx)[field] = value
                    prefix = {"location": "", "hotelName": "hotel ", "invoiceNo": "invoice no. "}[field]
                    applied.append(f"{label}: {prefix}{value}")
                    continue
            elif field in ("checkInDate", "checkOutDate"):
                d = to_iso_date(value)
                if d:
                    if outside_tour(d):
                        rejected.append(f"{label}: {_show_date(d)} is outside your approved tour ({window_text}).")
                        continue
                    _row_of(stay_rows, idx)[field] = d
                    applied.append(f"{label}: {'check-in' if field == 'checkInDate' else 'check-out'} {_show_date(d)}")
                    continue
            elif field in ("checkInTime", "checkOutTime"):
                t = to_time(value)
                if t:
                    _row_of(stay_rows, idx)[field] = t
                    applied.append(f"{label}: {'check-in' if field == 'checkInTime' else 'check-out'} time {t}")
                    continue
            elif field == "type":
                fields = accommodation_type_fields(value)
                r = _row_of(stay_rows, idx)
                if fields["selectedType"] == jget(r, "selectedType"):
                    applied.append(f"{label}: already {ACCOM_LABELS[fields['selectedType']]}")
                    continue
                # same reset the form does when the type is changed by hand (AccommodationSection.handleTypeChange)
                r.update(
                    fields,
                    billed="0",
                    tax="0",
                    totalBilled="0",
                    claimed="0",
                    hotelName="",
                    billFiles=[],
                    BillSGSTAmount="0",
                    BillCGSTAmount="0",
                    BillIGSTAmount="0",
                    validationErrors={},
                )
                cleared = "" if fields["selectedType"] == "hotel" else " (bill amounts cleared)"
                applied.append(f"{label}: {ACCOM_LABELS[fields['selectedType']]}{cleared}")
                continue
            rejected.append(f'{label}: couldn\'t use "{value}" for {js_str(field)}.')
            continue

        if section == "leave":
            leave_rows = state["leaveRows"]
            idx = resolve_row("leave", leave_rows, row, is_empty_leave, new_leave_row)
            if idx < 0:
                rejected.append(f"There is no leave entry {js_str(row)}.")
                continue
            state["includeLeave"] = True
            label = f"Leave {js_str(idx + 1)}"
            if field in ("startDate", "endDate"):
                d = to_iso_date(value)
                if d and outside_tour(d):
                    rejected.append(f"{label}: {_show_date(d)} is outside your tour ({window_text}).")
                    continue
                if d:
                    _row_of(leave_rows, idx)[field] = d
                    applied.append(f"{label}: {'from' if field == 'startDate' else 'to'} {_show_date(d)}")
                    continue
            elif field == "daDeductable":
                r = _row_of(leave_rows, idx)
                r["daDeductable"] = "Yes" if _YES.fullmatch(value) else "No"
                applied.append(f"{label}: DA deductable {r['daDeductable']}")
                continue
            rejected.append(f'{label}: couldn\'t use "{value}" for {js_str(field)}.')
            continue

        rejected.append(f"Couldn't apply a change to {js_str(section)} {js_str(field)}.")

    # delete from the bottom up so earlier row numbers stay valid
    def by_row_descending(a: dict[str, Any], b: dict[str, Any]) -> int:
        diff = js_number(jget(b, "row")) - js_number(jget(a, "row"))
        return -1 if diff < 0 else 1 if diff > 0 else 0

    for e in sorted(removals, key=functools.cmp_to_key(by_row_descending)):
        row = js_int(js_number(jget(e, "row")))
        section = jget(e, "section")
        name = section_name(section)
        rows = section_rows(state, section)
        if not (1 <= row <= len(rows)):
            rejected.append(f"There is no {name} {js_str(row)} to remove.")
            continue
        del rows[int(row - 1)]
        # an empty section goes back to the one blank row the form always has
        if len(rows) == 0:
            if section == "transport":
                state["transportationRows"].append(new_transport_row())
            elif section == "accommodation":
                state["accommodationRows"].append(new_accommodation_row())
                state["includeAccommodation"] = False
            else:
                state["leaveRows"].append(new_leave_row())
                state["includeLeave"] = False
        applied.append(f"Removed {name} {js_str(row)}")

    return {"state": state, "applied": applied, "rejected": rejected}
