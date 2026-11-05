"""Turn one OCR result into journey and stay extracts.

The OCR provider's JSON has no typed contract in the backend (see
Project-Context/backend/document_and_ocr_apis.md B.4); field names follow the web app's
`ocrResults.ts`. Everything here is a suggestion for the employee to confirm (FRS DOC-04),
and document text is treated as data only (DOC-08).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from tourclaim_agent.domain.claim import Money, TaxStructure, to_date, to_decimal


@dataclass
class JourneyExtract:
    depart_date: date | None
    depart_time: str | None
    arrive_date: date | None
    arrive_time: str | None
    source: str | None
    destination: str | None
    mode: str | None
    distance_km: Any
    money: Money | None


@dataclass
class StayExtract:
    location: str | None
    hotel_name: str | None
    class_of_city: str | None
    check_in_date: date | None
    check_in_time: str | None
    check_out_date: date | None
    check_out_time: str | None
    money: Money | None


@dataclass
class BillExtract:
    ok: bool
    bill_type: str | None
    journeys: list[JourneyExtract]
    stays: list[StayExtract]
    error: str | None = None


def _text(value: Any, limit: int = 120) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())[:limit]
    return text or None


def _time(value: Any) -> str | None:
    text = _text(value, 8)
    if not text:
        return None
    parts = text.replace(".", ":").split(":")
    try:
        hours, minutes = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    if 0 <= hours < 24 and 0 <= minutes < 60:
        return f"{hours:02d}:{minutes:02d}"
    return None


def normalise_time(value: Any) -> str | None:
    """A time as HH:mm, or None. Used for saved rows too, so a bill and its saved row compare equal."""
    return _time(value)


def _tax(value: Any) -> TaxStructure | None:
    text = (_text(value) or "").upper().replace(" ", "_").replace("/", "_")
    if text == "IGST":
        return TaxStructure.IGST
    if text in ("SGST_CGST", "CGST_SGST"):
        return TaxStructure.SGST_CGST
    if text in ("NOT_APPLICABLE", "NA", "N_A", "NONE"):
        return TaxStructure.NA
    return None


def _money(details: Any) -> Money | None:
    if not isinstance(details, dict):
        return None
    billed = (
        to_decimal(details.get("billedAmount"))
        or to_decimal(details.get("claimedAmountWithTax"))
        or to_decimal(details.get("totalClaimedAmount"))
    )
    money = Money(
        billed=billed,
        claimed=billed,  # proposal: claim what was billed; the employee can change it
        tax_structure=_tax(details.get("taxStructure")),
        bill_igst=to_decimal(details.get("billIGST")) or to_decimal(details.get("igst")) or 0,
        bill_cgst=to_decimal(details.get("billCGST")) or to_decimal(details.get("cgst")) or 0,
        bill_sgst=to_decimal(details.get("billSGST")) or to_decimal(details.get("sgst")) or 0,
        invoice_number=_text(details.get("invoiceNo"), 60),
        invoice_date=to_date(details.get("invoiceDate")),
    )
    has_content = money.billed is not None or money.invoice_number
    return money if has_content else None


def _journey(head: dict[str, Any], *, leg: bool) -> JourneyExtract:
    prefix = ("departure", "arrival") if leg else ("start", "end")
    return JourneyExtract(
        depart_date=to_date(head.get(f"{prefix[0]}Date")),
        depart_time=_time(head.get(f"{prefix[0]}Time")),
        arrive_date=to_date(head.get(f"{prefix[1]}Date")),
        arrive_time=_time(head.get(f"{prefix[1]}Time")),
        source=_text(head.get("from"), 60),
        destination=_text(head.get("to"), 60),
        mode=_text(head.get("modeOfTravel"), 40),
        distance_km=to_decimal(head.get("distance")),
        money=_money(head.get("amountDetails")),
    )


def extract_bill(response: Any) -> BillExtract:
    """Read one file's OCR response. Never raises: unreadable input gives ok=False."""
    if not isinstance(response, dict):
        return BillExtract(ok=False, bill_type=None, journeys=[], stays=[], error="unreadable result")
    per_file = (response.get("meta") or {}).get("per_file_results") or []
    first = per_file[0] if per_file and isinstance(per_file[0], dict) else {}
    if per_file and not any(isinstance(p, dict) and p.get("success") for p in per_file):
        return BillExtract(ok=False, bill_type=first.get("bill_type"), journeys=[], stays=[], error=first.get("error"))

    journeys: list[JourneyExtract] = []
    tour = response.get("tourDetails") or {}
    if isinstance(tour, dict):
        if tour.get("from") or tour.get("to") or tour.get("startDate"):
            journeys.append(_journey(tour, leg=False))
        for leg in tour.get("tourLegs") or []:
            if isinstance(leg, dict):
                journeys.append(_journey(leg, leg=True))

    stays: list[StayExtract] = []
    for entry in (response.get("accommodationDetails") or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        stays.append(
            StayExtract(
                location=_text(entry.get("location"), 60),
                hotel_name=_text(entry.get("hotelName"), 100),
                class_of_city=_text(entry.get("cityClass"), 10),
                check_in_date=to_date(entry.get("checkInDate")),
                check_in_time=_time(entry.get("checkInTime")),
                check_out_date=to_date(entry.get("checkOutDate")),
                check_out_time=_time(entry.get("checkOutTime")),
                money=_money(entry.get("invoice")),
            )
        )
    ok = bool(journeys or stays)
    return BillExtract(
        ok=ok,
        bill_type=first.get("bill_type"),
        journeys=journeys,
        stays=stays,
        error=None if ok else "nothing readable found",
    )
