"""Phase 2a parity check: generated model answers for every eval case, graded by cases.ts and tools/ai_eval/cases.py.

Usage: python evalchecks_gen.py probes.jsonl [per_case]
Understand cases get random actions (offered ones and others), tour ids, answer values and edits; narrate cases get
replies stitched from the words their checks look for.
"""

import json
import random
import re
import sys

CASES = r"D:\CETPA\TourClaim_AI_Agent\Project-Context\agent\old_assistant_parity\C_eval_cases.json"
rnd = random.Random(20261106)

T_FIELDS = [
    "from",
    "to",
    "departureDate",
    "departureTime",
    "arrivalDate",
    "arrivalTime",
    "mode",
    "distanceKm",
    "remove",
    "source",
    "destination",
    "time",
]
A_FIELDS = [
    "location",
    "checkInDate",
    "checkInTime",
    "checkOutDate",
    "checkOutTime",
    "hotelName",
    "invoiceNo",
    "type",
    "remove",
]
L_FIELDS = ["startDate", "endDate", "remove"]
W_FIELDS = ["purpose", "from", "to", "departureTime", "arrivalTime", "mode", "vehicleType", "distanceKm", "date"]
VALUES = [
    "Mumbai",
    "Pune",
    "Noida",
    "Delhi airport",
    "Mumbai airport",
    "hotel",
    "office",
    "CST station",
    "home",
    "Jaipur",
    "18:00",
    "20:00",
    "10:30",
    "13:20",
    "11:15",
    "10:00",
    "07:00",
    "25:00",
    "2026-10-05",
    "2026-10-07",
    "2026-10-08",
    "2026-10-09",
    "2026-12-20",
    "2026-02-31",
    "rail",
    "air",
    "public-transport",
    "personal-vehicle",
    "official-entitled",
    "Rail",
    "Company Provided Vehicle",
    "Personal Vehicle",
    "Two Wheeler",
    "Four Wheeler",
    "bike",
    "car",
    "150",
    "24",
    "12.5",
    "22",
    "1200",
    "yes",
    "ok",
    "true",
    "Lemon Tree",
    "LT-5531",
    "INV-2231",
    "company_free",
    "own_arrangement",
    "Hotel Sea View",
    "Goa",
    "Meeting at Manesar depot",
    "hi",
    "",
    "Site inspection",
    24,
    30,
    12.5,
    0,
    None,
    True,
]
ACTIONS = [
    "talk",
    "edit",
    "yes",
    "no",
    "answer_question",
    "pick_tour",
    "open_saved_claim",
    "confirm_submit",
    "no_remove_it",
    "yes_keep_it",
    "no_keep_as_is",
    "skip_question",
]


def rand_edit(assistant):
    if assistant == "within":
        e = {
            "tour": rnd.choice([1, 2, 3, 4, 5, "2", 0]),
            "leg": rnd.choice([0, 0, 1, 2, 3]),
            "field": rnd.choice(W_FIELDS),
            "value": rnd.choice(VALUES),
        }
    else:
        section = rnd.choice(["transport", "transport", "accommodation", "leave", "other"])
        fields = {"transport": T_FIELDS, "accommodation": A_FIELDS, "leave": L_FIELDS, "other": T_FIELDS}[section]
        e = {
            "section": section,
            "row": rnd.choice([0, 0, 1, 2, 3, 4, 9, "2"]),
            "field": rnd.choice(fields),
            "value": rnd.choice(VALUES),
        }
    if rnd.random() < 0.05:
        del e[rnd.choice(list(e))]
    return e


def understand_probes(c, n):
    offered = [a["name"] for a in c["actions"]]
    for _ in range(n):
        action = rnd.choice(offered + ACTIONS)
        args = {}
        r = rnd.random()
        if r < 0.3:
            args["tourId"] = rnd.choice([4521, 4522, 4530, 4533, "#4522", "4530", 9999, 1234, None])
        elif r < 0.6:
            args["value"] = rnd.choice(VALUES)
        edits = [rand_edit(c["assistant"]) for _ in range(rnd.choice([0, 1, 1, 2, 3, 4]))]
        yield {"action": action, "args": args, "edits": edits if action == "edit" or rnd.random() < 0.2 else []}


def words_of(check_text):
    pieces = set()
    for lit in re.findall(r"/((?:\\.|[^/\n])+)/[gim]*", check_text):
        for alt in lit.split("|"):
            w = re.sub(r"\\[bsdw]|[\\^$()?*+\[\]{}]", "", alt).strip()
            if w:
                pieces.add(w)
    return sorted(pieces)


FILLER = [
    "Your claim is ready.",
    "Do you have more bills?",
    "I've added the return journey.",
    "**Noida → Mumbai**",
    "• Tap [Upload it]",
    "[Not now]",
    "Thanks!",
    "It has been submitted.",
    "It has not been submitted.",
    "Please check the form?",
    "2 bills",
    "nothing is missing",
    "missing: nothing",
    "Hello Ravi, I'm Aryaan.",
    "📎",
    "₹ 1200",
    "rs 500",
    "word " * 40,
    "\n",
    "  ",
    '{"a": 1}',
    "```",
    "T1",
    "cab",
    "?",
]


def narrate_probes(c, n):
    words = words_of(c["check"]) or ["claim"]
    for _ in range(n):
        parts = [rnd.choice(words + FILLER) for _ in range(rnd.randint(0, 8))]
        text = rnd.choice([" ", "\n", ". ", ", "]).join(parts)
        if rnd.random() < 0.3:
            text += rnd.choice(["?", " ?", "?**", "\n• Tap [Upload it] below", ""])
        if rnd.random() < 0.3:
            text = text.upper() if rnd.random() < 0.5 else text.lower()
        yield text


if __name__ == "__main__":
    per_case = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)
    n = 0
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
        for i, c in enumerate(cases):
            probes = understand_probes(c, per_case) if c["kind"] == "understand" else narrate_probes(c, per_case)
            key = "u" if c["kind"] == "understand" else "reply"
            for p in probes:
                f.write(json.dumps({"i": i, key: p}, ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} probes")
