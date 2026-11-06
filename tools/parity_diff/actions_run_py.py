"""Phase 2a parity check, Python side: assistant_actions.py on every state, compared with the Node hashes.

Usage: python actions_run_py.py states.jsonl out_js.txt
"""

import hashlib
import json
import sys

sys.path.insert(0, r"D:\CETPA\TourClaim_AI_Agent\src")
from tourclaim_agent.claimchat.ai import assistant_actions as AA  # noqa: E402


def digest(kind, state):
    q = AA.outside_open_question(state) if kind == "outside" else AA.within_open_question(state)
    actions = AA.outside_actions(state) if kind == "outside" else AA.within_actions(state)
    listed = [
        {"name": a.name, "description": a.description, **({"args": a.args} if a.args is not None else {})}
        for a in actions
    ]
    text = json.dumps({"q": q, "actions": listed}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), text


if __name__ == "__main__":
    with open(sys.argv[2], encoding="utf-8") as f:
        js = f.read().split()
    diffs = total = 0
    counts = {"outside": 0, "within": 0}
    with open(sys.argv[1], encoding="utf-8") as f:
        for i, line in enumerate(f):
            case = json.loads(line)
            h, text = digest(case["kind"], case["state"])
            counts[case["kind"]] += 1
            total += 1
            if h != js[i]:
                diffs += 1
                if diffs <= 5:
                    print("DIFF", json.dumps(case, ensure_ascii=False), "\n  py:", text[:600])
    print(f"{total} states ({counts['outside']} outside, {counts['within']} within), {len(js)} from Node")
    print(f"{diffs} differences")
