"""What the agent understood from one employee message.

The LLM (or the rule-based fallback) only fills this schema. It never chooses which
backend endpoint to call or whose data to read: the graph decides that from the goal,
and identity always comes from the token.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class Goal(StrEnum):
    LIST_TOURS = "list_tours"
    CLAIMABLE_TOURS = "claimable_tours"
    TOUR_STATUS = "tour_status"
    PENDING_ACTIONS = "pending_actions"
    SELECT_OPTION = "select_option"
    RAISE_CLAIM = "raise_claim"
    CLAIM_REVIEW = "claim_review"
    SAVE_DRAFT = "save_draft"
    CANCEL_CLAIM = "cancel_claim"
    BILL_STATUS = "bill_status"  # "are my bills read?", "what happened to my bills"
    ASSIGN_BILLS = "assign_bills"  # only through a "which tour are these bills for?" choice
    ANSWER_CLARIFICATION = "answer_clarification"
    EDIT_CLAIM = "edit_claim"
    SUPPLEMENTARY_CLAIM = "supplementary_claim"
    POLICY_QUESTION = "policy_question"
    GREETING = "greeting"
    HELP = "help"
    OUT_OF_SCOPE = "out_of_scope"
    UNKNOWN = "unknown"


class TourReference(BaseModel):
    """How the employee referred to a tour. All fields optional; the resolver combines them."""

    tour_id: int | None = Field(default=None, description="Tour number if the employee gave one")
    place: str | None = Field(default=None, description="City or place name mentioned")
    month: int | None = Field(default=None, ge=1, le=12)
    year: int | None = Field(default=None, ge=2000, le=2100)
    latest: bool = Field(default=False, description="Employee means their most recent tour")


class Intent(BaseModel):
    goal: Goal = Goal.UNKNOWN
    tour: TourReference = Field(default_factory=TourReference)
    selection: int | None = Field(default=None, ge=1, description="1-based choice from offered options")


class IntentContext(BaseModel):
    """Minimal context given to the extractor. No personal data beyond option labels."""

    pending_options: list[str] = Field(default_factory=list)
    today_iso: str = ""


class IntentExtractor(Protocol):
    async def extract(self, message: str, context: IntentContext) -> Intent: ...
