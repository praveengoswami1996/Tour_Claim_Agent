"""Claim data: what the backend returns for a tour, and the agent's working copy of a claim.

The working copy (`ClaimWorkspace`) holds proposals only. The backend draft is the
canonical record once saved (FRS STA-06); amounts here are what the employee is claiming,
not calculations of entitlement (FRS P1).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from tourclaim_agent.domain.base import BackendModel, LenientDate, parse_date

# ----------------------------------------------------------------- backend views


class TentativeLeg(BackendModel):
    """One planned leg of the approved tour (GET Claim/RaiseClaimRequest → tentativeTourPlanList)."""

    leg_date: LenientDate = Field(default=None, alias="date")
    from_place: str | None = Field(default=None, alias="from")
    to_place: str | None = Field(default=None, alias="to")
    travel_mode: str | None = Field(default=None, alias="travelMode")
    class_of_city: str | None = Field(default=None, alias="classOfCity")
    remark: str | None = None


class ClaimTourHeader(BackendModel):
    purpose: str | None = Field(default=None, alias="purposeOfTour")
    source: str | None = None
    destination: str | None = None
    start_date: LenientDate = Field(default=None, alias="tentativeStartDate")
    no_of_days: int | None = Field(default=None, alias="noOfDays")

    @field_validator("no_of_days", mode="before")
    @classmethod
    def _int(cls, value: Any) -> int | None:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None


class ClaimPrefill(BackendModel):
    """GET Claim/RaiseClaimRequest/{tourId}: the approved tour and any rows already on it."""

    header: ClaimTourHeader = Field(default_factory=ClaimTourHeader, alias="empTourDetails")
    legs: list[TentativeLeg] = Field(default_factory=list, alias="tentativeTourPlanList")
    transportation_list: list[dict[str, Any]] = Field(default_factory=list, alias="transportationList")
    accommodation_list: list[dict[str, Any]] = Field(default_factory=list, alias="accommodationList")
    is_advance_required: bool | None = Field(default=None, alias="isAdvanceRequired")

    @property
    def tour_end(self) -> date | None:
        start = self.header.start_date
        if start is None or not self.header.no_of_days:
            return None
        return date.fromordinal(start.toordinal() + max(self.header.no_of_days, 1) - 1)


class ExistingDraft(BackendModel):
    draft_id: int = Field(alias="draftId")
    last_saved: str | None = Field(default=None, alias="lastSavedDate")


# ----------------------------------------------------------------- working copy


class Origin(StrEnum):
    PLAN = "plan"  # from the approved tentative tour plan
    BILL = "bill"  # read from an uploaded bill
    EMPLOYEE = "employee"  # stated by the employee


class TaxStructure(StrEnum):
    IGST = "IGST"
    SGST_CGST = "SGST_CGST"
    NA = "NA"


class Money(BaseModel):
    billed: Decimal | None = None
    claimed: Decimal | None = None
    tax_structure: TaxStructure | None = None
    bill_igst: Decimal = Decimal(0)
    bill_cgst: Decimal = Decimal(0)
    bill_sgst: Decimal = Decimal(0)
    invoice_number: str | None = None
    invoice_date: date | None = None


class Bill(BaseModel):
    """One uploaded file, its OCR status and what was read from it."""

    document_id: int
    file_name: str
    sha256: str
    status: str = "Pending"  # Pending | Processing | Completed | Failed
    kind: str | None = None  # transport | accommodation | unreadable
    error: str | None = None
    used_by: list[str] = Field(default_factory=list)  # row keys the bill is attached to


class TransportRow(BaseModel):
    key: str
    origin: Origin
    depart_date: date | None = None
    depart_time: str | None = None
    source: str | None = None
    mode: str | None = None
    arrive_date: date | None = None
    arrive_time: str | None = None
    destination: str | None = None
    distance_km: Decimal | None = None
    money: Money = Field(default_factory=Money)
    bill_ids: list[int] = Field(default_factory=list)


class StayRow(BaseModel):
    key: str
    origin: Origin
    location: str | None = None
    hotel_name: str | None = None
    class_of_city: str | None = None
    check_in_date: date | None = None
    check_in_time: str | None = None
    check_out_date: date | None = None
    check_out_time: str | None = None
    own_arrangement: bool = False
    free_accommodation: bool = False
    money: Money = Field(default_factory=Money)
    bill_ids: list[int] = Field(default_factory=list)


class ClaimWorkspace(BaseModel):
    """The claim being prepared in one workflow. Serialised into workflow state."""

    tour_id: int
    purpose: str | None = None
    source: str | None = None
    destination: str | None = None
    tour_start: date | None = None
    tour_end: date | None = None
    transport: list[TransportRow] = Field(default_factory=list)
    stays: list[StayRow] = Field(default_factory=list)
    bills: list[Bill] = Field(default_factory=list)
    draft_id: int | None = None
    # Set before a save is sent, so an unclear outcome can be reconciled by reading back (EXE-12).
    save_attempted: bool = False
    next_key: int = 1

    def new_key(self, prefix: str) -> str:
        key = f"{prefix}{self.next_key}"
        self.next_key += 1
        return key

    def bill(self, document_id: int) -> Bill | None:
        return next((b for b in self.bills if b.document_id == document_id), None)

    @property
    def pending_bills(self) -> list[Bill]:
        return [b for b in self.bills if b.status in ("Pending", "Processing")]

    @classmethod
    def from_prefill(cls, tour_id: int, prefill: ClaimPrefill) -> ClaimWorkspace:
        ws = cls(
            tour_id=tour_id,
            purpose=prefill.header.purpose,
            source=prefill.header.source,
            destination=prefill.header.destination,
            tour_start=prefill.header.start_date,
            tour_end=prefill.tour_end,
        )
        for leg in prefill.legs:
            if not (leg.from_place or leg.to_place):
                continue
            ws.transport.append(
                TransportRow(
                    key=ws.new_key("T"),
                    origin=Origin.PLAN,
                    depart_date=leg.leg_date,
                    source=leg.from_place,
                    destination=leg.to_place,
                    mode=leg.travel_mode,
                    arrive_date=leg.leg_date,
                )
            )
        return ws


def to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip()).quantize(Decimal("0.01"))
    except ArithmeticError:
        return None
    except ValueError:
        return None


def to_date(value: Any) -> date | None:
    return parse_date(value)
