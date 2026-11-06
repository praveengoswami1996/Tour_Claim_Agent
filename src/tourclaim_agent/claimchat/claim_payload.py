"""The SaveRaiseClaimDraft payload.

Port of Tour_React src/lib/claimPayloadBuilder.ts (cleanObjectForPayload, cleanArrayForPayload, buildClaimPayload,
collectNewFiles) and of the pure part of saveDraftFromAssistant in src/pages/RaiseClaim/RaiseClaim.tsx, the only
draft writer the old assistant used on the Raise Claim page (formData {}, no DA rows, section 'Transportation').

Files
-----
There are no browser File objects here. A *new* file on a row is a file dict holding the `documentId` of the bill
the OCR server holds (see ocr_mapping.py); it stands in for the TypeScript's `file.file instanceof File`. A saved
draft's file has a `fileId` (and `isRetained: True`) and is never uploaded again. The payload names no bytes:
`assistant_draft_save` returns the multipart parts in the order the old thunk appended them, as (part name, file
dict) pairs, and the caller downloads each file's bytes (OCR GetFile by `documentId`) to send under that part name
with the file's `name`.

Saved rows (deviations 1 and 2 in Project-Context/agent/old_assistant_parity/01_deviations.md)
-------------------------------------------------------------------------------------------
The old assistant rebuilt a saved draft's rows from the fields it had parsed out of them, which lost data (own
arrangement and free stays became hotel stays, stays lost their InvoiceDate, ...). Here a row may carry the saved
row it was read from, under the key `_saved` (SAVED_ROW_KEY):

    row["_saved"] = {
        "dto": {...},   # the saved row exactly as the draft holds it: one item of the draft's
                        # TransportaionDetailDtos / AccomodationDetailDto / LeaveAvailedDto list, as read
        "row": {...},   # optional: the chat row exactly as it was parsed from that item (claimStateFromDraft),
                        # before any change in the chat; a copy, never the live row
    }

Such a row is written back as `dto`, unchanged, except:
- `Id`, `StartFileIndexCount` and `EndFileIndexCount` are this save's (the row's place and its new files);
- with `row` given, every field the chat changed is applied on top: the row's DTO is built as the TypeScript built
  it, once from `row` (the baseline) and once from the row as it is now; each field whose value differs (compared as
  JSON) takes the new value, or is removed when the new DTO has no such field. Keys are matched without regard to
  case, and keep the saved key's spelling.
A row without `_saved` is built exactly as the TypeScript built it. `_saved` itself is never sent.

When saving into an existing draft, `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` are sent with
every `fileId` still on a row (the old code computed them but never sent them: `hasResumedDraft` was not passed).

JSON
----
`js_json_stringify` is JavaScript's JSON.stringify (the DTO lists are sent as JSON text inside `draftData`, and
`draftData` itself as JSON text): no spaces, numbers as JavaScript prints them (5, not 5.0), NaN and Infinity as
null, keys that are undefined left out.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any, Final, Literal, NotRequired, TypedDict

from .js_compat import (
    UNDEFINED,
    defined,
    is_nullish,
    jget,
    js_int,
    js_number,
    js_number_to_string,
    js_or,
    js_parse_float,
    js_str,
    js_truthy,
    nullish,
)
from .row_order import trip_ends

SAVED_ROW_KEY: Final = "_saved"

# fields of a row's DTO that come from this save, not from the saved row
_POSITIONAL: Final = ("Id", "StartFileIndexCount", "EndFileIndexCount")


# ─── JavaScript object keys and JSON ───────────────────────────────────────────

_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")


def _js_keys(obj: dict[str, Any]) -> list[str]:
    """Object.keys order: array-index keys ("0", "12") first, ascending, then the rest in insertion order."""
    keys = list(obj)
    index = sorted(
        (k for k in keys if isinstance(k, str) and _ARRAY_INDEX.fullmatch(k) and int(k) < 2**32 - 1),
        key=int,
    )
    taken = set(index)
    return [*index, *(k for k in keys if k not in taken)]


_ESCAPES: Final = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _quote(text: str) -> str:
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif code < 0x20 or 0xD800 <= code <= 0xDFFF:
            out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _stringify(value: Any) -> str | None:
    if value is UNDEFINED or callable(value):
        return None
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return js_number_to_string(value) if math.isfinite(value) else "null"
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(_stringify(v) or "null" for v in value) + "]"
    if isinstance(value, dict):
        items = []
        for k in _js_keys(value):
            text = _stringify(value[k])
            if text is not None:
                items.append(f"{_quote(js_str(k))}:{text}")
        return "{" + ",".join(items) + "}"
    raise TypeError(f"cannot serialise {type(value).__name__}")


def js_json_stringify(value: Any) -> str:
    """JSON.stringify(value) for plain dicts, lists, strings, numbers, booleans and None (UNDEFINED is left out)."""
    text = _stringify(value)
    if text is None:
        raise TypeError("JSON.stringify(undefined) is undefined")
    return text


# ─── cleaner utilities ────────────────────────────────────────────────────────


def clean_object_for_payload(obj: Any) -> Any:
    """claimPayloadBuilder.ts:cleanObjectForPayload.

    Drops null, undefined and '' values recursively (and objects left empty); keeps 0 and false. A null, undefined
    or '' item of a list becomes {}. Returns UNDEFINED for null, undefined and ''.
    """
    if obj is None or obj is UNDEFINED or obj == "":
        return UNDEFINED
    if not isinstance(obj, dict | list | tuple):
        return obj
    if isinstance(obj, list | tuple):
        out_list = []
        for item in obj:
            cleaned = clean_object_for_payload(item)
            out_list.append({} if cleaned is UNDEFINED else cleaned)
        return out_list
    out: dict[str, Any] = {}
    for key in _js_keys(obj):
        value = obj[key]
        if value is None or value is UNDEFINED or (isinstance(value, str) and value == ""):
            continue
        if isinstance(value, list | tuple):
            out[key] = clean_object_for_payload(value)  # an array is truthy even when empty
        elif isinstance(value, dict):
            nested = clean_object_for_payload(value)
            if len(nested) > 0:
                out[key] = nested
        else:
            out[key] = value
    return out


def clean_array_for_payload(arr: Any) -> list[Any]:
    """claimPayloadBuilder.ts:cleanArrayForPayload."""
    cleaned = clean_object_for_payload(arr)
    return cleaned if isinstance(cleaned, list) else []


# ─── helpers ──────────────────────────────────────────────────────────────────


def is_new_file(file: Any) -> bool:
    """claimPayloadBuilder.ts:isNewFile: a file to upload (not a saved draft's file); see the module docstring."""
    if js_truthy(jget(file, "fileId")):
        return False
    if jget(file, "isRetained") is True:
        return False
    return not is_nullish(jget(file, "documentId"))


class FileRange(TypedDict):
    startFileIndex: int
    endFileIndex: int
    count: int


def _file_index_ranges(rows: list[Any], file_key: str) -> list[FileRange]:
    """computeFileIndexRanges: each row's new files as indices into its category's list of uploaded files."""
    running = 0
    ranges: list[FileRange] = []
    for row in rows:
        total = len([f for f in nullish(jget(row, file_key), []) if is_new_file(f)])
        if total == 0:
            ranges.append({"startFileIndex": -1, "endFileIndex": -1, "count": 0})
            continue
        ranges.append({"startFileIndex": running, "endFileIndex": running + total - 1, "count": total})
        running += total
    return ranges


def _date_only(val: Any) -> Any:
    """toDateOnlyStr: the part before 'T', or None (a Date is kept as 'YYYY-MM-DD' here, so it is a string too)."""
    if not js_truthy(val):
        return None
    if not isinstance(val, str):
        raise TypeError("val.split is not a function")
    return val.split("T")[0]


def _utf16_prefix(text: str, units: int) -> str:
    raw = text.encode("utf-16-le", "surrogatepass")[: units * 2]
    return raw.decode("utf-16-le", "surrogatepass")


def _first5(val: Any) -> Any:
    """`val ? val.substring(0, 5) : null`"""
    if not js_truthy(val):
        return None
    if not isinstance(val, str):
        raise TypeError("val.substring is not a function")
    return _utf16_prefix(val, 5)


def _float_or_zero(val: Any) -> Any:
    """parseFloat(val) || 0"""
    n = js_parse_float(val)
    return js_int(n) if js_truthy(n) else 0


def _pad2(n: Any) -> str:
    """n.toString().padStart(2, '0')"""
    if is_nullish(n):
        raise TypeError("Cannot read properties of undefined (reading 'toString')")
    text = js_str(n)
    return text if len(text) >= 2 else text.rjust(2, "0")


# ─── the builder ──────────────────────────────────────────────────────────────


class BuildPayloadParams(TypedDict):
    """buildClaimPayload's argument (BuildPayloadParams), with the TypeScript names."""

    transportationRows: list[dict[str, Any]]
    accommodationRows: list[dict[str, Any]]
    daRowsState: list[dict[str, Any]]
    leaveRows: list[dict[str, Any]]
    currentTab: str
    formData: dict[str, Any]
    includeLeave: bool
    includeAccommodation: bool
    empId: int
    empTourPlanId: int
    draftId: NotRequired[int | None]
    retainedTransportationFileIds: NotRequired[list[str] | None]
    retainedAccommodationFileIds: NotRequired[list[str] | None]
    hasResumedDraft: NotRequired[bool]
    myEntitlement: NotRequired[Any]
    isClarificationMode: NotRequired[bool]
    clarificationData: NotRequired[Any]
    isPaidViaCreditCard: NotRequired[bool]
    creditCardPaidAmount: NotRequired[str]


class BuiltPayload(TypedDict):
    draftPayload: dict[str, Any]
    transportDTO: list[Any]
    accommodationDTO: list[Any]
    dailyAllowanceDTO: list[Any]
    leaveDTO: list[Any]
    transportFileRanges: list[FileRange]
    accommodationFileRanges: list[FileRange]


def _param(params: Any, key: str, default: Any = UNDEFINED) -> Any:
    """A destructured parameter: its default applies when it is undefined (not when it is null)."""
    value = jget(params, key)
    return default if value is UNDEFINED else value


def _transport_dto(t: Any, idx: int, rng: FileRange, clarification: bool, id_map: dict[int, Any]) -> dict[str, Any]:
    tax_structure = nullish(jget(t, "taxStructure"), "SGST_CGST")
    is_na = tax_structure == "NA"
    transportation_id = nullish(jget(t, "transportationId"), id_map.get(idx, UNDEFINED)) if clarification else UNDEFINED
    return {
        "Id": transportation_id if clarification else idx,
        "DateOfTravelSource": _date_only(jget(t, "DateOfTravelSource")),
        "TimeOfTravelSource": _first5(jget(t, "TimeOfTravelSource")),
        "Source": jget(t, "source"),
        "ModeOfTravel": jget(t, "ModeOfTravel"),
        "DateOfTravelDestination": _date_only(jget(t, "DateOfTravelDestination")),
        "TimeOfTravelDestiNation": _first5(jget(t, "TimeOfTravelDestiNation")),
        "Destination": jget(t, "destination"),
        "DistanceINKm": _float_or_zero(jget(t, "distanceKm")),
        "BilledAmount": _float_or_zero(jget(t, "travelCost")),
        "ClaimedAmount": _float_or_zero(jget(t, "claimedAmount")),
        "SGSTAmount": 0 if is_na else _float_or_zero(jget(t, "sgst")),
        "CGSTAmount": 0 if is_na else _float_or_zero(jget(t, "cgst")),
        "IGSTAmount": 0 if is_na else _float_or_zero(jget(t, "igst")),
        "BillSGSTAmount": 0 if is_na else _float_or_zero(jget(t, "BillSGSTAmount")),
        "BillCGSTAmount": 0 if is_na else _float_or_zero(jget(t, "BillCGSTAmount")),
        "BillIGSTAmount": 0 if is_na else _float_or_zero(jget(t, "BillIGSTAmount")),
        "TaxStructure": tax_structure,
        "InvoiceNumber": jget(t, "invoiceNo"),
        "InvoiceDate": _date_only(jget(t, "invoiceDate")),
        "StartFileIndexCount": rng["startFileIndex"],
        "EndFileIndexCount": rng["endFileIndex"],
        "Remark": jget(t, "remarks"),
    }


def _accommodation_dto(a: Any, idx: int, rng: FileRange, clarification: bool, id_map: dict[int, Any]) -> dict[str, Any]:
    tax_structure = nullish(nullish(jget(a, "taxStructure"), jget(a, "taxType")), "SGST_CGST")
    is_na = tax_structure == "NA"
    accommodation_id = nullish(jget(a, "accommodationId"), id_map.get(idx, UNDEFINED)) if clarification else UNDEFINED
    return {
        "Id": accommodation_id if clarification else idx,
        "Location": jget(a, "location"),
        "ClassOfCity": jget(a, "classOfCity"),
        "IsOwnArrangement": jget(a, "ownArrangement") == "Yes",
        "IsFreeAccomodation": jget(a, "freeProvidedByCompany") == "Yes",
        "isCompanyLodginPaid": jget(a, "selectedType") == "company_paid",
        "HotelName": js_or(jget(a, "hotelName"), ""),
        "HotelCity": jget(a, "location"),
        "DateOfCheckIn": js_or(_date_only(jget(a, "checkInDate")), ""),
        "TimeOfCheckIn": _first5(jget(a, "checkInTime")),
        "DateOfCheckOut": js_or(_date_only(jget(a, "checkOutDate")), ""),
        "TimeOfCheckOut": _first5(jget(a, "checkOutTime")),
        "BilledAmount": _float_or_zero(jget(a, "billed")),
        "ClaimedAmount": _float_or_zero(jget(a, "claimed")),
        "BillSGSTAmount": 0 if is_na else _float_or_zero(jget(a, "BillSGSTAmount")),
        "BillCGSTAmount": 0 if is_na else _float_or_zero(jget(a, "BillCGSTAmount")),
        "BillIGSTAmount": 0 if is_na else _float_or_zero(jget(a, "BillIGSTAmount")),
        "CGSTAmount": 0 if is_na else js_or(jget(a, "ClaimedCGSTAmount"), 0),
        "SGSTAmount": 0 if is_na else js_or(jget(a, "ClaimedSGSTAmount"), 0),
        "IGSTAmount": 0 if is_na else js_or(jget(a, "ClaimedIGSTAmount"), 0),
        "TaxStructure": tax_structure,
        "InvoiceNumber": js_or(jget(a, "invoiceNo"), ""),
        "InvoiceDate": js_or(jget(a, "invoiceDate"), ""),
        "StartFileIndexCount": rng["startFileIndex"],
        "EndFileIndexCount": rng["endFileIndex"],
        "Remark": jget(a, "remarks"),
    }


def _leave_dto(r: Any, idx: int, clarification: bool, clarification_data: Any) -> dict[str, Any]:
    # 1. in clarification mode, the real id (leaveDetailId) from the original data
    backend_id: Any = 0
    leave_list = jget(clarification_data, "leaveList")
    if clarification and js_truthy(leave_list):
        original = leave_list[idx] if isinstance(leave_list, list) and idx < len(leave_list) else UNDEFINED
        if js_truthy(original) and js_truthy(jget(original, "leaveDetailId")):
            backend_id = jget(original, "leaveDetailId")
    # 2. otherwise always 0 for drafts and new claims
    return defined(
        Id=backend_id,
        LeaveStartDate=jget(r, "startDate"),
        LeaveEndDate=jget(r, "endDate"),
        DADeductable=js_str(jget(r, "daDeductable")).lower() == "yes",
        Amount=js_int(js_number(js_or(jget(r, "amount"), 0))),
    )


def _id_map(items: Any, *keys: str) -> dict[int, Any]:
    """`list.forEach((item, idx) => { if (item.a) map.set(idx, item.a); else if (item.b) ... })`"""
    ids: dict[int, Any] = {}
    for idx, item in enumerate(items or []):
        for key in keys:
            if js_truthy(jget(item, key)):
                ids[idx] = jget(item, key)
                break
    return ids


def _find_key(obj: dict[str, Any], key: str) -> str:
    """The key of `obj` that matches `key` without regard to case (or `key` itself)."""
    return next((k for k in obj if isinstance(k, str) and k.lower() == key.lower()), key)


def _write_back(saved: Any, fresh: dict[str, Any], build: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """A saved row as read, with this save's place and files, and the chat's changes on top (see the docstring)."""
    dto = jget(saved, "dto")
    if not isinstance(dto, dict):
        return fresh
    out = dict(dto)
    baseline_row = jget(saved, "row")
    if isinstance(baseline_row, dict):
        base = build(baseline_row)
        for key in [*base, *(k for k in fresh if k not in base)]:
            if key in _POSITIONAL:
                continue
            new, old = fresh.get(key, UNDEFINED), base.get(key, UNDEFINED)
            if _stringify(new) == _stringify(old):
                continue
            target = _find_key(out, key)
            if new is UNDEFINED:
                out.pop(target, None)
            else:
                out[target] = new
    for key in _POSITIONAL:
        if key in fresh:
            out[_find_key(out, key)] = fresh[key]
    return out


def build_claim_payload(params: BuildPayloadParams | dict[str, Any]) -> BuiltPayload:
    """claimPayloadBuilder.ts:buildClaimPayload (rows carrying `_saved` are written back; see the docstring)."""
    transportation_rows = params["transportationRows"]
    accommodation_rows = params["accommodationRows"]
    da_rows = params["daRowsState"]
    leave_rows = params["leaveRows"]
    form_data = params["formData"]
    include_leave = jget(params, "includeLeave")
    include_accommodation = jget(params, "includeAccommodation")
    draft_id = jget(params, "draftId")
    retained_transport = jget(params, "retainedTransportationFileIds")
    retained_accommodation = jget(params, "retainedAccommodationFileIds")
    has_resumed_draft = _param(params, "hasResumedDraft", False)
    is_paid_via_credit_card = _param(params, "isPaidViaCreditCard", False)
    credit_card_paid_amount = _param(params, "creditCardPaidAmount", "")
    my_entitlement = jget(params, "myEntitlement")
    clarification = js_truthy(_param(params, "isClarificationMode", False))
    clarification_data = jget(params, "clarificationData")

    transport_ids: dict[int, Any] = {}
    accommodation_ids: dict[int, Any] = {}
    da_ids: dict[int, Any] = {}
    if clarification and js_truthy(clarification_data):
        if js_truthy(jget(clarification_data, "transportationList")):
            transport_ids = _id_map(jget(clarification_data, "transportationList"), "transportationId")
        if js_truthy(jget(clarification_data, "accommodationList")):
            accommodation_ids = _id_map(jget(clarification_data, "accommodationList"), "accomodationId")
        if js_truthy(jget(clarification_data, "dailyAllowancList")):
            da_ids = _id_map(jget(clarification_data, "dailyAllowancList"), "daDetailId", "id")

    transport_ranges = _file_index_ranges(transportation_rows, "uploadedFiles")
    accommodation_ranges = _file_index_ranges(accommodation_rows, "billFiles")

    # transportation
    def transport(row: Any, idx: int) -> dict[str, Any]:
        return _transport_dto(row, idx, transport_ranges[idx], clarification, transport_ids)

    transport_dto = clean_array_for_payload([transport(t, idx) for idx, t in enumerate(transportation_rows)])
    for idx, t in enumerate(transportation_rows):
        if SAVED_ROW_KEY in t:
            transport_dto[idx] = _write_back(
                t[SAVED_ROW_KEY], transport_dto[idx], lambda r, i=idx: clean_object_for_payload(transport(r, i))
            )

    # accommodation
    def accommodation(row: Any, idx: int) -> dict[str, Any]:
        return _accommodation_dto(row, idx, accommodation_ranges[idx], clarification, accommodation_ids)

    accommodation_dto: list[Any] = []
    if js_truthy(include_accommodation):
        accommodation_dto = clean_array_for_payload([accommodation(a, idx) for idx, a in enumerate(accommodation_rows)])
        for idx, a in enumerate(accommodation_rows):
            if SAVED_ROW_KEY in a:
                accommodation_dto[idx] = _write_back(
                    a[SAVED_ROW_KEY],
                    accommodation_dto[idx],
                    lambda r, i=idx: clean_object_for_payload(accommodation(r, i)),
                )

    # daily allowance
    raw_da = []
    for idx, row in enumerate(da_rows):
        mapped_id = da_ids.get(idx, UNDEFINED) if clarification else UNDEFINED
        raw_da.append(
            {
                "Id": nullish(nullish(jget(row, "id"), mapped_id), idx + 1),
                "Date": _date_only(jget(row, "startDate")),
                "SourceTime": _first5(jget(row, "startTime")),
                "EndTime": _first5(jget(row, "endTime")),
                "DASlab": js_str(js_or(jget(my_entitlement, "daAmount"), 0)),
                "TotalHoursMinutes": f"{_pad2(jget(row, 'Hours'))}:{_pad2(jget(row, 'Minutes'))}",
                "PercentageAdmissible": js_int(js_number(js_or(jget(row, "DAPercentage"), 0))),
                "Amount": js_int(js_number(js_or(jget(row, "DAOfTheDay"), 0))),
                "IsApplicable": jget(row, "applicable"),
                "TourNumber": 1,
            }
        )
    daily_allowance_dto = clean_array_for_payload(raw_da)

    # leave (not cleaned)
    leave_dto: list[Any] = []
    if js_truthy(include_leave):
        for idx, r in enumerate(leave_rows):
            dto = _leave_dto(r, idx, clarification, clarification_data)
            if SAVED_ROW_KEY in r:
                dto = _write_back(
                    r[SAVED_ROW_KEY], dto, lambda b, i=idx: _leave_dto(b, i, clarification, clarification_data)
                )
            leave_dto.append(dto)

    # the first departure and the last arrival by time, not by place in the list
    ends = trip_ends(transportation_rows)
    source = jget(ends["first"], "source")
    destination = jget(ends["last"], "destination")
    departure_date = jget(ends["first"], "DateOfTravelSource")

    documents = jget(form_data, "documents")
    has_documents = js_or(jget(documents, "tickets"), jget(documents, "hotel"))
    documents_attached = (
        {
            "IsApprovedTourProgramme": js_or(jget(documents, "tickets"), False),
            "IsTickets_BoardingPass_Bills": js_or(jget(documents, "tickets"), False),
            "IsHotelBills": js_or(jget(documents, "hotel"), False),
        }
        if js_truthy(has_documents)
        else UNDEFINED
    )

    raw: dict[str, Any] = {
        "DraftId": draft_id,
        "EmpId": params["empId"],
        "EmpTourPlanId": params["empTourPlanId"],
        "LastEditedSection": params["currentTab"],
    }
    for key, name in (
        ("recipientId", "RecipentId"),
        ("purposeOfTour", "PurposeOfTour"),
        ("claimAmount", "ClaimAmount"),
        ("paidAmount", "PaidAmount"),
        ("paymentReferenceNo", "PaymentReferenceNo"),
    ):
        if js_truthy(jget(form_data, key)):
            raw[name] = jget(form_data, key)
    if js_truthy(source):
        raw["Source"] = source
    if js_truthy(destination):
        raw["Destination"] = destination
    if js_truthy(departure_date):
        raw["TantetiveDateOfDeparture"] = departure_date
    if len(da_rows) > 0:
        raw["NoOfDays"] = len(da_rows)
    raw["IsPaidViaCreditCard"] = is_paid_via_credit_card
    if js_truthy(is_paid_via_credit_card) and js_truthy(credit_card_paid_amount):
        raw["CreditCardPaidAmount"] = _float_or_zero(credit_card_paid_amount)

    if len(transport_dto) > 0:
        raw["TransportaionDetailDtos"] = js_json_stringify(transport_dto)
    if js_truthy(include_accommodation) and len(accommodation_dto) > 0:
        raw["AccomodationDetailDto"] = js_json_stringify(accommodation_dto)
    if len(daily_allowance_dto) > 0:
        raw["DailyAllowanceDto"] = js_json_stringify(daily_allowance_dto)
    if len(leave_dto) > 0:
        raw["LeaveAvailedDto"] = js_json_stringify(leave_dto)
        raw["IsLeaveAvailedwithbetweentour"] = True
    if documents_attached is not UNDEFINED:
        raw["DocumentsAttached"] = documents_attached

    if js_truthy(has_resumed_draft):
        raw["RetainedTransportationFileIds"] = [] if is_nullish(retained_transport) else retained_transport
        if not is_nullish(retained_accommodation):
            raw["RetainedAccomodationFileIds"] = retained_accommodation

    # final top-level scrub
    draft_payload = clean_object_for_payload(raw)
    return {
        "draftPayload": draft_payload,
        "transportDTO": transport_dto,
        "accommodationDTO": accommodation_dto,
        "dailyAllowanceDTO": daily_allowance_dto,
        "leaveDTO": leave_dto,
        "transportFileRanges": transport_ranges,
        "accommodationFileRanges": accommodation_ranges,
    }


class NewFiles(TypedDict):
    transportationFiles: list[dict[str, Any]]
    accommodationFiles: list[dict[str, Any]]


def collect_new_files(transportation_rows: list[Any], accommodation_rows: list[Any]) -> NewFiles:
    """claimPayloadBuilder.ts:collectNewFiles: the files to upload, per category, in row order."""
    return {
        "transportationFiles": [
            f for r in transportation_rows for f in nullish(jget(r, "uploadedFiles"), []) if is_new_file(f)
        ],
        "accommodationFiles": [
            f for r in accommodation_rows for f in nullish(jget(r, "billFiles"), []) if is_new_file(f)
        ],
    }


# ─── saveDraftFromAssistant (RaiseClaim.tsx) ──────────────────────────────────


def assistant_save_target(
    all_drafts: list[Any], tour_id: Any, overwrite: bool, read_draft: Callable[[], Any]
) -> tuple[Literal["exists", "save"], Any]:
    """Which draft saveDraftFromAssistant writes: ("exists", None) when a draft is there and may not be overwritten,
    else ("save", draftId), draftId None for a new draft.

    `all_drafts` is the page's GetAllRaiseClaimDrafts list. `read_draft` is called only when the tour is not in it:
    it returns the body of GET Claim/GetRaiseClaimDraft {empId, empTourPlanId}, or None for a 404 (any other error
    is raised, and the save fails).
    """
    known = next((d for d in all_drafts if jget(d, "empTourPlanId") == tour_id), None)
    if known is not None:
        if not overwrite:
            return "exists", None
        return "save", nullish(jget(known, "id"), None)
    data = read_draft()
    if jget(data, "statusCode") == 200 and js_truthy(jget(data, "data")):
        if not overwrite:
            return "exists", None
        return "save", nullish(jget(jget(data, "data"), "draftId"), None)
    return "save", None


FilePart = tuple[Literal["transportationFiles", "accommodationFiles"], dict[str, Any]]


class AssistantDraftSave(TypedDict):
    # the draftData object, and the JSON text sent as the multipart part `draftData`
    draftData: dict[str, Any]
    draftDataText: str
    # the files after draftData, in order: (part name, file dict); fetch each one's bytes by its documentId
    files: list[FilePart]


def _file_ids(rows: list[Any], key: str) -> list[Any]:
    return [jget(f, "fileId") for r in rows for f in nullish(jget(r, key), []) if js_truthy(jget(f, "fileId"))]


def assistant_draft_save(
    state: Any, *, emp_id: Any, tour_id: Any, draft_id: Any = None, overwrite: bool = False
) -> AssistantDraftSave:
    """The POST Claim/SaveRaiseClaimDraft request of saveDraftFromAssistant (RaiseClaim.tsx).

    `state` is the chat's claim in stored order (asStored); `draft_id` comes from assistant_save_target. An
    overwritten draft keeps the bills it already holds: the rows say which of them are still in the claim, and those
    file ids are sent as retained (deviation 2).
    """
    overwriting = bool(overwrite) and draft_id is not None
    built = build_claim_payload(
        {
            "transportationRows": state["transportationRows"],
            "accommodationRows": state["accommodationRows"],
            "daRowsState": [],
            "leaveRows": state["leaveRows"],
            "currentTab": "Transportation",
            "formData": {},
            "includeLeave": state["includeLeave"],
            "includeAccommodation": state["includeAccommodation"],
            "empId": emp_id,
            "empTourPlanId": tour_id,
            "draftId": draft_id,
            "retainedTransportationFileIds": (
                _file_ids(state["transportationRows"], "uploadedFiles") if overwriting else None
            ),
            "retainedAccommodationFileIds": (
                _file_ids(state["accommodationRows"], "billFiles") if overwriting else None
            ),
            # deviation 2: the old code did not pass this, so the retained ids were never sent
            "hasResumedDraft": overwriting,
        }
    )
    files = collect_new_files(state["transportationRows"], state["accommodationRows"])
    parts: list[FilePart] = [
        *(("transportationFiles", f) for f in files["transportationFiles"]),
        *(("accommodationFiles", f) for f in files["accommodationFiles"]),
    ]
    payload = built["draftPayload"]
    return {"draftData": payload, "draftDataText": js_json_stringify(payload), "files": parts}
