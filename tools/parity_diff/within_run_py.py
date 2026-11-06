"""Runs within_tour_plan.py / within_assistant.py on the cases from within_gen.py and compares with Node's output.

Usage: python within_run_py.py within_cases.json within_out_js.json
"""

import json
import sys

sys.path.insert(0, r"D:\CETPA\TourClaim_AI_Agent\src")
from tourclaim_agent.claimchat import js_compat as J  # noqa: E402
from tourclaim_agent.claimchat import within_assistant as WA  # noqa: E402
from tourclaim_agent.claimchat import within_tour_plan as TP  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def norm(v):
    if v is J.UNDEFINED:
        return {"undef": True}
    return _norm(v)


def _norm(v):
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items() if x is not J.UNDEFINED}
    if isinstance(v, list | tuple):
        return [None if x is J.UNDEFINED else _norm(x) for x in v]
    if isinstance(v, float) and not J.js_is_finite(v):
        return {"num": J.js_str(v)}
    return v


def _tour_plan_error_text(e):
    return f"TourPlanError: {e}"


def plan_flow(plan, answers, edits, ctx):
    ts = TP.map_tour_plan_to_tours(plan)["tours"]
    gaps = WA.find_gaps(ts)
    errors = []
    for i, g in enumerate(gaps):
        r = WA.answer_gap(ts, g, answers[i % len(answers)])
        ts = r["tours"]
        errors.append(r.get("error"))
    e = WA.apply_within_edits(ts, edits)
    t = e["tours"]
    return [
        gaps,
        errors,
        e,
        WA.find_gaps(t),
        WA.tour_discrepancies(t),
        [WA.tour_ready_to_end(x) for x in t],
        WA.snapshot(t),
        WA.tour_summary_rows(t),
        WA.progress_text(t, ctx),
        WA.totals_text(t),
        WA.has_tour_data(t),
    ]


fns = {
    "planFlow": plan_flow,
    "mapWithinTravelMode": TP.map_within_travel_mode,
    "mapVehicleType": lambda v, m=J.UNDEFINED: TP.map_vehicle_type(v, m),
    "mapTourPlanToTours": TP.map_tour_plan_to_tours,
    "isoOf": WA.iso_of,
    "journeysOf": WA.journeys_of,
    "setJourneyField": WA.set_journey_field,
    "hasTourData": WA.has_tour_data,
    "tourDiscrepancies": WA.tour_discrepancies,
    "tourReadyToEnd": lambda ts: [WA.tour_ready_to_end(t) for t in ts],
    "findGaps": WA.find_gaps,
    "answerGap": WA.answer_gap,
    "answerFound": lambda ts, i, text: WA.answer_gap(
        ts, J.UNDEFINED if i >= len(g := WA.find_gaps(ts)) else g[i], text
    ),
    "applyWithinEdits": WA.apply_within_edits,
    "readWithinEdits": WA.read_within_edits,
    "tourSummaryRows": WA.tour_summary_rows,
    "claimTotals": WA.claim_totals,
    "totalsText": WA.totals_text,
    "progressText": WA.progress_text,
    "snapshot": WA.snapshot,
    "blankTour": WA.blank_tour,
    "constants": lambda: [WA.WITHIN_EDIT_FORMAT, WA.WITHIN_DA_RULES, TP.WITHIN_TRAVEL_MODES, TP.TOUR_PLAN_MIN_DATE],
}

MISSING = "__missing__"


def unmissing(v):
    """The generator writes a left-out key as MISSING (JSON has no undefined): drop it again."""
    if isinstance(v, dict):
        return {k: unmissing(x) for k, x in v.items() if x != MISSING}
    if isinstance(v, list):
        return [J.UNDEFINED if x == MISSING else unmissing(x) for x in v]
    return v


def main():
    cases = json.load(open(sys.argv[1], encoding="utf-8"))
    js = json.load(open(sys.argv[2], encoding="utf-8"))
    out = []
    for c in cases:
        with J.use_runtime(now_ms=lambda c=c: c.get("now", 1793934000000), random=lambda: 0.123456789):
            try:
                out.append({"ok": norm(fns[c["fn"]](*unmissing(c["args"])))})
            except TP.TourPlanError as e:
                out.append({"err": _tour_plan_error_text(e)})
            except Exception as e:  # noqa: BLE001
                out.append({"err": type(e).__name__})

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

    bad = {}
    seen = {}
    for c, p, j in zip(cases, out, js, strict=True):
        kind = "err" if "err" in j else "ok"
        seen[(c["fn"], kind)] = seen.get((c["fn"], kind), 0) + 1
        if not eq(p, j):
            bad.setdefault(c["fn"], []).append((c, p, j))
    print("cases per function (ok / threw):")
    for fn in sorted({k[0] for k in seen}):
        print(f"  {fn}: {seen.get((fn, 'ok'), 0)} / {seen.get((fn, 'err'), 0)}")
    print("differences:", {k: len(v) for k, v in bad.items()}, "of", len(cases))
    for k, v in bad.items():
        for c, p, j in v[:3]:
            print("==", k, json.dumps(c, ensure_ascii=False)[:900])
            print(" py", json.dumps(p, ensure_ascii=False)[:900])
            print(" js", json.dumps(j, ensure_ascii=False)[:900])


if __name__ == "__main__":
    main()
