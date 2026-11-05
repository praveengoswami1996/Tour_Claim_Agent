"""Typed events the agent sends to the UI (FRS UI-03). The UI renders cards from these."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from tourclaim_agent.domain.models import TourSummary, TrajectoryStep
from tourclaim_agent.domain.statuses import status_info


class TourCard(BaseModel):
    tour_id: int
    jurisdiction: Literal["outside", "within"] | None = None
    route: str
    purpose: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status_id: int | None = None
    status_name: str | None = None
    status_text: str
    action_needed: bool = False
    with_whom: str | None = None

    @classmethod
    def from_tour(cls, tour: TourSummary) -> TourCard:
        info = tour.status_info
        holder = None
        if tour.recipient_name:
            holder = tour.recipient_name + (f" ({tour.recipient_designation})" if tour.recipient_designation else "")
        jurisdiction = None
        if tour.kind is not None:
            jurisdiction = "outside" if tour.kind == 1 else "within"
        return cls(
            tour_id=tour.tour_id,
            jurisdiction=jurisdiction,
            route=tour.route,
            purpose=tour.purpose,
            start_date=tour.start_date,
            end_date=tour.end_date,
            status_id=tour.status_id,
            status_name=tour.status or info.backend_name,
            status_text=info.employee_text,
            action_needed=info.action_needed,
            with_whom=holder,
        )


class StepCard(BaseModel):
    when: datetime | None = None
    status_name: str
    from_person: str | None = None
    to_person: str | None = None
    remark: str | None = None

    @classmethod
    def from_step(cls, step: TrajectoryStep) -> StepCard:
        return cls(
            when=step.created_date,
            status_name=step.status_name or status_info(step.status_id).backend_name,
            from_person=step.sender_name,
            to_person=step.recipient_name,
            remark=step.remark or None,
        )


class TextEvent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class NoticeEvent(BaseModel):
    type: Literal["notice"] = "notice"
    level: Literal["info", "warning", "error"] = "info"
    text: str


class TourListEvent(BaseModel):
    type: Literal["tour_list"] = "tour_list"
    title: str
    tours: list[TourCard]


class TourStatusEvent(BaseModel):
    type: Literal["tour_status"] = "tour_status"
    tour: TourCard
    steps: list[StepCard] = Field(default_factory=list)


class ChoiceOption(BaseModel):
    number: int
    tour_id: int
    label: str


class ChoicesEvent(BaseModel):
    type: Literal["choices"] = "choices"
    prompt: str
    options: list[ChoiceOption]


class UploadRequestEvent(BaseModel):
    type: Literal["upload_request"] = "upload_request"
    prompt: str
    accept: list[str] = Field(default_factory=lambda: [".pdf", ".jpg", ".jpeg", ".png"])
    max_files: int
    max_file_mb: int


class JourneyLine(BaseModel):
    key: str
    origin: str
    travel_date: date | None = None
    route: str
    mode: str | None = None
    billed: str | None = None
    claimed: str | None = None
    invoice_number: str | None = None
    bills: list[str] = Field(default_factory=list)


class StayLine(BaseModel):
    key: str
    origin: str
    place: str
    check_in: date | None = None
    check_out: date | None = None
    billed: str | None = None
    claimed: str | None = None
    invoice_number: str | None = None
    bills: list[str] = Field(default_factory=list)


class FindingLine(BaseModel):
    severity: Literal["blocking", "warning", "info"]
    code: str
    message: str
    row: str | None = None


class BillLine(BaseModel):
    file_name: str
    status: str
    used: bool


class ClaimSummaryEvent(BaseModel):
    """The claim as prepared so far (FRS SUM-01/03). Amounts are what the employee claims."""

    type: Literal["claim_summary"] = "claim_summary"
    tour_id: int
    purpose: str | None = None
    route: str
    tour_start: date | None = None
    tour_end: date | None = None
    journeys: list[JourneyLine]
    stays: list[StayLine]
    bills: list[BillLine]
    total_billed: str
    total_claimed: str
    findings: list[FindingLine]
    bills_pending: int
    can_save: bool
    save_enabled: bool
    draft_id: int | None = None


class DraftSavedEvent(BaseModel):
    type: Literal["draft_saved"] = "draft_saved"
    tour_id: int
    draft_id: int
    open_in_form: str  # app route the UI can navigate to


AgentEvent = Annotated[
    TextEvent
    | NoticeEvent
    | TourListEvent
    | TourStatusEvent
    | ChoicesEvent
    | UploadRequestEvent
    | ClaimSummaryEvent
    | DraftSavedEvent,
    Field(discriminator="type"),
]
