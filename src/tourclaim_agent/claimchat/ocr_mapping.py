"""Turns the Tour OCR result into Raise Claim (outside jurisdiction) form rows.

Port of Tour_React src/lib/claimChatbot/ocrMapping.ts: the same mapping as the OCR form on the `ocrform` branch,
with the accommodation types matched to the values AccommodationSection uses ('company_free' / 'company_paid',
not the OCR's names). Also home of the date and time readers every other module uses (to_iso_date, to_time).

A bill is a dict {"file": {"name", "size", "type", "documentId"?}, "category"?, "hash"?}. Where the browser put
the File object on a row, the row's file dict here carries the bill file's `documentId` (when it has one).
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal, NotRequired, TypedDict

from .form_rows import AccommodationRow, RowFile, TransportationRow
from .js_compat import (
    JS_WS,
    UNDEFINED,
    defined,
    is_nullish,
    jget,
    js_add,
    js_local_date,
    js_or,
    js_random,
    js_str,
    js_trim,
    js_truthy,
    local_ymd,
    now_ms,
    nullish,
    opt_trim,
    utc_iso_string,
    v8_date_parse,
)
from .ocr_results import OcrClaimResult

TaxStructure = Literal["IGST", "SGST_CGST", "NA"]


def _pad2(n: int) -> str:
    return str(n).rjust(2, "0")


def file_key(f: dict[str, Any]) -> str:
    """ocrMapping.ts:fileKey: same name and same size = the same file (a phone often names every photo "image.jpg")."""
    return f"{js_str(jget(f, 'name'))}|{js_str(nullish(jget(f, 'size'), 0))}"


def is_real_day(y: int, m: int, d: int) -> bool:
    """ocrMapping.ts:isRealDay: is this a real calendar day (no 31 Feb, no 31 Apr)? Years 0-99 never are (JS Date)."""
    return js_local_date(y, m - 1, d) == (y, m, d)


_ISO = re.compile(r"^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})")
_DMY = re.compile(r"^([0-9]{1,2})[/.-]([0-9]{1,2})[/.-]([0-9]{4}|[0-9]{2})\Z")
_HAS_YEAR = re.compile(r"\b(19|20)[0-9]{2}\b", re.ASCII)
_ORDINAL = re.compile(r"([0-9])(st|nd|rd|th)\b", re.ASCII | re.IGNORECASE)


def to_iso_date(value: Any) -> str:
    """ocrMapping.ts:toIsoDate.

    Any date the OCR or the employee gives -> 'YYYY-MM-DD' (the form's format), or '' if unreadable.
    A date with no year ("05 Oct") is treated as unreadable rather than guessed: the assistant then asks.
    Text that is neither ISO nor day-first numeric goes through the browser's parser (V8 Date.parse, js_compat).
    """
    if not js_truthy(value):
        return ""
    s = js_trim(js_str(value))
    iso = _ISO.match(s)
    if iso:
        y, m, d = int(iso[1]), int(iso[2]), int(iso[3])
        return f"{iso[1]}-{_pad2(m)}-{_pad2(d)}" if is_real_day(y, m, d) else ""
    # Indian bills write day first: 05/10/2026, 05-10-2026, 05.10.2026, and 05/10/26
    dmy = _DMY.match(s)
    if dmy:
        year = 2000 + int(dmy[3]) if len(dmy[3]) == 2 else int(dmy[3])
        return (
            f"{year}-{_pad2(int(dmy[2]))}-{_pad2(int(dmy[1]))}" if is_real_day(year, int(dmy[2]), int(dmy[1])) else ""
        )
    if not _HAS_YEAR.search(s):
        return ""
    # "5th October 2026", "Oct 5, 2026"
    ms = v8_date_parse(_ORDINAL.sub(r"\1", s))
    if ms is None:
        return ""
    y, m, d = local_ymd(ms)
    return f"{y}-{_pad2(m)}-{_pad2(d)}"


_TIME_SUFFIX = re.compile(JS_WS + r"*(hrs?|hours?|h)\.?\Z")
_TIME_HM = re.compile(r"^([0-9]{1,2})[:.]([0-9]{1,2})(?::[0-9]{2})?" + JS_WS + r"*([ap]\.?m\.?)?\Z")
_TIME_H_MERIDIEM = re.compile(r"^([0-9]{1,2})()" + JS_WS + r"*([ap]\.?m\.?)\Z")
_TIME_HHMM = re.compile(r"^([0-9]{2})([0-9]{2})()\Z")


def to_time(value: Any) -> str:
    """ocrMapping.ts:toTime: '9:5', '09:05:00', '9.05 PM', '10 AM', '1030', '10:30 hrs', 'noon' -> 'HH:mm', or ''."""
    if not js_truthy(value):
        return ""
    raw = js_trim(js_str(value)).lower()
    if raw == "noon":
        return "12:00"
    if raw == "midnight":
        return "00:00"
    text = _TIME_SUFFIX.sub("", raw, count=1)
    m = _TIME_HM.match(text) or _TIME_H_MERIDIEM.match(text) or _TIME_HHMM.match(text)
    if not m:
        return ""
    h = int(m[1])
    minute = 0 if m[2] == "" else int(m[2])
    meridiem = m[3].replace(".", "") if m[3] is not None else None
    if meridiem == "pm" and h != 12:
        h += 12
    if meridiem == "am" and h == 12:
        h = 0
    if h > 23 or minute > 59:
        return ""
    return f"{_pad2(h)}:{_pad2(minute)}"


def _to_date_obj(value: Any) -> Any:
    """toDateObj: the form keeps a JavaScript Date (local midnight); here the same day as 'YYYY-MM-DD', or UNDEFINED."""
    iso = to_iso_date(value)
    return iso if iso else UNDEFINED


def _map_tax_structure(tax: Any) -> TaxStructure:
    if tax == "NOT_APPLICABLE":
        return "NA"
    if tax == "IGST":
        return "IGST"
    return "SGST_CGST"


TRAVEL_MODES: Final = (
    "air",
    "rail",
    "personal-vehicle",
    "public-transport",
    "official-entitled",
    "official-beyond",
    "official-dedicated",
)


def map_travel_mode(mode: Any) -> str:
    """ocrMapping.ts:mapTravelMode: any travel mode text -> one of TRAVEL_MODES, or 'select'."""
    if not js_truthy(mode):
        return "select"
    lower = mode.lower()
    if lower in TRAVEL_MODES:
        return lower
    if "public" in lower or "bus" in lower or "taxi" in lower or "cab" in lower or "auto" in lower:
        return "public-transport"
    if "personal" in lower or "own" in lower:
        return "personal-vehicle"
    if "air" in lower or "flight" in lower:
        return "air"
    if "rail" in lower or "train" in lower:
        return "rail"
    if "entitled" in lower:
        return "official-entitled"
    if "beyond" in lower:
        return "official-beyond"
    if "dedicated" in lower:
        return "official-dedicated"
    return "select"


def accommodation_type_fields(type_: Any) -> dict[str, str]:
    """ocrMapping.ts:accommodationTypeFields: accommodation type -> the flags AccommodationSection and DA read."""
    t = nullish(type_, "").lower()
    if "own" in t:
        return {
            "selectedType": "own_arrangement",
            "ownArrangement": "Yes",
            "freeProvidedByCompany": "No",
            "companyPaid": "No",
        }
    if "free" in t:
        return {
            "selectedType": "company_free",
            "ownArrangement": "No",
            "freeProvidedByCompany": "Yes",
            "companyPaid": "No",
        }
    if "paid" in t or "company" in t:
        return {
            "selectedType": "company_paid",
            "ownArrangement": "No",
            "freeProvidedByCompany": "No",
            "companyPaid": "Yes",
        }
    return {"selectedType": "hotel", "ownArrangement": "No", "freeProvidedByCompany": "No", "companyPaid": "No"}


def _to_file_entry(file: dict[str, Any]) -> RowFile:
    """toFileEntry: the bill as a row's file (the browser's File and blob url are the server's documentId here)."""
    entry: dict[str, Any] = {
        "id": f"file-{now_ms()}-{js_str(js_random())}",
        "name": file["name"],
        "size": jget(file, "size"),
        "type": jget(file, "type"),
        "uploadDate": utc_iso_string(now_ms()),
    }
    if "documentId" in file:
        entry["documentId"] = file["documentId"]
    return defined(**entry)  # type: ignore[return-value]


def _num(v: Any) -> str:
    return "0" if is_nullish(v) or v == "" else js_str(v)


class MappedOcrRows(TypedDict):
    transportationRows: list[TransportationRow]
    accommodationRows: list[AccommodationRow]
    # files the OCR could not read
    failedFiles: list[str]
    # files the OCR read but found no journey or stay in (not tied to any row: which claim they are for is not known)
    unmatchedFiles: list[str]


BillCategory = Literal["transport", "accommodation"]


class StagedBill(TypedDict):
    file: dict[str, Any]
    # where an unmatched bill goes; without it, the first journey (or the first stay if there is none)
    category: NotRequired[BillCategory]
    # SHA-256 of the bytes (see file_hash.py)
    hash: NotRequired[str | None]


def map_ocr_to_rows(result: OcrClaimResult, bills: list[StagedBill]) -> MappedOcrRows:
    """ocrMapping.ts:mapOcrToRows: the journeys and stays in an OCR result, each with the bill file it came from."""
    files = [b["file"] for b in bills]
    used: set[str] = set()

    def files_for(source_name: Any) -> list[RowFile]:
        match = next((f for f in files if f["name"] == source_name), None) if js_truthy(source_name) else None
        if match is None:
            return []
        used.add(match["name"])
        return [_to_file_entry(match)]

    def transport_row(row_id: str, leg: dict[str, Any], amount: Any) -> TransportationRow:
        distance = jget(leg, "distance")
        return defined(  # type: ignore[return-value]
            id=row_id,
            DateOfTravelSource=to_iso_date(jget(leg, "departureDate")),
            TimeOfTravelSource=to_time(jget(leg, "departureTime")),
            source=js_or(jget(leg, "from"), ""),
            ModeOfTravel=map_travel_mode(jget(leg, "modeOfTravel")),
            DateOfTravelDestination=to_iso_date(jget(leg, "arrivalDate")),
            TimeOfTravelDestiNation=to_time(jget(leg, "arrivalTime")),
            destination=js_or(jget(leg, "to"), ""),
            distanceKm=js_or(UNDEFINED if is_nullish(distance) else js_str(distance), ""),
            travelCost=_num(jget(amount, "billedAmount")),
            totalBilledAmount=_num(jget(amount, "billedAmount")),
            claimedAmount=_num(nullish(jget(amount, "totalClaimedAmount"), jget(amount, "claimedAmount"))),
            invoiceNo=js_or(jget(amount, "invoiceNo"), ""),
            invoiceDate=_to_date_obj(jget(amount, "invoiceDate")),
            taxStructure=_map_tax_structure(jget(amount, "taxStructure")),
            cgst=_num(jget(amount, "cgst")),
            sgst=_num(jget(amount, "sgst")),
            igst=_num(jget(amount, "igst")),
            BillCGSTAmount=_num(jget(amount, "billCGST")),
            BillSGSTAmount=_num(jget(amount, "billSGST")),
            BillIGSTAmount=_num(jget(amount, "billIGST")),
            uploadedFiles=files_for(jget(amount, "sourceFileName")),
            includeTravelCosts=js_truthy(jget(amount, "billedAmount")),
            billNotRequired=False,
            isPaidUsingCard=False,
            remarks="",
            financeRemark="",
        )

    # An unreadable bill still comes back as a tour with empty fields; such a leg must not replace real rows.
    def has_content(leg: Any) -> bool:
        return js_truthy(leg) and (
            js_truthy(opt_trim(jget(leg, "from")))
            or js_truthy(opt_trim(jget(leg, "to")))
            or bool(to_iso_date(jget(leg, "departureDate")))
            or bool(to_iso_date(jget(leg, "arrivalDate")))
        )

    tour = jget(result, "tourDetails")
    transportation_rows: list[TransportationRow] = []
    stamp = now_ms()
    if js_truthy(tour) and has_content(
        defined(
            **{"from": jget(tour, "from")},
            to=jget(tour, "to"),
            departureDate=jget(tour, "startDate"),
            arrivalDate=jget(tour, "endDate"),
        )
    ):
        transportation_rows.append(
            transport_row(
                f"transport-ocr-main-{stamp}",
                defined(
                    **{"from": jget(tour, "from")},
                    to=jget(tour, "to"),
                    departureDate=jget(tour, "startDate"),
                    departureTime=jget(tour, "startTime"),
                    arrivalDate=jget(tour, "endDate"),
                    arrivalTime=jget(tour, "endTime"),
                    modeOfTravel=jget(tour, "modeOfTravel"),
                    distance=jget(tour, "distance"),
                ),
                jget(tour, "amountDetails"),
            )
        )
    for idx, leg in enumerate(nullish(jget(tour, "tourLegs"), [])):
        if has_content(leg):
            transportation_rows.append(
                transport_row(f"transport-ocr-leg-{idx}-{stamp}", leg, jget(leg, "amountDetails"))
            )

    entries = nullish(jget(jget(result, "accommodationDetails"), "entries"), [])
    kept = [
        acc
        for acc in entries
        if js_truthy(opt_trim(jget(acc, "location")))
        or to_iso_date(jget(acc, "checkInDate"))
        or to_iso_date(jget(acc, "checkOutDate"))
    ]
    accommodation_rows: list[AccommodationRow] = []
    for idx, acc in enumerate(kept):
        inv = jget(acc, "invoice")
        tax_structure = _map_tax_structure(jget(inv, "taxStructure"))
        cgst, sgst, igst = (js_or(jget(inv, k), 0) for k in ("cgst", "sgst", "igst"))
        row = {
            "id": f"accom-ocr-{idx}-{stamp}",
            "location": js_or(jget(acc, "location"), ""),
            "classOfCity": js_or(jget(acc, "cityClass"), "Select"),
            "checkInDate": to_iso_date(jget(acc, "checkInDate")),
            "checkInTime": to_time(jget(acc, "checkInTime")),
            "checkOutDate": to_iso_date(jget(acc, "checkOutDate")),
            "checkOutTime": to_time(jget(acc, "checkOutTime")),
            **accommodation_type_fields(jget(acc, "accommodationType")),
            "hotelName": js_or(jget(acc, "hotelName"), ""),
            "billed": _num(jget(inv, "billedAmount")),
            "tax": js_str(js_add(js_add(cgst, sgst), igst)),
            "totalBilled": _num(jget(inv, "billedAmount")),
            "claimed": _num(nullish(jget(inv, "claimedAmountWithTax"), jget(inv, "claimedAmount"))),
            "invoiceNo": js_or(jget(inv, "invoiceNo"), ""),
            "invoiceDate": to_iso_date(jget(inv, "invoiceDate")),
            "taxType": tax_structure,
            "taxStructure": tax_structure,
            "ClaimedCGSTAmount": cgst,
            "ClaimedSGSTAmount": sgst,
            "ClaimedIGSTAmount": igst,
            "BillCGSTAmount": _num(jget(inv, "billCGST")),
            "BillSGSTAmount": _num(jget(inv, "billSGST")),
            "BillIGSTAmount": _num(jget(inv, "billIGST")),
            "billFiles": files_for(nullish(jget(acc, "sourceFileName"), jget(inv, "sourceFileName"))),
            "remarks": "",
            "financeRemark": "",
            "isPaidUsingCard": False,
        }
        accommodation_rows.append(row)  # type: ignore[arg-type]

    per_file = nullish(jget(jget(result, "meta"), "per_file_results"), [])
    failed_files = [jget(r, "filename") for r in per_file if not js_truthy(jget(r, "success"))]

    # a bill the OCR read but tied to no row: the chat decides where it goes (see attach_unmatched)
    unmatched_files = [
        b["file"]["name"] for b in bills if b["file"]["name"] not in used and b["file"]["name"] not in failed_files
    ]

    return {
        "transportationRows": transportation_rows,
        "accommodationRows": accommodation_rows,
        "failedFiles": failed_files,
        "unmatchedFiles": unmatched_files,
    }


class AttachedRows(TypedDict):
    transportationRows: list[TransportationRow]
    accommodationRows: list[AccommodationRow]
    attached: list[str]


def attach_unmatched(rows: dict[str, Any], files: list[str], bills: list[StagedBill]) -> AttachedRows:
    """ocrMapping.ts:attachUnmatched.

    Bills the OCR read but tied to no row still have to reach finance, once it is known which claim they are for:
    each goes to the first row of its own kind (a stay for a hotel bill, otherwise the first journey).
    """
    journeys = [{**r} for r in rows["transportationRows"]]
    stays = [{**r} for r in rows["accommodationRows"]]
    attached: list[str] = []
    for name in files:
        bill = next((b for b in bills if b["file"]["name"] == name), None)
        if bill is None:
            continue
        entry = _to_file_entry(bill["file"])
        if bill.get("category") == "accommodation" and len(stays) > 0:
            stays[0]["billFiles"] = [*stays[0]["billFiles"], entry]
        elif len(journeys) > 0:
            journeys[0]["uploadedFiles"] = [*journeys[0]["uploadedFiles"], entry]
        elif len(stays) > 0:
            stays[0]["billFiles"] = [*stays[0]["billFiles"], entry]
        else:
            continue
        attached.append(name)
    return {"transportationRows": journeys, "accommodationRows": stays, "attached": attached}  # type: ignore[typeddict-item]
