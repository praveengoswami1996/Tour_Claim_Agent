"""A tour's saved draft as context for the Claim Assistant.

Port of Tour_React src/lib/claimChatbot/savedClaim.ts: what the saved claim already holds, and what newly read bills
add to it. The saved rows are never replaced: new rows go after them, because the server ties a saved draft's files to
its rows by position.

Saved rows (deviation 1 in Project-Context/agent/old_assistant_parity/01_deviations.md)
--------------------------------------------------------------------------------------
`claim_state_from_draft` builds the chat rows exactly as the TypeScript did, quirks included: a stay's `selectedType`
is read but its `ownArrangement` / `freeProvidedByCompany` / `companyPaid` flags stay "No" (so the payload builder
would send an own-arrangement or free stay as a hotel stay), and a stay's InvoiceDate is not read.
In addition every journey, stay and leave row it builds from a saved item carries, under `SAVED_ROW_KEY` ("_saved"):

    {"dto": <the saved item exactly as read>, "row": <a copy of the row exactly as just parsed from it>}

so that claim_payload.build_claim_payload writes the saved row back unchanged, with only the chat's own changes on
top. Nothing else reads `_saved`: it never changes keys, order, summaries, texts or comparisons.

Dates
-----
A transport row's `invoiceDate` is a JavaScript Date in the TypeScript; here it is that Date's local day as
'YYYY-MM-DD' (form_rows.py), or 'NaN-NaN-NaN' for an Invalid Date (what the payload builder printed for one). A saved
file's `uploadDate` (`new Date(0)`) is its JSON text, '1970-01-01T00:00:00.000Z'.
"""

from __future__ import annotations

import copy
import json
from typing import Any, NotRequired, TypedDict

from .claim_assistant import (
    ClaimFormState,
    initial_claim_state,
    is_empty_accommodation,
    is_empty_transport,
    journey_key,
    new_accommodation_row,
    new_leave_row,
    new_transport_row,
    stay_key,
)
from .claim_payload import SAVED_ROW_KEY
from .claim_progress import covered_days, day_text, group_days, uncovered_days
from .form_rows import AccommodationRow, LeaveRow, TransportationRow
from .js_compat import (
    UNDEFINED,
    defined,
    is_nullish,
    jget,
    js_int,
    js_number,
    js_number_to_string,
    js_or,
    js_str,
    js_truthy,
    local_ymd,
    nullish,
    utc_iso_string,
)
from .ocr_mapping import to_iso_date, to_time
from .row_order import journeys_in_date_order, stays_in_date_order
from .tour_matcher import ClaimableTour
from .tour_review import missing_in_short, review_claim
from .within_assistant import _date_ms


class BillRows(TypedDict):
    """journeys and stays read from bills (or held by a saved claim)"""

    transportationRows: list[TransportationRow]
    accommodationRows: list[AccommodationRow]


# ─── Reading a saved draft ────────────────────────────────────────────────────


def _field(o: Any, name: str) -> Any:
    """The server sends journeys and stays as JSON text, in whichever casing the API uses."""
    if not isinstance(o, dict):
        return UNDEFINED
    lowered = name.lower()
    key = next((k for k in o if isinstance(k, str) and k.lower() == lowered), UNDEFINED)
    return UNDEFINED if key is UNDEFINED else o[key]


def _reject_constant(name: str) -> Any:
    raise ValueError(f"Unexpected token {name[0]} in JSON")


def _parse_int(text: str) -> int | float:
    """JSON numbers are JavaScript numbers: a whole number beyond 2^53 loses precision as a double."""
    n = int(text)
    return n if abs(n) <= 2**53 else float(text)


def json_parse(text: str) -> Any:
    """`JSON.parse(text)` (NaN and Infinity are not JSON; whole numbers are JavaScript numbers)."""
    return json.loads(text, parse_constant=_reject_constant, parse_int=_parse_int)


def _list(v: Any) -> list[Any]:
    try:
        parsed = json_parse(v) if isinstance(v, str) else v
        return parsed if isinstance(parsed, list) else []
    except (ValueError, RecursionError):
        return []


def _str(v: Any) -> str:
    return "" if is_nullish(v) else js_str(v)


def _num(v: Any) -> str:
    """String(Number(v) || 0)"""
    return js_number_to_string(js_or(js_number(v), 0))


def _day(v: Any) -> str:
    """dates as the chat keeps them (YYYY-MM-DD), so a bill and its saved row compare equal"""
    return js_or(to_iso_date(_str(v)), _str(v))


def _time(v: Any) -> str:
    """times as the chat keeps them (HH:mm)"""
    return js_or(to_time(_str(v)), _str(v))


def _date_value(v: Any) -> str:
    """`new Date(v)` as a row keeps it here: its local day 'YYYY-MM-DD', or 'NaN-NaN-NaN' for an Invalid Date."""
    ms = _date_ms(v)
    if ms is None:
        return "NaN-NaN-NaN"
    y, m, d = local_ymd(ms)
    return f"{y}-{m:02d}-{d:02d}"


class _FileRef(TypedDict):
    fileId: str
    name: str
    size: NotRequired[int | float]
    rowIndex: int | float
    tempFilePath: NotRequired[str]


def _refs_of(v: Any) -> list[_FileRef]:
    refs: list[_FileRef] = []
    for f in _list(v):
        size = _field(f, "fileSize")
        temp = _field(f, "tempFilePath")
        name = nullish(nullish(_field(f, "originalFileName"), _field(f, "displayName")), _field(f, "fileId"))
        refs.append(
            defined(  # type: ignore[arg-type]
                fileId=_str(_field(f, "fileId")),
                name=_str(name),
                size=UNDEFINED if is_nullish(size) else js_int(js_number(size)),
                rowIndex=js_int(js_number(nullish(_field(f, "rowIndex"), 0))),
                tempFilePath=_str(temp) if js_truthy(temp) else UNDEFINED,
            )
        )
    return refs


def _saved_files(refs: list[_FileRef], row_index: int) -> list[dict[str, Any]]:
    """a file the server already holds: it stays with its row and is never uploaded again"""
    return [
        defined(
            fileId=f["fileId"],
            name=f["name"],
            size=nullish(f.get("size", UNDEFINED), 0),
            isRetained=True,
            tempFilePath=f.get("tempFilePath", UNDEFINED),
            type="",
            uploadDate=utc_iso_string(0),
        )
        for f in refs
        if f["rowIndex"] == row_index  # `===`: NaN never matches (nor does it in Python)
    ]


def _keep_saved(row: dict[str, Any], item: Any) -> dict[str, Any]:
    """Deviation 1: the row remembers the saved item it was read from, and how it was parsed (see the docstring)."""
    row[SAVED_ROW_KEY] = {"dto": copy.deepcopy(item), "row": copy.deepcopy(row)}
    return row


def claim_state_from_draft(draft: Any) -> ClaimFormState:
    """savedClaim.ts:claimStateFromDraft: the claim as the saved draft holds it. Fields the chat does not read keep
    their empty defaults. Each row read from a saved item carries it under `_saved` (deviation 1)."""
    data = nullish(_field(draft, "draftData"), draft)
    journeys = _list(nullish(_field(data, "TransportaionDetailDtos"), _field(data, "TransportationDetailDtos")))
    stays = _list(nullish(_field(data, "AccomodationDetailDto"), _field(data, "AccommodationDetailDto")))
    leaves = _list(_field(data, "LeaveAvailedDto"))
    travel_refs = _refs_of(_field(data, "transportationFileReferences"))
    stay_refs = _refs_of(_field(data, "accomodationFileReferences"))

    transportation_rows: list[TransportationRow] = []
    for idx, item in enumerate(journeys):
        f = lambda name, item=item: _field(item, name)  # noqa: E731
        is_na = f("TaxStructure") == "NA"
        tax_structure = (
            "NA"
            if is_na
            else "IGST"
            if f("TaxStructure") == "IGST" or js_number(nullish(f("IGSTAmount"), 0)) > 0
            else "SGST_CGST"
        )
        invoice_date = f("InvoiceDate")
        row: dict[str, Any] = {
            **new_transport_row(),
            "id": f"transport-draft-{idx}",
            "DateOfTravelSource": _day(f("DateOfTravelSource")),
            "TimeOfTravelSource": _time(f("TimeOfTravelSource")),
            "source": _str(f("Source")),
            "ModeOfTravel": _str(nullish(nullish(f("ModeOfTravel"), f("departureModeofTravel")), "select")),
            "DateOfTravelDestination": _day(f("DateOfTravelDestination")),
            "TimeOfTravelDestiNation": _time(f("TimeOfTravelDestiNation")),
            "destination": _str(f("Destination")),
            "distanceKm": _num(f("DistanceINKm")),
            "invoiceNo": _str(f("InvoiceNumber")),
            "travelCost": _num(f("BilledAmount")),
            "claimedAmount": _num(f("ClaimedAmount")),
            "sgst": "0" if is_na else _num(f("SGSTAmount")),
            "cgst": "0" if is_na else _num(f("CGSTAmount")),
            "igst": "0" if is_na else _num(f("IGSTAmount")),
            "BillSGSTAmount": "0" if is_na else _num(f("BillSGSTAmount")),
            "BillCGSTAmount": "0" if is_na else _num(f("BillCGSTAmount")),
            "BillIGSTAmount": "0" if is_na else _num(f("BillIGSTAmount")),
            "taxStructure": tax_structure,
            "includeTravelCosts": js_number(nullish(f("BilledAmount"), 0)) > 0,
            "remarks": _str(f("Remark")),
            "isPaidUsingCard": js_truthy(f("isPaidUsingCard")),
            "uploadedFiles": _saved_files(travel_refs, idx),
        }
        if js_truthy(invoice_date):
            row["invoiceDate"] = _date_value(invoice_date)
        transportation_rows.append(_keep_saved(row, item))  # type: ignore[arg-type]

    accommodation_rows: list[AccommodationRow] = []
    for idx, item in enumerate(stays):
        f = lambda name, item=item: _field(item, name)  # noqa: E731
        is_na = f("TaxStructure") == "NA"
        selected_type = (
            "own_arrangement"
            if js_truthy(f("IsOwnArrangement"))
            else "company_free"
            if js_truthy(f("IsFreeAccomodation"))
            else "company_paid"
            if js_truthy(f("isCompanyLodginPaid"))
            else "hotel"
        )
        row = {
            **new_accommodation_row(),
            "id": f"stay-draft-{idx}",
            "checkInDate": _day(f("DateOfCheckIn")),
            "checkInTime": _time(f("TimeOfCheckIn")),
            "checkOutDate": _day(f("DateOfCheckOut")),
            "checkOutTime": _time(f("TimeOfCheckOut")),
            "location": _str(f("Location")),
            "classOfCity": _str(nullish(f("ClassOfCity"), "Select")),
            "hotelName": _str(f("HotelName")),
            "invoiceNo": _str(f("InvoiceNumber")),
            "billed": _num(f("BilledAmount")),
            "claimed": _num(f("ClaimedAmount")),
            "taxStructure": "NA" if is_na else "IGST" if f("TaxStructure") == "IGST" else "SGST_CGST",
            "selectedType": selected_type,
            "isPaidUsingCard": js_truthy(f("isPaidUsingCard")),
            "billFiles": _saved_files(stay_refs, idx),
        }
        accommodation_rows.append(_keep_saved(row, item))  # type: ignore[arg-type]

    leave_rows: list[LeaveRow] = []
    for idx, item in enumerate(leaves):
        row = {
            **new_leave_row(),
            "id": f"leave-draft-{idx}",
            "startDate": _day(_field(item, "LeaveStartDate")),
            "endDate": _day(_field(item, "LeaveEndDate")),
            "daDeductable": "Yes" if js_truthy(_field(item, "DADeductable")) else "No",
            "amount": _num(_field(item, "Amount")),
        }
        leave_rows.append(_keep_saved(row, item))  # type: ignore[arg-type]

    base = initial_claim_state()
    return {
        **base,
        "transportationRows": transportation_rows if transportation_rows else base["transportationRows"],
        "accommodationRows": accommodation_rows if accommodation_rows else base["accommodationRows"],
        "leaveRows": leave_rows if leave_rows else base["leaveRows"],
        "includeAccommodation": len(accommodation_rows) > 0,
        "includeLeave": any(
            js_truthy(_field(x, "LeaveStartDate")) or js_truthy(_field(x, "LeaveEndDate")) for x in leaves
        ),
    }


# ─── New bills against the saved claim ────────────────────────────────────────


class SavedComparison(TypedDict):
    added: BillRows
    alreadySaved: BillRows


def compare_with_saved(saved: ClaimFormState, incoming: BillRows | dict[str, Any]) -> SavedComparison:
    """savedClaim.ts:compareWithSaved: what newly read rows add to the saved claim, and which of them it already
    holds."""
    saved_journeys = {journey_key(r) for r in saved["transportationRows"] if not is_empty_transport(r)}
    saved_stays = {
        stay_key(r)
        for r in (saved["accommodationRows"] if js_truthy(saved["includeAccommodation"]) else [])
        if not is_empty_accommodation(r)
    }
    journeys, stays = incoming["transportationRows"], incoming["accommodationRows"]
    return {
        "added": {
            "transportationRows": [r for r in journeys if journey_key(r) not in saved_journeys],
            "accommodationRows": [r for r in stays if stay_key(r) not in saved_stays],
        },
        "alreadySaved": {
            "transportationRows": [r for r in journeys if journey_key(r) in saved_journeys],
            "accommodationRows": [r for r in stays if stay_key(r) in saved_stays],
        },
    }


def rows_count(r: BillRows | dict[str, Any]) -> int:
    """savedClaim.ts:rowsCount"""
    return len(r["transportationRows"]) + len(r["accommodationRows"])


def add_to_saved(saved: ClaimFormState, added: BillRows | dict[str, Any]) -> ClaimFormState:
    """savedClaim.ts:addToSaved: the saved claim with the new rows added after the saved ones (the server ties the
    saved files to the rows by position, so they never move). The claim form and the Claim Assistant show them sorted
    by date (row_order.py)."""
    journeys = [r for r in saved["transportationRows"] if not is_empty_transport(r)]
    stays = (
        [r for r in saved["accommodationRows"] if not is_empty_accommodation(r)]
        if js_truthy(saved["includeAccommodation"])
        else []
    )
    all_journeys = [*journeys, *journeys_in_date_order(added["transportationRows"])]
    all_stays = [*stays, *stays_in_date_order(added["accommodationRows"])]
    return {
        **saved,
        "transportationRows": all_journeys if all_journeys else saved["transportationRows"],
        "accommodationRows": all_stays if all_stays else saved["accommodationRows"],
        "includeAccommodation": len(all_stays) > 0 or saved["includeAccommodation"],
    }


def bill_days(rows: BillRows | dict[str, Any]) -> list[str]:
    """savedClaim.ts:billDays: the days the rows cover."""
    state = {**initial_claim_state(), **rows, "includeAccommodation": len(rows["accommodationRows"]) > 0}
    return sorted(covered_days(state))  # type: ignore[arg-type]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _rows_text(rows: BillRows | dict[str, Any]) -> str:
    journeys, stays = len(rows["transportationRows"]), len(rows["accommodationRows"])
    return " and ".join(p for p in (journeys and _plural(journeys, "journey"), stays and _plural(stays, "stay")) if p)


def saved_claim_text(args: dict[str, Any], *, gaps: bool = True) -> str:
    """savedClaim.ts:savedClaimText: what the bills mean for the saved claim, day by day.

    "Your saved claim already has 10 Sep and 12 Sep. These bills add 11 Sep. Every day of the tour is now covered."
    `args`: claim, saved, added, alreadySaved, merged, tour.

    `gaps=False` (owner requirement 2026-11-06, the Raise Claim chat): the last line, about the days of the tour that
    have no bills ("Still no bills for ...", "Every day of the tour is now covered."), is left out.
    """
    claim, saved, added = args["claim"], args["saved"], args["added"]
    already_saved, merged, tour = args["alreadySaved"], args["merged"], args["tour"]
    saved_days = sorted(covered_days(saved))
    lines = [
        f"📁 Your saved claim for {js_str(claim)} already has {group_days(saved_days)}."
        if saved_days
        else f"📁 You have a saved claim for {js_str(claim)}, with no journeys or stays in it yet."
    ]
    if rows_count(added) > 0:
        new_days = [d for d in bill_days(added) if d not in saved_days]
        lines.append(
            f"These bills add {group_days(new_days)}."
            if new_days
            else f"These bills add {_rows_text(added)} on days the saved claim already has."
        )
    if rows_count(already_saved) > 0:
        n = rows_count(already_saved)
        lines.append(
            f"{_rows_text(already_saved)} ({group_days(bill_days(already_saved))}) {'is' if n == 1 else 'are'} "
            f"already in your saved claim, so I didn't add {'it' if n == 1 else 'them'} again."
        )
    if not gaps:
        return "\n".join(lines)
    missing = uncovered_days(merged, tour)
    if len(missing) > 0:
        lines.append(f"Still no bills for {group_days(missing)}.")
    elif len(covered_days(merged)) > 0:
        lines.append("Every day of the tour is now covered.")
    return "\n".join(lines)


# ─── Saved drafts, for the greeting ───────────────────────────────────────────


class SavedDraftSummary(TypedDict):
    tourId: int
    route: str
    tourDates: str
    # "Noida → Mumbai, 05 Oct 2026"
    journeys: list[str]
    # "Mumbai, 05 Oct 2026 – 09 Oct 2026"
    stays: list[str]
    # the days the saved claim covers, or '' when it has nothing yet
    daysCovered: str
    # what the tour still lacks (no return ticket, days with no bills, missing details), as plain facts
    stillMissing: list[str]
    # the same in one plain sentence, to be said word for word: "Missing Return journey ticket, ..." or "Nothing is
    # missing."
    missing: str
    # when it was last saved, as the server says
    lastSaved: NotRequired[str]
    # this chat saved it (in an earlier conversation)
    savedInChat: bool


def _day_of(v: Any) -> str:
    """dayOf: '05 Oct 2026', or '' for no readable date."""
    iso = to_iso_date(v)
    return day_text(iso) if iso else ""


_STILL_MISSING_KINDS = (
    "oddPlace",
    "noJourneys",
    "noOutbound",
    "noReturn",
    "routeBreak",
    "overlap",
    "uncoveredDays",
    "missingField",
    "nightsWithoutStay",
    "conveyanceBefore",
    "conveyanceAfter",
    "conveyanceTransfer",
    "localConveyance",
    "ticketMissing",
    "hotelBillMissing",
)


def saved_draft_summary(
    tour: ClaimableTour | dict[str, Any], saved: ClaimFormState, opts: dict[str, Any] | None = None
) -> SavedDraftSummary:
    """savedClaim.ts:savedDraftSummary: what a tour's saved draft already holds and what it still lacks, for the AI
    model to tell the employee. `opts`: lastSaved, savedInChat, tourDates."""
    opts = {} if opts is None else opts
    journeys = [r for r in saved["transportationRows"] if not is_empty_transport(r)]
    stays = (
        [r for r in saved["accommodationRows"] if not is_empty_accommodation(r)]
        if js_truthy(saved["includeAccommodation"])
        else []
    )
    days = bill_days({"transportationRows": journeys, "accommodationRows": stays})
    review = review_claim({"state": saved, "tour": tour, "leaveAnswered": True})
    still = [i for i in review if i["kind"] in _STILL_MISSING_KINDS]

    def journey_text(r: Any) -> str:
        day = _day_of(jget(r, "DateOfTravelSource"))
        route = f"{js_str(js_or(jget(r, 'source'), '?'))} → {js_str(js_or(jget(r, 'destination'), '?'))}"
        return f"{route}{f', {day}' if day else ''}"

    def stay_text(r: Any) -> str:
        inn, out = _day_of(jget(r, "checkInDate")), _day_of(jget(r, "checkOutDate"))
        dates = f", {inn}{f' – {out}' if out else ''}" if inn else ""
        return f"{js_str(js_or(jget(r, 'location'), '?'))}{dates}"

    last_saved = jget(opts, "lastSaved")
    return defined(  # type: ignore[return-value]
        tourId=jget(tour, "tourId"),
        route=f"{js_str(js_or(jget(tour, 'source'), '?'))} → {js_str(js_or(jget(tour, 'destination'), '?'))}",
        tourDates=nullish(jget(opts, "tourDates"), ""),
        journeys=[journey_text(r) for r in journeys],
        stays=[stay_text(r) for r in stays],
        daysCovered=group_days(days) if days else "",
        stillMissing=[i["fact"] for i in still],
        missing=js_or(missing_in_short(still), "Nothing is missing."),
        lastSaved=js_or(_day_of(last_saved), UNDEFINED) if js_truthy(last_saved) else UNDEFINED,
        savedInChat=js_truthy(jget(opts, "savedInChat")),
    )


def draft_for_model(summary: SavedDraftSummary | dict[str, Any]) -> dict[str, Any]:
    """savedClaim.ts:draftForModel: a draft summary as the AI model gets it: what is missing only as the one plain
    sentence (the long notes behind it only clutter the reply)."""
    return {k: v for k, v in summary.items() if k != "stillMissing"}


def saved_drafts_greeting_note(
    summaries: list[SavedDraftSummary] | list[dict[str, Any]], waiting_without_draft: int
) -> str:
    """savedClaim.ts:savedDraftsGreetingNote: the note for the greeting when the employee has saved drafts (the AI
    model writes the words)."""
    n = len(summaries)
    from_chat = len([s for s in summaries if js_truthy(jget(s, "savedInChat"))])
    if from_chat == n:
        origin = (
            'All of them were saved in an earlier conversation with you: say so ("from our previous conversation"). '
        )
    elif from_chat > 0:
        origin = (
            "Say which of them were saved in an earlier conversation with you (savedInChat); the others were saved "
            "from the claim form. "
        )
    else:
        origin = (
            "They were saved from the claim form, not in a chat with you: do not say they came from a conversation "
            "with you. "
        )
    waiting = (
        f"or start on one of the other {js_str(waiting_without_draft)} approved "
        f"tour{'' if waiting_without_draft == 1 else 's'} waiting for a claim. "
        if waiting_without_draft > 0
        else ""
    )
    return (
        "The chat just opened on the Raise Claim page. Greet the employee by name and introduce yourself as Aryaan, "
        "their Claim Assistant. "
        f"Then tell them they already have {'a saved draft' if n == 1 else f'{n} saved drafts'} (claims saved but "
        f"not submitted) for {'this tour' if n == 1 else 'these tours'}, "
        "name each tour with its route and dates and say briefly what its draft already holds (savedDrafts in the "
        "data). "
        'For what each draft still lacks, say that draft\'s own "missing" sentence from the data once, exactly as '
        "written and word for word, and never a sentence from another draft. Put no label or heading before it, do "
        "not shorten, reword or explain it, and add nothing else about what is missing. "
        f"{origin}"
        "Then say they can upload more tickets and bills with the 📎 button (they are added to the right draft), "
        "continue one of these claims with the buttons, "
        f"{waiting}"
        "End by asking what they would like to do."
    )
