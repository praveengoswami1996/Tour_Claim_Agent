"""Port of Tour_React tests/claimChatbot/keptReports.test.ts, plus checks of our own (expected values taken from the
TypeScript, run under Node). The browser's localStorage is a plain dict here."""

from __future__ import annotations

import json

from tourclaim_agent.claimchat.js_compat import use_runtime
from tourclaim_agent.claimchat.kept_reports import (
    REPORT_KEEP_MS,
    drop_reports,
    keep_report,
    read_reports,
    reports_for,
    reports_key,
)

key = reports_key(1001)


# TS: 'kept reports (positive): a report not seen yet is there the next time, in its own place'
def test_a_report_not_seen_yet_is_there_the_next_time_in_its_own_place():
    s: dict[str, str] = {}
    keep_report(
        s, key, {"scope": "page", "text": "Your 2 bills have been read: …", "tone": "success", "tourIds": [4600]}
    )
    keep_report(s, key, {"scope": "tour-4600", "text": "Your bill has been read: …", "tourIds": [4600]})
    assert [r["text"] for r in reports_for(s, key, "page")] == ["Your 2 bills have been read: …"]
    assert [r["text"] for r in reports_for(s, key, "tour-4600")] == ["Your bill has been read: …"]
    assert reports_for(s, key, "page")[0]["tourIds"] == [4600]


# TS: 'kept reports (negative): once seen, it is not said again; another place never gets it'
def test_once_seen_it_is_not_said_again():
    s: dict[str, str] = {}
    report_id = keep_report(s, key, {"scope": "page", "text": "A", "tourIds": []})
    assert reports_for(s, key, "tour-4600") == []
    drop_reports(s, key, [report_id])
    assert reports_for(s, key, "page") == []
    assert key not in s


# TS: 'kept reports (negative): another employee on the same browser does not get them'
def test_another_employee_does_not_get_them():
    s: dict[str, str] = {}
    keep_report(s, key, {"scope": "page", "text": "A", "tourIds": []})
    assert reports_for(s, reports_key(2002), "page") == []


class Blocked(dict):
    """storage that refuses everything"""

    def get(self, *_):
        raise RuntimeError("blocked")

    def __setitem__(self, *_):
        raise RuntimeError("blocked")

    def pop(self, *_):
        raise RuntimeError("blocked")


# TS: 'kept reports (negative): old, broken or blocked storage never breaks the chat'
def test_old_broken_or_blocked_storage_never_breaks_the_chat():
    s: dict[str, str] = {}
    t0 = 1_000_000
    keep_report(s, key, {"scope": "page", "text": "old", "tourIds": []}, t0)
    assert read_reports(s, key, t0 + REPORT_KEEP_MS + 1) == []
    s[key] = "{not json"
    assert read_reports(s, key) == []
    blocked = Blocked()
    assert read_reports(blocked, key) == []
    keep_report(blocked, key, {"scope": "page", "text": "x", "tourIds": []})  # does not throw
    keep_report(None, key, {"scope": "page", "text": "x", "tourIds": []})  # does not throw


# ── our own ──


def test_the_record_and_its_text_are_the_old_ones():
    s: dict[str, str] = {}
    with use_runtime(now_ms=lambda: 1790000000000, random=lambda: 0.123456789):
        report_id = keep_report(s, key, {"scope": "page", "text": "A", "tone": "error", "tourIds": [1, 2]})
    assert key == "aryaan.billReports.v1.1001"
    assert reports_key(None) == "aryaan.billReports.v1.anon"
    assert report_id == "1790000000000-4fzzzx"
    assert s[key] == (
        '[{"scope":"page","text":"A","tone":"error","tourIds":[1,2],"id":"1790000000000-4fzzzx","at":1790000000000}]'
    )


def test_records_that_are_not_reports_are_left_out_and_dropped_on_the_next_write():
    ok = {"text": "ok", "scope": "page", "at": "100", "id": "a"}
    s = {key: json.dumps([None, 5, {"scope": "page"}, {"text": "t", "scope": 1}, ok])}
    assert [r["text"] for r in read_reports(s, key, 200)] == ["ok"]
    # a report exactly REPORT_KEEP_MS old is gone
    assert read_reports(s, key, 100 + REPORT_KEEP_MS) == []
    drop_reports(s, key, ["zzz"], 200)
    assert json.loads(s[key]) == [{"text": "ok", "scope": "page", "at": "100", "id": "a"}]
