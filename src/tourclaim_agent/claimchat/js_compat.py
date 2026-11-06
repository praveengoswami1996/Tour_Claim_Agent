"""JavaScript semantics the port of the old Claim Assistant relies on.

The old assistant ran in the browser, so its rules lean on JavaScript: `Number()` and `String()` conversions,
truthiness (`''` is falsy, `[]` is truthy), `??` versus `||`, `trim()` with JavaScript's whitespace set, string
lengths in UTF-16 units, `Math.round`, the browser's free-text date parser (V8's legacy `Date.parse`), and en-IN
number and date formatting (`₹12,34,567.50`, `05 Sept 2026`). They are reproduced here once, so the ported modules
read like their TypeScript sources.

Dicts stand in for JavaScript objects. A key that is not there reads as `UNDEFINED` (JavaScript `undefined`, which
JSON leaves out) and a key holding `None` is JSON `null`; both are "nullish". The browser ran in India Standard
Time, so "local time" is a fixed UTC+05:30 here.

The clock and the random numbers the old code took from `Date.now()` and `Math.random()` come from `now_ms()` and
`js_random()`, which `use_runtime()` replaces (tests use it to get fixed ids and dates).
"""

from __future__ import annotations

import math
import random as _random
import re
import time as _time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

# ─── undefined, null, truthiness ────────────────────────────────────────────────


class _Undefined:
    """JavaScript `undefined`: a key that is not in the dict."""

    _instance: _Undefined | None = None

    def __new__(cls) -> _Undefined:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNDEFINED"

    def __bool__(self) -> bool:
        return False


UNDEFINED: Final = _Undefined()


def jget(obj: Any, key: str) -> Any:
    """`obj?.key`: the value, or UNDEFINED when obj is not a dict or has no such key."""
    if isinstance(obj, dict):
        return obj.get(key, UNDEFINED)
    return UNDEFINED


def is_nullish(value: Any) -> bool:
    """`value == null` (null or undefined)."""
    return value is None or value is UNDEFINED


def nullish(value: Any, fallback: Any) -> Any:
    """`value ?? fallback`."""
    return fallback if is_nullish(value) else value


def js_truthy(value: Any) -> bool:
    """`!!value`: false for undefined, null, false, 0, NaN and ''; true for everything else (also [] and {})."""
    if is_nullish(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True


def js_or(value: Any, fallback: Any) -> Any:
    """`value || fallback`."""
    return value if js_truthy(value) else fallback


def defined(**fields: Any) -> dict[str, Any]:
    """An object literal as JSON keeps it: keys whose value is undefined are left out."""
    return {k: v for k, v in fields.items() if v is not UNDEFINED}


# ─── strings ────────────────────────────────────────────────────────────────────

# JavaScript's WhiteSpace and LineTerminator characters: what `\s` matches and `trim()` removes.
JS_WS_CHARS: Final = "\t\n\v\f\r                  　﻿"
# JavaScript `\s` as a Python character class (Python's own `\s` differs: it adds \x1c-\x1f and \x85, lacks ﻿).
JS_WS: Final = "[" + re.escape(JS_WS_CHARS) + "]"


def js_trim(value: str) -> str:
    """`String.prototype.trim`."""
    return value.strip(JS_WS_CHARS)


def js_trim_start(value: str) -> str:
    """`String.prototype.trimStart`."""
    return value.lstrip(JS_WS_CHARS)


def js_len(value: str) -> int:
    """`string.length`: UTF-16 code units (a character outside the BMP counts 2)."""
    return len(value) + sum(1 for ch in value if ord(ch) > 0xFFFF)


def opt_trim(value: Any) -> Any:
    """`value?.trim()`: UNDEFINED for null/undefined; a non-string throws, as `trim is not a function` did."""
    if is_nullish(value):
        return UNDEFINED
    if not isinstance(value, str):
        raise TypeError("value.trim is not a function")
    return js_trim(value)


# ─── numbers ────────────────────────────────────────────────────────────────────

_DECIMAL_LITERAL = re.compile(r"[+-]?(?:Infinity|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)")
_NON_DECIMAL = re.compile(r"0(?:[xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)")


def _decimal_to_float(text: str) -> float:
    sign = -1.0 if text.startswith("-") else 1.0
    body = text.lstrip("+-")
    if body == "Infinity":
        return sign * math.inf
    return sign * float(body)


def js_number(value: Any) -> float:
    """`Number(value)` (always a float; NaN when JavaScript gives NaN)."""
    if value is UNDEFINED:
        return math.nan
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        text = js_trim(value)
        if text == "":
            return 0.0
        if _DECIMAL_LITERAL.fullmatch(text):
            return _decimal_to_float(text)
        if _NON_DECIMAL.fullmatch(text):
            base = {"x": 16, "o": 8, "b": 2}[text[1].lower()]
            return float(int(text[2:], base))
        return math.nan
    if isinstance(value, list):
        # Number([]) = 0, Number([x]) = Number(String(x)), longer arrays NaN
        return js_number(js_str(value))
    return math.nan


def js_parse_float(value: Any) -> float:
    """`parseFloat(value)`: the longest number at the start of the text, else NaN."""
    text = js_trim_start(js_str(value))
    m = _DECIMAL_LITERAL.match(text)
    return _decimal_to_float(m.group(0)) if m else math.nan


def js_is_finite(value: float) -> bool:
    """`Number.isFinite` for a number."""
    return not (math.isnan(value) or math.isinf(value))


def js_int(value: float) -> int | float:
    """A JavaScript number as Python keeps it: an int when it is a whole number, else the float (NaN, 1.5)."""
    if js_is_finite(value) and value == int(value):
        return int(value)
    return value


def js_round(value: float) -> float:
    """`Math.round`: halves go up (2.5 -> 3, -2.5 -> -2)."""
    if not js_is_finite(value):
        return value
    floor = math.floor(value)
    return float(floor + 1) if value - floor >= 0.5 else float(floor)


def _shortest_digits(value: float) -> tuple[str, int]:
    """Shortest round-trip digits `s` and exponent `n` with value = 0.s × 10^n (value finite and > 0)."""
    sign, digits, exponent = Decimal(repr(value)).as_tuple()
    text = "".join(str(d) for d in digits)
    stripped = text.rstrip("0")
    exponent += len(text) - len(stripped)
    text = stripped.lstrip("0") or "0"
    assert isinstance(exponent, int)
    return text, exponent + len(text)


def js_number_to_string(value: float | int) -> str:
    """`String(number)` (Number::toString, radix 10)."""
    value = float(value)
    if math.isnan(value):
        return "NaN"
    if value == 0:
        return "0"
    if value < 0:
        return "-" + js_number_to_string(-value)
    if math.isinf(value):
        return "Infinity"
    s, n = _shortest_digits(value)
    k = len(s)
    if k <= n <= 21:
        return s + "0" * (n - k)
    if 0 < n <= 21:
        return s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + s
    e = n - 1
    exp = ("+" if e >= 0 else "-") + str(abs(e))
    return (s if k == 1 else s[0] + "." + s[1:]) + "e" + exp


def js_str(value: Any) -> str:
    """`String(value)` (also what a template literal `${value}` gives)."""
    if value is UNDEFINED:
        return "undefined"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return js_number_to_string(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return ",".join("" if is_nullish(v) else js_str(v) for v in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


# What `${table[key]}` gives for a key an object literal inherits from Object.prototype ("toString", ...).
_OBJECT_PROTOTYPE: Final = {
    **{
        name: f"function {name}() {{ [native code] }}"
        for name in (
            "__defineGetter__",
            "__defineSetter__",
            "hasOwnProperty",
            "__lookupGetter__",
            "__lookupSetter__",
            "isPrototypeOf",
            "propertyIsEnumerable",
            "toString",
            "valueOf",
            "toLocaleString",
        )
    },
    "constructor": "function Object() { [native code] }",
    "__proto__": "[object Object]",
}


def js_lookup(table: dict[str, Any], key: Any) -> Any:
    """`table[key]` on an object literal: the key as a string, Object.prototype's members (as text) included."""
    name = js_str(key)
    if name in table:
        return table[name]
    return _OBJECT_PROTOTYPE.get(name, UNDEFINED)


# ICU root collation (String.prototype.localeCompare) of the ASCII characters: whitespace, then punctuation and
# symbols, then digits, then letters with upper and lower case equal at first; C0 controls and DEL are ignored.
_COLLATION_ORDER: Final = (
    "\t\n\v\f\r _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$0123456789aAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ"
)
_PRIMARY: Final = {
    ch: (i if not ch.isalpha() else _COLLATION_ORDER.index(ch.lower())) for i, ch in enumerate(_COLLATION_ORDER)
}


def _collation_keys(text: str) -> tuple[list[int], list[int]]:
    primary: list[int] = []
    tertiary: list[int] = []
    for ch in text:
        if ch in _PRIMARY:
            primary.append(_PRIMARY[ch])
            tertiary.append(1 if "A" <= ch <= "Z" else 0)
        elif ord(ch) < 0x80:
            continue  # ignorable control character
        else:
            # outside ASCII: after the ASCII letters, by code point (an approximation of ICU's order)
            primary.append(1000 + ord(ch.lower()))
            tertiary.append(0 if ch == ch.lower() else 1)
    return primary, tertiary


def js_locale_compare(a: str, b: str) -> int:
    """`a.localeCompare(b)` (root locale): -1, 0 or 1. Exact for ASCII text."""
    pa, ta = _collation_keys(a)
    pb, tb = _collation_keys(b)
    if pa != pb:
        return -1 if pa < pb else 1
    if ta != tb:
        return -1 if ta < tb else 1
    return 0


def js_add(a: Any, b: Any) -> Any:
    """`a + b`: string concatenation when either side is a string, else numeric addition."""
    if isinstance(a, str) or isinstance(b, str):
        return js_str(a) + js_str(b)
    return js_number(a) + js_number(b)


_RADIX_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"


def js_fraction_to_radix(value: float, radix: int = 36) -> str:
    """`value.toString(radix)` for 0 <= value < 1 (V8 DoubleToRadixCString), e.g. Math.random().toString(36)."""
    if value == 0:
        return "0"
    fraction = value
    delta = max(0.5 * (math.nextafter(value, math.inf) - value), math.nextafter(0.0, 1.0))
    digits: list[int] = []
    integer = 0
    if fraction >= delta:
        while True:
            fraction *= radix
            delta *= radix
            digit = int(fraction)
            digits.append(digit)
            fraction -= digit
            if (fraction > 0.5 or (fraction == 0.5 and (digit & 1))) and fraction + delta > 1:
                # round up, carrying over already written digits
                while True:
                    if not digits:
                        integer += 1
                        break
                    last = digits.pop()
                    if last + 1 < radix:
                        digits.append(last + 1)
                        break
                break
            if not fraction >= delta:
                break
    head = str(integer)
    return head + ("." + "".join(_RADIX_CHARS[d] for d in digits) if digits else "")


# ─── clock and random numbers ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Runtime:
    """Where `Date.now()` and `Math.random()` come from."""

    now_ms: Callable[[], int]
    random: Callable[[], float]


_DEFAULT_RUNTIME = Runtime(now_ms=lambda: _time.time_ns() // 1_000_000, random=_random.random)
_runtime: ContextVar[Runtime] = ContextVar("claimchat_js_runtime", default=_DEFAULT_RUNTIME)


def now_ms() -> int:
    """`Date.now()`."""
    return _runtime.get().now_ms()


def js_random() -> float:
    """`Math.random()`."""
    return _runtime.get().random()


@contextmanager
def use_runtime(
    *, now_ms: Callable[[], int] | None = None, random: Callable[[], float] | None = None
) -> Iterator[None]:
    """Replace the clock and/or the random numbers (for tests: fixed ids and dates)."""
    current = _runtime.get()
    token = _runtime.set(Runtime(now_ms=now_ms or current.now_ms, random=random or current.random))
    try:
        yield
    finally:
        _runtime.reset(token)


# ─── dates ──────────────────────────────────────────────────────────────────────

MS_PER_DAY: Final = 86_400_000
# India Standard Time, the browser's time zone (no daylight saving; the historical pre-1945 offsets are not modelled)
LOCAL_OFFSET_MS: Final = (5 * 60 + 30) * 60_000
MAX_TIME_MS: Final = 8.64e15


def days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 1970-01-01 of a proleptic Gregorian date (any year)."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z: int) -> tuple[int, int, int]:
    """(year, month 1-12, day) of a day number since 1970-01-01."""
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return y + (m <= 2), m, d


def make_day(year: int, month0: int, day: int) -> int:
    """ECMAScript MakeDay: a month or day out of range rolls over (31 Feb -> 3 Mar)."""
    year += month0 // 12
    month0 %= 12
    return days_from_civil(year, month0 + 1, 1) + day - 1


def js_local_date(year: int, month0: int, day: int) -> tuple[int, int, int]:
    """`new Date(year, month0, day)` read back as local (year, month 1-12, day); years 0-99 mean 1900-1999."""
    if 0 <= year <= 99:
        year += 1900
    return civil_from_days(make_day(year, month0, day))


def local_ymd(ms: float) -> tuple[int, int, int]:
    """getFullYear / getMonth()+1 / getDate of a time value, in local time."""
    return civil_from_days(int((ms + LOCAL_OFFSET_MS) // MS_PER_DAY))


def utc_iso_string(ms: float) -> str:
    """`new Date(ms).toISOString()` (years 0-9999)."""
    days, rest = divmod(int(ms), MS_PER_DAY)
    y, m, d = civil_from_days(days)
    hh, rest = divmod(rest, 3_600_000)
    mm, rest = divmod(rest, 60_000)
    ss, msec = divmod(rest, 1000)
    return f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:{mm:02d}:{ss:02d}.{msec:03d}Z"


# en-IN short month names: September is "Sept"
EN_IN_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec")


def en_in_date(y: int, m: int, d: int) -> str:
    """`toLocaleDateString('en-IN', {day: '2-digit', month: 'short', year: 'numeric'})`: '05 Sept 2026'."""
    return f"{d:02d} {EN_IN_MONTHS[m - 1]} {y}"


def en_in_money(value: float) -> str:
    """`n.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})`: '12,34,567.50'."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "-∞" if value < 0 else "∞"
    amount = Decimal(repr(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if amount.is_signed() else ""
    whole, _, cents = f"{abs(amount):f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}{whole}.{cents}"


# ─── V8's Date.parse (the browser's free-text date parser) ──────────────────────
#
# A port of V8's DateParser (src/date/dateparser-inl.h): first the ES5 ISO form, then the legacy parser that
# accepts "5 Oct 2026", "Oct 5, 2026", "Mon, 05 Oct 2026 10:30", "2026/10/05", "31 Feb 2026" (rolls over to
# 3 Mar), "Oct 5" (year 2001) and refuses a stray word after a number ("5 Oct 2026 PNR 2034").

_NONE = -(10**9)
_MONTHS3 = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_KEYWORDS: Final = (
    *((name, "month", i + 1) for i, name in enumerate(_MONTHS3)),
    ("am", "ampm", 0),
    ("pm", "ampm", 12),
    ("ut", "tz", 0),
    ("utc", "tz", 0),
    ("z", "tz", 0),
    ("gmt", "tz", 0),
    ("cdt", "tz", -5),
    ("cst", "tz", -6),
    ("edt", "tz", -4),
    ("est", "tz", -5),
    ("mdt", "tz", -6),
    ("mst", "tz", -7),
    ("pdt", "tz", -7),
    ("pst", "tz", -8),
    ("t", "tsep", 0),
)


@dataclass
class _Tok:
    kind: str  # number | symbol | word | space | unknown | end | invalid
    value: int = 0
    length: int = 0
    symbol: str = ""
    word_type: str = ""  # month | ampm | tz | tsep | "" (unknown word)

    def is_number(self) -> bool:
        return self.kind == "number"

    def is_fixed(self, length: int) -> bool:
        return self.kind == "number" and self.length == length

    def is_symbol(self, ch: str) -> bool:
        return self.kind == "symbol" and self.symbol == ch

    def is_sign(self) -> bool:
        return self.kind == "symbol" and self.symbol in "+-"

    def is_keyword_z(self) -> bool:
        return self.kind == "word" and self.word_type == "tz" and self.length == 1 and self.value == 0


class _Scanner:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.next_tok = self._scan()

    def _ch(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def _scan(self) -> _Tok:
        start = self.i
        ch = self._ch()
        if ch == "":
            return _Tok("end")
        if "0" <= ch <= "9":
            while self._ch() == "0":
                self.i += 1
            n, count = 0, 0
            while "0" <= self._ch() <= "9":
                if count < 9:
                    n = n * 10 + ord(self._ch()) - 48
                count += 1
                self.i += 1
            return _Tok("number", value=n, length=self.i - start)
        if ch in ":-+.)":
            self.i += 1
            return _Tok("symbol", symbol=ch)
        if ch >= "A" and ch not in JS_WS_CHARS:
            # a word: every character from 'A' up that is not whitespace; only its first 3 (lower-cased) count
            prefix = ""
            length = 0
            while self._ch() and self._ch() >= "A" and self._ch() not in JS_WS_CHARS:
                code = ord(self._ch())
                if len(prefix) < 3:
                    prefix += chr(code | 0x20) if code < 0x10000 else "\0"
                length += 2 if code > 0xFFFF else 1
                self.i += 1
            for name, kind, value in _KEYWORDS:
                # a word longer than the keyword only counts for month names ("Sept", "October")
                if prefix == name and (length <= 3 or kind == "month"):
                    return _Tok("word", value=value, length=length, word_type=kind)
            return _Tok("word", length=length, word_type="")
        if ch in JS_WS_CHARS:
            self.i += 1
            return _Tok("space", length=1)
        if ch == "(":
            balance = 0
            while True:
                c = self._ch()
                if c == ")":
                    balance -= 1
                elif c == "(":
                    balance += 1
                self.i += 1
                if not (balance > 0 and self._ch()):
                    break
            return _Tok("unknown")
        self.i += 1
        return _Tok("unknown")

    def next(self) -> _Tok:
        tok = self.next_tok
        self.next_tok = self._scan()
        return tok

    def peek(self) -> _Tok:
        return self.next_tok

    def skip_symbol(self, ch: str) -> bool:
        if self.next_tok.is_symbol(ch):
            self.next()
            return True
        return False


def _between(x: int, lo: int, hi: int) -> bool:
    return lo <= x <= hi


class _Day:
    def __init__(self) -> None:
        self.comp: list[int] = []
        self.named_month = _NONE
        self.iso = False

    def add(self, n: int) -> bool:
        if len(self.comp) < 3:
            self.comp.append(n)
            return True
        return False

    def write(self) -> tuple[int, int, int] | None:
        if not self.comp:
            return None
        comp = self.comp + [1] * (3 - len(self.comp))
        year = 0
        if self.named_month == _NONE:
            if self.iso or not _between(comp[0], 1, 31):
                year, month, day = comp
            else:
                month, day, year = comp
        else:
            month = self.named_month
            if not _between(comp[0], 1, 31):
                year, day = comp[0], comp[1]
            else:
                day, year = comp[0], comp[1]
        if not self.iso:
            if _between(year, 0, 49):
                year += 2000
            elif _between(year, 50, 99):
                year += 1900
        if not (_between(month, 1, 12) and _between(day, 1, 31)):
            return None
        return year, month - 1, day


class _Time:
    def __init__(self) -> None:
        self.comp: list[int] = []
        self.hour_offset = _NONE

    def empty(self) -> bool:
        return not self.comp

    def expecting(self, n: int) -> bool:
        i = len(self.comp)
        return (i == 1 and _between(n, 0, 59)) or (i == 2 and _between(n, 0, 59)) or (i == 3 and _between(n, 0, 999))

    def add(self, n: int) -> bool:
        if len(self.comp) < 4:
            self.comp.append(n)
            return True
        return False

    def add_final(self, n: int) -> bool:
        if not self.add(n):
            return False
        self.comp += [0] * (4 - len(self.comp))
        return True

    def write(self) -> tuple[int, int, int, int] | None:
        hour, minute, second, ms = self.comp + [0] * (4 - len(self.comp))
        if self.hour_offset != _NONE:
            if not _between(hour, 0, 12):
                return None
            hour = hour % 12 + self.hour_offset
        valid = _between(hour, 0, 23) and _between(minute, 0, 59) and _between(second, 0, 59) and _between(ms, 0, 999)
        # a 24th hour is allowed when minutes, seconds and milliseconds are 0
        if not valid and (hour != 24 or minute or second or ms):
            return None
        return hour, minute, second, ms


class _Zone:
    def __init__(self) -> None:
        self.sign = _NONE
        self.hour = _NONE
        self.minute = _NONE

    def set(self, offset_hours: int) -> None:
        self.sign = -1 if offset_hours < 0 else 1
        self.hour = abs(offset_hours)
        self.minute = 0

    def empty(self) -> bool:
        return self.hour == _NONE

    def is_utc(self) -> bool:
        return self.hour == 0 and self.minute == 0

    def expecting(self, n: int) -> bool:
        return self.hour != _NONE and self.minute == _NONE and _between(n, 0, 59)

    def offset_seconds(self) -> int | None:
        """None = local time."""
        if self.sign == _NONE:
            return None
        hour = 0 if self.hour == _NONE else self.hour
        minute = 0 if self.minute == _NONE else self.minute
        total = hour * 3600 + minute * 60
        return -total if self.sign < 0 else total


def _read_ms(tok: _Tok) -> int:
    number, length = tok.value, tok.length
    if length == 1:
        return number * 100
    if length == 2:
        return number * 10
    if length > 3:
        length = min(length, 9)
        factor = 1
        while length > 3:
            factor *= 10
            length -= 1
        return number // factor
    return number


def _parse_es5(sc: _Scanner, day: _Day, tm: _Time, tz: _Zone) -> _Tok:
    if sc.peek().is_sign():
        sign_tok = sc.next()
        if not sc.peek().is_fixed(6):
            return sign_tok
        year = sc.next().value
        if sign_tok.symbol == "-" and year == 0:
            return sign_tok
        day.add(-year if sign_tok.symbol == "-" else year)
    elif sc.peek().is_fixed(4):
        day.add(sc.next().value)
    else:
        return sc.next()
    if sc.skip_symbol("-"):
        if not sc.peek().is_fixed(2) or not _between(sc.peek().value, 1, 12):
            return sc.next()
        day.add(sc.next().value)
        if sc.skip_symbol("-"):
            if not sc.peek().is_fixed(2) or not _between(sc.peek().value, 1, 31):
                return sc.next()
            day.add(sc.next().value)
    if not (sc.peek().kind == "word" and sc.peek().word_type == "tsep"):
        if sc.peek().kind != "end":
            return sc.next()
    else:
        sc.next()
        if not sc.peek().is_fixed(2) or not _between(sc.peek().value, 0, 24):
            return _Tok("invalid")
        hour_is_24 = sc.peek().value == 24
        tm.add(sc.next().value)
        if not sc.skip_symbol(":"):
            return _Tok("invalid")
        if not sc.peek().is_fixed(2) or not _between(sc.peek().value, 0, 59) or (hour_is_24 and sc.peek().value > 0):
            return _Tok("invalid")
        tm.add(sc.next().value)
        if sc.skip_symbol(":"):
            if (
                not sc.peek().is_fixed(2)
                or not _between(sc.peek().value, 0, 59)
                or (hour_is_24 and sc.peek().value > 0)
            ):
                return _Tok("invalid")
            tm.add(sc.next().value)
            if sc.skip_symbol("."):
                if not sc.peek().is_number() or (hour_is_24 and sc.peek().value > 0):
                    return _Tok("invalid")
                tm.add(_read_ms(sc.next()))
        if sc.peek().is_keyword_z():
            sc.next()
            tz.set(0)
        elif sc.peek().is_sign():
            tz.sign = 1 if sc.next().symbol == "+" else -1
            if sc.peek().is_fixed(4):
                hourmin = sc.next().value
                hour, minute = divmod(hourmin, 100)
                if not (_between(hour, 0, 23) and _between(minute, 0, 59)):
                    return _Tok("invalid")
                tz.hour, tz.minute = hour, minute
            else:
                if not sc.peek().is_fixed(2) or not _between(sc.peek().value, 0, 23):
                    return _Tok("invalid")
                tz.hour = sc.next().value
                if not sc.skip_symbol(":"):
                    return _Tok("invalid")
                if not sc.peek().is_fixed(2) or not _between(sc.peek().value, 0, 59):
                    return _Tok("invalid")
                tz.minute = sc.next().value
        if sc.peek().kind != "end":
            return _Tok("invalid")
    if tz.empty() and tm.empty():
        tz.set(0)
    day.iso = True
    return _Tok("end")


def v8_date_parse(text: str) -> float | None:
    """`Date.parse(text)` as V8 does it: the time value in ms, or None for an Invalid Date."""
    sc = _Scanner(text)
    day, tm, tz = _Day(), _Time(), _Zone()
    tok = _parse_es5(sc, day, tm, tz)
    if tok.kind == "invalid":
        return None
    has_read_number = bool(day.comp)
    while tok.kind != "end":
        if tok.is_number():
            has_read_number = True
            n = tok.value
            if sc.skip_symbol(":"):
                if sc.skip_symbol(":"):
                    if not tm.empty():
                        return None
                    tm.add(n)
                    tm.add(0)
                else:
                    if not tm.add(n):
                        return None
                    if sc.peek().is_symbol("."):
                        sc.next()
            elif sc.skip_symbol(".") and tm.expecting(n):
                tm.add(n)
                if not sc.peek().is_number():
                    return None
                ms = _read_ms(sc.next())
                if ms < 0:
                    return None
                tm.add_final(ms)
            elif tz.expecting(n):
                tz.minute = n
            elif tm.expecting(n):
                tm.add_final(n)
                peek = sc.peek()
                if not (peek.kind in ("end", "space") or peek.is_keyword_z() or peek.is_sign()):
                    return None
            else:
                if not day.add(n):
                    return None
                sc.skip_symbol("-")
        elif tok.kind == "word":
            if tok.word_type == "ampm" and not tm.empty():
                tm.hour_offset = tok.value
            elif tok.word_type == "month":
                day.named_month = tok.value
                sc.skip_symbol("-")
            elif tok.word_type == "tz" and has_read_number:
                tz.set(tok.value)
            else:
                # garbage words are illegal once a number has been read, or right before one
                if has_read_number or sc.peek().is_number():
                    return None
        elif tok.is_sign() and (tz.is_utc() or not tm.empty()):
            tz.sign = 1 if tok.symbol == "+" else -1
            n, length = 0, 0
            if sc.peek().is_number():
                nt = sc.next()
                n, length = nt.value, nt.length
            has_read_number = True
            if sc.peek().is_symbol(":"):
                tz.hour, tz.minute = n, _NONE
            elif length in (1, 2):
                tz.hour, tz.minute = n, 0
            elif length in (3, 4):
                tz.hour, tz.minute = n // 100, n % 100
            else:
                return None
        elif (tok.is_sign() or tok.is_symbol(")")) and has_read_number:
            return None
        tok = sc.next()

    ymd = day.write()
    hms = tm.write()
    if ymd is None or hms is None:
        return None
    offset = tz.offset_seconds()
    year, month0, d = ymd
    hour, minute, second, msec = hms
    value = make_day(year, month0, d) * MS_PER_DAY + ((hour * 60 + minute) * 60 + second) * 1000 + msec
    value -= LOCAL_OFFSET_MS if offset is None else offset * 1000
    if abs(value) > MAX_TIME_MS:
        return None
    return float(value)
