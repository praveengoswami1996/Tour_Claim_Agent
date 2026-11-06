"""Phase 2a parity check: every combination of the chat-state fields, for assistantActions.ts vs assistant_actions.py.

Usage: python actions_gen.py states.jsonl   (writes one {"kind", "state"} per line; a missing key = undefined)
"""

import itertools
import json
import sys

ABSENT = object()
STEPS = [
    "start",
    "confirmTour",
    "pickTour",
    "bulkDrafts",
    "splitTours",
    "moreFiles",
    "gaps",
    "details",
    "stay",
    "leave",
    "confirm",
    "validate",
    "uploadChoice",
    "review",
    "submit",
]
BOOLS = ["inForm", "noTours", "resetAsk", "hasTour", "hasBulk", "hasSplit", "hasMissing", "canUndo", "hasSavedTours"]
YES_NO = [ABSENT, None, {"question": 'Shall I save "it"?', "yesLabel": 'Yes, "save" it', "noLabel": "Not now"}]
SUGGESTED = [ABSENT, None, 0, 4521]
PENDING = [ABSENT, None, "", "What time did you leave for journey 2 (Mumbai → Pune) on 07 Oct 2026?"]
RESOLUTIONS = [list(c) for n in range(5) for c in itertools.combinations(["chat", "upload", "form", "confirm"], n)]


def put(state, key, value):
    if value is not ABSENT:
        state[key] = value


def outside_states():
    for step, flags, yes_no, sug, pend in itertools.product(
        STEPS, itertools.product([False, True], repeat=9), YES_NO, SUGGESTED, PENDING
    ):
        s = {"step": step, **dict(zip(BOOLS, flags, strict=True))}
        put(s, "yesNo", yes_no)
        put(s, "suggestedTourId", sug)
        put(s, "pendingQuestion", pend)
        yield s
    # the review step: every shape of the review question
    reviews = []
    for blocking, res, yes, no, new_j, odd in itertools.product(
        [False, True],
        RESOLUTIONS,
        [ABSENT, "upload", "confirm", "details"],
        [ABSENT, "confirm", "remove"],
        [ABSENT, False, True],
        [ABSENT, False, True],
    ):
        r = {
            "question": "There are no return journey tickets. Do you have them?",
            "blocking": blocking,
            "resolutions": res,
        }
        put(r, "yes", yes)
        put(r, "no", no)
        put(r, "newJourney", new_j)
        put(r, "oddPlace", odd)
        reviews.append(r)
    for r, flags in itertools.product(reviews, itertools.product([False, True], repeat=5)):
        s = {
            "step": "review",
            "noTours": False,
            "hasBulk": False,
            "hasSplit": False,
            "hasMissing": False,
            **dict(zip(["inForm", "resetAsk", "hasTour", "canUndo", "hasSavedTours"], flags, strict=True)),
            "review": r,
        }
        yield s
    for step in STEPS:  # a review object outside the review step, and a null one inside it
        yield {"step": step, **dict.fromkeys(BOOLS, False), "review": reviews[-1]}
        yield {"step": step, **dict.fromkeys(BOOLS, False), "review": None}


def within_states():
    for yes_no, flags, pend in itertools.product(YES_NO, itertools.product([False, True], repeat=3), PENDING):
        s = dict(zip(["resetAsk", "declare", "canUndo"], flags, strict=True))
        put(s, "yesNo", yes_no)
        put(s, "pendingQuestion", pend)
        yield s


if __name__ == "__main__":
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
        n = 0
        for s in outside_states():
            f.write(json.dumps({"kind": "outside", "state": s}, ensure_ascii=False) + "\n")
            n += 1
        for s in within_states():
            f.write(json.dumps({"kind": "within", "state": s}, ensure_ascii=False) + "\n")
            n += 1
    print(f"{n} states")
