"""Attach what was read from a bill to the claim rows.

Journeys are matched to the approved plan's legs by date (±1 day) and place; anything that
doesn't match becomes a new row. A ticket covering several legs is attached to each of
them, but its total amount is put on the first leg only, so nothing is counted twice.
"""

from __future__ import annotations

import re

from tourclaim_agent.domain.claim import Bill, ClaimWorkspace, Origin, StayRow, TransportRow
from tourclaim_agent.drafting.ocr_mapping import BillExtract, JourneyExtract


def _words(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 3}


def same_place(a: str | None, b: str | None) -> bool:
    return bool(_words(a) & _words(b))


def _score(row: TransportRow, journey: JourneyExtract) -> int:
    """How well a bill's journey fits an unbilled row. 0 = no match.

    Both places must match: a bill never changes a row's route. Dates may differ by a day
    (overnight travel, plan vs actual).
    """
    if row.bill_ids:
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


def merge_bill(ws: ClaimWorkspace, bill: Bill, extract: BillExtract) -> None:
    if not extract.ok:
        bill.kind = "unreadable"
        bill.error = extract.error
        return
    bill.kind = "accommodation" if extract.stays and not extract.journeys else "transport"

    amount_used = False
    for journey in extract.journeys:
        has_own_amount = journey.money is not None and journey.money.billed is not None
        with_amount = has_own_amount or not amount_used
        best = max(ws.transport, key=lambda r: _score(r, journey), default=None)
        if best is not None and _score(best, journey) > 0:
            row = best
        else:
            row = TransportRow(key=ws.new_key("T"), origin=Origin.BILL)
            ws.transport.append(row)
        _apply_journey(row, journey, bill, with_amount=with_amount)
        amount_used = amount_used or (journey.money is not None and journey.money.billed is not None)

    for stay in extract.stays:
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
        row.bill_ids.append(bill.document_id)
        bill.used_by.append(row.key)
        ws.stays.append(row)

    ws.transport.sort(key=lambda r: (r.depart_date is None, r.depart_date, r.depart_time or ""))
