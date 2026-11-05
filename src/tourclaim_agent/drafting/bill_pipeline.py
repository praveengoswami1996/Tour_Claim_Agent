"""Processing finished bills: claim the OCR results, find each bill's tour, and save it into
that tour's draft (BIL-10..53).

Runs inside a request that carries the employee's token (BIL-11): the agent never keeps the
token, so nothing happens in the background. The caller serialises runs per employee (NFR-2);
MarkAsRead decides which results this run may use (BIL-13), so each one is applied once.

The pipeline returns plain results; agent/bill_flow.py turns them into events. Logs carry
document ids, tour ids, outcomes and status codes only, never amounts (NFR-3).
"""

from __future__ import annotations

import dataclasses
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from tourclaim_agent.clients.errors import BackendError, BackendForbidden, BackendUnauthorized
from tourclaim_agent.clients.http import FilePart
from tourclaim_agent.clients.ocr_api import OcrApiClient, OcrDocument
from tourclaim_agent.clients.tour_api import TourApiClient
from tourclaim_agent.domain.claim import (
    Bill,
    ClaimPrefill,
    ClaimWorkspace,
    Origin,
    StayRow,
    TransportRow,
    field_ci,
    to_date,
)
from tourclaim_agent.domain.models import TourSummary
from tourclaim_agent.drafting.analysis import Finding, analyse
from tourclaim_agent.drafting.merge import merge_journey, merge_stay, order_rows
from tourclaim_agent.drafting.ocr_mapping import BillExtract, JourneyExtract, StayExtract, extract_bill
from tourclaim_agent.drafting.payload import build_draft_request
from tourclaim_agent.drafting.saved_draft import rows_of, saved_counts, workspace_from_draft
from tourclaim_agent.drafting.tour_match import candidates_for, tour_window, triage
from tourclaim_agent.state.uploads import Outcome, UploadRecord, UploadStatus, UploadStore, strongest

log = logging.getLogger(__name__)

AGENT_PREFIX = "agent:"
FINISHED = ("Completed", "Failed")
NOTHING_READABLE = "nothing readable found"

SAVING_OFF = "Saving drafts from the chat is switched off; enter these details in the claim form."
AIR_NOT_IN_PLAN = "This bill shows air travel, but this tour has no air leg, so I left the travel mode empty."

# Where each outcome leaves the file in the store, and whether its OCR result is handed back.
_FINAL = UploadStatus.PROCESSED
_STATUS = {
    Outcome.SAVE_FAILED: UploadStatus.COMPLETED_UNPROCESSED,  # handed back; the next check tries again (BIL-17)
    Outcome.TOUR_NOT_CLAIMABLE: UploadStatus.COMPLETED_UNPROCESSED,
    Outcome.NO_TOUR: UploadStatus.RELEASED,
    Outcome.AWAITING_TOUR: UploadStatus.AWAITING_TOUR,
}
_RELEASED = {Outcome.SAVE_FAILED, Outcome.TOUR_NOT_CLAIMABLE, Outcome.NO_TOUR}


# --------------------------------------------------------------------------- wording


def day_text(d: date) -> str:
    return d.strftime("%d %b %Y")


def span_text(a: date | None, b: date | None) -> str:
    if a and b and a != b:
        if (a.year, a.month) == (b.year, b.month):
            return f"{a:%d}–{b:%d %b %Y}"
        if a.year == b.year:
            return f"{a:%d %b}–{b:%d %b %Y}"
        return f"{day_text(a)}–{day_text(b)}"
    d = a or b
    return day_text(d) if d else ""


def days_text(days: list[date]) -> str:
    """Days in words, consecutive days grouped: "05–06 Oct 2026, 08 Oct 2026"."""
    groups: list[list[date]] = []
    for d in sorted(set(days)):
        if groups and (d - groups[-1][1]).days == 1:
            groups[-1][1] = d
        else:
            groups.append([d, d])
    return ", ".join(span_text(a, b) for a, b in groups)


def tour_dates(tour: TourSummary) -> str:
    window = tour_window(tour)
    return span_text(*window) if window else ""


def tour_label(tour: TourSummary) -> str:
    dates = tour_dates(tour)
    return f"Tour {tour.tour_id} ({tour.route}{', ' + dates if dates else ''})"


def journey_found(j: JourneyExtract) -> str:
    when = f", {day_text(j.depart_date)}" if j.depart_date else ""
    return f"ticket {j.source or '?'} → {j.destination or '?'}{when}"


def stay_found(s: StayExtract) -> str:
    when = span_text(s.check_in_date, s.check_out_date)
    return f"hotel stay in {s.location or s.hotel_name or '?'}{', ' + when if when else ''}"


# --------------------------------------------------------------------------- rules


def is_air(mode: str | None) -> bool:
    return bool(re.search(r"\b(air|flight|aero|plane)", (mode or "").lower()))


def _travelled(ws: ClaimWorkspace) -> list[TransportRow]:
    """Journeys backed by a bill or a saved draft (not bare planned legs)."""
    return [r for r in ws.transport if r.origin is Origin.SAVED or r.bill_ids]


def stay_problem(stay: StayExtract, ws: ClaimWorkspace) -> str | None:
    """Why a stay bill can't be added (BIL-23, as the in-app `stayBillProblem`), or None."""
    inn, out = stay.check_in_date, stay.check_out_date
    if not inn and not out:
        return None
    if (
        inn
        and out
        and (
            out < inn
            or (out == inn and stay.check_in_time and stay.check_out_time and stay.check_out_time < stay.check_in_time)
        )
    ):
        return "the check-out date is before the check-in date"
    stay_dates = span_text(inn, out)
    lo = ws.tour_start - timedelta(days=1) if ws.tour_start else None
    end = ws.tour_end or ws.tour_start
    hi = end + timedelta(days=1) if end else None
    if any(d and ((lo and d < lo) or (hi and d > hi)) for d in (inn, out)):
        tour_text = f" ({span_text(ws.tour_start, end)})" if ws.tour_start else ""
        return f"its dates ({stay_dates}) are outside your tour dates{tour_text}"
    journeys = _travelled(ws)
    dates = sorted(d for r in journeys for d in (r.depart_date, r.arrive_date) if d)
    if dates:
        first, last = dates[0], dates[-1]
        # One day of room either side; a stay past the only ticket so far is fine (the return may come later).
        early = inn is not None and inn < first - timedelta(days=1)
        late = len(journeys) > 1 and out is not None and out > last + timedelta(days=1)
        if early or late:
            return f"its dates ({stay_dates}) don't fit your travel dates ({span_text(first, last)})"
    return None


def _each_day(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1)] if b >= a else [a]


def covered_days(ws: ClaimWorkspace) -> set[date]:
    """Days the claim has a ticket, a stay or a leave for (as the in-app `coveredDays`)."""
    days: set[date] = set()

    def span(a: date | None, b: date | None) -> None:
        if a and b and b >= a:
            days.update(_each_day(a, b))
        else:
            days.update(d for d in (a, b) if d)

    for r in _travelled(ws):
        span(r.depart_date, r.arrive_date)
    for s in ws.stays:
        if s.origin is Origin.SAVED or s.bill_ids:
            span(s.check_in_date, s.check_out_date)
    for leave in rows_of(field_ci(ws.raw_draft or {}, "LeaveAvailedDto")):
        start = to_date(field_ci(leave, "LeaveStartDate"))
        span(start, to_date(field_ci(leave, "LeaveEndDate")) or start)
    return days


def coverage_text(before: set[date], after: ClaimWorkspace, tour: TourSummary) -> str:
    """Day coverage for a saved tour (BIL-62, as the in-app `savedClaimText`)."""
    now = covered_days(after)
    lines = []
    if before:
        lines.append(f"Your saved claim already had {days_text(sorted(before))}.")
    added = sorted(now - before)
    if added:
        lines.append(f"These bills add {days_text(added)}.")
    window = tour_window(tour)
    if window and now:
        missing = [d for d in _each_day(*window) if d not in now]
        lines.append(
            f"Still no bills for {days_text(missing)}." if missing else "Every day of the tour is now covered."
        )
    return " ".join(lines)


# --------------------------------------------------------------------------- results


@dataclass
class Item:
    """One journey or stay read from one file."""

    record: UploadRecord
    journey: JourneyExtract | None = None
    stay: StayExtract | None = None
    with_amount: bool = True
    probe: TransportRow | StayRow | None = None  # used only for tour matching
    outcome: Outcome | None = None
    tour_id: int | None = None
    detail: str | None = None

    @property
    def found(self) -> str:
        return journey_found(self.journey) if self.journey else stay_found(self.stay)  # type: ignore[arg-type]


@dataclass
class FileResult:
    record: UploadRecord
    outcome: Outcome
    tour_id: int | None = None
    found: str | None = None
    detail: str | None = None


@dataclass
class TourResult:
    tour: TourSummary
    workspace: ClaimWorkspace
    saved: bool
    findings: list[Finding] = field(default_factory=list)
    coverage: str | None = None


@dataclass
class Unsure:
    document_ids: list[int]
    candidates: list[TourSummary]


@dataclass
class CheckResult:
    files: list[FileResult] = field(default_factory=list)
    tours: list[TourResult] = field(default_factory=list)
    unsure: Unsure | None = None
    no_claimable: bool = False
    slow: list[UploadRecord] = field(default_factory=list)


class DraftUnusable(Exception):
    """A tour's draft can't be used right now. The message is safe to show to the employee."""


# --------------------------------------------------------------------------- pipeline


class BillPipeline:
    def __init__(
        self,
        *,
        emp_id: int,
        emp_code: str,
        tours: TourApiClient,
        ocr: OcrApiClient,
        uploads: UploadStore,
        auto_save: bool,
        slow_minutes: int = 15,
        keep_days: int = 14,
        claim_tour_id: int | None = None,
        workspaces: dict[int, ClaimWorkspace] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.emp_id = emp_id
        self.emp_code = emp_code
        self.tours = tours
        self.ocr = ocr
        self.uploads = uploads
        self.auto_save = auto_save
        self.slow_minutes = slow_minutes
        self.keep_days = keep_days
        self.claim_tour_id = claim_tour_id
        # Workspaces of this workflow by tour: the claim in progress and, with saving off, other tours (BIL-49).
        self.workspaces: dict[int, ClaimWorkspace] = dict(workspaces or {})
        self.clock = clock or time.time

    # ------------------------------------------------------------------ entry points

    async def check(self) -> CheckResult:
        """Process every finished, unprocessed upload of this employee, from any workflow (BIL-12)."""
        result = CheckResult()
        await self._expire_awaiting(result)
        if not await self.uploads.has_work(self.emp_code):
            return result
        records = await self.uploads.with_status(
            self.emp_code, UploadStatus.PENDING, UploadStatus.COMPLETED_UNPROCESSED
        )
        documents = {d.document_id: d for d in await self.ocr.list_documents(AGENT_PREFIX, include_response=True)}
        finished: list[UploadRecord] = []
        for record in records:
            doc = documents.get(record.document_id)
            if doc is None:
                continue
            if doc.status in FINISHED:
                finished.append(record)
            elif doc.status == "Cancelled":
                await self.uploads.update(self.emp_code, record.document_id, status=UploadStatus.CANCELLED.value)
            elif (
                record.status == UploadStatus.PENDING
                and not record.slow_reported
                and self.clock() - record.created_at > self.slow_minutes * 60
            ):
                result.slow.append(record)
                await self.uploads.update(self.emp_code, record.document_id, slow_reported=1)
        if not finished:
            return result

        won = await self.ocr.mark_as_read([r.document_id for r in finished])
        items: list[Item] = []
        for record in finished:
            doc = documents[record.document_id]
            if record.document_id not in won:
                # Another client (the web app, another tab) took this result: never apply it twice.
                result.files.append(FileResult(record, Outcome.USED_ELSEWHERE, detail="it was already used elsewhere"))
                continue
            items += self._read(record, doc, result)
        await self._place(items, result)
        return result

    async def assign(self, document_ids: list[int], tour_id: int | None) -> CheckResult:
        """The employee named the tour for bills that were unsure (BIL-36), or said "none of these"."""
        result = CheckResult()
        records = [
            r for r in await self.uploads.get(self.emp_code, document_ids) if r.status == UploadStatus.AWAITING_TOUR
        ]
        if not records:
            return result
        if tour_id is None:
            for record in records:
                result.files.append(
                    FileResult(record, Outcome.NO_TOUR, detail="you said it isn't for any of these tours")
                )
            await self._record(result.files)
            return result
        documents = {d.document_id: d for d in await self.ocr.list_documents(AGENT_PREFIX, include_response=True)}
        items: list[Item] = []
        for record in records:
            doc = documents.get(record.document_id)
            if doc is None:
                result.files.append(FileResult(record, Outcome.NOTHING_FOUND, detail="the bill is no longer available"))
                continue
            items += self._read(record, doc, result)
        claimable = await self.tours.list_claimable_tours(self.emp_id)
        tour = next((t for t in claimable if t.tour_id == tour_id), None)
        if tour is None:
            for item in items:
                item.outcome = Outcome.TOUR_NOT_CLAIMABLE
                item.detail = f"Tour {tour_id} is no longer waiting for a claim"
        else:
            for item in items:
                item.tour_id = tour.tour_id
            await self._save_into(tour, items, result)
        await self._finish(items, result)
        return result

    # ------------------------------------------------------------------ reading

    def _read(self, record: UploadRecord, doc: OcrDocument, result: CheckResult) -> list[Item]:
        if doc.status == "Failed":
            result.files.append(FileResult(record, Outcome.UNREADABLE, detail=doc.error_message or "could not be read"))
            return []
        extract = extract_bill(doc.response)  # untrusted data (BIL-18): only typed fields are taken
        if not extract.ok:
            if extract.error == NOTHING_READABLE:
                result.files.append(FileResult(record, Outcome.NOTHING_FOUND, detail="no journey or stay was found"))
            else:
                result.files.append(FileResult(record, Outcome.UNREADABLE, detail=extract.error or "could not be read"))
            return []
        return _items(record, extract)

    # ------------------------------------------------------------------ matching

    async def _place(self, items: list[Item], result: CheckResult) -> None:
        if not items:
            await self._finish(items, result)
            return
        try:
            claimable = await self.tours.list_claimable_tours(self.emp_id)
        except (BackendUnauthorized, BackendForbidden):
            raise
        except BackendError as exc:
            log.warning("claimable tours unavailable during bill check (%s)", exc.status_code)
            for item in items:
                item.outcome, item.detail = Outcome.SAVE_FAILED, "I couldn't reach the Tour system"
            await self._finish(items, result)
            return
        if not claimable:
            result.no_claimable = True
            for item in items:
                item.outcome, item.detail = Outcome.NO_TOUR, "you have no approved tours waiting for a claim"
            await self._finish(items, result)
            return

        by_probe = {id(i.probe): i for i in items}
        sorted_rows = triage([i.probe for i in items], claimable, self.claim_tour_id)  # type: ignore[misc]
        groups: dict[int, list[Item]] = {
            tid: [by_probe[id(r)] for r in rows] for tid, rows in sorted_rows.groups.items()
        }
        # Rows of one bill belong together: an unsure leg goes with the tour its other legs went to.
        tours_of_file: dict[int, set[int]] = {}
        for tid, group in groups.items():
            for item in group:
                tours_of_file.setdefault(item.record.document_id, set()).add(tid)
        unsure: list[Item] = []
        for row in sorted_rows.unsure:
            item = by_probe[id(row)]
            known = tours_of_file.get(item.record.document_id, set())
            if len(known) == 1:
                groups[next(iter(known))].append(item)
            else:
                unsure.append(item)
        for row, looks_like in sorted_rows.no_tour:
            item = by_probe[id(row)]
            item.outcome = Outcome.NO_TOUR
            item.detail = "it doesn't match any approved tour waiting for a claim"
            if looks_like is not None:
                item.detail += f"; it looks closest to {tour_label(looks_like)}"
        if unsure:
            for item in unsure:
                item.outcome = Outcome.AWAITING_TOUR
                item.detail = "I couldn't tell which tour it is for"
            ids = list(dict.fromkeys(i.record.document_id for i in unsure))
            result.unsure = Unsure(ids, candidates_for([i.probe for i in unsure], claimable))  # type: ignore[misc]

        by_id = {t.tour_id: t for t in claimable}
        for tid, group in groups.items():
            for item in group:
                item.tour_id = tid
            await self._save_into(by_id[tid], group, result)
        await self._finish(items, result)

    # ------------------------------------------------------------------ saving

    async def _load(self, tour: TourSummary) -> tuple[ClaimWorkspace, ClaimPrefill | None]:
        """The tour's claim, read fresh from the server (BIL-40/41). Raises DraftUnusable or BackendError."""
        draft = await self.tours.get_outside_draft(self.emp_id, tour.tour_id)
        prefill: ClaimPrefill | None = None
        if draft is not None:
            ws = workspace_from_draft(tour.tour_id, draft, tour)
        else:
            prefill = await self.tours.get_claim_prefill(tour.tour_id)
            if prefill.transportation_list or prefill.accommodation_list:
                raise DraftUnusable("this tour already has claim details recorded; please add these in the claim form")
            ws = ClaimWorkspace.from_prefill(tour.tour_id, prefill)
        ws.purpose = ws.purpose or tour.purpose
        ws.source = ws.source or tour.source
        ws.destination = ws.destination or tour.destination
        ws.tour_start = ws.tour_start or tour.start_date
        window = tour_window(tour)
        ws.tour_end = ws.tour_end or (window[1] if window else None)
        return ws, prefill

    async def _plan_has_air(self, tour_id: int, prefill: ClaimPrefill | None) -> bool | None:
        """Whether the approved plan has an air leg; None when unknown (then nothing is changed)."""
        if prefill is None:
            try:
                prefill = await self.tours.get_claim_prefill(tour_id)
            except (BackendUnauthorized, BackendForbidden):
                raise
            except BackendError:
                return None
        legs = [leg for leg in prefill.legs if leg.travel_mode]
        return any(is_air(leg.travel_mode) for leg in legs) if legs else None

    async def _save_into(self, tour: TourSummary, items: list[Item], result: CheckResult) -> None:
        """Merge the rows into the tour's claim and save it (BIL-40..53). Each tour is independent (BIL-52)."""
        tid = tour.tour_id
        try:
            if not self.auto_save and tid in self.workspaces:
                ws, prefill = self.workspaces[tid], None
            else:
                ws, prefill = await self._load(tour)
        except DraftUnusable as exc:
            self._fail(items, Outcome.SAVE_FAILED, str(exc))
            return
        except (BackendUnauthorized, BackendForbidden):
            raise
        except BackendError as exc:
            # A draft that exists but can't be read is never written over (BIL-45).
            log.warning("draft for tour %s unreadable (%s)", tid, exc.status_code)
            self._fail(items, Outcome.SAVE_FAILED, "I couldn't read this tour's draft just now; I'll try again")
            return

        previous = self.workspaces.get(tid)
        for item in items:
            record = item.record
            bill = ws.bill(record.document_id)
            if bill is None:
                old = previous.bill(record.document_id) if previous else None
                bill = (
                    old.model_copy()
                    if old
                    else Bill(document_id=record.document_id, file_name=record.file_name, sha256=record.sha256)
                )
                ws.bills.append(bill)
            bill.status = "Completed"
        before = covered_days(ws)

        changed: list[Item] = []
        air_known: bool | None = None
        air_checked = False
        for item in [i for i in items if i.journey is not None]:
            journey = item.journey
            assert journey is not None
            cleared = False
            if is_air(journey.mode):
                if not air_checked:
                    air_known, air_checked = await self._plan_has_air(tid, prefill), True
                if air_known is False:
                    journey, cleared = dataclasses.replace(journey, mode=None), True
            outcome = merge_journey(ws, ws.bill(item.record.document_id), journey, with_amount=item.with_amount)  # type: ignore[arg-type]
            if outcome.kind == "already_saved":
                item.outcome, item.detail = Outcome.ALREADY_SAVED, f"it is already in your claim for Tour {tid}"
                continue
            if cleared:
                row = next(r for r in ws.transport if r.key == outcome.row_key)
                row.air_cleared = True
                item.detail = AIR_NOT_IN_PLAN
            changed.append(item)
        for item in [i for i in items if i.stay is not None]:
            stay = item.stay
            assert stay is not None
            problem = stay_problem(stay, ws)
            if problem:
                item.outcome, item.detail = Outcome.PROBLEM, problem
                continue
            outcome = merge_stay(ws, ws.bill(item.record.document_id), stay)  # type: ignore[arg-type]
            if outcome.kind == "already_saved":
                item.outcome, item.detail = Outcome.ALREADY_SAVED, f"it is already in your claim for Tour {tid}"
                continue
            changed.append(item)
        order_rows(ws)
        if not changed:
            return

        if not self.auto_save:
            for item in changed:
                item.outcome, item.detail = Outcome.READY_NOT_SAVED, item.detail or SAVING_OFF
            self.workspaces[tid] = ws
            result.tours.append(TourResult(tour, ws, saved=False, findings=analyse(ws)))
            return

        # Re-check before writing (STA-05): the tour must still be waiting for a claim (BIL-46).
        try:
            still = {t.tour_id for t in await self.tours.list_claimable_tours(self.emp_id)}
        except (BackendUnauthorized, BackendForbidden):
            raise
        except BackendError:
            self._fail(changed, Outcome.SAVE_FAILED, "I couldn't reach the Tour system; I'll try again")
            return
        if tid not in still:
            self._fail(changed, Outcome.TOUR_NOT_CLAIMABLE, f"Tour {tid} is no longer waiting for a claim")
            return
        try:
            saved = await self._write(ws, tour)
        except DraftUnusable as exc:
            self._fail(changed, Outcome.SAVE_FAILED, str(exc))
            return
        saved.bills = ws.bills
        for item in changed:
            item.outcome = Outcome.SAVED
        if tid == self.claim_tour_id or tid in self.workspaces:
            self.workspaces[tid] = saved  # the claim in progress shows the saved state (BIL-53)
        log.info("saved %d row(s) into draft %s for tour %s", len(changed), saved.draft_id, tid)
        result.tours.append(
            TourResult(tour, saved, saved=True, findings=analyse(saved), coverage=coverage_text(before, saved, tour))
        )

    async def _write(self, ws: ClaimWorkspace, tour: TourSummary) -> ClaimWorkspace:
        return await write_draft(self.tours, self.ocr, self.emp_id, ws, tour)

    @staticmethod
    def _fail(items: list[Item], outcome: Outcome, detail: str) -> None:
        for item in items:
            item.outcome, item.detail = outcome, detail

    # ------------------------------------------------------------------ recording

    async def _finish(self, items: list[Item], result: CheckResult) -> None:
        """One outcome per file, strongest first (BIL-61), then store it and hand back unused results."""
        by_file: dict[int, list[Item]] = {}
        for item in items:
            by_file.setdefault(item.record.document_id, []).append(item)
        for file_items in by_file.values():
            outcomes = [i.outcome or Outcome.NOTHING_FOUND for i in file_items]
            best = strongest(outcomes)
            lead = next(i for i in file_items if (i.outcome or Outcome.NOTHING_FOUND) is best)
            details = [i.detail for i in file_items if i.detail]
            found = "; ".join(dict.fromkeys(i.found for i in file_items))
            result.files.append(
                FileResult(lead.record, best, lead.tour_id, found, lead.detail or (details[0] if details else None))
            )
        await self._record(result.files)

    async def _record(self, files: list[FileResult]) -> None:
        to_release: list[int] = []
        for f in files:
            await self.uploads.set_outcome(
                self.emp_code,
                f.record.document_id,
                status=_STATUS.get(f.outcome, _FINAL),
                outcome=f.outcome,
                detail=f.detail,
                found=f.found,
                tour_id=f.tour_id,
            )
            if f.outcome in _RELEASED:
                to_release.append(f.record.document_id)
        if to_release:
            try:
                await self.ocr.release(to_release)
            except (BackendUnauthorized, BackendForbidden):
                raise
            except BackendError as exc:
                log.warning("could not hand back %d OCR result(s) (%s)", len(to_release), exc.status_code)
        for f in files:
            log.info("bill %s -> %s (tour %s)", f.record.document_id, f.outcome.value, f.tour_id)

    async def _expire_awaiting(self, result: CheckResult) -> None:
        """Bills nobody named a tour for are handed back after the report-keeping period (BIL-74)."""
        cutoff = self.clock() - self.keep_days * 86400
        old = [
            r
            for r in await self.uploads.with_status(self.emp_code, UploadStatus.AWAITING_TOUR)
            if (r.processed_at or r.created_at) < cutoff
        ]
        if old:
            files = [FileResult(r, Outcome.NO_TOUR, detail="no tour was chosen for it") for r in old]
            await self._record(files)


async def write_draft(
    tours: TourApiClient, ocr: OcrApiClient, emp_id: int, ws: ClaimWorkspace, tour: TourSummary | None
) -> ClaimWorkspace:
    """Save the claim as a draft, then confirm by reading back (BIL-47); returns the claim as saved.

    An unclear result (timeout, or a read-back that doesn't match) is read back once more and is
    never re-sent (EXE-12). Raises DraftUnusable when the save can't be confirmed.
    """
    request = build_draft_request(ws, emp_id)
    files: list[FilePart] = []
    try:
        for name, ids in (
            ("transportationFiles", request.transport_file_ids),
            ("accommodationFiles", request.stay_file_ids),
        ):
            for document_id in ids:
                content, content_type = await ocr.get_file(document_id)
                bill = ws.bill(document_id)
                files.append((name, (bill.file_name if bill else f"bill-{document_id}", content, content_type)))
    except (BackendUnauthorized, BackendForbidden):
        raise
    except BackendError as exc:
        raise DraftUnusable("I couldn't fetch the bill files just now; I'll try again") from exc

    expected = (len(ws.transport), len(ws.stays))
    draft_id: int | None = None
    try:
        draft_id = await tours.save_outside_draft(request.draft_data, files)
    except (BackendUnauthorized, BackendForbidden):
        raise
    except (BackendError, ValueError) as exc:
        log.warning("draft save for tour %s unclear (%s); reading back", ws.tour_id, type(exc).__name__)
    for _ in range(2):
        try:
            back = await tours.get_outside_draft(emp_id, ws.tour_id)
        except (BackendUnauthorized, BackendForbidden):
            raise
        except BackendError:
            back = None
        if (
            back is not None
            and (ws.draft_id is None or back.draft_id == ws.draft_id)
            and (draft_id is None or back.draft_id == draft_id)
            and saved_counts(back) == expected
        ):
            saved = workspace_from_draft(ws.tour_id, back, tour)
            saved.bills = ws.bills
            return saved
    raise DraftUnusable("I couldn't confirm that the draft was saved; I'll try again")


def has_unsaved_rows(ws: ClaimWorkspace) -> bool:
    """Anything in the claim that its saved draft doesn't hold yet."""
    return ws.draft_id is None or any(r.origin is not Origin.SAVED or r.bill_ids for r in [*ws.transport, *ws.stays])


def _items(record: UploadRecord, extract: BillExtract) -> list[Item]:
    items: list[Item] = []
    amount_used = False
    for journey in extract.journeys:
        has_own = journey.money is not None and journey.money.billed is not None
        items.append(
            Item(
                record,
                journey=journey,
                with_amount=has_own or not amount_used,
                probe=TransportRow(
                    key="probe",
                    origin=Origin.BILL,
                    source=journey.source,
                    destination=journey.destination,
                    depart_date=journey.depart_date,
                    arrive_date=journey.arrive_date,
                ),
            )
        )
        amount_used = amount_used or has_own
    for stay in extract.stays:
        items.append(
            Item(
                record,
                stay=stay,
                probe=StayRow(
                    key="probe",
                    origin=Origin.BILL,
                    location=stay.location,
                    check_in_date=stay.check_in_date,
                    check_out_date=stay.check_out_date,
                ),
            )
        )
    return items
