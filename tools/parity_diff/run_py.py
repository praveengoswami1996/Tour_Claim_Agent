import json, math, sys
sys.path.insert(0, r"D:\CETPA\TourClaim_AI_Agent\src")
from tourclaim_agent.claimchat import claim_assistant as CA, row_order as RO, ocr_mapping as OM, place_check as PC, intents as IN, ocr_results as OR, ocr_references as OF
from tourclaim_agent.claimchat import js_compat as J

def ids(rows): return [r["id"] for r in rows]
def norm(v):
    if isinstance(v, dict): return {k: norm(x) for k, x in v.items() if k not in ("uploadDate",) and x is not J.UNDEFINED}
    if isinstance(v, (list, tuple)): return [norm(x) for x in v]
    if isinstance(v, set): return sorted(v)
    if isinstance(v, float) and not J.js_is_finite(v): return {"num": J.js_str(v)}
    if v is J.UNDEFINED: return None
    return v
def show_date(v):
    return CA._show_date(v) if J.js_truthy(v) else CA._show_date(v)
fns = {
  "applyEdits": lambda *a: CA.apply_edits(*a),
  "readClaimEdits": CA.read_claim_edits,
  "findMissingFields": CA.find_missing_fields,
  "findAmountIssues": CA.find_amount_issues,
  "findFormOnlyIssues": CA.find_form_only_issues,
  "describeMissing": lambda s: CA.describe_missing(s, CA.find_missing_fields(s)),
  "summarizeJourneys": CA.summarize_journeys, "summarizeStays": CA.summarize_stays, "summarizeLeave": CA.summarize_leave,
  "snapshot": lambda *a: CA.snapshot(*a),
  "mergeOcrRows": CA.merge_ocr_rows, "tourRulesFrom": CA.tour_rules_from,
  "journeysInDateOrder": lambda s: ids(RO.journeys_in_date_order(s["transportationRows"])),
  "staysInDateOrder": lambda s: ids(RO.stays_in_date_order(s["accommodationRows"])),
  "journeyOverlaps": lambda s: [[o["earlier"]["id"], o["later"]["id"]] for o in RO.journey_overlaps(s["transportationRows"])],
  "tripEnds": lambda s: [(t["first"] or {}).get("id"), (t["last"] or {}).get("id")] if (t := RO.trip_ends(s["transportationRows"])) else None,
  "keptFileIds": lambda s: RO.kept_file_ids([*s["transportationRows"], *s["accommodationRows"]]),
  "keys": lambda s: [[[CA.journey_key(r), CA.is_empty_transport(r)] for r in s["transportationRows"]], [[CA.stay_key(r), CA.is_empty_accommodation(r)] for r in s["accommodationRows"]], [CA.is_empty_leave(r) for r in s["leaveRows"]]],
  "toIsoDate": OM.to_iso_date, "toTime": OM.to_time, "mapTravelMode": OM.map_travel_mode,
  "placeProblem": PC.place_problem, "samePlaceBothEnds": PC.same_place_both_ends, "cleanMessage": IN.clean_message,
  "checkFiles": lambda fs: (lambda r: {"accepted": [f["name"] for f in r["accepted"]], "problems": r["problems"], "rejected": r["rejected"]})(IN.check_files(fs)),
  "number": J.js_number, "parseFloat": J.js_parse_float, "string": lambda v: J.js_str(J.js_number(v)),
  "inr": lambda v: J.en_in_money(0.0 if (n := J.js_number(v)) != n or n == 0 else n),
  "radix": J.js_fraction_to_radix, "showDate": CA._show_date, "claimTourOf": OF.claim_tour_of,
  "mapOcr": OM.map_ocr_to_rows, "combine": OR.combine_ocr_results,
  "combineMap": lambda files, bills: OM.map_ocr_to_rows(OR.combine_ocr_results(files), bills),
  "uniquelyNamed": lambda names, taken: [f["name"] for f in OR.uniquely_named([{"name": n} for n in names], taken)],
}
cases = json.load(open(sys.argv[1], encoding="utf-8"))
out = []
with J.use_runtime(now_ms=lambda: 1790000000000, random=lambda: 0.123456789):
    for c in cases:
        try:
            out.append({"ok": norm(fns[c["fn"]](*c["args"]))})
        except Exception as e:
            out.append({"err": type(e).__name__})
js = json.load(open(sys.argv[2], encoding="utf-8"))
def eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool): return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)): return a == b
    if isinstance(a, dict) and isinstance(b, dict): return a.keys() == b.keys() and all(eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list): return len(a) == len(b) and all(eq(x, y) for x, y in zip(a, b))
    return a == b
bad = {}
for c, p, j in zip(cases, out, js):
    if "err" in p and "err" in j: continue
    if not eq(p.get("ok", p), j.get("ok", j)):
        bad.setdefault(c["fn"], []).append((c, p, j))
print({k: len(v) for k, v in bad.items()}, "of", len(cases))
for k, v in bad.items():
    for c, p, j in v[:2]:
        print("==", k, json.dumps(c["args"], ensure_ascii=False)[:600]); print(" py", json.dumps(p, ensure_ascii=False)[:900]); print(" js", json.dumps(j, ensure_ascii=False)[:900])
