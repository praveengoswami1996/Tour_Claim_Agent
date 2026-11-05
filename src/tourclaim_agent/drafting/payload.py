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
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from tourclaim_agent.domain.claim import ClaimWorkspace, Money, TaxStructure


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
    ranges = []
    for row in rows:
        if row.bill_ids:
            start = len(files)
            files.extend(row.bill_ids)
            ranges.append((start, len(files) - 1))
        else:
            ranges.append((-1, -1))
    return ranges


def claimed_total(ws: ClaimWorkspace) -> Decimal:
    return sum((r.money.claimed or Decimal(0) for r in [*ws.transport, *ws.stays]), Decimal(0))


def build_draft_request(ws: ClaimWorkspace, emp_id: int) -> DraftRequest:
    transport_files: list[int] = []
    stay_files: list[int] = []
    transport = []
    for row, (start, end) in zip(ws.transport, _ranges(ws.transport, transport_files), strict=True):
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
    return DraftRequest(draft_data=draft, transport_file_ids=transport_files, stay_file_ids=stay_files)
