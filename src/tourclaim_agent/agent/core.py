"""Workflow state, per-request context, and small helpers shared by the graph's nodes."""

from __future__ import annotations

import logging
import operator
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, TypedDict

from tourclaim_agent.agent.events import NoticeEvent
from tourclaim_agent.auth.identity import Employee
from tourclaim_agent.clients.errors import BackendError, BackendForbidden, BackendUnauthorized
from tourclaim_agent.clients.ocr_api import OcrApiClient
from tourclaim_agent.clients.tour_api import TourApiClient
from tourclaim_agent.llm.intent import Intent, IntentExtractor
from tourclaim_agent.state.uploads import UploadStore

log = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """Checkpointed per workflow. Must stay JSON-friendly and never hold tokens."""

    messages: Annotated[list[dict[str, str]], operator.add]
    intent: dict[str, Any] | None
    pending_choice: dict[str, Any] | None
    focus_tour_id: int | None
    events: list[dict[str, Any]]
    # The claim being prepared (ClaimWorkspace as JSON), if any.
    claim: dict[str, Any] | None
    # Bills uploaded in this request, already recorded in the upload store (input only).
    new_bills: list[dict[str, Any]]
    # What kind of request this turn is: "message", "upload" or "check" (input only).
    turn: str
    # Events about bills produced before the message is handled (BIL-14); returned first.
    bill_events: list[dict[str, Any]]
    # A "which tour?" question raised by bill processing during a message turn; asked after the reply.
    queued_choice: dict[str, Any] | None
    # The choice the employee just answered (set by `understand`).
    chosen_choice: dict[str, Any] | None
    # With saving switched off: claims of tours other than the claim in progress, by tour id (BIL-49).
    parked: dict[str, dict[str, Any]]


@dataclass
class AgentContext:
    """Per-request dependencies. Passed as LangGraph runtime context, never checkpointed."""

    tours: TourApiClient
    ocr: OcrApiClient
    employee: Employee
    intents: IntentExtractor
    today: date
    workflow_id: str
    uploads: UploadStore
    save_draft_enabled: bool = False
    # Save read bills into drafts without being asked (BIL-49). Only meaningful when saving is enabled.
    auto_save_bills: bool = True
    max_upload_files: int = 20
    max_upload_mb: int = 20
    bill_report_keep_days: int = 14
    bill_slow_minutes: int = 15

    @property
    def auto_save(self) -> bool:
        return self.save_draft_enabled and self.auto_save_bills


# Every agent tag starts with "agent:" so the web app's own screens never pick these files up (BIL-03).
AGENT_REFERENCE_PREFIX = "agent:"


def ocr_reference(workflow_id: str, tour_id: int | None = None) -> str:
    """OCR reference tag for agent uploads; distinct from the web app's own tags."""
    return f"agent:{workflow_id}:" + (f"tour-{tour_id}" if tour_id is not None else "unassigned")


def reply(*events: Any, **state: Any) -> dict[str, Any]:
    """Build a node result: UI events plus a plain-text transcript entry."""
    dumped = [e.model_dump(mode="json") for e in events]
    text = "\n".join(e["text"] for e in dumped if e["type"] in ("text", "notice"))
    return {"events": dumped, "messages": [{"role": "assistant", "text": text}], **state}


def backend_problem(exc: BackendError, action: str = "get that information from") -> dict[str, Any]:
    """Turn a backend failure into a friendly notice. Auth failures propagate to the API layer."""
    if isinstance(exc, BackendUnauthorized | BackendForbidden):
        raise exc
    log.warning("backend error in workflow: %s (status %s)", exc.endpoint, exc.status_code)
    return reply(
        NoticeEvent(level="error", text=f"I couldn't {action} the Tour system just now. Please try again in a moment.")
    )


def intent_of(state: AgentState) -> Intent:
    return Intent.model_validate(state.get("intent") or {})
