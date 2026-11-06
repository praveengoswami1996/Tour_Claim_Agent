"""Random cases for the Within Jurisdiction parity check (within_run.mjs vs within_run_py.py).

Usage: python within_gen.py within_cases.json [count per function]
"""

import json
import random
import sys

random.seed(int(sys.argv[3]) if len(sys.argv) > 3 else 23)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 400
M = "__missing__"

# clock: 6 Nov 2026, 30 Nov 2026 (setMonth rolls 30 Feb over), 31 Oct 2025, 15 Jan 2026 (IST)
NOWS = [1793934000000, 1795980600000, 1761890400000, 1768455000000]

MODES_TEXT = [
    "Rail",
    "rail",
    "train",
    "By Train",
    "Public Transport",
    "public transport beyond entitled",
    "beyond",
    "bus",
    "Taxi",
    "cab",
    "auto rickshaw",
    "metro",
    "Company Provided Vehicle",
    "official car",
    "company",
    "Personal Vehicle",
    "personal vehicle (Four Wheeler)",
    "own car",
    "own bike",
    "down town",
    "helicopter",
    "walk",
    "",
    "  ",
    None,
    M,
    "Personal Vehicle - 2 wheeler",
    "PUBLIC",
    "Air",
    "flight",
]
VEHICLES = [
    "Two Wheeler",
    "Four Wheeler",
    "2-wheeler",
    "two  wheel",
    "4 wheel",
    "bike",
    "scooter",
    "scooty",
    "car",
    "Car",
    "carpool",
    "motorcycle",
    "",
    None,
    M,
    "4wheeler",
    "two wheeler",
]
OCR_DATES = [
    "2026-10-05",
    "2026-10-1",
    "2026-10-31",
    "05/10/2026",
    "5-10-26",
    "5 Oct 2026",
    "Oct 7, 2026",
    "7th October 2026",
    "2026-10-05T00:00:00",
    "",
    None,
    M,
    "garbage",
    "2026-02-30",
    "31/04/2026",
    "2026-10-15",
    "2026-10-20",
    "2026-10-02",
]
BOUNDARY_MONTHS = [
    ["2025-09-20", "23/09/2025", "2025-09-24", "24 Sep 2025", "2025-09-25", "2025-09-30"],
    ["2027-02-01", "2027-02-05", "06/02/2027", "2027-02-07", "Feb 20, 2027", "2027-02-28"],
    ["2027-03-01", "2027-03-02", "3 Mar 2027", "2027-03-31"],
    ["2026-01-01", "2026-01-31", "2026-01-15"],
]
OTHER_MONTH = ["2026-11-02", "2025-09-20", "2025-10-01", "2027-06-01", "2026-01-10", "2026-12-25", "1999-12-31"]
TIMES = [
    "09:30",
    "9:30",
    "9.30 pm",
    "10 AM",
    "1030",
    "noon",
    "midnight",
    "10:30 hrs",
    "25:00",
    "",
    None,
    M,
    "7:5",
    "12 am",
    "12:15 pm",
    "18:00",
    "abc",
]
PLACES = [
    "Delhi",
    "Noida",
    "Sector 62, Noida",
    " Gurugram ",
    "",
    "  ",
    None,
    M,
    "New Delhi (NDLS)",
    "Greater Noida",
    "बड़ौदा",
    "A",
    "X1",
]
DISTANCES = [0, 12, 12.5, "12", "", None, M, -1, "1e2", "abc", "1,200", 2500, True, [7]]
PURPOSES = [
    "Inspect; Review; Audit; Meeting",
    "Inspect;Review",
    "One purpose",
    "",
    None,
    M,
    " ; ; ",
    "A;B;C;D;E;F",
    "Site visit; ",
    "x",
]


def pick(d, k, pool):
    v = random.choice(pool)
    if v != M:
        d[k] = v


def ocr_journey(month_dates):
    j = {}
    pick(j, "from", PLACES)
    pick(j, "to", PLACES)
    pick(j, "departureDate", month_dates)
    pick(j, "departureTime", TIMES)
    pick(j, "arrivalDate", month_dates + [M, None, ""])
    pick(j, "arrivalTime", TIMES)
    pick(j, "modeOfTravel", MODES_TEXT)
    pick(j, "personalVehicleType", VEHICLES)
    pick(j, "distance", DISTANCES)
    pick(j, "includeTravelCosts", [True, False, None, 0, M])
    return j


def ocr_plan():
    r = random.random()
    if r < 0.08:
        meta = {
            "per_file_results": [{"filename": "x.pdf", "success": False, "error": random.choice(["bad", "", None])}]
        }
    elif r < 0.14:
        meta = {
            "per_file_results": [
                {"filename": "x.pdf", "success": True, "bill_type": random.choice(["ticket", "hotel_bill"])}
            ]
        }
    elif r < 0.5:
        meta = {"per_file_results": [{"filename": "x.pdf", "success": True, "bill_type": "tour_plan"}]}
    else:
        meta = random.choice(
            [M, {}, {"per_file_results": []}, None, {"per_file_results": [{"filename": "x", "success": True}]}]
        )
    scenario = random.random()
    if scenario < 0.5:
        month_dates = OCR_DATES + (OTHER_MONTH if random.random() < 0.15 else [])
    else:
        # dates on both sides of a window edge: 24 Sep 2025, and today + 3 months (30 Nov -> 2 Mar)
        month_dates = random.choice(BOUNDARY_MONTHS) + ["", None, M, "garbage"]
    td = {}
    main = ocr_journey(month_dates)
    for k_ocr, k_main in [
        ("from", "from"),
        ("to", "to"),
        ("departureDate", "startDate"),
        ("departureTime", "startTime"),
        ("arrivalDate", "endDate"),
        ("arrivalTime", "endTime"),
        ("modeOfTravel", "modeOfTravel"),
        ("personalVehicleType", "personalVehicleType"),
        ("distance", "distance"),
        ("includeTravelCosts", "includeTravelCosts"),
    ]:
        if k_ocr in main:
            td[k_main] = main[k_ocr]
    pick(td, "purpose", PURPOSES)
    td["tourLegs"] = [ocr_journey(month_dates) for _ in range(random.randint(0, 9))]
    if random.random() < 0.03:
        td["tourLegs"].append(None)
    plan = {"tourDetails": td}
    if meta != M:
        plan["meta"] = meta
    if random.random() < 0.03:
        plan = {"meta": plan.get("meta")}
    return plan


# ─── form tours ───────────────────────────────────────────────────────────────

FORM_DATES = [
    "2026-09-30T18:30:00.000Z",
    "2026-10-01",
    "2026-10-05",
    "2026-02-30",
    "2026-10-05T10:00:00",
    "Oct 5 2026",
    "",
    None,
    M,
    "garbage",
    1790000000000,
    "0000-00-00",
    "2026-10-04T18:30:00.000Z",
]
LEG_DATES = ["2026-10-01", "2026-10-05", "", None, M, "2026-10-05T00:00:00", "5/10/2026", "2026-10-02"]
FORM_TIMES = ["09:30", "11:00", "", None, M, "09:30", "21:15"]
FORM_PLACES = ["Delhi", "Noida", "Sector 62, Noida", "", "  ", None, M, "Delhi", "Gurugram, Haryana", " Pune"]
FORM_MODES = [
    "Rail",
    "Public Transport",
    "Public Transport Beyond Entitled",
    "Company Provided Vehicle",
    "Personal Vehicle",
    "Personal Vehicle",
    "",
    None,
    M,
]
FORM_VEHICLES = ["Two Wheeler", "Four Wheeler", "", None, M]
FORM_DIST = [0, 12, "12", "", None, M, "abc", 24.5, -2, "0"]
AMOUNTS = [0, 100, "250.5", "", None, M, "abc", 1234567.555, 99999.995, 0.1, 0.2, "1e3"]


def amount_details():
    if random.random() < 0.3:
        return random.choice([M, {}, None])
    a = {}
    pick(a, "totalClaimedAmount", AMOUNTS)
    pick(a, "claimedAmount", AMOUNTS)
    pick(a, "billedAmount", AMOUNTS)
    return a


def form_leg(i):
    leg = {"id": f"leg-{i}"}
    pick(leg, "departureDate", LEG_DATES)
    pick(leg, "departureTime", FORM_TIMES)
    pick(leg, "from", FORM_PLACES)
    pick(leg, "arrivalDate", LEG_DATES)
    pick(leg, "arrivalTime", FORM_TIMES)
    pick(leg, "to", FORM_PLACES)
    pick(leg, "modeOfTravel", FORM_MODES)
    pick(leg, "personalVehicleType", FORM_VEHICLES)
    pick(leg, "distance", FORM_DIST)
    leg["includeTravelCosts"] = random.random() < 0.5
    ad = amount_details()
    if ad != M:
        leg["amountDetails"] = ad
    return leg


def form_tour(i, complete=False):
    if complete:
        # a tour as the plan makes it, maybe with gaps
        d = {
            "startDate": "2026-10-04T18:30:00.000Z",
            "startTime": "09:00",
            "endDate": "2026-10-04T18:30:00.000Z",
            "endTime": random.choice(["11:00", "09:00"]),
            "modeOfTravel": random.choice(FORM_MODES[:5] + [""]),
            "personalVehicleType": random.choice(["", "Two Wheeler"]),
            "distance": random.choice([0, 12]),
            "from": "Delhi",
            "to": random.choice(["Noida", ""]),
            "purpose": random.choice(["Audit", ""]),
            "includeTravelCosts": True,
            "amountDetails": {},
            "tourEnd": random.random() < 0.2,
            "subTotal": 0,
            "tourLegs": [form_leg(k) for k in range(random.randint(0, 2))],
        }
    else:
        d = {}
        pick(d, "startDate", FORM_DATES)
        pick(d, "startTime", FORM_TIMES)
        pick(d, "endDate", FORM_DATES)
        pick(d, "endTime", FORM_TIMES)
        pick(d, "modeOfTravel", FORM_MODES)
        pick(d, "personalVehicleType", FORM_VEHICLES)
        pick(d, "distance", FORM_DIST)
        pick(d, "from", FORM_PLACES)
        pick(d, "to", FORM_PLACES)
        pick(d, "purpose", ["Audit", "", "  ", None, M, " Site visit "])
        d["includeTravelCosts"] = random.random() < 0.5
        d["tourLegs"] = [form_leg(k) for k in range(random.choice([0, 0, 1, 2, 3]))]
        pick(d, "tourEnd", [True, False, False, False, M, 1, ""])
        d["subTotal"] = 0
        ad = amount_details()
        if ad != M:
            d["amountDetails"] = ad
        if random.random() < 0.02:
            d["from"] = 5
        if random.random() < 0.02:
            d["tourLegs"] = None
    t = {"id": random.choice([f"tour-{i}", f"tour-{i}", M, 7]), "tourDetails": d}
    if t["id"] == M:
        del t["id"]
    r = random.random()
    if r < 0.6:
        entries = []
        for _ in range(random.randint(0, 2)):
            e = {"id": 1}
            if random.random() < 0.9:
                inv = {}
                pick(inv, "claimedAmountWithTax", AMOUNTS)
                pick(inv, "claimedAmount", AMOUNTS)
                e["invoice"] = inv
            entries.append(e)
        t["accommodationDetails"] = {"entries": entries, "totalAmount": 0}
    elif r < 0.65:
        t["accommodationDetails"] = random.choice([None, {}, {"entries": None}])
    if random.random() < 0.6:
        t["daAllowanceDetails"] = {
            "entries": [
                {"id": k, "amount": random.choice(AMOUNTS), "daApplicable": random.choice([True, False, M, 1, None])}
                for k in range(random.randint(0, 3))
            ],
            "subTotal": 0,
            "approvingAuthority": "",
            "uploadedFile": None,
        }
    t["declarationAccepted"] = False
    t["isExpanded"] = False
    return t


def tours():
    n = random.choice([0, 1, 1, 2, 3, 4])
    complete = random.random() < 0.5
    return [form_tour(i, complete) for i in range(n)]


EDIT_FIELDS = [
    "purpose",
    "from",
    "to",
    "departureTime",
    "arrivalTime",
    "mode",
    "vehicleType",
    "distanceKm",
    "departureDate",
    "amount",
    5,
    None,
]
EDIT_VALUES = [
    "Audit work",
    "ab",
    "",
    "  ",
    "Noida",
    "Sector 62, Noida",
    "12",
    "1,200",
    "12 km",
    "0",
    "-3",
    "2001",
    "2000",
    24,
    24.5,
    -3,
    "9.30 pm",
    "noon",
    "25:00",
    "Rail",
    "train",
    "own two wheeler",
    "Two Wheeler",
    "four wheeler",
    "scooty",
    None,
    True,
    "x" * 600,
    "😀" * 300,
    "a1",
    "बड़ौदा",
    "1e3",
    "Infinity",
    "0x10",
]


def edit():
    e = {}
    pick(e, "tour", [1, 2, 3, 1, 2, "2", 0, -1, 1.5, None, M, "x", 9, 2.0])
    pick(e, "leg", [0, 0, 1, 2, 3, -1, 0.5, "1", None, M, 1.0])
    pick(e, "field", EDIT_FIELDS)
    pick(e, "value", EDIT_VALUES + [M])
    return e


ANSWERS = [
    "12 km",
    "about 24.5km",
    "1,200",
    "0",
    "a lot",
    "99999",
    "2001 km",
    "2000",
    "Gurugram",
    "a1",
    "बड़ौदा",
    "x" * 81,
    "  Noida  ",
    "9.30 pm",
    "25:00",
    "noon",
    "by train",
    "personal vehicle two wheeler",
    "own car",
    "helicopter",
    "bike",
    "4 wheel",
    "ok",
    "Audit of stations",
    "😀" * 300,
    "ab",
    "Delhi, Haryana",
    "",
]
GAP_KINDS = ["purpose", "from", "to", "depTime", "arrTime", "mode", "vehicle", "distance", "junk"]

cases = []


def add(fn, *args, now=None):
    c = {"fn": fn, "args": list(args)}
    if now is not None:
        c["now"] = now
    cases.append(c)


for _ in range(N):
    add("mapWithinTravelMode", random.choice(MODES_TEXT + [5]))
    add("mapVehicleType", random.choice(VEHICLES), random.choice(MODES_TEXT))
    add("isoOf", random.choice(FORM_DATES + LEG_DATES + [True, 0, -1, 8.64e15 + 1, "2026-10-05\n", {"a": 1}]))
for _ in range(N * 3):
    add("mapTourPlanToTours", ocr_plan(), now=random.choice(NOWS))
for _ in range(N):
    ts = tours()
    add("journeysOf", ts[0] if ts else form_tour(0), random.choice([1, 2, "3", None]))
    t = form_tour(1, True)
    add(
        "setJourneyField",
        t,
        random.choice([0, 1, 2, "1", 0.5, -1]),
        random.choice(["from", "to", "depTime", "arrTime", "mode", "vehicle", "distance", "junk"]),
        random.choice(["Noida", 12, "12", "x", None, True]),
    )
    add("hasTourData", tours())
    add("tourDiscrepancies", tours())
    add("tourReadyToEnd", tours())
    add("findGaps", tours())
    add("answerFound", tours(), random.randint(0, 4), random.choice(ANSWERS))
    ts = tours()
    gap = {
        "tourId": random.choice(["tour-0", "tour-1", "tour-9", 7, M]),
        "tourNo": 1,
        "leg": random.choice([0, 1, 2, "1", 0.5]),
        "kind": random.choice(GAP_KINDS),
        "question": "?",
    }
    add("answerGap", ts, {k: v for k, v in gap.items() if v != M}, random.choice(ANSWERS))
    add("applyWithinEdits", tours(), [edit() for _ in range(random.randint(0, 5))])
    add(
        "readWithinEdits",
        [
            random.choice([edit(), None, "x", 5, [1], {"tour": None, "field": "a", "value": 0}])
            for _ in range(random.randint(0, 4))
        ],
    )
    add("tourSummaryRows", tours())
    add("claimTotals", tours())
    add("totalsText", tours())
    add(
        "progressText",
        tours(),
        {"selfApprove": random.choice([True, False]), "approverChosen": random.choice([True, False, ""])},
    )
    add("snapshot", tours())
for _ in range(N * 2):
    add(
        "planFlow",
        ocr_plan(),
        random.sample(ANSWERS, 4),
        [edit() for _ in range(random.randint(0, 6))],
        {"selfApprove": random.random() < 0.3, "approverChosen": random.random() < 0.5},
        now=NOWS[0],
    )
add("blankTour")
add("constants")

json.dump(cases, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
print(len(cases), "cases")
