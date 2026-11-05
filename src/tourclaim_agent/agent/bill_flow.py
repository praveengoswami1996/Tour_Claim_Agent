"""Bills uploaded at any time: acknowledging uploads, processing finished bills, and telling
the employee what happened to each one (BIL-01..74).

Uploads are recorded in the employee-level upload store (state/uploads.py). Reading, tour
matching and saving happen in drafting/bill_pipeline.py, inside a request that carries the
employee's token (BIL-11). All wording is fixed templates (BIL-65).
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from langgraph.runtime import Runtime

from tourclaim_agent.agent.claim_flow import form_link, summary_event
from tourclaim_agent.agent.core import AgentContext, AgentState
from tourclaim_agent.agent.events import (
    BillFileStatus,
    BillReportEvent,
    BillReportLine,
    BillReportTour,
    BillStatusEvent,
    ChoiceOption,
    ChoicesEvent,
    DraftSavedEvent,
    NoticeEvent,
    TextEvent,
)
from tourclaim_agent.clients.errors import BackendError, BackendForbidden, BackendUnauthorized
from tourclaim_agent.domain.claim import Bill, ClaimWorkspace
from tourclaim_agent.drafting.analysis import blocking
from tourclaim_agent.drafting.bill_pipeline import BillPipeline, CheckResult, tour_dates, tour_label
from tourclaim_agent.llm.intent import Goal
from tourclaim_agent.state.uploads import Outcome, UploadRecord, UploadStatus, UploadStore

log = logging.getLogger(__name__)

MAX_TOUR_CHOICES = 5
NONE_OF_THESE = 0  # tour_id of the "None of these" option


async def bill_status(uploads: UploadStore, emp_code: str) -> dict[str, Any]:
    """The employee's bills still being read, as a bill_status event (§6.1)."""
    pending = await uploads.with_status(emp_code, UploadStatus.PENDING)
    return BillStatusEvent(
        pending=len(pending),
        files=[BillFileStatus(document_id=r.document_id, file_name=r.file_name, status="Pending") for r in pending],
    ).model_dump(mode="json")


def _names(items: list[str]) -> str:
    return ", ".join(items)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _is_are(n: int) -> str:
    return "is" if n == 1 else "are"


# ----------------------------------------------------------------------------- upload


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
    them = "them" if count != 1 else "it"
    text = (
        f"Got {_plural(count, 'file')}: {_names([i['file_name'] for i in new])}. I'm reading {them} now; this "
        f"usually takes a minute or two. I'll tell you which tour {'they are' if count != 1 else 'it is'} for"
    )
    text += f" and save {them} into your drafts." if ctx.auto_save else "."
    status = await bill_status(ctx.uploads, ctx.employee.emp_code)
    events = [TextEvent(text=text).model_dump(mode="json"), status]
    return {**result, "events": events, "messages": [{"role": "assistant", "text": text}]}


# ----------------------------------------------------------------------------- processing


def _workspaces(state: AgentState) -> tuple[ClaimWorkspace | None, dict[int, ClaimWorkspace]]:
    claim = ClaimWorkspace.model_validate(state["claim"]) if state.get("claim") else None
    workspaces = {int(k): ClaimWorkspace.model_validate(v) for k, v in (state.get("parked") or {}).items()}
    if claim is not None:
        workspaces[claim.tour_id] = claim
    return claim, workspaces


def _pipeline(ctx: AgentContext, claim: ClaimWorkspace | None, workspaces: dict[int, ClaimWorkspace]) -> BillPipeline:
    return BillPipeline(
        emp_id=ctx.employee.emp_id,
        emp_code=ctx.employee.emp_code,
        tours=ctx.tours,
        ocr=ctx.ocr,
        uploads=ctx.uploads,
        auto_save=ctx.auto_save,
        slow_minutes=ctx.bill_slow_minutes,
        keep_days=ctx.bill_report_keep_days,
        claim_tour_id=claim.tour_id if claim else None,
        workspaces=workspaces,
    )


async def _state_after(
    ctx: AgentContext, claim: ClaimWorkspace | None, before: set[int], pipeline: BillPipeline
) -> dict[str, Any]:
    """Write the workflow's workspaces back: the claim in progress, and other tours' claims (saving off)."""
    workspaces = dict(pipeline.workspaces)
    if claim is None and not ctx.auto_save:
        new = [tid for tid in workspaces if tid not in before]
        if len(new) == 1 and len(workspaces) == 1:
            claim = workspaces[new[0]]  # the only tour these bills went to becomes the claim in progress
    if claim is not None:
        claim = workspaces.get(claim.tour_id, claim)
        await _sync_bill_status(ctx.uploads, ctx.employee.emp_code, claim)
    parked = {
        str(tid): ws.model_dump(mode="json") for tid, ws in workspaces.items() if not claim or tid != claim.tour_id
    }
    return {"claim": claim.model_dump(mode="json") if claim else None, "parked": parked}


async def _sync_bill_status(uploads: UploadStore, emp_code: str, ws: ClaimWorkspace) -> None:
    """Bills listed on the claim in progress that are no longer being read (they may have gone elsewhere)."""
    waiting = [b for b in ws.bills if b.status in ("Pending", "Processing")]
    for record in await uploads.get(emp_code, [b.document_id for b in waiting]):
        if record.status != UploadStatus.PENDING:
            bill = ws.bill(record.document_id)
            if bill is not None:
                bill.status = "Failed" if record.outcome == Outcome.UNREADABLE else "Completed"
                if record.outcome == Outcome.UNREADABLE:
                    bill.kind, bill.error = "unreadable", record.outcome_detail


def _line(record: UploadRecord) -> BillReportLine:
    return BillReportLine(
        file_name=record.file_name,
        outcome=record.outcome or Outcome.NOTHING_FOUND.value,
        tour_id=record.tour_id,
        found=record.found,
        detail=record.outcome_detail,
        retry_document_id=record.document_id if record.outcome == Outcome.UNREADABLE else None,
    )


def _summary(records: list[UploadRecord], result: CheckResult) -> str:
    """One short paragraph, from fixed templates (BIL-60/65)."""
    labels = {t.tour.tour_id: tour_label(t.tour) for t in result.tours}
    by_outcome = Counter(r.outcome for r in records)
    lines = [f"I read {_plural(len(records), 'bill')}."]
    for outcome in (Outcome.SAVED, Outcome.READY_NOT_SAVED):
        per_tour = Counter(r.tour_id for r in records if r.outcome == outcome)
        for tour_id, n in per_tour.items():
            label = labels.get(tour_id, f"Tour {tour_id}")
            if outcome is Outcome.SAVED:
                lines.append(f"{n} {_is_are(n)} saved in your draft for {label}.")
            else:
                lines.append(
                    f"{n} {_is_are(n)} for {label}. Saving drafts from the chat is switched off, so please enter "
                    "the details in the claim form."
                )
    texts = {
        Outcome.ALREADY_SAVED: lambda n: (
            f"{n} {'was' if n == 1 else 'were'} already in your draft, so I didn't add "
            f"{'it' if n == 1 else 'them'} again."
        ),
        Outcome.SAVE_FAILED: lambda n: f"{n} couldn't be saved yet; I'll try again.",
        Outcome.TOUR_NOT_CLAIMABLE: lambda n: f"{n} {_is_are(n)} for a tour that is no longer waiting for a claim.",
        Outcome.NO_TOUR: lambda n: f"{n} {'doesn' if n == 1 else 'don'}'t match any approved tour waiting for a claim.",
        Outcome.AWAITING_TOUR: lambda n: f"For {n} I need you to tell me the tour.",
        Outcome.PROBLEM: lambda n: f"{n} couldn't be added; the reason is in the list.",
        Outcome.NOTHING_FOUND: lambda n: f"In {n} I found no journey or hotel stay.",
        Outcome.UNREADABLE: lambda n: f"{n} couldn't be read.",
        Outcome.USED_ELSEWHERE: lambda n: f"{n} {'was' if n == 1 else 'were'} already used elsewhere.",
    }
    for outcome, template in texts.items():
        if by_outcome.get(outcome.value):
            lines.append(template(by_outcome[outcome.value]))
    return " ".join(lines)


def _choice_for(
    document_ids: list[int], candidates: list, names: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The "Which tour are these bills for?" question (BIL-36)."""
    options = [
        ChoiceOption(number=i, tour_id=t.tour_id, label=tour_label(t))
        for i, t in enumerate(candidates[:MAX_TOUR_CHOICES], 1)
    ]
    options.append(ChoiceOption(number=len(options) + 1, tour_id=NONE_OF_THESE, label="None of these"))
    prompt = "Which tour are these bills for?"
    events = [
        ChoicesEvent(prompt=prompt, options=options).model_dump(mode="json"),
        TextEvent(text=f"{prompt} ({_names(names)}) Reply with its number.").model_dump(mode="json"),
    ]
    pending = {
        "goal": Goal.ASSIGN_BILLS.value,
        "options": [o.model_dump() for o in options],
        "document_ids": document_ids,
    }
    return events, pending


async def report(ctx: AgentContext, result: CheckResult, claim_tour_id: int | None) -> list[dict[str, Any]]:
    """Every outcome not yet told to the employee, from any workflow (BIL-60..63), then mark them told."""
    events: list[dict[str, Any]] = []
    records = await ctx.uploads.unreported(ctx.employee.emp_code, ctx.bill_report_keep_days)
    lines = [_line(r) for r in records]
    lines += [
        BillReportLine(
            file_name=r.file_name,
            outcome=Outcome.SLOW.value,
            detail="it is taking longer than usual to read",
            cancel_document_id=r.document_id,
        )
        for r in result.slow
    ]
    if result.no_claimable:
        events.append(
            TextEvent(
                text="You have no approved tours waiting for a claim, so I couldn't save these bills."
            ).model_dump(mode="json")
        )
    if records:
        events.append(TextEvent(text=_summary(records, result)).model_dump(mode="json"))
    if lines:
        tours = [
            BillReportTour(
                tour_id=t.tour.tour_id,
                route=t.tour.route,
                dates=tour_dates(t.tour),
                draft_id=t.workspace.draft_id if t.saved else None,
                coverage=t.coverage,
            )
            for t in result.tours
        ]
        events.append(BillReportEvent(files=lines, tours=tours).model_dump(mode="json"))
    for t in result.tours:
        if t.saved and t.workspace.draft_id is not None:
            events.append(
                DraftSavedEvent(
                    tour_id=t.tour.tour_id, draft_id=t.workspace.draft_id, open_in_form=form_link(t.workspace)
                ).model_dump(mode="json")
            )
        if t.tour.tour_id == claim_tour_id or blocking(t.findings):
            events.append(
                summary_event(t.workspace, t.findings, save_enabled=ctx.save_draft_enabled).model_dump(mode="json")
            )
    await ctx.uploads.mark_reported(ctx.employee.emp_code, [r.document_id for r in records])
    return events


def _transcript(events: list[dict[str, Any]]) -> list[dict[str, str]]:
    text = "\n".join(e["text"] for e in events if e.get("type") in ("text", "notice"))
    return [{"role": "assistant", "text": text}] if text else []


async def _ask_again(ctx: AgentContext, state: AgentState) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Bills still waiting for a tour are asked about again when no question about them is open."""
    open_choice = state.get("pending_choice") or {}
    if open_choice.get("goal") == Goal.ASSIGN_BILLS.value:
        return [], None
    waiting = await ctx.uploads.with_status(ctx.employee.emp_code, UploadStatus.AWAITING_TOUR)
    if not waiting:
        return [], None
    claimable = await ctx.tours.list_claimable_tours(ctx.employee.emp_id)
    return _choice_for([r.document_id for r in waiting], claimable, [r.file_name for r in waiting])


async def process_bills(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """Process finished bills before anything else (BIL-10/14). Silent when nothing finished."""
    ctx = runtime.context
    check_turn = state.get("turn") == "check"
    claim, workspaces = _workspaces(state)
    pipeline = _pipeline(ctx, claim, workspaces)
    try:
        result = await pipeline.check()
    except (BackendUnauthorized, BackendForbidden):
        raise
    except BackendError as exc:
        if check_turn:
            raise
        log.warning("bill processing skipped this turn (%s %s)", exc.endpoint, exc.status_code)
        return {"bill_events": []}

    update = await _state_after(ctx, claim, set(workspaces), pipeline)
    events = await report(ctx, result, claim.tour_id if claim else None)
    choice: dict[str, Any] | None = None
    if result.unsure is not None:
        records = await ctx.uploads.get(ctx.employee.emp_code, result.unsure.document_ids)
        choice_events, choice = _choice_for(
            result.unsure.document_ids, result.unsure.candidates, [r.file_name for r in records]
        )
        events += choice_events
    elif check_turn:
        try:
            choice_events, choice = await _ask_again(ctx, state)
        except BackendError:
            choice_events, choice = [], None
        events += choice_events
    status = await bill_status(ctx.uploads, ctx.employee.emp_code)
    if check_turn or events or status["pending"]:
        events.append(status)
    if choice is not None:
        update["pending_choice" if check_turn else "queued_choice"] = choice
    return {**update, "bill_events": events, "messages": _transcript(events)}


async def assign_bills(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """The answer to "Which tour are these bills for?" (BIL-36)."""
    ctx = runtime.context
    chosen = state.get("chosen_choice") or {}
    tour_id = (state.get("intent") or {}).get("tour", {}).get("tour_id")
    document_ids = [int(i) for i in chosen.get("document_ids") or []]
    claim, workspaces = _workspaces(state)
    pipeline = _pipeline(ctx, claim, workspaces)
    try:
        result = await pipeline.assign(document_ids, None if tour_id in (None, NONE_OF_THESE) else int(tour_id))
    except (BackendUnauthorized, BackendForbidden):
        raise
    except BackendError as exc:
        log.warning("assigning bills failed (%s %s)", exc.endpoint, exc.status_code)
        events = [
            NoticeEvent(
                level="error", text="I couldn't save these bills just now. I'll ask you again shortly."
            ).model_dump(mode="json")
        ]
        return {"events": events, "messages": _transcript(events)}
    update = await _state_after(ctx, claim, set(workspaces), pipeline)
    events = await report(ctx, result, claim.tour_id if claim else None)
    if not events:
        events = [TextEvent(text="Those bills have already been dealt with.").model_dump(mode="json")]
    return {**update, "events": events, "messages": _transcript(events)}


async def bill_status_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    """ "Are my bills read?" (BIL-72). New results were already reported at the start of this turn."""
    ctx = runtime.context
    emp_code = ctx.employee.emp_code
    pending = await ctx.uploads.with_status(emp_code, UploadStatus.PENDING)
    waiting = await ctx.uploads.with_status(emp_code, UploadStatus.AWAITING_TOUR)
    retrying = await ctx.uploads.with_status(emp_code, UploadStatus.COMPLETED_UNPROCESSED)
    parts = []
    if pending:
        parts.append(
            f"I'm still reading {_plural(len(pending), 'bill')}: {_names([r.file_name for r in pending])}. "
            "This usually takes a minute or two."
        )
    if retrying:
        parts.append(f"{_plural(len(retrying), 'bill')} couldn't be saved yet; I'll try again shortly.")
    if waiting:
        parts.append(f"For {_plural(len(waiting), 'bill')} I need you to tell me the tour.")
    if not parts:
        parts.append(
            "All the bills you uploaded have been read and dealt with. Upload more at any time."
            if state.get("bill_events")
            else "No bills are being read right now. Upload your tickets and hotel bills at any time."
        )
    events: list[dict[str, Any]] = [TextEvent(text=" ".join(parts)).model_dump(mode="json")]
    update: dict[str, Any] = {}
    if waiting:
        try:
            choice_events, choice = await _ask_again(ctx, state)
        except BackendError:
            choice_events, choice = [], None
        events += choice_events
        if choice is not None:
            update["pending_choice"] = choice
    events.append(await bill_status(ctx.uploads, emp_code))
    return {**update, "events": events, "messages": _transcript(events)}
