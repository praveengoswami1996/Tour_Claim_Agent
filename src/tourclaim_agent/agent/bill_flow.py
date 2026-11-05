"""Bills uploaded at any time: acknowledging uploads, and reporting what happened to them.

Uploads are recorded in the employee-level upload store (state/uploads.py). Reading,
tour matching and saving happen in drafting/bill_pipeline.py, inside a request that
carries the employee's token (BIL-11).
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from tourclaim_agent.agent.core import AgentContext, AgentState
from tourclaim_agent.agent.events import BillFileStatus, BillStatusEvent, TextEvent
from tourclaim_agent.domain.claim import Bill, ClaimWorkspace
from tourclaim_agent.state.uploads import UploadStatus, UploadStore


async def bill_status(uploads: UploadStore, emp_code: str) -> dict[str, Any]:
    """The employee's bills still being read, as a bill_status event (§6.1)."""
    pending = await uploads.with_status(emp_code, UploadStatus.PENDING)
    return BillStatusEvent(
        pending=len(pending),
        files=[BillFileStatus(document_id=r.document_id, file_name=r.file_name, status="Pending") for r in pending],
    ).model_dump(mode="json")


def _names(items: list[str]) -> str:
    return ", ".join(items)


async def register_bills(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Acknowledge an upload (BIL-06). A claim in progress also lists the bills on its summary."""
    ctx = runtime.context
    new = state.get("new_bills") or []
    result: dict[str, Any] = {"new_bills": []}
    raw = state.get("claim")
    if raw:
        ws = ClaimWorkspace.model_validate(raw)
        for item in new:
            if ws.bill(item["document_id"]) is None:
                ws.bills.append(Bill(**item))
        result["claim"] = ws.model_dump(mode="json")
    count = len(new)
    text = (
        f"Got {count} file{'s' if count != 1 else ''}: {_names([i['file_name'] for i in new])}. I'm reading "
        f"{'them' if count != 1 else 'it'} now; this usually takes a minute or two. I'll tell you which tour "
        f"{'they are' if count != 1 else 'it is'} for and save {'them' if count != 1 else 'it'} into your drafts."
    )
    if not ctx.auto_save:
        text = (
            f"Got {count} file{'s' if count != 1 else ''}: {_names([i['file_name'] for i in new])}. I'm reading "
            f"{'them' if count != 1 else 'it'} now; this usually takes a minute or two. I'll tell you which tour "
            f"{'they are' if count != 1 else 'it is'} for."
        )
    status = await bill_status(ctx.uploads, ctx.employee.emp_code)
    events = [TextEvent(text=text).model_dump(mode="json"), status]
    return {**result, "events": events, "messages": [{"role": "assistant", "text": text}]}
