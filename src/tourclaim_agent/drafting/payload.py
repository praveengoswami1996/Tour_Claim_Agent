"""Build the SaveRaiseClaimDraft request exactly as the .NET API expects it (FRS EXE-11).

Contract (Project-Context/backend/tour_claim_api_reference.md §0.3, §4.3, and
TourProgram.Model/Dtos/Draft/RaiseClaimDraftDto.cs):
- multipart: `draftData` (JSON) + file parts `transportationFiles` / `accommodationFiles`;
- the row arrays are JSON *strings* inside draftData, with the backend's spellings
  (`TransportaionDetailDtos`, `AccomodationDetailDto`, `TimeOfTravelDestiNation`, ...);
- files map to rows through inclusive 0-based `StartFileIndexCount`/`EndFileIndexCount`
  into that category's flat file list; a row without files must send -1/-1;
- dates `YYYY-MM-DD`, times `HH:mm`.
Daily allowance and leave are left empty: the claim form calculates DA (DEC-1 interim).
RecipentId is left empty: the form resolves the bill-passing user (DEP-6).

Saving into an existing draft (BIL-44, mirrors the React `buildClaimPayload` + `collectNewFiles`):
- saved rows (`Origin.SAVED`) are written back exactly as read, in their positions, with -1/-1;
- `RetainedTransportationFileIds` / `RetainedAccomodationFileIds` list every saved file;
- `StartFileIndexCount`/`EndFileIndexCount` count only the newly attached files;
- everything else in the saved draft (leave, daily allowance, remarks, ...) is carried through.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from tourclaim_agent.domain.claim import ClaimWorkspace, Money, Origin, TaxStructure


@dataclass(frozen=True)
class DraftRequest:
    draft_data: dict[str, Any]
    transport_file_ids: list[int]  # OCR document IDs, in upload order
    stay_file_ids: list[int]


def _num(value: Decimal | None) -> float:
    return float(value) if value is not None else 0.0


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _tax_fields(money: Money) -> dict[str, Any]:
    # Claimed tax follows the bill only when the full billed amount is claimed.
    full = money.billed is not None and money.claimed == money.billed
    return {
        "TaxStructure": (money.tax_structure or TaxStructure.NA).value,
        "BillIGSTAmount": _num(money.bill_igst),
        "BillCGSTAmount": _num(money.bill_cgst),
        "BillSGSTAmount": _num(money.bill_sgst),
        "IGSTAmount": _num(money.bill_igst) if full else 0.0,
        "CGSTAmount": _num(money.bill_cgst) if full else 0.0,
        "SGSTAmount": _num(money.bill_sgst) if full else 0.0,
        "InvoiceNumber": money.invoice_number,
        "InvoiceDate": _iso(money.invoice_date),
    }


def _ranges(rows, files: list[int]) -> list[tuple[int, int]]:
    """File index ranges over new files only. Saved rows hold no new files (their files are retained)."""
    ranges = []
    for row in rows:
        if row.bill_ids and row.origin is not Origin.SAVED:
            start = len(files)
            files.extend(row.bill_ids)
            ranges.append((start, len(files) - 1))
        else:
            ranges.append((-1, -1))
    return ranges


def claimed_total(ws: ClaimWorkspace) -> Decimal:
    return sum((r.money.claimed or Decimal(0) for r in [*ws.transport, *ws.stays]), Decimal(0))


# Field names as the .NET DTOs spell them. Saved content may arrive in camelCase; it is written back
# under these names so nothing is sent twice under two spellings.
_ROW_ALIASES = {
    "transportationdetaildtos": "TransportaionDetailDtos",
    "accommodationdetaildto": "AccomodationDetailDto",
}
# Read-only parts of a saved draft that are never sent back.
_NOT_SENT = {
    "draftdata",
    "draftid",
    "lastsaveddate",
    "transportationfilereferences",
    "accomodationfilereferences",
    "accommodationfilereferences",
}
# Lists the API takes as JSON text inside draftData.
_JSON_TEXT_FIELDS = {"DailyAllowanceDto", "LeaveAvailedDto"}
# Set from the workspace, never copied from the saved draft.
_OWNED = {
    "DraftId",
    "EmpId",
    "EmpTourPlanId",
    "TransportaionDetailDtos",
    "AccomodationDetailDto",
    "ClaimAmount",
    "DocumentsAttached",
    "RetainedTransportationFileIds",
    "RetainedAccomodationFileIds",
}


def _canonical(raw: dict[str, Any], names: list[str]) -> dict[str, Any]:
    by_lower = {n.lower(): n for n in names}
    by_lower.update(_ROW_ALIASES)
    return {by_lower.get(k.lower(), k): v for k, v in raw.items() if isinstance(k, str)}


def _saved_row(raw: dict[str, Any], names: list[str]) -> dict[str, Any]:
    row = _canonical(raw, names)
    row["StartFileIndexCount"] = -1
    row["EndFileIndexCount"] = -1
    return row


def _carry_through(ws: ClaimWorkspace, draft: dict[str, Any], new_claimed: Decimal) -> None:
    """Merge the saved draft's own content into the request (§5.2): nothing entered in the form is lost."""
    saved = _canonical({k: v for k, v in (ws.raw_draft or {}).items() if k.lower() not in _NOT_SENT}, list(draft))
    for key, value in saved.items():
        if key in _OWNED:
            continue
        if key in _JSON_TEXT_FIELDS and isinstance(value, list | dict):
            value = json.dumps(value, ensure_ascii=False)
        draft[key] = value
    saved_amount = saved.get("ClaimAmount")
    if isinstance(saved_amount, int | float):
        # The saved total (which may include allowances entered in the form) plus the new rows.
        draft["ClaimAmount"] = float(Decimal(str(saved_amount)) + new_claimed)
    attached = saved.get("DocumentsAttached")
    if isinstance(attached, dict):
        merged = dict(attached)
        for key, value in draft["DocumentsAttached"].items():
            if isinstance(value, bool):
                merged[key] = bool(merged.get(key)) or value
        draft["DocumentsAttached"] = merged


def build_draft_request(ws: ClaimWorkspace, emp_id: int) -> DraftRequest:
    transport_files: list[int] = []
    stay_files: list[int] = []
    transport = []
    for row, (start, end) in zip(ws.transport, _ranges(ws.transport, transport_files), strict=True):
        if row.origin is Origin.SAVED and row.raw is not None:
            transport.append(_saved_row(row.raw, TRANSPORT_FIELDS))
            continue
        transport.append(
            {
                "Id": 0,
                "DateOfTravelSource": _iso(row.depart_date),
                "TimeOfTravelSource": row.depart_time or "",
                "Source": row.source,
                "ModeOfTravel": row.mode or "",
                "DateOfTravelDestination": _iso(row.arrive_date),
                "TimeOfTravelDestiNation": row.arrive_time or "",
                "Destination": row.destination,
                "DistanceINKm": _num(row.distance_km),
                "BilledAmount": _num(row.money.billed),
                "ClaimedAmount": _num(row.money.claimed),
                "StartFileIndexCount": start,
                "EndFileIndexCount": end,
                "Remark": None,
                "TourNumber": 1,
                **_tax_fields(row.money),
            }
        )
    stays = []
    for row, (start, end) in zip(ws.stays, _ranges(ws.stays, stay_files), strict=True):
        if row.origin is Origin.SAVED and row.raw is not None:
            stays.append(_saved_row(row.raw, STAY_FIELDS))
            continue
        stays.append(
            {
                "Id": 0,
                "Location": row.location or "",
                "ClassOfCity": row.class_of_city or "",
                "IsOwnArrangement": row.own_arrangement,
                "IsFreeAccomodation": row.free_accommodation,
                "isCompanyLodginPaid": False,
                "HotelName": row.hotel_name,
                "HotelCity": row.location or "",
                "DateOfCheckIn": _iso(row.check_in_date),
                "TimeOfCheckIn": row.check_in_time or "",
                "DateOfCheckOut": _iso(row.check_out_date),
                "TimeOfCheckOut": row.check_out_time or "",
                "BilledAmount": _num(row.money.billed),
                "ClaimedAmount": _num(row.money.claimed),
                "StartFileIndexCount": start,
                "EndFileIndexCount": end,
                "Remark": None,
                "TourNumber": 1,
                **_tax_fields(row.money),
            }
        )
    days = (ws.tour_end - ws.tour_start).days + 1 if ws.tour_start and ws.tour_end else None
    draft = {
        "DraftId": ws.draft_id,
        "EmpId": emp_id,
        "EmpTourPlanId": ws.tour_id,
        "RecipentId": None,
        "PurposeOfTour": ws.purpose,
        "Source": ws.source,
        "Destination": ws.destination,
        "TantetiveDateOfDeparture": _iso(ws.tour_start),
        "NoOfDays": days,
        "ClaimAmount": _num(claimed_total(ws)),
        "IsPaidViaCreditCard": False,
        "TransportaionDetailDtos": json.dumps(transport, ensure_ascii=False),
        "AccomodationDetailDto": json.dumps(stays, ensure_ascii=False),
        "DailyAllowanceDto": "[]",
        "LeaveAvailedDto": "[]",
        "IsLeaveAvailedwithbetweentour": False,
        "DocumentsAttached": {
            "IsApprovedTourProgramme": None,
            "IsTickets_BoardingPass_Bills": bool(transport_files),
            "IsHotelBills": bool(stay_files),
        },
        "LastEditedSection": None,
        "IsIncidentRelatedClaim": False,
        "IsCancelationClaim": False,
    }
    if ws.raw_draft is not None:
        draft["RetainedTransportationFileIds"] = [f.file_id for r in ws.transport for f in r.saved_files]
        draft["RetainedAccomodationFileIds"] = [f.file_id for r in ws.stays for f in r.saved_files]
        new_claimed = sum(
            (r.money.claimed or Decimal(0) for r in [*ws.transport, *ws.stays] if r.origin is not Origin.SAVED),
            Decimal(0),
        )
        _carry_through(ws, draft, new_claimed)
    return DraftRequest(draft_data=draft, transport_file_ids=transport_files, stay_file_ids=stay_files)


TRANSPORT_FIELDS = [
    "Id", "DateOfTravelSource", "TimeOfTravelSource", "Source", "ModeOfTravel", "DateOfTravelDestination",
    "TimeOfTravelDestiNation", "Destination", "DistanceINKm", "BilledAmount", "ClaimedAmount",
    "StartFileIndexCount", "EndFileIndexCount", "Remark", "TourNumber", "TaxStructure", "BillIGSTAmount",
    "BillCGSTAmount", "BillSGSTAmount", "IGSTAmount", "CGSTAmount", "SGSTAmount", "InvoiceNumber", "InvoiceDate",
    "isPaidUsingCard",
]  # fmt: skip
STAY_FIELDS = [
    "Id", "Location", "ClassOfCity", "IsOwnArrangement", "IsFreeAccomodation", "isCompanyLodginPaid", "HotelName",
    "HotelCity", "DateOfCheckIn", "TimeOfCheckIn", "DateOfCheckOut", "TimeOfCheckOut", "BilledAmount",
    "ClaimedAmount", "StartFileIndexCount", "EndFileIndexCount", "Remark", "TourNumber", "TaxStructure",
    "BillIGSTAmount", "BillCGSTAmount", "BillSGSTAmount", "IGSTAmount", "CGSTAmount", "SGSTAmount",
    "InvoiceNumber", "InvoiceDate", "isPaidUsingCard",
]  # fmt: skip
