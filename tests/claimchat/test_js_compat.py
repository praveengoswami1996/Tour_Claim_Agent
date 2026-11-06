"""JavaScript semantics the port relies on.

Expected values were taken from Node 26 (V8, ICU, time zone Asia/Kolkata).
"""

from __future__ import annotations

import math

import pytest

from tourclaim_agent.claimchat.js_compat import (
    UNDEFINED,
    en_in_date,
    en_in_money,
    jget,
    js_fraction_to_radix,
    js_locale_compare,
    js_lookup,
    js_number,
    js_number_to_string,
    js_or,
    js_parse_float,
    js_round,
    js_str,
    js_trim,
    js_truthy,
    local_ymd,
    nullish,
    utc_iso_string,
    v8_date_parse,
)


def test_truthiness_nullish_and_or():
    assert [js_truthy(v) for v in ["", "0", 0, 0.0, math.nan, [], {}, None, UNDEFINED, False, " "]] == [
        False,
        True,
        False,
        False,
        False,
        True,
        True,
        False,
        False,
        False,
        True,
    ]
    assert nullish("", "x") == "" and js_or("", "x") == "x"
    assert jget({"a": None}, "a") is None and jget({}, "a") is UNDEFINED and jget("text", "a") is UNDEFINED


@pytest.mark.parametrize(
    ("value", "number"),
    [
        ("", 0.0),
        (" 12 ", 12.0),
        ("1e3", 1000.0),
        ("1.", 1.0),
        (".5", 0.5),
        ("0x1F", 31.0),
        ("0b101", 5.0),
        ("-Infinity", -math.inf),
        (None, 0.0),
        (True, 1.0),
        ([5], 5.0),
        ([], 0.0),
    ],
)
def test_number(value, number):
    assert js_number(value) == number


@pytest.mark.parametrize("value", [".", "12abc", "inf", "nan", "1_000", "-0x1", "1,000", UNDEFINED, {}, [1, 2]])
def test_number_nan(value):
    assert math.isnan(js_number(value))


def test_parse_float_and_round():
    assert js_parse_float("120 km") == 120 and js_parse_float(" -1.5e2x") == -150 and math.isnan(js_parse_float("far"))
    assert js_parse_float("1e") == 1 and js_parse_float("Infinityx") == math.inf
    assert [js_round(v) for v in (2.5, -2.5, 0.49999999999999994, -0.5, 1.4)] == [3, -2, 0, -0.0, 1]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (5, "5"),
        (5.0, "5"),
        (-0.0, "0"),
        (12.5, "12.5"),
        (0.1 + 0.2, "0.30000000000000004"),
        (1e21, "1e+21"),
        (1e20, "100000000000000000000"),
        (123456789012345680000, "123456789012345680000"),
        (0.000001, "0.000001"),
        (1e-7, "1e-7"),
        (1.23e-5, "0.0000123"),
        (1.5e300, "1.5e+300"),
        (math.inf, "Infinity"),
        (math.nan, "NaN"),
    ],
)
def test_number_to_string(value, text):
    assert js_number_to_string(value) == text


def test_string_of_other_values():
    assert [js_str(v) for v in (None, UNDEFINED, True, [1, None, "a"], {"a": 1})] == [
        "null",
        "undefined",
        "true",
        "1,,a",
        "[object Object]",
    ]
    assert js_trim("﻿  x  ") == "x" and js_trim("\x85x") == "\x85x"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1.005, "1.01"),
        (2.675, "2.68"),
        (0.125, "0.13"),
        (-1234.5, "-1,234.50"),
        (1234567.5, "12,34,567.50"),
        (9000, "9,000.00"),
        (1e21, "1,00,00,00,00,00,00,00,00,00,000.00"),
        (123456789012.345, "1,23,45,67,89,012.35"),
        (-0.004, "-0.00"),
        (99999.995, "1,00,000.00"),
    ],
)
def test_en_in_money(value, text):
    assert en_in_money(value) == text


def test_en_in_dates_say_sept():
    assert en_in_date(2026, 9, 5) == "05 Sept 2026"
    assert en_in_date(2026, 6, 5) == "05 Jun 2026"
    assert en_in_date(999, 9, 5) == "05 Sept 999"


def _local(text: str) -> str | None:
    ms = v8_date_parse(text)
    if ms is None:
        return None
    y, m, d = local_ymd(ms)
    return f"{y:04d}-{m:02d}-{d:02d}"


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("Oct 5", "2001-10-05"),  # no year: V8 takes 2001
        ("10/5", "2001-10-05"),
        ("Oct 5 26", "2026-10-05"),
        ("Oct 5 99", "1999-10-05"),
        ("2026 Oct 5", "2026-10-05"),
        ("5 Oct 2026 (IST)", "2026-10-05"),
        ("Sun Oct 05 2026 00:00:00 GMT+0530 (India Standard Time)", "2026-10-05"),
        ("5 Oct 2026 10:00 -5", "2026-10-05"),
        ("5 Oct 2026 20:00 PST", "2026-10-06"),
        ("Octopus 5 2026", "2026-10-05"),
        ("5 Oct 2026 24:00", "2026-10-06"),
        ("2026-10", "2026-10-01"),
        ("5 Oct 2026 24:01", None),
        ("32 Oct 2026", None),
        ("a5 Oct 2026", None),  # a stray word right before a number
        ("Oct5 2026", "2026-10-05"),
        ("5 Oct 2026 10 AM", None),
        ("5 Oct 2026 - 7 Oct 2026", None),
    ],
)
def test_v8_date_parse(text, day):
    assert _local(text) == day


def test_iso_string_and_radix():
    assert utc_iso_string(1790000000000) == "2026-09-21T14:13:20.000Z"
    assert js_fraction_to_radix(0.123456789) == "0.4fzzzxjylrx"
    assert js_fraction_to_radix(0.5) == "0.i"


def test_locale_compare_is_icu_root_order():
    assert js_locale_compare("garbage", "Select") == -1  # plain string order would say 1
    assert (
        js_locale_compare("a", "A") == -1
        and js_locale_compare("ab", "aB") == -1
        and js_locale_compare("aB", "Ab") == -1
    )
    assert js_locale_compare("a b", "ab") == -1 and js_locale_compare("a:b", "a0") == -1
    assert js_locale_compare("a\x01b", "ab") == 0  # control characters are ignored
    assert js_locale_compare("2026-10-05 09:00", "2026-10-05 10:30") == -1


def test_object_literal_lookup_sees_object_prototype():
    table = {"air": "Air"}
    assert js_lookup(table, "air") == "Air"
    assert js_lookup(table, "constructor") == "function Object() { [native code] }"
    assert js_lookup(table, "__proto__") == "[object Object]"
    assert js_lookup(table, None) is UNDEFINED
