"""Random inputs for the phase 1b parity check (tourMatcher, claimProgress, billSummary, loadAssistantTour,
claimLink, claimPayloadBuilder). Usage: python gen1b.py cases1b.json [scale] [seed]"""

import json
import random
import sys

random.seed(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
SCALE = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
M = "__missing__"
places = ["Noida", "Pune", "", " ", "select", None, M, "New Delhi (NDLS)", "New Delhi, Delhi", "Delhi", "Mumbai",
          "pune ", "Goa", "Jaipur, Rajasthan", "Jaipur (JAI)", "Pune (PNQ)", "AMBALA CANT JN", "Ny", "Café Delhi",
          "Noida Corporate Office", "toString"]
days = [f"2026-01-{d:02d}" for d in range(13, 23)] + [f"2026-09-{d:02d}" for d in range(8, 15)] + \
       ["2026-10-05", "2026-10-08", "2026-12-31", "2027-01-01", "2025-11-06"]
odd_dates = ["2026-01-16T00:00:00", "16/01/2026", "", "garbage", "2026-02-30", None, "5 Oct 2026", M, "Select",
             "0099-01-01", "18 Jan 2026"]


def date():
    return random.choice(days) if random.random() < 0.75 else random.choice(odd_dates)


times = ["10:30", "09:00:00", "", "9.05 pm", None, "18:00", M, "7:5", "23:59", "00:00", "17:15", "19:00", "12:00"]
modes = ["rail", "air", "select", "", None, "public-transport", "personal-vehicle", "official-beyond", M, "toString",
         "taxi"]
amounts = ["0", "1200", "", "abc", 0, 5000, None, "12.5", M, "1e3", " 450 ", 1234567.555, -3, "0x10", "-0", 7.25]
tax = ["IGST", "SGST_CGST", "NA", None, M, "x"]
cnt = [0]


def pick(d, k, pool):
    v = random.choice(pool)
    if v != M:
        d[k] = v


def uid(p):
    cnt[0] += 1
    return f"{p}-{cnt[0]}"


def files():
    out = []
    for _ in range(random.choice([0, 0, 1, 1, 2, 3])):
        f = {"name": random.choice(["a.pdf", "b.jpg", "t (2).pdf", "h.pdf", "x.png"]), "size": random.choice([1, 20, 0])}
        r = random.random()
        if r < 0.4:
            f["documentId"] = random.choice([11, 12, "d-3"])
        elif r < 0.6:
            f["fileId"] = random.choice(["f-1", "f-2", ""])
            f["isRetained"] = random.choice([True, False])
        elif r < 0.7:
            f["isRetained"] = True
            f["documentId"] = 5
        elif r < 0.8:
            f["fileId"] = "f-9"
            f["documentId"] = 6
        out.append(f)
    return out


def trow():
    r = {"id": uid("t")}
    pick(r, "source", places)
    pick(r, "destination", places)
    v = date()
    if v != M:
        r["DateOfTravelSource"] = v
    pick(r, "TimeOfTravelSource", times)
    if random.random() < 0.5 and "DateOfTravelSource" in r:
        r["DateOfTravelDestination"] = r["DateOfTravelSource"]
    else:
        v = date()
        if v != M:
            r["DateOfTravelDestination"] = v
    pick(r, "TimeOfTravelDestiNation", times)
    pick(r, "ModeOfTravel", modes)
    for k in ("claimedAmount", "travelCost", "distanceKm", "sgst", "cgst", "igst", "BillSGSTAmount", "BillCGSTAmount",
              "BillIGSTAmount"):
        pick(r, k, amounts)
    pick(r, "taxStructure", tax)
    pick(r, "invoiceNo", ["", "INV-1", None, M])
    pick(r, "invoiceDate", ["2026-01-16", "2026-01-16T00:00:00", "", None, M])
    pick(r, "remarks", ["", "late", None, M])
    r["uploadedFiles"] = files()
    r["billNotRequired"] = random.random() < 0.2
    return r


def arow():
    r = {"id": uid("a")}
    pick(r, "location", places)
    for k in ("checkInDate", "checkOutDate"):
        v = date()
        if v != M:
            r[k] = v
    pick(r, "checkInTime", times)
    pick(r, "checkOutTime", times)
    pick(r, "selectedType", ["hotel", "own_arrangement", "company_free", "company_paid", None, M])
    pick(r, "ownArrangement", ["Yes", "No", M])
    pick(r, "freeProvidedByCompany", ["Yes", "No", M])
    pick(r, "hotelName", places)
    pick(r, "invoiceNo", ["", "INV-1", None, M])
    pick(r, "classOfCity", ["Select", "X", "", M])
    pick(r, "invoiceDate", ["2026-01-16", "", None, M])
    pick(r, "taxStructure", tax)
    pick(r, "taxType", tax)
    for k in ("billed", "claimed", "ClaimedIGSTAmount", "ClaimedCGSTAmount", "ClaimedSGSTAmount", "BillSGSTAmount",
              "BillCGSTAmount", "BillIGSTAmount"):
        pick(r, k, amounts)
    pick(r, "remarks", ["", "x", M])
    r["billFiles"] = files()
    return r


def lrow():
    r = {"id": uid("l")}
    for k in ("startDate", "endDate"):
        v = date()
        if v != M:
            r[k] = v
    pick(r, "daDeductable", ["Yes", "No", "YES", None, M])
    pick(r, "amount", amounts)
    return r


def state():
    return {"transportationRows": [trow() for _ in range(random.randint(0 if random.random() < 0.05 else 1, 5))],
            "accommodationRows": [arow() for _ in range(random.randint(1, 3))],
            "leaveRows": [lrow() for _ in range(random.randint(1, 2))],
            "includeAccommodation": random.random() < 0.6, "includeLeave": random.random() < 0.5}


def tour():
    t = {}
    pick(t, "tourId", [1, 2, 3, 5, 12, 45, 4522, "12", 7.0])
    pick(t, "source", places)
    pick(t, "destination", places)
    v = date()
    if v != M:
        t["tourStartDate"] = v
    if random.random() < 0.6:
        v = date()
        if v != M:
            t["tantetiveEndDate"] = v
    pick(t, "totalDays", [1, 2, 3, 5, 0, None, "3", "abc", -2, 1.5, M, 200, [4]])
    pick(t, "statusId", [2, 9, None, M])
    return t


def rules():
    r = {"allowAir": random.random() < 0.5}
    if random.random() < 0.7:
        r["minDate"] = random.choice(["2026-01-14", "2026-09-09", "2026-10-05"])
    if random.random() < 0.7:
        r["maxDate"] = random.choice(["2026-01-18", "2026-09-13", "2026-10-11"])
    return r


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if v != M}
    if isinstance(o, list):
        return [strip(v) for v in o]
    return o


def n(k):
    return int(k * SCALE)


cases = []


def add(fn, *args):
    cases.append({"fn": fn, "args": list(args)})


for _ in range(n(400)):
    add("tourWindow", tour())
for _ in range(n(300)):
    add("norm", random.choice(places + ["  A b--C ", "ÉCOLE", "x y", "KK", "İstanbul"]))
    add("samePlace", random.choice(["delhi", "new delhi", "ny", "pune", ""]),
        random.choice(["delhi", "new delhi", "ny", "pun", "pune"]))
for _ in range(n(1500)):
    add("matchTours", state(), [tour() for _ in range(random.randint(0, 4))])
for _ in range(n(400)):
    add("findTourByText", random.choice(["tour 12", "#45", "pune", "Pune trip", "12", "4522", "go", "delhi", "jaipur",
                                         "x 1234 y", "Noida"]), [tour() for _ in range(random.randint(0, 4))])
for _ in range(n(1500)):
    s = state()
    ts = [tour() for _ in range(random.randint(0, 3))]
    picked = random.choice(ts) if ts and random.random() < 0.4 else None
    add("triageRows", {"transportationRows": s["transportationRows"], "accommodationRows": s["accommodationRows"]},
        ts, picked)
for _ in range(n(300)):
    add("eachDay", random.choice(days + ["garbage", "2026-1-5"]), random.choice(days + ["garbage", "NaN-NaN-NaN", ""]))
    add("cpDayText", random.choice(days + ["garbage", "2026-1-5", "0050-03-01"]))
    a, b = random.choice(days), random.choice(days)
    add("cpRangeText", a, random.choice([a, b]))
    add("groupDays", random.sample(days, random.randint(0, 8)))
for fn in ("coveredDays", "coveredText", "findRelationIssues", "findTimeOverlaps"):
    for _ in range(n(600)):
        add(fn, state())
for _ in range(n(600)):
    add("uncoveredDays", state(), random.choice([tour(), None]))
for _ in range(n(800)):
    add("findClaimIssues", state(), random.choice([rules(), None]))
for fn in ("buildProgress", "progressText"):
    for _ in range(n(800)):
        p = {"state": state(), "tour": random.choice([tour(), None]), "staysAnswered": random.random() < 0.5,
             "leaveAnswered": random.random() < 0.5, "doneUploading": random.random() < 0.5}
        if random.random() < 0.7:
            p["rules"] = rules()
        add(fn, p)
for _ in range(n(800)):
    add("stayBillProblem", arow(), random.choice([rules(), None, {"allowAir": True}]),
        [trow() for _ in range(random.randint(0, 3))])
fate_pool = [{"kind": "saved", "tour": "Delhi → Jaipur"}, {"kind": "alreadySaved", "tour": "A → B"},
             {"kind": "saveFailed", "tour": "T"}, {"kind": "problem", "reason": "r"}, {"kind": "unsure"},
             {"kind": "noTour"}, {"kind": "nothingFound"}, {"kind": "unreadable"}, {"kind": "duplicate"},
             {"kind": "rejected", "reason": "too big"}, None, {"kind": "weird"}, {}]
fnames = ["a.pdf", "b.jpg", "t (2).pdf", "h.pdf", "x.png"]


def read_row():
    if random.random() < 0.5:
        r = trow()
        return {k: r[k] for k in ("ModeOfTravel", "source", "destination", "DateOfTravelSource", "uploadedFiles")
                if k in r}
    r = arow()
    return {k: r[k] for k in ("location", "checkInDate", "checkOutDate", "billFiles") if k in r}


for _ in range(n(500)):
    add("describeRow", read_row())
    add("bsDayText", date() if random.random() < 0.9 else None)
    add("bsRangeText", random.choice(days + ["", None, "garbage"]), random.choice(days + ["", None]))
    add("findingsByFile", [read_row() for _ in range(random.randint(0, 5))])
    add("fileLine", random.choice(fnames), random.sample(["A", "B", "C", "D", "E"], random.randint(0, 5)),
        random.choice(fate_pool),
        {"tagSaved": random.random() < 0.5, **({"inClaim": random.random() < 0.5} if random.random() < 0.7 else {})})
    add("groupOf", random.choice(fate_pool), random.sample(["A", "B"], random.randint(0, 2)))
    fs_ = random.sample(fnames, random.randint(0, 5))
    add("uploadSortedText", fs_, [[f, random.choice(fate_pool[:-3])] for f in fs_ if random.random() < 0.8])
    fs_ = random.sample(fnames, random.randint(0, 5))
    s = {"header": random.choice(["Your 3 bills have been read:", "h"]), "files": fs_,
         "rows": [read_row() for _ in range(random.randint(0, 4))],
         "fates": [[f, random.choice(fate_pool[:-3])] for f in fs_ if random.random() < 0.8],
         "savedTours": random.sample(["A", "B", "C"], random.randint(0, 3))}
    if random.random() < 0.3:
        s["notes"] = ["note one", "", "note two"][: random.randint(0, 3)]
    if random.random() < 0.4:
        s["claim"] = {"journeys": random.choice([0, 1, 2]), "stays": random.choice([0, 1, 3]),
                      "saved": random.random() < 0.5}
    add("readSummaryText", s)
    add("claimFooter", {"journeys": random.choice([0, 1, 2, True, "1", None]), "stays": random.choice([0, 1, 3]),
                        "saved": random.choice([True, False, 0])})
    o = {}
    pick(o, "earlier", [True, False, M])
    pick(o, "name", ["Anil", "", None, M])
    add("readHeader", random.choice([0, 1, 2, 3, 1.0, True, "1"]), o)
    add("strongerFate", random.choice(fate_pool), random.choice(fate_pool[:-3]))
for _ in range(n(300)):
    t = {"tourId": random.choice([4522, 7, 1, "12 a"])}
    pick(t, "statusId", [3, None, M, 0, 2.5])
    pick(t, "totalDays", [6, None, M, 0, "x y"])
    add("claimLink", t, random.choice(["https://tour.example", ""]))
    add("stateFromClaimLink", random.choice([
        "", "?statusId=3", "?tourId=abc", "?tourId=-4", "?tourId=9", "?tourId=4522&isDraft=true&statusId=3&totalDays=6",
        "tourId=12&tourId=13", "?tourId=1e3&statusId=&totalDays=x", "?tourId=+7&isDraft=TRUE",
        "?tourId=%37&statusId=0x10", "?tourId=7&statusId=Infinity", "?tour%49d=5", "?tourId=0", "?tourId=1.5",
        "?a=1&&tourId=2&isDraft=true", "?tourId= 8 ", "?tourId=8&isDraft=tr%75e", "?tourId=%E2%82%AC"]))


def clean_input(d=0):
    r = random.random()
    if d > 2 or r < 0.3:
        return random.choice(["", None, 0, False, "x", 5, True, 1.5])
    if r < 0.6:
        return [clean_input(d + 1) for _ in range(random.randint(0, 3))]
    return {random.choice(["a", "b", "1", "0", "c", "10"]): clean_input(d + 1) for _ in range(random.randint(0, 4))}


for _ in range(n(400)):
    add("clean", clean_input())
DA_ROWS = [{"startDate": "2026-01-16", "startTime": "10:30:00", "endTime": "", "Hours": 5, "Minutes": 3,
            "DAPercentage": 70, "DAOfTheDay": "350", "applicable": True},
           {"id": 9, "startDate": "", "Hours": 12, "Minutes": 0, "applicable": False}]
FORM_DATA = [{}, {"recipientId": 5, "purposeOfTour": "Audit", "claimAmount": 0, "paidAmount": 12,
                  "documents": {"tickets": True}}, {"documents": {"hotel": True, "tickets": False}}]
for _ in range(n(1200)):
    s = state()
    p = {"transportationRows": s["transportationRows"], "accommodationRows": s["accommodationRows"],
         "daRowsState": random.choice([[], DA_ROWS]), "leaveRows": s["leaveRows"],
         "currentTab": random.choice(["Transportation", "DA"]), "formData": random.choice(FORM_DATA),
         "includeLeave": s["includeLeave"], "includeAccommodation": s["includeAccommodation"], "empId": 101,
         "empTourPlanId": 4522}
    pick(p, "draftId", [None, 77, M])
    pick(p, "retainedTransportationFileIds", [None, ["f-1"], [], M])
    pick(p, "retainedAccommodationFileIds", [None, ["f-2"], M])
    pick(p, "hasResumedDraft", [True, False, M])
    pick(p, "isPaidViaCreditCard", [True, False, M])
    pick(p, "creditCardPaidAmount", ["", "12.5", "abc", M])
    pick(p, "myEntitlement", [{"daAmount": 500}, None, M])
    if random.random() < 0.15:
        p["isClarificationMode"] = True
        p["clarificationData"] = {"transportationList": [{"transportationId": 31}, {}],
                                  "accommodationList": [{"accomodationId": 41}],
                                  "dailyAllowancList": [{"id": 51}, {"daDetailId": 52}],
                                  "leaveList": [{"leaveDetailId": 61}]}
    add("buildClaimPayload", p)
    add("collectNewFiles", state())
for _ in range(n(1000)):
    add("assistantSave", state(), 101, 4522, random.choice([None, 77]), random.random() < 0.5)
for _ in range(n(300)):
    plans = [{"date": random.choice(days + ["", None]), "travelMode": random.choice(["Air", "rail"])}
             for _ in range(random.randint(0, 3))]
    cd = {}
    pick(cd, "empTourDetails", [{"source": "Noida", "destination": "Pune", "purposeOfTour": "Audit",
                                 "tentativeStartDate": "2026-01-16"}, None, M])
    pick(cd, "employeeDetails", [{"source": "Delhi", "tentativeStartDate": "2026-09-10T00:00:00"}, M])
    pick(cd, "trDetails", [{"tentativeStartDate": random.choice(days)}, {}, M, None])
    pick(cd, "tentativeTourPlans", [plans, [], M, None])
    pick(cd, "tentativeTourPlanList", [plans, M])
    plan_body = random.choice([None, {"data": {"tentativeTourPlans": plans}}, {"data": {"tentativeTourPlans": []}},
                               {"data": None}, {}])
    add("fetchAssistantTour", 4522, {"statusCode": 200, "data": cd}, plan_body)
json.dump(strip(cases), open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
print(len(cases))
