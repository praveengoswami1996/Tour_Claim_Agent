"""Attach what was read from a bill to the claim rows.

Journeys are matched to the approved plan's legs by date (±1 day) and place; anything that
doesn't match becomes a new row. A ticket covering several legs is attached to each of
them, but its total amount is put on the first leg only, so nothing is counted twice.

Rows of a saved draft (`Origin.SAVED`) are never moved or changed: the server ties their
files to them by position. A journey or stay already in the claim (same key, BIL-43) is not
added again, and new rows go after the saved ones, in date order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from tourclaim_agent.domain.claim import Bill, ClaimWorkspace, Origin, StayRow, TransportRow
from tourclaim_agent.drafting.ocr_mapping import BillExtract, JourneyExtract, StayExtract
from tourclaim_agent.drafting.tour_match import norm


def _words(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 3}


def same_place(a: str | None, b: str | None) -> bool:
    return bool(_words(a) & _words(b))


def _iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def journey_key(source: str | None, destination: str | None, depart: date | None, time: str | None) -> tuple:
    """Source, destination, departure date and time, normalised (BIL-43)."""
    return ("J", norm(source), norm(destination), _iso(depart), time or "")


def stay_key(location: str | None, check_in: date | None, check_out: date | None) -> tuple:
    return ("S", norm(location), _iso(check_in), _iso(check_out))


def row_key(row: TransportRow | StayRow) -> tuple:
    if isinstance(row, TransportRow):
        return journey_key(row.source, row.destination, row.depart_date, row.depart_time)
    return stay_key(row.location, row.check_in_date, row.check_out_date)


def _held(row: TransportRow | StayRow) -> bool:
    """A row that already holds a bill or is saved; a second copy of it is not added."""
    return row.origin is Origin.SAVED or bool(row.bill_ids)


@dataclass
class RowResult:
    """What one journey or stay of a bill did to the claim."""

    kind: str  # "added" (new row) | "filled" (a planned leg) | "already_saved"
    row_key: str | None  # the workspace row it went into, or the saved row it duplicates
    is_stay: bool


def _score(row: TransportRow, journey: JourneyExtract) -> int:
    """How well a bill's journey fits an unbilled planned row. 0 = no match.

    Both places must match: a bill never changes a row's route. Dates may differ by a day
    (overnight travel, plan vs actual). Saved rows are never matched: they don't change.
    """
    if row.bill_ids or row.origin is Origin.SAVED:
        return 0
    if not (same_place(row.source, journey.source) and same_place(row.destination, journey.destination)):
        return 0
    if row.depart_date and journey.depart_date:
        gap = abs((row.depart_date - journey.depart_date).days)
        if gap > 1:
            return 0
        return 3 if gap == 0 else 2
    return 1


def _apply_journey(row: TransportRow, journey: JourneyExtract, bill: Bill, *, with_amount: bool) -> None:
    row.depart_date = journey.depart_date or row.depart_date
    row.depart_time = journey.depart_time or row.depart_time
    row.arrive_date = journey.arrive_date or row.arrive_date or row.depart_date
    row.arrive_time = journey.arrive_time or row.arrive_time
    row.source = journey.source or row.source
    row.destination = journey.destination or row.destination
    row.mode = journey.mode or row.mode
    row.distance_km = journey.distance_km or row.distance_km
    if with_amount and journey.money is not None:
        row.money = journey.money.model_copy()
    if bill.document_id not in row.bill_ids:
        row.bill_ids.append(bill.document_id)
    if row.key not in bill.used_by:
        bill.used_by.append(row.key)


def stay_row(ws: ClaimWorkspace, stay: StayExtract) -> StayRow:
    row = StayRow(
        key=ws.new_key("S"),
        origin=Origin.BILL,
        location=stay.location,
        hotel_name=stay.hotel_name,
        class_of_city=stay.class_of_city,
        check_in_date=stay.check_in_date,
        check_in_time=stay.check_in_time,
        check_out_date=stay.check_out_date,
        check_out_time=stay.check_out_time,
    )
    if stay.money is not None:
        row.money = stay.money.model_copy()
    return row


def _in_order(rows: list, sort_key) -> list:
    """Saved rows first, as stored; everything else after them, by date."""
    saved = [r for r in rows if r.origin is Origin.SAVED]
    others = sorted((r for r in rows if r.origin is not Origin.SAVED), key=sort_key)
    return saved + others


def order_rows(ws: ClaimWorkspace) -> None:
    ws.transport = _in_order(ws.transport, lambda r: (r.depart_date is None, r.depart_date, r.depart_time or ""))
    ws.stays = _in_order(ws.stays, lambda r: (r.check_in_date is None, r.check_in_date, r.check_in_time or ""))


def merge_journey(
    ws: ClaimWorkspace, bill: Bill, journey: JourneyExtract, *, with_amount: bool, row: TransportRow | None = None
) -> RowResult:
    key = journey_key(journey.source, journey.destination, journey.depart_date, journey.depart_time)
    twin = next((r for r in ws.transport if _held(r) and row_key(r) == key), None)
    if twin is not None:
        return RowResult("already_saved", twin.key, is_stay=False)
    if row is None:
        best = max(ws.transport, key=lambda r: _score(r, journey), default=None)
        row = best if best is not None and _score(best, journey) > 0 else None
    kind = "filled"
    if row is None:
        row = TransportRow(key=ws.new_key("T"), origin=Origin.BILL)
        ws.transport.append(row)
        kind = "added"
    _apply_journey(row, journey, bill, with_amount=with_amount)
    return RowResult(kind, row.key, is_stay=False)


def merge_stay(ws: ClaimWorkspace, bill: Bill, stay: StayExtract) -> RowResult:
    key = stay_key(stay.location, stay.check_in_date, stay.check_out_date)
    twin = next((r for r in ws.stays if _held(r) and row_key(r) == key), None)
    if twin is not None:
        return RowResult("already_saved", twin.key, is_stay=True)
    row = stay_row(ws, stay)
    row.bill_ids.append(bill.document_id)
    bill.used_by.append(row.key)
    ws.stays.append(row)
    return RowResult("added", row.key, is_stay=True)


def merge_bill(ws: ClaimWorkspace, bill: Bill, extract: BillExtract) -> list[RowResult]:
    if not extract.ok:
        bill.kind = "unreadable"
        bill.error = extract.error
        return []
    bill.kind = "accommodation" if extract.stays and not extract.journeys else "transport"

    results: list[RowResult] = []
    amount_used = False
    for journey in extract.journeys:
        has_own_amount = journey.money is not None and journey.money.billed is not None
        result = merge_journey(ws, bill, journey, with_amount=has_own_amount or not amount_used)
        results.append(result)
        if result.kind != "already_saved":
            amount_used = amount_used or has_own_amount
    for stay in extract.stays:
        results.append(merge_stay(ws, bill, stay))
    order_rows(ws)
    return results
