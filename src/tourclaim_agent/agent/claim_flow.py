"""Journey J1: prepare an outside-jurisdiction claim on an approved tour.

1. start_claim     pick the tour (own, approved, no existing draft), seed rows from the plan
2. register_bills  record bills the employee uploaded (sent to the backend OCR queue)
3. review_claim    pull finished OCR results, merge them, run checks, show the summary
4. save_draft      save to the backend as a draft the employee finishes in the claim form
5. cancel_claim    drop the claim and hand unused OCR results back

Nothing is submitted from here. Daily allowance is calculated by the claim form (DEC-1).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from langgraph.runtime import Runtime

from tourclaim_agent.agent.core import (
    AgentContext,
    AgentState,
    backend_problem,
    intent_of,
    ocr_reference,
    reply,
)
from tourclaim_agent.agent.events import (
    BillLine,
    ChoiceOption,
    ChoicesEvent,
    ClaimSummaryEvent,
    DraftSavedEvent,
    FindingLine,
    JourneyLine,
    NoticeEvent,
    StayLine,
    TextEvent,
    TourCard,
    TourListEvent,
    UploadRequestEvent,
)
from tourclaim_agent.agent.resolver import has_reference, resolve_tours, tour_label
from tourclaim_agent.clients.errors import BackendError
from tourclaim_agent.domain.claim import Bill, ClaimPrefill, ClaimWorkspace
from tourclaim_agent.drafting.analysis import Finding, Severity, analyse, blocking
from tourclaim_agent.drafting.merge import merge_bill, same_place
from tourclaim_agent.drafting.ocr_mapping import extract_bill
from tourclaim_agent.drafting.payload import build_draft_request, claimed_total
from tourclaim_agent.llm.intent import Goal

log = logging.getLogger(__name__)

CLAIM_FORM_PATH = "/raise-claim-approver"


def _workspace(state: AgentState) -> ClaimWorkspace | None:
    raw = state.get("claim")
    return ClaimWorkspace.model_validate(raw) if raw else None


def _dump(ws: ClaimWorkspace | None) -> dict[str, Any] | None:
    return ws.model_dump(mode="json") if ws else None


def _rupees(value: Decimal | None) -> str | None:
    return None if value is None else f"₹{value:,.2f}"


def _route(ws: ClaimWorkspace) -> str:
    return " → ".join(p for p in (ws.source, ws.destination) if p) or "route not recorded"


def form_link(ws: ClaimWorkspace) -> str:
    days = (ws.tour_end - ws.tour_start).days + 1 if ws.tour_start and ws.tour_end else None
    query = f"tourId={ws.tour_id}&isDraft=true&statusId=2"
    return f"{CLAIM_FORM_PATH}?{query}" + (f"&totalDays={days}" if days else "")


def summary_event(ws: ClaimWorkspace, findings: list[Finding], *, save_enabled: bool) -> ClaimSummaryEvent:
    names = {b.document_id: b.file_name for b in ws.bills}
    billed = sum((r.money.billed or Decimal(0) for r in [*ws.transport, *ws.stays]), Decimal(0))
    return ClaimSummaryEvent(
        tour_id=ws.tour_id,
        purpose=ws.purpose,
        route=_route(ws),
        tour_start=ws.tour_start,
        tour_end=ws.tour_end,
        journeys=[
            JourneyLine(
                key=r.key,
                origin=r.origin.value,
                travel_date=r.depart_date,
                route=f"{r.source or '?'} → {r.destination or '?'}",
                mode=r.mode,
                billed=_rupees(r.money.billed),
                claimed=_rupees(r.money.claimed),
                invoice_number=r.money.invoice_number,
                bills=[names.get(i, str(i)) for i in r.bill_ids],
            )
            for r in ws.transport
        ],
        stays=[
            StayLine(
                key=s.key,
                origin=s.origin.value,
                place=s.hotel_name or s.location or "?",
                check_in=s.check_in_date,
                check_out=s.check_out_date,
                billed=_rupees(s.money.billed),
                claimed=_rupees(s.money.claimed),
                invoice_number=s.money.invoice_number,
                bills=[names.get(i, str(i)) for i in s.bill_ids],
            )
            for s in ws.stays
        ],
        bills=[BillLine(file_name=b.file_name, status=b.status, used=bool(b.used_by)) for b in ws.bills],
        total_billed=_rupees(billed) or "₹0.00",
        total_claimed=_rupees(claimed_total(ws)) or "₹0.00",
        findings=[FindingLine(**f.model_dump(mode="json")) for f in findings],
        bills_pending=len(ws.pending_bills),
        can_save=not blocking(findings) and not ws.pending_bills,
        save_enabled=save_enabled,
        draft_id=ws.draft_id,
    )


def _upload_request(ctx: AgentContext) -> UploadRequestEvent:
    return UploadRequestEvent(
        prompt="Upload your tickets, boarding passes and hotel bills.",
        max_files=ctx.max_upload_files,
        max_file_mb=ctx.max_upload_mb,
    )


# ----------------------------------------------------------------------------- start


async def start_claim(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    ref = intent_of(state).tour
    current = _workspace(state)
    if current is not None:
        other_tour = ref.tour_id not in (None, current.tour_id) or (
            ref.tour_id is None and ref.place and not same_place(ref.place, f"{current.source} {current.destination}")
        )
        if not other_tour:
            return await review_claim(state, runtime)
        return reply(
            TextEvent(
                text=f"You're already preparing the claim for Tour {current.tour_id}. "
                'Say "cancel this claim" first if you want to start a different one.'
            )
        )

    try:
        claimable = await ctx.tours.list_claimable_tours(ctx.employee.emp_id)
    except BackendError as exc:
        return backend_problem(exc)
    if not claimable:
        return reply(TextEvent(text="You have no approved tours waiting for a claim."))

    candidates = resolve_tours(claimable, ref) if has_reference(ref) else claimable
    if not candidates:
        if ref.tour_id is not None:
            try:
                own = {t.tour_id: t for t in await ctx.tours.list_all_tours(ctx.employee.emp_id)}
            except BackendError as exc:
                return backend_problem(exc)
            if ref.tour_id in own:
                info = own[ref.tour_id].status_info
                return reply(
                    TextEvent(
                        text=f"Tour {ref.tour_id} is {info.employee_text}. A claim can only be started on an "
                        "approved tour that hasn't been claimed yet."
                    )
                )
            return reply(TextEvent(text=f"I couldn't find tour {ref.tour_id} among your tours."))
        return reply(
            TextEvent(text="None of your approved tours match that. These are ready for a claim:"),
            TourListEvent(title="Ready to claim", tours=[TourCard.from_tour(t) for t in claimable]),
        )
    if len(candidates) > 1:
        options = [
            ChoiceOption(number=i, tour_id=t.tour_id, label=tour_label(t)) for i, t in enumerate(candidates[:5], 1)
        ]
        return reply(
            ChoicesEvent(prompt="Which tour is the claim for?", options=options),
            TextEvent(text="Which tour is the claim for? Reply with its number."),
            pending_choice={"goal": Goal.RAISE_CLAIM.value, "options": [o.model_dump() for o in options]},
        )

    tour = candidates[0]  # From the employee's own claimable list: ownership established.
    try:
        existing = await ctx.tours.get_outside_draft(ctx.employee.emp_id, tour.tour_id)
        if existing is not None:
            return reply(
                TextEvent(
                    text=f"You already have a saved draft for Tour {tour.tour_id}. Please continue it in the claim "
                    "form so nothing you entered there is overwritten."
                ),
                DraftSavedEvent(
                    tour_id=tour.tour_id,
                    draft_id=existing.draft_id,
                    open_in_form=f"{CLAIM_FORM_PATH}?tourId={tour.tour_id}&isDraft=true&statusId=2",
                ),
                focus_tour_id=tour.tour_id,
            )
        prefill: ClaimPrefill = await ctx.tours.get_claim_prefill(tour.tour_id)
    except BackendError as exc:
        return backend_problem(exc)
    if prefill.transportation_list or prefill.accommodation_list:
        return reply(
            TextEvent(
                text=f"Tour {tour.tour_id} already has claim details recorded. Please continue in the claim form."
            )
        )

    ws = ClaimWorkspace.from_prefill(tour.tour_id, prefill)
    ws.source = ws.source or tour.source
    ws.destination = ws.destination or tour.destination
    ws.tour_start = ws.tour_start or tour.start_date
    ws.tour_end = ws.tour_end or tour.end_date
    when = f" from {ws.tour_start:%d %b %Y}" if ws.tour_start else ""
    planned = (
        f"From your approved plan I've added {len(ws.transport)} journey{'s' if len(ws.transport) != 1 else ''}."
        if ws.transport
        else "Your approved plan has no journey details, so I'll build the journeys from your bills."
    )
    return reply(
        TextEvent(
            text=f"Let's prepare the claim for Tour {tour.tour_id} ({_route(ws)}{when}). {planned} "
            "Upload your tickets and hotel bills and I'll read them and match them to the journeys."
        ),
        summary_event(ws, analyse(ws), save_enabled=ctx.save_draft_enabled),
        _upload_request(ctx),
        claim=_dump(ws),
        focus_tour_id=tour.tour_id,
    )


# ----------------------------------------------------------------------------- bills


async def register_bills(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ws = _workspace(state)
    new = state.get("new_bills") or []
    if ws is None:  # The API layer prevents this; kept as a safeguard.
        return reply(NoticeEvent(level="warning", text="Start a claim before uploading bills."), new_bills=[])
    for item in new:
        if ws.bill(item["document_id"]) is None:
            ws.bills.append(Bill(**item))
    names = ", ".join(item["file_name"] for item in new)
    return reply(
        TextEvent(
            text=f"Got {len(new)} file{'s' if len(new) != 1 else ''}: {names}. I'm reading them now; this usually "
            'takes a minute or two. Say "review my claim" to see what I found.'
        ),
        claim=_dump(ws),
        new_bills=[],
    )


async def refresh_bills(ctx: AgentContext, ws: ClaimWorkspace) -> None:
    """Pull finished OCR results for this claim's bills and merge them into the rows."""
    if not ws.pending_bills:
        return
    documents = await ctx.ocr.list_documents(ocr_reference(ctx.workflow_id, ws.tour_id), include_response=True)
    by_id = {d.document_id: d for d in documents}
    finished: list[int] = []
    for bill in ws.pending_bills:
        doc = by_id.get(bill.document_id)
        if doc is None:
            continue
        bill.status = doc.status
        if doc.status in ("Completed", "Failed"):
            finished.append(bill.document_id)
    if not finished:
        return
    won = await ctx.ocr.mark_as_read(finished)
    for document_id in finished:
        bill = ws.bill(document_id)
        doc = by_id[document_id]
        if document_id not in won:
            # Another client (e.g. the web app) already took this result; don't apply it twice.
            bill.kind, bill.error = "unreadable", "used elsewhere"
            continue
        if doc.status == "Failed":
            bill.kind, bill.error = "unreadable", doc.error_message or "could not be read"
            continue
        merge_bill(ws, bill, extract_bill(doc.response))


# ----------------------------------------------------------------------------- review


def _next_step(ws: ClaimWorkspace, findings: list[Finding], save_enabled: bool) -> str:
    if ws.pending_bills:
        return f"I'm still reading {len(ws.pending_bills)} bill(s). Ask me again in a minute."
    if blockers := blocking(findings):
        return f"{len(blockers)} thing(s) must be fixed before this can be saved; see the list."
    if not save_enabled:
        return (
            "Saving drafts from the chat isn't switched on yet. You can enter these details in the claim form "
            "from the Raise Claim screen."
        )
    return 'Say "save draft" to save this as a draft that you can finish and submit in the claim form.'


async def review_claim(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    ws = _workspace(state)
    if ws is None:
        return reply(
            TextEvent(text='You\'re not preparing a claim right now. Say "start a claim for my <place> tour".')
        )
    try:
        await refresh_bills(ctx, ws)
    except BackendError as exc:
        result = backend_problem(exc, "check your bills with")
        return {**result, "claim": _dump(ws)}
    findings = analyse(ws)
    warnings = sum(1 for f in findings if f.severity is Severity.WARNING)
    text = (
        f"Tour {ws.tour_id}: {len(ws.transport)} journey(s) and {len(ws.stays)} stay(s), "
        f"claimed total {_rupees(claimed_total(ws))} (excluding daily allowance)."
    )
    if warnings:
        text += f" {warnings} point(s) to check."
    return reply(
        TextEvent(text=f"{text} {_next_step(ws, findings, ctx.save_draft_enabled)}"),
        summary_event(ws, findings, save_enabled=ctx.save_draft_enabled),
        claim=_dump(ws),
    )


# ----------------------------------------------------------------------------- save


async def save_draft(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    ws = _workspace(state)
    if ws is None:
        return reply(TextEvent(text="There's no claim in progress to save."))
    if not ctx.save_draft_enabled:
        return reply(
            TextEvent(
                text="Saving drafts from the chat isn't switched on yet. Please enter these details in the claim "
                "form from the Raise Claim screen."
            ),
            summary_event(ws, analyse(ws), save_enabled=False),
        )
    try:
        await refresh_bills(ctx, ws)
        findings = analyse(ws)
        if ws.pending_bills or blocking(findings):
            return reply(
                TextEvent(text=f"Not saved yet. {_next_step(ws, findings, True)}"),
                summary_event(ws, findings, save_enabled=True),
                claim=_dump(ws),
            )

        # Re-check against the backend before writing (FRS STA-05).
        claimable = {t.tour_id for t in await ctx.tours.list_claimable_tours(ctx.employee.emp_id)}
        if ws.tour_id not in claimable:
            return reply(
                TextEvent(text=f"Tour {ws.tour_id} is no longer waiting for a claim, so I haven't saved anything."),
                claim=_dump(ws),
            )
        existing = await ctx.tours.get_outside_draft(ctx.employee.emp_id, ws.tour_id)
        if existing is not None and ws.draft_id is None:
            if ws.save_attempted:
                # Our earlier save went through even though we didn't get a clear answer.
                ws.draft_id = existing.draft_id
                return _saved(ws)
            return reply(
                TextEvent(
                    text="A draft for this tour was saved from the claim form meanwhile. I haven't overwritten it; "
                    "please continue there."
                ),
                claim=_dump(ws),
            )

        request = build_draft_request(ws, ctx.employee.emp_id)
        if ws.draft_id is not None:
            # Replace the files of our own earlier save rather than adding duplicates.
            request.draft_data["RetainedTransportationFileIds"] = []
            request.draft_data["RetainedAccomodationFileIds"] = []
        files = await _download_files(ctx, ws, request.transport_file_ids, request.stay_file_ids)
        ws.save_attempted = True
        draft_id = await ctx.tours.save_outside_draft(request.draft_data, files)
    except BackendError as exc:
        result = backend_problem(exc, "save the draft in")
        return {**result, "claim": _dump(ws)}

    ws.draft_id = draft_id
    try:
        check = await ctx.tours.get_outside_draft(ctx.employee.emp_id, ws.tour_id)
    except BackendError:
        check = None
    if check is None or check.draft_id != draft_id:
        return reply(
            NoticeEvent(
                level="warning",
                text=f"The Tour system said the draft was saved (number {draft_id}), but I couldn't read it back. "
                "Please check it in the claim form.",
            ),
            claim=_dump(ws),
        )
    return _saved(ws)


def _saved(ws: ClaimWorkspace) -> dict[str, Any]:
    return reply(
        TextEvent(
            text=f"Saved as draft {ws.draft_id} for Tour {ws.tour_id}. Open it in the claim form to add daily "
            "allowance, review everything and submit. Nothing has been submitted yet."
        ),
        DraftSavedEvent(tour_id=ws.tour_id, draft_id=ws.draft_id, open_in_form=form_link(ws)),
        claim=_dump(ws),
    )


async def _download_files(
    ctx: AgentContext, ws: ClaimWorkspace, transport_ids: list[int], stay_ids: list[int]
) -> list[tuple[str, tuple[str, bytes, str]]]:
    cache: dict[int, tuple[bytes, str]] = {}
    parts: list[tuple[str, tuple[str, bytes, str]]] = []
    for field, ids in (("transportationFiles", transport_ids), ("accommodationFiles", stay_ids)):
        for document_id in ids:
            if document_id not in cache:
                cache[document_id] = await ctx.ocr.get_file(document_id)
            content, content_type = cache[document_id]
            bill = ws.bill(document_id)
            parts.append((field, (bill.file_name if bill else f"bill-{document_id}", content, content_type)))
    return parts


# ----------------------------------------------------------------------------- cancel


async def cancel_claim(state: AgentState, runtime: Runtime[AgentContext]) -> dict[str, Any]:
    ctx = runtime.context
    ws = _workspace(state)
    if ws is None:
        return reply(TextEvent(text="There's no claim in progress."))
    try:
        await ctx.ocr.cancel([b.document_id for b in ws.pending_bills])
        await ctx.ocr.release([b.document_id for b in ws.bills if b.status in ("Completed", "Failed")])
    except BackendError as exc:
        log.warning("could not tidy OCR documents on cancel: %s", exc.endpoint)
    saved = f" Draft {ws.draft_id} stays saved in the claim form." if ws.draft_id else ""
    return reply(TextEvent(text=f"I've stopped preparing the claim for Tour {ws.tour_id}.{saved}"), claim=None)
