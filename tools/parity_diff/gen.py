import json, random
random.seed(11)
MISSING = "__missing__"
places = ["Noida", "Pune", "", " ", "select", None, MISSING, "New Delhi (NDLS)", "Mumbai", "pune ", "Goa"]
dates = ["2026-10-05", "2026-10-08", "2026-10-05T00:00:00", "05/10/2026", "", "garbage", "2026-02-30", None, "5 Oct 2026",
         "2026-12-01", "2026-10-11", "2026-10-12", "2026-09-05", MISSING, "Select"]
times = ["10:30", "09:00:00", "", "9.05 pm", None, "noon", "25:00", "18:00", MISSING, "7:5"]
modes = ["rail", "air", "select", "", "taxi", None, "public-transport", MISSING, "toString"]
amounts = ["0", "1200", "", "abc", 0, 5000, None, "12.5", MISSING, "1e3", " 450 ", 1234567.555, -3, "0x10"]
files = [[], [{"name": "a.pdf", "size": 1, "type": "application/pdf"}], [{"name": "s.pdf", "size": 2, "type": "", "fileId": "f-1"}],
         [{"name": "e.pdf", "size": 3, "type": "x", "fileId": ""}]]
types = ["hotel", "own_arrangement", "company_free", "company_paid", None, MISSING]
cnt = [0]
def pick(d, k, pool):
    v = random.choice(pool)
    if v != MISSING:
        d[k] = v
def uid(p):
    cnt[0] += 1
    return f"{p}-{cnt[0]}"
def trow():
    r = {"id": uid("t")}
    pick(r, "source", places); pick(r, "destination", places)
    pick(r, "DateOfTravelSource", dates); pick(r, "TimeOfTravelSource", times)
    pick(r, "DateOfTravelDestination", dates); pick(r, "TimeOfTravelDestiNation", times)
    pick(r, "ModeOfTravel", modes); pick(r, "claimedAmount", amounts)
    r["uploadedFiles"] = random.choice(files)
    r["billNotRequired"] = random.random() < 0.2
    return r
def arow():
    r = {"id": uid("a")}
    pick(r, "location", places); pick(r, "checkInDate", dates); pick(r, "checkInTime", times)
    pick(r, "checkOutDate", dates); pick(r, "checkOutTime", times); pick(r, "selectedType", types)
    pick(r, "hotelName", places); pick(r, "invoiceNo", ["", "INV-1", None, MISSING]); pick(r, "classOfCity", ["Select", "X", "", MISSING])
    for k in ("billed", "claimed", "ClaimedIGSTAmount", "ClaimedCGSTAmount", "ClaimedSGSTAmount"):
        pick(r, k, amounts)
    r["billFiles"] = random.choice(files)
    return r
def lrow():
    r = {"id": uid("l")}
    pick(r, "startDate", dates); pick(r, "endDate", dates); pick(r, "daDeductable", ["Yes", "No", MISSING])
    return r
def state():
    return {"transportationRows": [trow() for _ in range(random.randint(0 if random.random() < 0.05 else 1, 4))],
            "accommodationRows": [arow() for _ in range(random.randint(1, 3))],
            "leaveRows": [lrow() for _ in range(random.randint(1, 2))],
            "includeAccommodation": random.random() < 0.6, "includeLeave": random.random() < 0.5}
sections = ["transport", "transport", "accommodation", "leave", "nonsense"]
rowsv = [0, 1, 2, 3, 4, 5, 6, 9, "2", None, "x", 1.5, -1, True, MISSING]
fields = ["from", "to", "departureDate", "arrivalDate", "departureTime", "arrivalTime", "mode", "distanceKm", "location",
          "hotelName", "invoiceNo", "checkInDate", "checkOutDate", "checkInTime", "checkOutTime", "type", "startDate",
          "endDate", "daDeductable", "remove", "unknownField", 5]
values = dates + times + places + modes + ["yes", "no", "maybe", "-5", "120 km", "far", "own", "free company",
          "company paid", "hotel", "", "   ", 5, True, "TRUE", "y", "1e3", "Infinity", "Sept 5 2026", "5th Oct 2026", "10 AM", "1030", {"a": 1}, [1, 2]]
def edit():
    e = {}
    pick(e, "section", sections + [MISSING])
    pick(e, "row", rowsv); pick(e, "field", fields); pick(e, "value", values)
    return e
def rules():
    r = {"allowAir": random.random() < 0.5}
    if random.random() < 0.7: r["minDate"] = random.choice(["2026-10-05", "2026-10-01"])
    if random.random() < 0.7: r["maxDate"] = random.choice(["2026-10-11", "2026-10-20"])
    if random.random() < 0.3: r["lockedJourneys"] = random.choice([[1], [2], [1, 2]])
    return r
def strip(o):
    if isinstance(o, dict): return {k: strip(v) for k, v in o.items() if v != MISSING}
    if isinstance(o, list): return [strip(v) for v in o]
    return o
cases = []
for _ in range(1500):
    s = state()
    eds = [edit() for _ in range(random.randint(1, 6))]
    cases.append({"fn": "applyEdits", "args": [s, eds] + ([rules()] if random.random() < 0.8 else [])})
for _ in range(400):
    cases.append({"fn": "readClaimEdits", "args": [[edit() for _ in range(5)] + [None, "x", [1]]]})
for fn in ["findMissingFields", "findAmountIssues", "findFormOnlyIssues", "describeMissing", "summarizeJourneys",
           "summarizeStays", "summarizeLeave", "journeysInDateOrder", "staysInDateOrder", "journeyOverlaps", "tripEnds",
           "keptFileIds", "keys"]:
    for _ in range(300):
        cases.append({"fn": fn, "args": [state()]})
for _ in range(300):
    tour = random.choice([None, {"tourId": 4521, "source": "Noida", "destination": "Mumbai", "startDate": "2026-10-05"}, {"tourId": "7"}])
    cases.append({"fn": "snapshot", "args": [state()] + ([tour] if tour is not None or random.random() < 0.5 else [])})
for _ in range(500):
    s = state()
    m = {"transportationRows": [trow() for _ in range(random.randint(0, 3))], "accommodationRows": [arow() for _ in range(random.randint(0, 2))]}
    if random.random() < 0.3 and s["transportationRows"]:
        m["transportationRows"].append(dict(random.choice(s["transportationRows"]), id=uid("dup")))
    cases.append({"fn": "mergeOcrRows", "args": [s, m]})
for _ in range(300):
    plans = random.choice([[], [{"date": random.choice(dates), "travelMode": random.choice(["Air", "rail", None, MISSING])} for _ in range(3)]])
    cd = {}
    pick(cd, "tentativeTourPlans", [plans, MISSING, None])
    pick(cd, "tentativeTourPlanList", [plans, MISSING])
    pick(cd, "trDetails", [{"tentativeStartDate": random.choice(dates)}, {}, MISSING, None, "str"])
    pick(cd, "employeeDetails", [{"tentativeStartDate": random.choice(dates)}, MISSING])
    cases.append({"fn": "tourRulesFrom", "args": [cd]})
json.dump(strip(cases), open("cases.json", "w", encoding="utf-8"), ensure_ascii=False)
print(len(cases))
