"""Read a saved outside-jurisdiction draft into a ClaimWorkspace (§5.2, BIL-42).

Mirrors the in-app assistant's `savedClaim.ts › claimStateFromDraft`. Saved rows become
`Origin.SAVED` rows that keep their position, their raw content and their files: the
server ties a saved draft's files to its rows by position (`rowIndex`), so these rows are
never moved, changed or removed. Writing back is done by drafting/payload.py.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from tourclaim_agent.domain.claim import (
    ClaimWorkspace,
    ExistingDraft,
    Money,
    Origin,
    SavedFile,
    StayRow,
    TaxStructure,
    TransportRow,
    field_ci,
    to_date,
    to_decimal,
)
from tourclaim_agent.domain.models import TourSummary
from tourclaim_agent.drafting.ocr_mapping import normalise_time


def rows_of(value: Any) -> list[dict[str, Any]]:
    """A list that the API may send as a JSON string or as an array."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def journeys_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return rows_of(field_ci(data, "TransportaionDetailDtos") or field_ci(data, "TransportationDetailDtos"))


def stays_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return rows_of(field_ci(data, "AccomodationDetailDto") or field_ci(data, "AccommodationDetailDto"))


def _files(value: Any, row_index: int) -> list[SavedFile]:
    files = []
    for ref in rows_of(value):
        index = field_ci(ref, "rowIndex")
        try:
            index = int(index) if index is not None else 0
        except (TypeError, ValueError):
            continue
        if index != row_index:
            continue
        file_id = field_ci(ref, "fileId")
        if file_id in (None, ""):
            continue
        size = field_ci(ref, "fileSize")
        files.append(
            SavedFile(
                file_id=file_id,
                name=str(field_ci(ref, "originalFileName") or field_ci(ref, "displayName") or file_id),
                size=int(size) if isinstance(size, int | float) else 0,
                row_index=row_index,
            )
        )
    return files


def _tax(item: dict[str, Any]) -> TaxStructure:
    value = str(field_ci(item, "TaxStructure") or "").upper()
    if value == "NA":
        return TaxStructure.NA
    if value == "IGST" or (to_decimal(field_ci(item, "IGSTAmount")) or 0) > 0:
        return TaxStructure.IGST
    return TaxStructure.SGST_CGST


def _money(item: dict[str, Any]) -> Money:
    na = _tax(item) is TaxStructure.NA
    return Money(
        billed=to_decimal(field_ci(item, "BilledAmount")),
        claimed=to_decimal(field_ci(item, "ClaimedAmount")),
        tax_structure=_tax(item),
        bill_igst=0 if na else to_decimal(field_ci(item, "BillIGSTAmount")) or 0,
        bill_cgst=0 if na else to_decimal(field_ci(item, "BillCGSTAmount")) or 0,
        bill_sgst=0 if na else to_decimal(field_ci(item, "BillSGSTAmount")) or 0,
        invoice_number=(str(field_ci(item, "InvoiceNumber")) if field_ci(item, "InvoiceNumber") else None),
        invoice_date=to_date(field_ci(item, "InvoiceDate")),
    )


def _text(value: Any) -> str | None:
    return str(value).strip() or None if value is not None else None


def workspace_from_draft(tour_id: int, draft: ExistingDraft, tour: TourSummary | None = None) -> ClaimWorkspace:
    data = draft.data
    ws = ClaimWorkspace(
        tour_id=tour_id,
        purpose=_text(field_ci(data, "PurposeOfTour")) or (tour.purpose if tour else None),
        source=(tour.source if tour else None) or _text(field_ci(data, "Source")),
        destination=(tour.destination if tour else None) or _text(field_ci(data, "Destination")),
        tour_start=(tour.start_date if tour else None) or to_date(field_ci(data, "TantetiveDateOfDeparture")),
        draft_id=draft.draft_id,
        raw_draft=dict(data),
    )
    ws.tour_end = tour.end_date if tour else None
    if ws.tour_end is None and ws.tour_start:
        days = field_ci(data, "NoOfDays") or (tour.total_days if tour else None)
        if isinstance(days, int | float) and days > 0:
            ws.tour_end = ws.tour_start + timedelta(days=int(days) - 1)

    travel_refs = field_ci(data, "transportationFileReferences")
    for index, item in enumerate(journeys_of(data)):
        ws.transport.append(
            TransportRow(
                key=ws.new_key("T"),
                origin=Origin.SAVED,
                depart_date=to_date(field_ci(item, "DateOfTravelSource")),
                depart_time=normalise_time(field_ci(item, "TimeOfTravelSource")),
                source=_text(field_ci(item, "Source")),
                mode=_text(field_ci(item, "ModeOfTravel")),
                arrive_date=to_date(field_ci(item, "DateOfTravelDestination")),
                arrive_time=normalise_time(field_ci(item, "TimeOfTravelDestiNation")),
                destination=_text(field_ci(item, "Destination")),
                distance_km=to_decimal(field_ci(item, "DistanceINKm")),
                money=_money(item),
                saved_files=_files(travel_refs, index),
                raw=item,
            )
        )
    stay_refs = field_ci(data, "accomodationFileReferences") or field_ci(data, "accommodationFileReferences")
    for index, item in enumerate(stays_of(data)):
        ws.stays.append(
            StayRow(
                key=ws.new_key("S"),
                origin=Origin.SAVED,
                location=_text(field_ci(item, "Location")),
                hotel_name=_text(field_ci(item, "HotelName")),
                class_of_city=_text(field_ci(item, "ClassOfCity")),
                check_in_date=to_date(field_ci(item, "DateOfCheckIn")),
                check_in_time=normalise_time(field_ci(item, "TimeOfCheckIn")),
                check_out_date=to_date(field_ci(item, "DateOfCheckOut")),
                check_out_time=normalise_time(field_ci(item, "TimeOfCheckOut")),
                own_arrangement=bool(field_ci(item, "IsOwnArrangement")),
                free_accommodation=bool(field_ci(item, "IsFreeAccomodation")),
                money=_money(item),
                saved_files=_files(stay_refs, index),
                raw=item,
            )
        )
    return ws


def saved_counts(draft: ExistingDraft) -> tuple[int, int]:
    """Number of journeys and stays in a draft, to confirm a save by reading back (BIL-47)."""
    return len(journeys_of(draft.data)), len(stays_of(draft.data))
