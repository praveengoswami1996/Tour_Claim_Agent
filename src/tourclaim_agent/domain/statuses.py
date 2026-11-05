"""Tour and claim status vocabulary.

Source: the seeded ClaimStatusCategory rows and RecordMasterClaimStatusCategory enum in the
Tour API (see Project-Context/backend/claim_lifecycle.md). Status 18 does not exist in the
backend and is deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Holder(StrEnum):
    EMPLOYEE = "employee"
    APPROVER = "approver"
    FINANCE = "finance"
    NONE = "none"


@dataclass(frozen=True)
class StatusInfo:
    status_id: int
    backend_name: str
    employee_text: str
    holder: Holder
    action_needed: bool = False
    final: bool = False


_STATUSES: dict[int, StatusInfo] = {
    s.status_id: s
    for s in (
        StatusInfo(1, "Pending for Approval", "waiting for your approver's decision", Holder.APPROVER),
        StatusInfo(2, "Tour Approved", "approved; you can raise the claim now", Holder.EMPLOYEE),
        StatusInfo(3, "Finance Processing", "with bill passing / finance for processing", Holder.FINANCE),
        StatusInfo(4, "Rejected By Finance", "rejected by finance", Holder.NONE, final=True),
        StatusInfo(5, "Tour Rejected", "rejected by the approver", Holder.NONE, final=True),
        StatusInfo(6, "Tour Transfer", "transferred to another approver", Holder.APPROVER),
        StatusInfo(7, "Tour Not Availed", "marked as not availed (cancelled)", Holder.NONE, final=True),
        StatusInfo(8, "Finance Processing", "with finance for payment processing", Holder.FINANCE),
        StatusInfo(9, "Payment Transferred", "payment transferred", Holder.NONE),
        StatusInfo(10, "Tour Not Availed Processing", "cancellation is being processed", Holder.FINANCE),
        StatusInfo(
            11,
            "Clarification Processing",
            "finance has asked you a question; your reply is needed",
            Holder.EMPLOYEE,
            action_needed=True,
        ),
        StatusInfo(12, "Process Completed", "completed", Holder.NONE, final=True),
        StatusInfo(
            13,
            "Send Back To Requester",
            "sent back to you for changes",
            Holder.EMPLOYEE,
            action_needed=True,
        ),
        StatusInfo(14, "Re Approval Request", "waiting for re-approval of your changes", Holder.APPROVER),
        StatusInfo(15, "Pre Approval Request", "waiting for pre-approval", Holder.APPROVER),
        StatusInfo(
            16,
            "Send Back To Approval Requester",
            "your re-approval request was sent back to you",
            Holder.EMPLOYEE,
            action_needed=True,
        ),
        StatusInfo(17, "Clarification Respond", "your reply was sent; waiting for finance", Holder.FINANCE),
        StatusInfo(19, "Finance Role Transfer", "moved to another finance user", Holder.FINANCE),
        StatusInfo(20, "Supplementary Raised", "supplementary claim raised; with finance", Holder.FINANCE),
        StatusInfo(21, "Supplementary Payment Approved", "supplementary payment approved", Holder.NONE),
        StatusInfo(22, "Tour Supplementary Rejected", "supplementary claim rejected", Holder.NONE, final=True),
        StatusInfo(
            23,
            "Supplementary Clarification Requested",
            "finance has a question on your supplementary claim; your reply is needed",
            Holder.EMPLOYEE,
            action_needed=True,
        ),
        StatusInfo(
            24, "Supplementary Clarification responded", "your reply was sent; waiting for finance", Holder.FINANCE
        ),
        StatusInfo(25, "Finance Rejection Processing", "a rejection by finance is being processed", Holder.FINANCE),
        StatusInfo(26, "Approver Role Transfer", "moved to another approver", Holder.APPROVER),
    )
}

ACTION_NEEDED_STATUS_IDS = frozenset(s.status_id for s in _STATUSES.values() if s.action_needed)
CLAIMABLE_STATUS_IDS = frozenset({2})


def status_info(status_id: int | None, fallback_name: str | None = None) -> StatusInfo:
    """Look up a status. Unknown IDs get a neutral description using the backend's own name."""
    if status_id is not None and status_id in _STATUSES:
        return _STATUSES[status_id]
    name = fallback_name or "Unknown status"
    return StatusInfo(status_id or 0, name, f"in status '{name}'", Holder.NONE)
