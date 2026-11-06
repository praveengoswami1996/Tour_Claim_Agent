"""Phase 2a parity check, Python side: the ported checks (tools/ai_eval/cases.py) on the probes, compared with Node.

Usage: python evalchecks_run_py.py probes.jsonl out_js.jsonl
"""

import json
import sys
from collections import Counter

sys.path.insert(0, r"D:\CETPA\TourClaim_AI_Agent\src")
sys.path.insert(0, r"D:\CETPA\TourClaim_AI_Agent\tools\ai_eval")
import cases as C  # noqa: E402

from tourclaim_agent.claimchat.ai.ai_assistant import Understood  # noqa: E402
from tourclaim_agent.claimchat.js_compat import use_runtime  # noqa: E402

CASES = r"D:\CETPA\TourClaim_AI_Agent\Project-Context\agent\old_assistant_parity\C_eval_cases.json"
NOW = 1793860200000  # Date.UTC(2026, 10, 5, 6, 30), as fake_runtime.mjs

if __name__ == "__main__":
    with open(CASES, encoding="utf-8") as f:
        pairs = C.attach_checks(json.load(f))
    with open(sys.argv[2], encoding="utf-8") as f:
        js = [json.loads(line) for line in f if line.strip()]
    with open(sys.argv[1], encoding="utf-8") as f:
        probes = [json.loads(line) for line in f if line.strip()]
    diffs = 0
    outcomes = Counter()
    with use_runtime(now_ms=lambda: NOW, random=lambda: 0.123456789):
        for k, p in enumerate(probes):
            case, cc = pairs[p["i"]]
            try:
                if "u" in p:
                    u = p["u"]
                    r = cc.check(Understood(u["action"], u["args"], u["edits"]))
                else:
                    r = cc.check(p["reply"])
            except Exception:  # noqa: BLE001
                r = "THROW"
            outcomes[f"{case['kind']} " + ("pass" if r is None else "THROW" if r == "THROW" else "fail")] += 1
            if r != js[k]["r"]:
                diffs += 1
                if diffs <= 10:
                    print(
                        "DIFF", case["id"], json.dumps(p, ensure_ascii=False)[:500], "\n  js:", js[k]["r"], "\n  py:", r
                    )
    print(f"{len(js)} probes, outcomes {dict(outcomes)}, {diffs} differences")
