"""Phase 1c parity check, Python side: runs the cases through the Python port and compares with the Node output.

Usage: python run1c_py.py cases1c.json out_js1c.json

The rows claimStateFromDraft builds carry `_saved` (deviation 1): it is stripped before comparing, and checked on its
own (dto = the saved item as read, row = the row as parsed).
"""

import copy
import json
import sys

sys.path.insert(0, r"D:\CETPA\TourClaim_AI_Agent\src")
from tourclaim_agent.claimchat import js_compat as J  # noqa: E402
from tourclaim_agent.claimchat import kept_reports as KR  # noqa: E402
from tourclaim_agent.claimchat import saved_claim as SC  # noqa: E402
from tourclaim_agent.claimchat import tour_review as TR  # noqa: E402
from tourclaim_agent.claimchat.claim_payload import SAVED_ROW_KEY  # noqa: E402


def norm(v):
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items() if k not in ("uploadDate", SAVED_ROW_KEY) and x is not J.UNDEFINED}
    if isinstance(v, list | tuple):
        return [norm(x) for x in v]
    if isinstance(v, float) and not J.js_is_finite(v):
        return {"num": J.js_str(v)}
    if v is J.UNDEFINED:
        return None
    return v


SAVED = {"rows": 0, "bad": 0}


def check_saved(state, draft):
    """every row read from a saved item carries it, and the row as parsed"""
    data = SC._field(draft, "draftData")
    data = draft if J.is_nullish(data) else data
    lists = {
        "transportationRows": SC._list(J.nullish(SC._field(data, "TransportaionDetailDtos"),
                                                 SC._field(data, "TransportationDetailDtos"))),
        "accommodationRows": SC._list(J.nullish(SC._field(data, "AccomodationDetailDto"),
                                                SC._field(data, "AccommodationDetailDto"))),
        "leaveRows": SC._list(SC._field(data, "LeaveAvailedDto")),
    }  # fmt: skip
    for section, items in lists.items():
        rows = state[section] if items else []
        for row, item in zip(rows, items, strict=True):
            SAVED["rows"] += 1
            kept = row.get(SAVED_ROW_KEY)
            plain = {k: v for k, v in row.items() if k != SAVED_ROW_KEY}
            if not kept or kept["dto"] != item or kept["row"] != plain or kept["row"] is plain:
                SAVED["bad"] += 1
    for section in lists:
        if not lists[section]:
            SAVED["bad"] += sum(1 for r in state[section] if SAVED_ROW_KEY in r)


def claim_state_from_draft(d):
    state = SC.claim_state_from_draft(copy.deepcopy(d))
    check_saved(state, d)
    return state


def short(inp, excl):
    return TR.missing_in_short([i for i in TR.review_claim(inp) if i["kind"] not in excl])


def loop(inp, actions):
    items = TR.review_claim(inp)
    confirmed, deferred, steps = [], [], []
    cset, dset = set(), set()
    for how in [*actions, None]:
        s = TR.next_review_step(items, cset, dset)
        steps.append([s["type"], s["item"]["id"] if "item" in s else None, len(s["open"]) if "open" in s else None,
                      [i["id"] for i in s.get("blocking", [])]])  # fmt: skip
        if s["type"] != "ask" or how is None:
            break
        before_c, before_d = set(cset), set(dset)
        TR.settle(s["item"], how, cset, dset)
        confirmed += [x for x in cset if x not in before_c]
        deferred += [x for x in dset if x not in before_d]
    return {"steps": steps, "confirmed": confirmed, "deferred": deferred}


def notes(inp, again, claim_text):
    items = TR.review_claim(inp)
    return {
        "q": [TR.review_question_note(it, items[k:], again) for k, it in enumerate(items)],
        "s": [[TR.review_settled_note(it, how) for how in ("confirm", "later", "upload")] for it in items],
        "b": TR.review_blocked_note([i for i in items if i["blocking"]]),
        "d": [TR.review_done_note(claim_text, True), TR.review_done_note(claim_text, False)],
    }


def saved_flow(d, incoming, tour, claim):
    saved = claim_state_from_draft(d)
    cmp = SC.compare_with_saved(saved, incoming)
    added, already = cmp["added"], cmp["alreadySaved"]
    merged = SC.add_to_saved(saved, added)
    return {
        "added": added, "alreadySaved": already, "merged": merged, "count": SC.rows_count(added),
        "days": SC.bill_days(added), "savedDays": SC.bill_days(saved),
        "text": SC.saved_claim_text({"claim": claim, "saved": saved, "added": added, "alreadySaved": already,
                                     "merged": merged, "tour": tour}),
    }  # fmt: skip


def summary(tour, state, opts, waiting):
    s = SC.saved_draft_summary(tour, state, opts)
    return {
        "s": s,
        "model": SC.draft_for_model(s),
        "notes": [SC.saved_drafts_greeting_note([s], waiting),
                  SC.saved_drafts_greeting_note([s, {**s, "savedInChat": False}], waiting),
                  SC.saved_drafts_greeting_note([s, {**s, "savedInChat": True}, {**s}], waiting)],
    }  # fmt: skip


def kept(ops):
    store: dict[str, str] = {}
    out = []
    for op in ops:
        key = KR.reports_key(op[1])
        if op[0] == "keep":
            out.append(KR.keep_report(store, key, op[2], op[3]))
        elif op[0] == "drop":
            out.append(KR.drop_reports(store, key, op[2], op[3]))
        elif op[0] == "for":
            out.append(KR.reports_for(store, key, op[2], op[3]))
        elif op[0] == "read":
            out.append(KR.read_reports(store, key, op[2]))
        else:
            store[key] = op[2]
    return {"out": out, "store": sorted([k, v] for k, v in store.items())}


fns = {
    "reviewClaim": TR.review_claim,
    "short": short,
    "loop": loop,
    "notes": notes,
    "claimStateFromDraft": claim_state_from_draft,
    "savedFlow": saved_flow,
    "savedDraftSummary": summary,
    "savedDraftSummaryFromDraft": lambda tour, d: SC.saved_draft_summary(tour, claim_state_from_draft(d)),
    "kept": kept,
}

cases = json.load(open(sys.argv[1], encoding="utf-8"))
js = json.load(open(sys.argv[2], encoding="utf-8"))
out = []
with J.use_runtime(now_ms=lambda: 1790000000000, random=lambda: 0.123456789):
    for c in cases:
        try:
            out.append({"ok": norm(fns[c["fn"]](*c["args"]))})
        except Exception as e:  # noqa: BLE001
            out.append({"err": type(e).__name__, "msg": str(e)})


def eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(eq(x, y) for x, y in zip(a, b, strict=True))
    return a == b


bad: dict[str, list] = {}
errors: dict[str, int] = {}
one_threw: dict[str, int] = {}
for c, p, j in zip(cases, out, js, strict=True):
    if "err" in p and "err" in j:
        errors[c["fn"]] = errors.get(c["fn"], 0) + 1
        continue
    if not eq(p.get("ok", p), j.get("ok", j)):
        bad.setdefault(c["fn"], []).append((c, p, j))
counts: dict[str, int] = {}
for c in cases:
    counts[c["fn"]] = counts.get(c["fn"], 0) + 1
kinds: dict[str, int] = {}
for c, j in zip(cases, js, strict=True):
    if c["fn"] == "reviewClaim" and "ok" in j:
        for i in j["ok"]:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
print("cases:", len(cases), counts)
print("review item kinds seen (Node):", dict(sorted(kinds.items())))
print("both threw:", errors)
print("saved rows checked (deviation 1):", SAVED)
print("differences:", {k: len(v) for k, v in bad.items()} or "none")
for k, v in bad.items():
    for c, p, j in v[:2]:
        print("==", k, json.dumps(c["args"], ensure_ascii=False)[:2500])
        print(" py", json.dumps(p, ensure_ascii=False)[:2500])
        print(" js", json.dumps(j, ensure_ascii=False)[:2500])
