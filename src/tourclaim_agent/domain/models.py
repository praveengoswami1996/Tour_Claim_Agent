"""Typed views of the backend payloads the agent reads.

Field names follow the Tour API exactly, including its spellings (e.g. `purposeofTour`,
`tantetiveEndDate`, `lavel`). See Project-Context/backend/tour_claim_api_reference.md §2.
"""

from __future__ import annotations

from datetime import date
from enum import IntEnum

from pydantic import Field

from tourclaim_agent.domain.base import BackendModel, LenientDate, LenientDateTime
from tourclaim_agent.domain.statuses import StatusInfo, status_info


class TourKind(IntEnum):
    """`touretype` query value on GetTourRequest."""

    OUTSIDE_JURISDICTION = 1
    WITHIN_JURISDICTION = 2


class EmployeeProfile(BackendModel):
    """Organisation service: GET Organization/GetEmployeDetailsWithEmpCode/{empCode}."""

    emp_id: int = Field(alias="empId")
    emp_code: str = Field(alias="empCode")
    emp_name: str | None = Field(default=None, alias="empName")
    designation: str | None = None
    unit: str | None = None
    unit_id: int | None = Field(default=None, alias="unitId")
    department: str | None = None
    level: str | None = Field(default=None, alias="lavel")
    grade: str | None = Field(default=None, alias="reDesignatedGrade")
    reporting_officer: str | int | None = Field(default=None, alias="reportingOfficer")
    promotion_date: LenientDate = Field(default=None, alias="promotionDate")
    role: str | None = None

    @property
    def roles(self) -> list[str]:
        return [r.strip() for r in (self.role or "").split(",") if r.strip()]


class TourSummary(BackendModel):
    """One item of GET TourListing/GetTourRequest/{empId}."""

    tour_id: int = Field(alias="tourId")
    emp_id: int | None = Field(default=None, alias="empId")
    tour_type: str | None = Field(default=None, alias="tourType")
    purpose: str | None = Field(default=None, alias="purposeofTour")
    source: str | None = None
    destination: str | None = None
    request_date: LenientDate = Field(default=None, alias="requestDate")
    start_date: LenientDate = Field(default=None, alias="tourStartDate")
    end_date: LenientDate = Field(default=None, alias="tantetiveEndDate")
    total_days: int | None = Field(default=None, alias="totalDays")
    status: str | None = None
    status_id: int | None = Field(default=None, alias="statusId")
    recipient_id: int | None = Field(default=None, alias="recipientId")
    recipient_name: str | None = Field(default=None, alias="recipientEmpName")
    recipient_designation: str | None = Field(default=None, alias="recipientDesignation")
    is_advance_taken: bool | None = Field(default=None, alias="isAdvanceTaken")
    advance_request_amount: float | None = Field(default=None, alias="advanceRequestAmount")
    approved_amount: float | None = Field(default=None, alias="approvedAmount")
    send_back_remark: str | None = Field(default=None, alias="sendBackToRemark")
    # Set by the agent (not the backend) to record which listing the tour came from.
    kind: TourKind | None = None

    @property
    def status_info(self) -> StatusInfo:
        return status_info(self.status_id, self.status)

    @property
    def route(self) -> str:
        parts = [p for p in (self.source, self.destination) if p]
        return " → ".join(parts) if parts else "route not recorded"

    def overlaps(self, start: date | None, end: date | None) -> bool:
        if start is None and end is None:
            return True
        tour_start = self.start_date or self.end_date
        tour_end = self.end_date or self.start_date
        if tour_start is None:
            return False
        lo = start or date.min
        hi = end or date.max
        return tour_start <= hi and tour_end >= lo


class TrajectoryStep(BackendModel):
    """One step of GET TourListing/GetTourTrajectory/{tourId}."""

    id: int | None = None
    sender_name: str | None = Field(default=None, alias="senderName")
    sender_designation: str | None = Field(default=None, alias="senderDesignation")
    recipient_name: str | None = Field(default=None, alias="recipientName")
    recipient_designation: str | None = Field(default=None, alias="recipientDesignation")
    claim_type_name: str | None = Field(default=None, alias="claimTypeName")
    status_id: int | None = Field(default=None, alias="statusId")
    status_name: str | None = Field(default=None, alias="statusName")
    remark: str | None = None
    created_date: LenientDateTime = Field(default=None, alias="createdDate")


class TourTrajectory(BackendModel):
    emp_tour_id: int | None = Field(default=None, alias="empTourId")
    title: str | None = Field(default=None, alias="tourPlanTitle")
    steps: list[TrajectoryStep] = Field(default_factory=list, alias="trajectory")

    @property
    def latest(self) -> TrajectoryStep | None:
        return self.steps[-1] if self.steps else None
