"""What was read from bills, kept until the employee has seen it.

Port of Tour_React src/lib/claimChatbot/keptReports.ts. The bills are saved into the drafts (or the claim) as soon as
they are read, so a report the employee never saw (the window or the browser was closed first) is said the next time
the chat opens in the same place.

The TypeScript kept the reports in the browser's localStorage; here the store is passed in: any dict-like mapping of
key -> text (`store.get(key)`, `store[key] = text`, `store.pop(key, None)`), or None for no storage. The records and
their JSON text are the same: under `reports_key(emp_id)`, a JSON list of
{scope, text, tone?, tourIds, ..., id, at}. A store that fails (raises) is treated as the TypeScript treated blocked
storage: nothing is kept, and reading gives no reports.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Final, Literal, NotRequired, TypedDict

from .claim_payload import js_json_stringify
from .js_compat import jget, js_fraction_to_radix, js_number, js_random, js_str, now_ms, nullish
from .saved_claim import json_parse


class KeptReport(TypedDict):
    id: str
    # where it is said: "page" (Raise Claim page) or "tour-<id>" (that claim)
    scope: str
    text: str
    tone: NotRequired[Literal["error", "success"]]
    # tours whose claim the bills went into (their "Open claim" buttons)
    tourIds: list[int]
    at: int


Store = MutableMapping[str, str] | None

# a report not seen in this long is not said any more
REPORT_KEEP_MS: Final = 14 * 24 * 60 * 60 * 1000


def reports_key(emp_id: Any) -> str:
    """keptReports.ts:reportsKey"""
    return f"aryaan.billReports.v1.{js_str(nullish(emp_id, 'anon'))}"


def _now(now: int | float | None) -> int | float:
    """the `now = Date.now()` default"""
    return now_ms() if now is None else now


def read_reports(store: Store, key: str, now: int | float | None = None) -> list[KeptReport]:
    """keptReports.ts:readReports: the reports kept under `key`, without those older than REPORT_KEEP_MS."""
    now = _now(now)
    try:
        text = store.get(key) if store is not None else None
        parsed = json_parse(text if isinstance(text, str) and text else "[]")
        if not isinstance(parsed, list):
            return []
        return [
            r
            for r in parsed
            if isinstance(r, dict)
            and isinstance(jget(r, "text"), str)
            and isinstance(jget(r, "scope"), str)
            and now - js_number(jget(r, "at")) < REPORT_KEEP_MS
        ]
    except Exception:  # noqa: BLE001 - blocked or broken storage never breaks the chat
        return []


def _write(store: Store, key: str, reports: list[Any]) -> None:
    try:
        if store is None:
            return
        if reports:
            store[key] = js_json_stringify(reports)
        else:
            store.pop(key, None)
    except Exception:  # noqa: BLE001, S110 - storage can be blocked: the report is then only said now
        pass


def keep_report(store: Store, key: str, report: dict[str, Any], now: int | float | None = None) -> str:
    """keptReports.ts:keepReport: keeps a report (scope, text, tone?, tourIds); returns its id."""
    now = _now(now)
    report_id = f"{js_str(now)}-{js_fraction_to_radix(js_random(), 36)[2:8]}"
    _write(store, key, [*read_reports(store, key, now), {**report, "id": report_id, "at": now}])
    return report_id


def drop_reports(store: Store, key: str, ids: list[str], now: int | float | None = None) -> None:
    """keptReports.ts:dropReports: the employee has seen these."""
    now = _now(now)
    _write(store, key, [r for r in read_reports(store, key, now) if not any(jget(r, "id") == i for i in ids)])


def reports_for(store: Store, key: str, scope: str, now: int | float | None = None) -> list[KeptReport]:
    """keptReports.ts:reportsFor: reports of one place, oldest first."""
    return [r for r in read_reports(store, key, now) if r["scope"] == scope]
