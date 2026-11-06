"""Phase 1b parity check, Python side: runs the cases through the Python port and compares with the Node output.

Usage: python run1b_py.py cases1b.json out_js1b.json
"""

import json
import sys

sys.path.insert(0, r"D:\CETPA\TourClaim_AI_Agent\src")
from tourclaim_agent.claimchat import bill_summary as BS  # noqa: E402
from tourclaim_agent.claimchat import claim_link as CL  # noqa: E402
from tourclaim_agent.claimchat import claim_payload as PB  # noqa: E402
from tourclaim_agent.claimchat import claim_progress as CP  # noqa: E402
from tourclaim_agent.claimchat import js_compat as J  # noqa: E402
from tourclaim_agent.claimchat import load_assistant_tour as LT  # noqa: E402
from tourclaim_agent.claimchat import tour_matcher as TM  # noqa: E402


def norm(v):
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items() if k not in ("uploadDate",) and x is not J.UNDEFINED}
    if isinstance(v, list | tuple):
        return [norm(x) for x in v]
    if isinstance(v, float) and not J.js_is_finite(v):
        return {"num": J.js_str(v)}
    if v is J.UNDEFINED:
        return None
    return v


def build(p):
    r = PB.build_claim_payload(p)
    return {**r, "text": J.js_str(PB.js_json_stringify(r["draftPayload"]))}


def assistant_save(s, emp_id, tour_id, draft_id, overwrite):
    r = PB.assistant_draft_save(s, emp_id=emp_id, tour_id=tour_id, draft_id=draft_id, overwrite=overwrite)
    # the old code did not send the retained ids (deviation 2): compare without them, and count them
    payload = {k: v for k, v in r["draftData"].items() if not k.startswith("Retained")}
    if len(payload) != len(r["draftData"]):
        RETAINED[0] += 1
    return {"text": PB.js_json_stringify(payload), "parts": [[part, f["name"]] for part, f in r["files"]]}


RETAINED = [0]
fns = {
    "tourWindow": TM.tour_window,
    "norm": TM.norm,
    "samePlace": TM.same_place,
    "matchTours": TM.match_tours,
    "findTourByText": TM.find_tour_by_text,
    "triageRows": TM.triage_rows,
    "eachDay": CP.each_day,
    "cpDayText": CP.day_text,
    "cpRangeText": CP.range_text,
    "groupDays": CP.group_days,
    "coveredDays": CP.covered_days,
    "uncoveredDays": CP.uncovered_days,
    "coveredText": CP.covered_text,
    "findRelationIssues": CP.find_relation_issues,
    "findTimeOverlaps": CP.find_time_overlaps,
    "findClaimIssues": CP.find_claim_issues,
    "buildProgress": CP.build_progress,
    "progressText": lambda p: CP.progress_text(CP.build_progress(p)),
    "stayBillProblem": CP.stay_bill_problem,
    "describeRow": BS.describe_row,
    "bsDayText": lambda v: BS.day_text(J.UNDEFINED if v is None else v),
    "bsRangeText": lambda a, b: BS.range_text(J.UNDEFINED if a is None else a, J.UNDEFINED if b is None else b),
    "findingsByFile": lambda rows: list(map(list, BS.findings_by_file(rows).items())),
    "fileLine": BS.file_line,
    "groupOf": BS.group_of,
    "uploadSortedText": lambda files, fates: BS.upload_sorted_text(files, dict(map(tuple, fates))),
    "readSummaryText": lambda s: BS.read_summary_text({**s, "fates": dict(map(tuple, s["fates"]))}),
    "claimFooter": BS.claim_footer,
    "readHeader": BS.read_header,
    "strongerFate": BS.stronger_fate,
    "claimLink": CL.claim_link,
    "stateFromClaimLink": CL.state_from_claim_link,
    "clean": lambda o: "<undefined>" if (r := PB.clean_object_for_payload(o)) is J.UNDEFINED else r,
    "buildClaimPayload": build,
    "collectNewFiles": lambda s: (
        lambda r: [[f["name"] for f in r[k]] for k in ("transportationFiles", "accommodationFiles")]
    )(PB.collect_new_files(s["transportationRows"], s["accommodationRows"])),
    "assistantSave": assistant_save,
    "fetchAssistantTour": LT.assistant_tour_from,
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
errors = {}
for c, p, j in zip(cases, out, js, strict=True):
    if "err" in p or "err" in j:
        errors[c["fn"]] = errors.get(c["fn"], 0) + 1
    if "err" in p and "err" in j:
        continue
    if not eq(p.get("ok", p), j.get("ok", j)):
        bad.setdefault(c["fn"], []).append((c, p, j))
counts: dict[str, int] = {}
for c in cases:
    counts[c["fn"]] = counts.get(c["fn"], 0) + 1
print("cases:", len(cases), counts)
print("both threw:", errors)
print("retained ids sent (deviation 2) in", RETAINED[0], "assistantSave cases")
print("differences:", {k: len(v) for k, v in bad.items()} or "none")
for k, v in bad.items():
    for c, p, j in v[:2]:
        print("==", k, json.dumps(c["args"], ensure_ascii=False)[:1500])
        print(" py", json.dumps(p, ensure_ascii=False)[:1500])
        print(" js", json.dumps(j, ensure_ascii=False)[:1500])
