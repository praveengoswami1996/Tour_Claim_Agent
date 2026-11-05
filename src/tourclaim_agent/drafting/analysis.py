"""Advisory checks on a claim before it is saved (FRS ANA-01..06, DOC-06/07).

These help the employee fix problems early. They never replace the backend's own
validation, and they apply no entitlement limits: those rules are not available from the
backend yet (DEP-1/2), so the agent does not guess them.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from tourclaim_agent.domain.claim import ClaimWorkspace, TaxStructure
from tourclaim_agent.drafting.merge import same_place


class Severity(StrEnum):
    BLOCKING = "blocking"  # the draft cannot be saved until this is fixed
    WARNING = "warning"
    INFO = "info"


class Finding(BaseModel):
    severity: Severity
    code: str
    message: str
    row: str | None = None


# Modes for which a ticket or receipt is normally expected. Own vehicle and similar are not.
_NO_TICKET_MODES = ("own", "personal", "self", "office vehicle", "company vehicle", "official vehicle")


def _ticket_expected(mode: str | None) -> bool:
    text = (mode or "").lower()
    return bool(text) and not any(word in text for word in _NO_TICKET_MODES)


def analyse(ws: ClaimWorkspace) -> list[Finding]:
    findings: list[Finding] = []
    add = findings.append

    if not ws.transport and not ws.stays:
        add(Finding(severity=Severity.BLOCKING, code="EMPTY", message="The claim has no journeys or stays yet."))

    for row in ws.transport:
        label = f"Journey {row.source or '?'} → {row.destination or '?'}"
        if row.air_cleared:
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="AIR_NOT_IN_PLAN",
                    message=f"{label}: one bill shows air travel, but this tour has no air leg, so I left the "
                    "travel mode empty.",
                    row=row.key,
                )
            )
        if row.depart_date is None or row.arrive_date is None:
            add(
                Finding(
                    severity=Severity.BLOCKING,
                    code="MISSING_DATE",
                    message=f"{label}: travel date is missing.",
                    row=row.key,
                )
            )
        if not row.source or not row.destination:
            add(
                Finding(
                    severity=Severity.BLOCKING,
                    code="MISSING_PLACE",
                    message=f"{label}: from/to is missing.",
                    row=row.key,
                )
            )
        if row.depart_date and row.arrive_date and row.arrive_date < row.depart_date:
            add(
                Finding(
                    severity=Severity.BLOCKING,
                    code="ARRIVAL_BEFORE_DEPARTURE",
                    message=f"{label}: arrival is before departure.",
                    row=row.key,
                )
            )
        if ws.tour_start and row.depart_date and row.depart_date < ws.tour_start:
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="BEFORE_TOUR",
                    message=f"{label}: travel date is before the approved tour start.",
                    row=row.key,
                )
            )
        if ws.tour_end and row.arrive_date and row.arrive_date > ws.tour_end:
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="AFTER_TOUR",
                    message=f"{label}: travel date is after the approved tour end.",
                    row=row.key,
                )
            )
        findings.extend(_money_findings(label, row.key, row.money))
        if not row.bill_ids and _ticket_expected(row.mode):
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="MISSING_BILL",
                    message=f"{label}: no ticket or bill attached.",
                    row=row.key,
                )
            )
        if row.money.billed is None and row.bill_ids:
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="MISSING_AMOUNT",
                    message=f"{label}: the bill's amount could not be read.",
                    row=row.key,
                )
            )

    ordered = [r for r in ws.transport if r.source and r.destination]
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if not same_place(previous.destination, current.source):
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="BROKEN_CHAIN",
                    message=f"After arriving at {previous.destination}, the next journey starts from {current.source}. "
                    "Is a journey missing?",
                    row=current.key,
                )
            )
    if ordered and ws.source and not same_place(ordered[-1].destination, ws.source):
        add(
            Finding(
                severity=Severity.WARNING,
                code="NO_RETURN",
                message=f"The last journey ends at {ordered[-1].destination}, not {ws.source}. "
                "Is the return journey missing?",
            )
        )

    for stay in ws.stays:
        label = f"Stay at {stay.hotel_name or stay.location or '?'}"
        if stay.check_in_date is None or stay.check_out_date is None:
            add(
                Finding(
                    severity=Severity.BLOCKING,
                    code="MISSING_DATE",
                    message=f"{label}: check-in or check-out date is missing.",
                    row=stay.key,
                )
            )
        elif stay.check_out_date < stay.check_in_date:
            add(
                Finding(
                    severity=Severity.BLOCKING,
                    code="CHECKOUT_BEFORE_CHECKIN",
                    message=f"{label}: check-out is before check-in.",
                    row=stay.key,
                )
            )
        if (ws.tour_start and stay.check_in_date and stay.check_in_date < ws.tour_start) or (
            ws.tour_end and stay.check_out_date and stay.check_out_date > ws.tour_end
        ):
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="STAY_OUTSIDE_TOUR",
                    message=f"{label}: dates fall outside the approved tour.",
                    row=stay.key,
                )
            )
        findings.extend(_money_findings(label, stay.key, stay.money))
        if not stay.bill_ids and not (stay.own_arrangement or stay.free_accommodation):
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="MISSING_BILL",
                    message=f"{label}: no hotel bill attached.",
                    row=stay.key,
                )
            )

    findings.extend(_duplicate_findings(ws))

    for bill in ws.bills:
        if bill.status == "Failed" or bill.kind == "unreadable":
            add(
                Finding(
                    severity=Severity.WARNING,
                    code="UNREADABLE_BILL",
                    message=f"{bill.file_name} could not be read. Please check it or enter its details in the form.",
                )
            )
        elif bill.status == "Completed" and not bill.used_by:
            add(
                Finding(
                    severity=Severity.INFO,
                    code="UNUSED_BILL",
                    message=f"{bill.file_name} was read but not matched to any journey or stay.",
                )
            )

    add(
        Finding(
            severity=Severity.INFO,
            code="DA_IN_FORM",
            message="Daily allowance is not calculated here. The claim form calculates it when you open the draft.",
        )
    )
    return findings


def _money_findings(label: str, key: str, money) -> list[Finding]:
    out: list[Finding] = []
    if money.billed is not None and money.claimed is not None and money.claimed > money.billed:
        out.append(
            Finding(
                severity=Severity.BLOCKING,
                code="CLAIMED_ABOVE_BILLED",
                message=f"{label}: claimed amount is more than the billed amount.",
                row=key,
            )
        )
    if money.billed and not money.invoice_number:
        out.append(
            Finding(
                severity=Severity.WARNING,
                code="MISSING_INVOICE",
                message=f"{label}: invoice number is missing.",
                row=key,
            )
        )
    if money.tax_structure is TaxStructure.IGST and (money.bill_cgst or money.bill_sgst):
        out.append(
            Finding(
                severity=Severity.WARNING,
                code="TAX_MISMATCH",
                message=f"{label}: bill shows CGST/SGST but the tax type is IGST.",
                row=key,
            )
        )
    if money.tax_structure is TaxStructure.SGST_CGST and money.bill_igst:
        out.append(
            Finding(
                severity=Severity.WARNING,
                code="TAX_MISMATCH",
                message=f"{label}: bill shows IGST but the tax type is CGST+SGST.",
                row=key,
            )
        )
    return out


def _duplicate_findings(ws: ClaimWorkspace) -> list[Finding]:
    out: list[Finding] = []
    hashes = Counter(b.sha256 for b in ws.bills)
    for sha, count in hashes.items():
        if count > 1:
            names = ", ".join(b.file_name for b in ws.bills if b.sha256 == sha)
            out.append(
                Finding(
                    severity=Severity.WARNING,
                    code="DUPLICATE_FILE",
                    message=f"The same file was uploaded more than once: {names}.",
                )
            )
    invoices: Counter[tuple[str, Decimal | None]] = Counter()
    for row in [*ws.transport, *ws.stays]:
        if row.money.invoice_number:
            invoices[(row.money.invoice_number.lower(), row.money.billed)] += 1
    for (number, _), count in invoices.items():
        if count > 1:
            out.append(
                Finding(
                    severity=Severity.WARNING,
                    code="DUPLICATE_INVOICE",
                    message=f"Invoice {number} appears on {count} rows with the same amount.",
                )
            )
    return out


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity is Severity.BLOCKING]
