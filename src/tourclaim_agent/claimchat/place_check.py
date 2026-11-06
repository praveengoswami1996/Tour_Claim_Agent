"""Does a From / To value look like a place?

Port of Tour_React src/lib/placeCheck.ts. Bill readers sometimes put a label or an amount from the bill ("Total Net
Amount", "₹1,240.00", a PNR, a date) into the place fields. This spots those so the claim form can mark them and the
Claim Assistant can ask about them. Real station and airport names (with codes, terminals or sectors) pass; an empty
value is not judged here (a missing place is reported as missing).

The JavaScript patterns have no `u` flag, so `\\b`, `\\d` and the case-insensitive match are ASCII-only (re.ASCII);
`\\s` is JavaScript's whitespace set.
"""

from __future__ import annotations

import re
from typing import Any

from .js_compat import JS_WS, is_nullish, js_len, js_str, js_trim, nullish

_FLAGS = re.IGNORECASE | re.ASCII

# Words that belong on a bill, not in a place name (whole words only: "Netaji" or "Billimora" are fine).
_BILL_WORDS = re.compile(
    r"\b(total|sub ?total|grand total|net amount|net payable|amount|amt|fare|base fare|gst|cgst|sgst|igst|tax|taxes|vat"
    r"|cess|invoice|receipt|pnr|booking|booking id|ticket no|passenger|pax|seat|berth|coach|quota|charges?|fees?"
    r"|convenience|discount|paid|payment|payable|balance|refund|cancellation|toll|surcharge|insurance|reference|ref no"
    r"|txn|transaction|gstin|hsn|sac)\b",
    _FLAGS,
)
_PLACEHOLDER = re.compile(
    r"^(n/?a|na|nil|null|undefined|unknown|not available|not applicable|none|tbd|tba|select|-+|\.+|\?+|x+)\Z", _FLAGS
)
_MONEY = re.compile(
    r"[₹$€£]|\b(rs|inr|usd)\.?" + JS_WS + r"*\d|\b\d{1,3}(,\d{2,3})+(\.\d{1,2})?\b|\b\d+\.\d{2}\b", _FLAGS
)
_DATE_OR_TIME = re.compile(
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}" + JS_WS + r"*(am|pm|hrs)?\b", _FLAGS
)
_CONTACT = re.compile(r"@|https?://|www\.|\.com\b|\.in\b", _FLAGS)
_DIGIT = re.compile(r"\d", re.ASCII)
_WORD_SPLIT = re.compile(JS_WS + "+")


def place_problem(raw: Any) -> str | None:
    """placeCheck.ts:placeProblem: why this doesn't look like a place, in plain words, or None when it looks fine."""
    value = js_trim(js_str(nullish(raw, "")))
    if not value:
        return None
    if _PLACEHOLDER.search(value):
        return "it is a placeholder, not a place name"
    if js_len(value) < 2:
        return "it is too short to be a place name"
    if _MONEY.search(value):
        return "it looks like an amount from the bill"
    if _DATE_OR_TIME.search(value):
        return "it looks like a date or time from the bill"
    if _CONTACT.search(value):
        return "it looks like an email or web address"
    if _BILL_WORDS.search(value):
        return "it looks like a label from the bill, not a place"
    letters = sum(1 for ch in value if ch.isalpha())
    digits = len(_DIGIT.findall(value))
    if letters == 0:
        return "it has no letters, so it is not a place name"
    if digits >= 4 and digits > letters:
        return "it is mostly numbers (like a PNR or booking number)"
    if js_len(value) > 80 or len(_WORD_SPLIT.split(value)) > 12:
        return "it is too long to be a place name"
    return None


_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(v: str) -> str:
    return js_trim(_NOT_ALNUM.sub(" ", v.lower()))


def same_place_both_ends(from_: str | None = None, to: str | None = None) -> bool:
    """placeCheck.ts:samePlaceBothEnds: From and To are the same place (a misread, or the wrong field filled twice)."""
    if is_nullish(from_) or is_nullish(to):
        return False
    return bool(js_trim(from_)) and bool(js_trim(to)) and _norm(from_) == _norm(to)
