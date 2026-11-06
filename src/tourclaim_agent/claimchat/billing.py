"""Bill amounts and claim totals for the employee's billing questions (owner requirement 2026-11-06, not in the old
assistant).

Every figure is worked out here, in code, with Decimal; the AI model only reads them out (the BILLS section of a
typed turn's narrate message). Nothing is estimated: an amount that is not on the bill or in the draft is reported as
"could not be determined".

- **A bill's amount** is the OCR field `claimedAmountWithTax` of that bill: of a journey's `amountDetails`, or of a
  stay's `invoice`. Line items, subtotals and taxes are never added up. A ticket with several journeys (legs) is one
  bill: its amount counts once.
- **The ledger** (`new_ledger`, `record_read`, `record_skipped`) lists every bill read in this conversation, upload by
  upload (one upload = one read report). A bill that is a copy of one already counted is kept with `duplicateOf` and
  never counted again: an exact copy skipped at upload (same bytes), a different scan of the same journey or stay
  (same journey / stay, or already in the saved claim), or the same invoice number with the same amount.
- **A draft's totals** (`draft_amounts`) are worked out from the saved draft (GetRaiseClaimDraft) exactly as the claim
  form works them out once it has loaded that draft (Tour_React src/pages/RaiseClaim):
  * Total Transportation Amount: TransportationSection.tsx:541-543, the sum of every row's
    `parseFloat(row.claimedAmount) || 0`, with `claimedAmount = String(item.ClaimedAmount ?? 0)`
    (ApproverPage.tsx:1098), rounded with `toFixed(2)`;
  * Total Accommodation Amount: AccommodationSection.tsx:2284-2290, the sum of `parseFloat(r.claimed) || 0` while the
    section is included (`claimed = String(item.ClaimedAmount ?? 0)`, ApproverPage.tsx:1162; included as soon as the
    draft has stays, ApproverPage.tsx:1195), else 0;
  * Total DA Amount: ApproverPage.tsx:543 / 1800 and DailyAllowanceSection.tsx:429, the sum of `DAOfTheDay` of the
    applicable rows, with the rows read from the draft's `DailyAllowanceDto`
    (`DAOfTheDay = Number(item.Amount || 0)`, `applicable = item.IsApplicable === true`, ApproverPage.tsx:1198-1220).
    A draft without DA rows has its DA worked out by the form itself: the agent never works out DA, so it is "could
    not be determined";
  * leave deduction: LeaveSection.tsx:133-158, 208-217, 236-241 (the leave section is included when a leave row has a
    date, ApproverPage.tsx:1225-1238): each leave row marked DA deductable takes the DA of the days it covers
    (`DAOfTheDay` of the DA rows whose date is within the leave), and the deduction is their sum;
  * the total: ApproverPage.tsx:543-553, `Number((transportation + accommodation + DA - leave).toFixed(2))`. Without DA
    it is transportation + accommodation (the leave deduction is DA taken back, so it goes with the DA).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final

from .claim_assistant import journey_key, stay_key
from .js_compat import en_in_money, jget, js_str, js_truthy, nullish
from .ocr_mapping import to_iso_date
from .saved_claim import _field, _list

CENT: Final = Decimal("0.01")
_NUMBER: Final = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# what happened to a file in the read report, in plain words (only the kinds that mean it was not added)
_STATUS: Final = {
    "noTour": "not added: it matches none of the approved tours",
    "unsure": "not added: which tour it is for could not be told",
    "problem": "not added: a problem on the bill itself",
    "saveFailed": "not saved yet",
}


# ─── numbers ──────────────────────────────────────────────────────────────────


def _decimal(value: Any) -> Decimal | None:
    """A number as the API gives it (number or text), or None when it is not one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return Decimal(repr(value))
    if isinstance(value, str):
        text = value.replace(",", "").replace("₹", "").strip()
        if not _NUMBER.fullmatch(text):
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def bill_amount(value: Any) -> Decimal | None:
    """A bill's claimedAmountWithTax, or None when it gives no usable amount (missing, not a number, 0 or less)."""
    amount = _decimal(value)
    return amount if amount is not None and amount > 0 else None


def _parse_float(value: Any) -> Decimal:
    """`parseFloat(String(value)) || 0` as a Decimal (the longest number at the start of the text)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _decimal(value) or Decimal(0)
    match = _NUMBER.match(js_str(value).lstrip())
    if not match:
        return Decimal(0)
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return Decimal(0)


def _js_number(value: Any) -> Decimal:
    """`Number(value || 0)` as a Decimal (NaN counts as 0, as `|| 0` / the sums treat it)."""
    if not js_truthy(value):
        return Decimal(0)
    if isinstance(value, str):
        text = value.strip()
        return _decimal(text) or Decimal(0) if text else Decimal(0)
    return _decimal(value) or Decimal(0)


def to_fixed(value: Decimal) -> Decimal:
    """`Number(x.toFixed(2))`"""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money(value: Decimal) -> str:
    """₹ in the en-IN format the old assistant used ('₹12,34,567.50')."""
    return f"₹{en_in_money(float(to_fixed(value)))}"


def _day(iso: str) -> str:
    if not iso:
        return ""
    y, m, d = iso.split("-")
    return f"{d} {_MONTHS[int(m) - 1]} {y}"


# ─── the ledger: bills read in this conversation ──────────────────────────────


def new_ledger() -> dict[str, Any]:
    return {"uploads": 0, "bills": []}


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", js_str(nullish(text, "")).lower())


def _row_file(row: Any) -> str | None:
    files = [*(jget(row, "uploadedFiles") or []), *(jget(row, "billFiles") or [])]
    return files[0]["name"] if files else None


def _same_bill(entry: dict[str, Any], other: dict[str, Any]) -> bool:
    """Two bills that are the same bill: the same journey / stay, or the same invoice number with the same amount."""
    if entry["kind"] is None or entry["kind"] != other["kind"]:
        return False
    if entry["keys"] and entry["keys"] == other["keys"]:
        return True
    return bool(
        entry["invoiceKey"]
        and entry["invoiceKey"] == other["invoiceKey"]
        and entry["amount"] is not None
        and entry["amount"] == other["amount"]
    )


def _next_upload(ledger: dict[str, Any]) -> int:
    ledger["uploads"] = int(ledger.get("uploads") or 0) + 1
    return ledger["uploads"]


def _add(ledger: dict[str, Any], entry: dict[str, Any]) -> None:
    in_claim = entry.pop("inClaim", False)
    if entry.get("duplicateOf") is None and entry["kind"] is not None:
        original = next(
            (b for b in ledger["bills"] if b.get("duplicateOf") is None and _same_bill(entry, b)),
            None,
        )
        if original is not None:
            entry["duplicateOf"] = original["file"]
        elif in_claim:
            # a different scan of a journey or stay the claim already has (read before this chat)
            entry["duplicateOf"] = "a bill already in the claim"
    ledger["bills"].append(entry)


def _copy_of(ledger: dict[str, Any], hash_: str | None) -> str:
    original = next((b for b in ledger["bills"] if hash_ and b.get("hash") == hash_), None)
    return original["file"] if original else "a bill uploaded earlier"


def record_skipped(ledger: dict[str, Any], duplicates: list[str], hashes: dict[str, Any]) -> None:
    """An upload with nothing to read: its exact copies (same bytes as a bill already uploaded) were skipped."""
    if not duplicates:
        return
    upload = _next_upload(ledger)
    for name in duplicates:
        _add(ledger, _skipped_entry(ledger, upload, name, hashes.get(name)))


def _skipped_entry(ledger: dict[str, Any], upload: int, name: str, hash_: Any) -> dict[str, Any]:
    return {
        "upload": upload,
        "file": name,
        "documentId": None,
        "hash": hash_,
        "kind": None,
        "what": "",
        "date": "",
        "invoiceNo": "",
        "invoiceKey": "",
        "amount": None,
        "tourId": None,
        "tour": "",
        "keys": [],
        "status": "",
        "duplicateOf": _copy_of(ledger, hash_),
    }


def record_read(
    ledger: dict[str, Any],
    *,
    result: dict[str, Any],
    bills: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    names: list[str],
    fates: dict[str, Any],
    tour_of: Callable[[str], Any],
    tour_name: Callable[[Any], str],
    skipped_hashes: dict[str, Any],
) -> None:
    """One read report: every file in it goes into the ledger as one upload.

    `result` is the combined OCR result, `bills` the files read (name, documentId, hash), `rows` the journeys and
    stays mapped from the result (each with its bill file), `names` every file of the report (read and skipped),
    `fates` what happened to each file, `tour_of(name)` the tour a file went to (or None).
    """
    upload = _next_upload(ledger)
    by_name = {b["file"]["name"]: b for b in bills}
    tour_details = jget(result, "tourDetails")
    legs = [tour_details, *(jget(tour_details, "tourLegs") or [])] if js_truthy(tour_details) else []
    stays = list(jget(jget(result, "accommodationDetails"), "entries") or [])
    journey_keys: dict[str, list[str]] = {}
    stay_keys: dict[str, list[str]] = {}
    for r in rows:
        name = _row_file(r)
        if name is None:
            continue
        if "billFiles" in r:
            stay_keys.setdefault(name, []).append(stay_key(r))
        else:
            journey_keys.setdefault(name, []).append(journey_key(r))

    for name in names:
        fate = fates.get(name) or {}
        kind_of_fate = jget(fate, "kind")
        bill = by_name.get(name)
        if kind_of_fate in ("rejected",):
            continue  # not a bill: the file was turned away (not a PDF or photo, empty)
        if kind_of_fate == "duplicate" and bill is None:
            _add(ledger, _skipped_entry(ledger, upload, name, skipped_hashes.get(name)))
            continue
        tour = tour_of(name)
        base = {
            "upload": upload,
            "file": name,
            "documentId": jget(jget(bill, "file"), "documentId") if bill else None,
            "hash": bill.get("hash") if bill else None,
            "tourId": jget(tour, "tourId") if tour is not None else None,
            "tour": tour_name(tour) if tour is not None else "",
            "status": _STATUS.get(kind_of_fate, ""),
            "duplicateOf": None,
            "inClaim": kind_of_fate == "alreadySaved",
        }
        found = False
        own_legs = [leg for leg in legs if jget(jget(leg, "amountDetails"), "sourceFileName") == name]
        if own_legs:
            found = True
            first, last = own_legs[0], own_legs[-1]
            amount = next(
                (
                    a
                    for a in (bill_amount(jget(jget(leg, "amountDetails"), "claimedAmountWithTax")) for leg in own_legs)
                    if a is not None
                ),
                None,
            )
            invoice = next(
                (
                    js_str(jget(jget(leg, "amountDetails"), "invoiceNo"))
                    for leg in own_legs
                    if js_truthy(jget(jget(leg, "amountDetails"), "invoiceNo"))
                ),
                "",
            )
            start = to_iso_date(nullish(jget(first, "startDate"), jget(first, "departureDate")))
            _add(
                ledger,
                {
                    **base,
                    "kind": "journey",
                    "what": f"{js_str(jget(first, 'from') or '?')} → {js_str(jget(last, 'to') or '?')}",
                    "date": start,
                    "invoiceNo": invoice,
                    "invoiceKey": _norm(invoice),
                    "amount": str(amount) if amount is not None else None,
                    "keys": sorted(set(journey_keys.get(name, []))),
                },
            )
        own_stays = [s for s in stays if nullish(jget(s, "sourceFileName"), "") == name]
        seen_invoices: list[str] = []
        for s in own_stays:
            invoice_no = (
                js_str(jget(jget(s, "invoice"), "invoiceNo"))
                if js_truthy(jget(jget(s, "invoice"), "invoiceNo"))
                else ""
            )
            if invoice_no and _norm(invoice_no) in seen_invoices:
                continue  # the same invoice twice on one file: one bill
            seen_invoices.append(_norm(invoice_no))
            found = True
            amount = bill_amount(jget(jget(s, "invoice"), "claimedAmountWithTax"))
            check_in = to_iso_date(jget(s, "checkInDate"))
            _add(
                ledger,
                {
                    **base,
                    "kind": "stay",
                    "what": f"{js_str(jget(s, 'hotelName') or 'stay')} in {js_str(jget(s, 'location') or '?')}",
                    "date": check_in,
                    "invoiceNo": invoice_no,
                    "invoiceKey": _norm(invoice_no),
                    "amount": str(amount) if amount is not None else None,
                    "keys": sorted(set(stay_keys.get(name, []))),
                },
            )
        if not found:
            # read, but no journey or stay was found in it (or it could not be read at all)
            _add(
                ledger,
                {
                    **base,
                    "kind": None,
                    "what": "",
                    "date": "",
                    "invoiceNo": "",
                    "invoiceKey": "",
                    "amount": None,
                    "keys": [],
                    "status": "could not be read" if kind_of_fate == "unreadable" else "no journey or stay found",
                },
            )


# ─── what the model is given ──────────────────────────────────────────────────


def _amount(entry: dict[str, Any]) -> Decimal | None:
    return Decimal(entry["amount"]) if entry.get("amount") is not None else None


def _counted(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The unique bills (no duplicates), with or without an amount."""
    return [e for e in entries if e.get("duplicateOf") is None and e.get("kind") is not None]


def _total_line(label: str, entries: list[dict[str, Any]]) -> str:
    unique = _counted(entries)
    known = [e for e in unique if _amount(e) is not None]
    unknown = len(unique) - len(known)
    duplicates = len([e for e in entries if e.get("duplicateOf") is not None])
    unread = len([e for e in entries if e.get("kind") is None and e.get("duplicateOf") is None])
    total = sum((_amount(e) or Decimal(0) for e in known), Decimal(0))
    parts = [f"{label}: {len(unique)} unique bill{'' if len(unique) == 1 else 's'}"]
    if known:
        parts.append(f"total {money(total)} (of the {len(known)} with an amount)")
    elif unique:
        parts.append("total could not be determined (no bill has an amount)")
    else:
        parts.append("no bill to count")
    if unknown:
        parts.append(f"{unknown} bill{'' if unknown == 1 else 's'} whose amount could not be determined, not included")
    parts.append(f"{duplicates} duplicate{'' if duplicates == 1 else 's'} left out")
    if unread:
        parts.append(f"{unread} file{'' if unread == 1 else 's'} with no bill read from it")
    return "; ".join(parts) + "."


def _bill_line(e: dict[str, Any]) -> str:
    if e.get("duplicateOf") is not None:
        return f"• {e['file']}: duplicate of {e['duplicateOf']}, not counted"
    if e.get("kind") is None:
        return f"• {e['file']}: {e['status']}, no amount"
    what = f"{'journey' if e['kind'] == 'journey' else 'stay'} {e['what']}"
    details = [what]
    if e.get("date"):
        details.append(_day(e["date"]))
    if e.get("invoiceNo"):
        details.append(f"invoice {e['invoiceNo']}")
    if e.get("tour"):
        details.append(e["tour"])
    if e.get("status"):
        details.append(e["status"])
    amount = _amount(e)
    shown = money(amount) if amount is not None else "amount could not be determined"
    return f"• {e['file']} ({', '.join(details)}): {shown}"


def bills_section(ledger: dict[str, Any] | None, drafts: list[dict[str, Any]] | None) -> str:
    """The BILLS section of a typed turn: the bills read in this conversation and the drafts' totals."""
    ledger = ledger or new_ledger()
    entries: list[dict[str, Any]] = list(ledger.get("bills") or [])
    lines = [
        "All amounts are in ₹. A bill's amount is its total with tax as read from the bill; each bill is counted "
        "once (duplicates are left out).",
    ]
    if not entries:
        lines.append("BILLS READ IN THIS CHAT: none yet.")
    else:
        lines.append("BILLS READ IN THIS CHAT, by upload (oldest first):")
        uploads = sorted({e["upload"] for e in entries})
        for n in uploads:
            lines.append(f"Upload {n}:")
            lines.extend(_bill_line(e) for e in entries if e["upload"] == n)
        latest = uploads[-1]
        lines.append(_total_line(f"LATEST UPLOAD (upload {latest})", [e for e in entries if e["upload"] == latest]))
        lines.append(_total_line("ALL BILLS READ IN THIS CHAT", entries))
        known = [e for e in _counted(entries) if _amount(e) is not None]
        if known:
            high = max(_amount(e) or Decimal(0) for e in known)
            low = min(_amount(e) or Decimal(0) for e in known)
            lines.append(f"HIGHEST BILL: {money(high)} ({', '.join(e['file'] for e in known if _amount(e) == high)}).")
            lines.append(f"LOWEST BILL: {money(low)} ({', '.join(e['file'] for e in known if _amount(e) == low)}).")
        tours: list[tuple[Any, str]] = []
        for e in _counted(entries):
            if e.get("tourId") is not None and all(t[0] != e["tourId"] for t in tours):
                tours.append((e["tourId"], e["tour"]))
        if tours:
            lines.append("BY TOUR (bills read in this chat only, not the whole draft):")
            for tour_id, name in tours:
                lines.append(_total_line(f"• {name}", [e for e in entries if e.get("tourId") == tour_id]))
    lines.append(drafts_section(drafts))
    return "\n".join(lines)


# ─── saved drafts: totals as the claim form works them out ────────────────────


def draft_amounts(draft: Any) -> dict[str, Decimal | None]:
    """The claim form's subtotals and total for a saved draft (GetRaiseClaimDraft `data`); see the module docstring.

    DA (and so the leave deduction and the total with DA) is None when the draft holds no DA rows.
    """
    data = nullish(_field(draft, "draftData"), draft)
    journeys = _list(nullish(_field(data, "TransportaionDetailDtos"), _field(data, "TransportationDetailDtos")))
    stays = _list(nullish(_field(data, "AccomodationDetailDto"), _field(data, "AccommodationDetailDto")))
    da_rows = _list(_field(data, "DailyAllowanceDto"))
    leaves = _list(_field(data, "LeaveAvailedDto"))

    transport = to_fixed(sum((_parse_float(nullish(_field(j, "ClaimedAmount"), 0)) for j in journeys), Decimal(0)))
    accommodation = to_fixed(sum((_parse_float(nullish(_field(s, "ClaimedAmount"), 0)) for s in stays), Decimal(0)))

    da: Decimal | None = None
    leave: Decimal | None = Decimal(0)
    if da_rows:
        days = [
            (to_iso_date(js_str(_field(r, "Date"))[:10]), _js_number(_field(r, "Amount")), _field(r, "IsApplicable"))
            for r in da_rows
        ]
        da = to_fixed(sum((amount for _, amount, applicable in days if applicable is True), Decimal(0)))
    real_leave = [x for x in leaves if js_truthy(_field(x, "LeaveStartDate")) or js_truthy(_field(x, "LeaveEndDate"))]
    deductible = [x for x in real_leave if js_truthy(_field(x, "DADeductable"))]
    if deductible:
        if da is None:
            leave = None
        else:
            total = Decimal(0)
            for x in deductible:
                start = js_str(nullish(_field(x, "LeaveStartDate"), "")).split("T")[0]
                end = js_str(nullish(_field(x, "LeaveEndDate"), "")).split("T")[0]
                if not start or not end:
                    continue
                low, high = (start, end) if start <= end else (end, start)
                total += sum((amount for day, amount, _ in days if day and low <= day <= high), Decimal(0))
            leave = to_fixed(total)
    without_da = to_fixed(transport + accommodation)
    with_da = to_fixed(transport + accommodation + da - leave) if da is not None and leave is not None else None
    return {
        "transportation": transport,
        "accommodation": accommodation,
        "da": da,
        "leave": leave,
        "totalWithDa": with_da,
        "totalWithoutDa": without_da,
    }


def _or_unknown(value: Decimal | None) -> str:
    return money(value) if value is not None else "could not be determined"


def drafts_section(drafts: list[dict[str, Any]] | None) -> str:
    """`drafts`: [{tourId, label, amounts (draft_amounts) or None when the draft could not be read}]."""
    if drafts is None:
        return "SAVED DRAFTS: could not be read just now."
    if not drafts:
        return "SAVED DRAFTS: none."
    lines = [
        "SAVED DRAFTS (each claim's totals as the claim form works them out from the saved draft: Total "
        "Transportation Amount + Total Accommodation Amount + Total DA Amount, less the DA of leave days marked DA "
        "deductable; DA as saved in the draft):"
    ]
    read = []
    for d in drafts:
        a = d.get("amounts")
        if a is None:
            lines.append(f"• {d['label']}: the draft could not be read, so its amounts could not be determined.")
            continue
        read.append(a)
        parts = [
            f"transportation {money(a['transportation'])}",  # type: ignore[arg-type]
            f"accommodation {money(a['accommodation'])}",  # type: ignore[arg-type]
            f"DA {_or_unknown(a['da'])}" + ("" if a["da"] is not None else " (no DA saved in the draft)"),
        ]
        if a["leave"] is None or a["leave"] > 0:
            parts.append(f"leave deduction {_or_unknown(a['leave'])}")
        parts.append(f"total with DA {_or_unknown(a['totalWithDa'])}")
        parts.append(f"total without DA {_or_unknown(a['totalWithoutDa'])}")
        lines.append(f"• {d['label']}: " + ", ".join(parts) + ".")
    unread = len(drafts) - len(read)
    if read:
        transport = to_fixed(sum((a["transportation"] or Decimal(0) for a in read), Decimal(0)))
        accommodation = to_fixed(sum((a["accommodation"] or Decimal(0) for a in read), Decimal(0)))
        das = [a["da"] for a in read]
        with_das = [a["totalWithDa"] for a in read]
        da = to_fixed(sum((x for x in das if x is not None), Decimal(0))) if None not in das else None
        with_da = to_fixed(sum((x for x in with_das if x is not None), Decimal(0))) if None not in with_das else None
        without_da = to_fixed(sum((a["totalWithoutDa"] or Decimal(0) for a in read), Decimal(0)))
        tail = f" ({unread} draft{'' if unread == 1 else 's'} that could not be read not included)" if unread else ""
        lines.append(
            f"ALL {len(read)} DRAFT{'' if len(read) == 1 else 'S'} TOGETHER{tail}: transportation {money(transport)}, "
            f"accommodation {money(accommodation)}, DA {_or_unknown(da)}, total with DA {_or_unknown(with_da)}, "
            f"total without DA {money(without_da)}."
        )
    return "\n".join(lines)
