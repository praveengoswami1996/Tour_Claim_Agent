"""Random inputs for the phase 1c parity check (tourReview, savedClaim, keptReports).
Usage: python gen1c.py cases1c.json [scale] [seed]"""

import json
import random
import sys

random.seed(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
SCALE = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
M = "__missing__"

CITIES = ["Noida", "Mumbai", "Pune", "Delhi", "Goa", "Agra", "New Delhi (NDLS)", "Noida, Uttar Pradesh", "Mumbai CST",
          "Pune (PNQ)", "AMBALA CANT JN"]
LOCAL_SPOTS = ["Airport", "Hotel", "Office", "Station", "Site", "Market"]
ODD_PLACES = ["", " ", "select", None, M, "Total Net Amount", "₹1,240.00", "GSTIN 07AAACD1234F1Z5", "12/10/2026",
              "PNR 1234567890", "x", "a@b.com", "NA", "Ny", "Café Delhi", "toString", "Hazrat Nizamuddin (NZM)",
              "Mumbai\nCST", "Pune, Maharashtra, India"]
ODD_DATES = ["2026-10-06T00:00:00", "06/10/2026", "", "garbage", "2026-02-30", None, "5 Oct 2026", M, "Select",
             "0099-01-01", "18 Jan 2026", "2026-09-30", "2026-10-20"]
TIMES = ["10:30", "09:00:00", "", "9.05 pm", None, "18:00", M, "7:5", "23:59", "00:00", "17:15", "19:00", "12:00",
         "8:00", "24:10"]
MAIN = ["air", "rail"]
LOCAL = ["public-transport", "personal-vehicle", "official-entitled", "official-beyond", "taxi", "select", "", None, M]
cnt = [0]


def uid(p):
    cnt[0] += 1
    return f"{p}-{cnt[0]}"


def day(base_day, spread=6):
    d = base_day + random.randint(-1, spread)
    return f"2026-10-{d:02d}" if 1 <= d <= 31 else f"2026-11-{d - 31:02d}"


def hhmm():
    return f"{random.randint(0, 23):02d}:{random.choice([0, 5, 15, 30, 45, 59]):02d}"


def maybe(v, pool, p=0.08):
    return random.choice(pool) if random.random() < p else v


def files(p_empty=0.25):
    if random.random() < p_empty:
        return random.choice([[]] * 30 + [None, M])
    return [{"name": random.choice(["t.pdf", "c.jpg"])}]


def put(d, k, v):
    if v != M:
        d[k] = v


def journey(src, dst, d1, t1, d2, t2, mode):
    r = {"id": uid("t")}
    put(r, "source", maybe(src, ODD_PLACES))
    put(r, "destination", maybe(dst, ODD_PLACES))
    put(r, "DateOfTravelSource", maybe(d1, ODD_DATES))
    put(r, "TimeOfTravelSource", maybe(t1, TIMES, 0.12))
    put(r, "DateOfTravelDestination", maybe(d2, ODD_DATES))
    put(r, "TimeOfTravelDestiNation", maybe(t2, TIMES, 0.12))
    put(r, "ModeOfTravel", maybe(mode, MAIN + LOCAL, 0.06))
    put(r, "uploadedFiles", files())
    put(r, "claimedAmount", random.choice(["4500", "0", "", "abc", 300, M]))
    r["billNotRequired"] = random.random() < 0.1
    return r


def tour_scenario():
    """A plausible tour (home -> cities -> home) with cabs around it, then disturbed."""
    home = random.choice(CITIES[:6])
    stops = random.sample([c for c in CITIES if c != home], random.randint(1, 3))
    route = [home, *stops] + ([home] if random.random() < 0.75 else [])
    base = random.randint(3, 8)
    d = base
    rows, stays = [], []
    for a, b in zip(route, route[1:], strict=False):
        mode = random.choice(MAIN) if random.random() < 0.85 else random.choice(LOCAL[:3])
        t1 = hhmm()
        overnight = random.random() < 0.2
        d2 = d + (1 if overnight else 0)
        t2 = hhmm()
        if random.random() < 0.6:  # cab to the station
            rows.append(journey(f"{a} {random.choice(LOCAL_SPOTS)}" if random.random() < 0.5 else a,
                                f"{a} Airport", day(d, 0), hhmm(), day(d, 0), hhmm(), random.choice(LOCAL)))
        rows.append(journey(a, b, day(d, 0), t1, day(d2, 0), t2, mode))
        if random.random() < 0.6:  # cab from the station
            rows.append(journey(f"{b} Airport", f"{b} {random.choice(LOCAL_SPOTS)}", day(d2, 0), hhmm(), day(d2, 0),
                                hhmm(), random.choice(LOCAL)))
        stay_len = random.randint(0, 3)
        if stay_len and b != home and random.random() < 0.8:
            st = {"id": uid("a")}
            put(st, "location", maybe(b, ODD_PLACES))
            put(st, "checkInDate", maybe(day(d2, 0), ODD_DATES))
            put(st, "checkInTime", maybe("14:00", TIMES))
            put(st, "checkOutDate", maybe(day(d2 + stay_len, 0), ODD_DATES))
            put(st, "checkOutTime", maybe("11:00", TIMES))
            put(st, "selectedType", random.choice(["hotel", "hotel", "own_arrangement", "company_free",
                                                   "company_paid", None, M]))
            put(st, "hotelName", random.choice(["H", "", M]))
            put(st, "invoiceNo", random.choice(["I", "", M]))
            put(st, "billFiles", files())
            stays.append(st)
        if random.random() < 0.4 and b != home:  # getting around
            rows.append(journey(f"{b} Hotel", f"{b} Office", day(d2 + 1, 0), hhmm(), day(d2 + 1, 0), hhmm(),
                                random.choice(LOCAL)))
        d = d2 + stay_len
    random.shuffle(rows) if random.random() < 0.3 else None
    if random.random() < 0.15:
        rows.append({"id": uid("t"), "source": "", "destination": "", "DateOfTravelSource": "",
                     "DateOfTravelDestination": "", "uploadedFiles": []})
    leaves = []
    if random.random() < 0.3:
        lv = {"id": uid("l"), "daDeductable": "Yes", "amount": "0"}
        put(lv, "startDate", maybe(day(base + 1, 3), ODD_DATES))
        put(lv, "endDate", random.choice([day(base + 2, 3), "", M, None]))
        leaves.append(lv)
    blank_t = {"id": "t0", "source": "", "destination": "", "DateOfTravelSource": "", "DateOfTravelDestination": "",
               "uploadedFiles": []}
    blank_a = {"id": "a0", "location": "", "checkInDate": "", "checkOutDate": "", "billFiles": []}
    blank_l = {"id": "l0", "startDate": "", "endDate": "", "daDeductable": "Yes", "amount": "0"}
    state = {"transportationRows": rows or [blank_t], "accommodationRows": stays or [blank_a],
             "leaveRows": leaves or [blank_l], "includeAccommodation": bool(stays) and random.random() < 0.9,
             "includeLeave": bool(leaves)}
    tour = {"tourId": random.choice([4521, 7, "12"]), "statusId": 2}
    put(tour, "source", maybe(home, ["", None, M, " Noida ", "Delhi", 5], 0.15))
    put(tour, "destination", random.choice([stops[0], M]))
    put(tour, "tourStartDate", maybe(day(base, 0), ODD_DATES, 0.1))
    if random.random() < 0.7:
        put(tour, "tantetiveEndDate", maybe(day(d, 0), ODD_DATES, 0.1))
    put(tour, "totalDays", random.choice([1, 3, 5, None, M, "x"]))
    return state, tour


def rules():
    r = {"allowAir": random.random() < 0.5}
    if random.random() < 0.7:
        r["minDate"] = random.choice(["2026-10-02", "2026-10-05", "2026-10-07"])
    if random.random() < 0.7:
        r["maxDate"] = random.choice(["2026-10-09", "2026-10-12", "2026-10-20"])
    return r


FORM_PROBLEMS = [
    {"kind": "overEntitlement", "section": "accommodation", "row": 1, "message": "Claimed exceeds your limit."},
    {"kind": "field", "section": "transport", "row": 1, "message": "Departure Time is missing for Main Tour"},
    {"kind": "noApprover", "section": "general", "row": 0, "message": "Select an approver."},
    {"kind": "cityClass", "section": "accommodation", "row": 2, "message": "Choose the city class."},
]


def review_input():
    state, tour = tour_scenario()
    inp = {"state": state, "tour": tour if random.random() < 0.9 else None}
    if random.random() < 0.8:
        inp["rules"] = rules()
    if random.random() < 0.3:
        inp["formProblems"] = random.sample(FORM_PROBLEMS, random.randint(0, 3))
    if random.random() < 0.8:
        inp["leaveAnswered"] = random.random() < 0.5
    return inp


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if v != M}
    if isinstance(o, list):
        return [strip(v) for v in o]
    return o


# ─── saved drafts ──────────────────────────────────────────────────────────────

AMOUNTS = [500, "500", 0, None, "abc", 12.5, -3, "1e3", M, True, [], [7], "  42 ", "0x10"]


def pick_key(name):
    return random.choice([name, name, name.lower(), name.upper(), name[0].lower() + name[1:]])


def saved_journey(dt):
    item = {}
    for k, v in [("DateOfTravelSource", random.choice([f"{dt}T00:00:00", dt, "16/10/2026", "garbage", None, M])),
                 ("TimeOfTravelSource", random.choice(["09:00:00", "9.05 pm", "", None, M, "0930"])),
                 ("Source", random.choice(CITIES + [None, M, 5])),
                 ("ModeOfTravel", random.choice(["rail", "air", None, M, "public-transport"])),
                 ("departureModeofTravel", random.choice(["air", M, M])),
                 ("DateOfTravelDestination", random.choice([f"{dt}T00:00:00", dt, None, M])),
                 ("TimeOfTravelDestiNation", random.choice(["18:00:00", "", M])),
                 ("Destination", random.choice(CITIES + [None, M])),
                 ("DistanceINKm", random.choice(AMOUNTS)), ("BilledAmount", random.choice(AMOUNTS)),
                 ("ClaimedAmount", random.choice(AMOUNTS)), ("SGSTAmount", random.choice(AMOUNTS)),
                 ("IGSTAmount", random.choice(AMOUNTS)), ("BillCGSTAmount", random.choice(AMOUNTS)),
                 ("TaxStructure", random.choice(["IGST", "NA", "SGST_CGST", None, M])),
                 ("InvoiceNumber", random.choice(["INV-1", "", None, M, 77])),
                 ("InvoiceDate", random.choice(["2026-10-05", "2026-10-05T00:00:00", "2026-10-05T23:30:00Z", "",
                                                None, M, "garbage", 1790000000000, "5 Oct 2026", True, 0])),
                 ("Remark", random.choice(["r", "", None, M])), ("isPaidUsingCard", random.choice([True, 0, "x", M])),
                 ("Id", random.choice([0, 3, M])), ("TourNumber", random.choice([1, M]))]:
        if v != M:
            item[pick_key(k)] = v
    return item


def saved_stay(dt):
    item = {}
    for k, v in [("DateOfCheckIn", random.choice([f"{dt}T00:00:00", dt, None, M])),
                 ("TimeOfCheckIn", random.choice(["14:00:00", "", M])),
                 ("DateOfCheckOut", random.choice([f"{dt}T00:00:00", "2026-10-09", None, M])),
                 ("TimeOfCheckOut", random.choice(["11:00:00", M])),
                 ("Location", random.choice(CITIES + [None, M])), ("ClassOfCity", random.choice(["X", None, M])),
                 ("HotelName", random.choice(["H", "", M])), ("InvoiceNumber", random.choice(["I", M])),
                 ("BilledAmount", random.choice(AMOUNTS)), ("ClaimedAmount", random.choice(AMOUNTS)),
                 ("TaxStructure", random.choice(["IGST", "NA", M])),
                 ("IsOwnArrangement", random.choice([True, False, "Yes", 0, M])),
                 ("IsFreeAccomodation", random.choice([True, False, M])),
                 ("isCompanyLodginPaid", random.choice([True, False, M])),
                 ("isPaidUsingCard", random.choice([True, M])), ("InvoiceDate", random.choice(["2026-10-06", M]))]:
        if v != M:
            item[pick_key(k)] = v
    return item


def saved_leave():
    item = {}
    for k, v in [("LeaveStartDate", random.choice(["2026-10-06T00:00:00", "", None, M])),
                 ("LeaveEndDate", random.choice(["2026-10-07T00:00:00", "", M])),
                 ("DADeductable", random.choice([True, False, 1, M])), ("Amount", random.choice(AMOUNTS))]:
        if v != M:
            item[pick_key(k)] = v
    return item


def refs(n):
    out = []
    for _ in range(random.randint(0, 4)):
        f = {}
        for k, v in [("fileId", random.choice(["f-1", "f-2", 9, None, M])),
                     ("originalFileName", random.choice(["a.pdf", None, M])),
                     ("displayName", random.choice(["d.pdf", M])),
                     ("fileSize", random.choice([100, "7", None, M, "x"])),
                     ("rowIndex", random.choice([*range(max(n, 1)), "1", None, M, -0.0, 1.5, "x"])),
                     ("tempFilePath", random.choice(["temp/a.pdf", "", None, M]))]:
            if v != M:
                f[pick_key(k)] = v
        out.append(f)
    return out


def as_list(items):
    r = random.random()
    if r < 0.5:
        return json.dumps(items)
    if r < 0.8:
        return items
    return random.choice(["{not json", "NaN", "[1, 2", "", None, 5, '{"a": 1}', "[]", "  [] "])


def draft():
    days = [f"2026-10-{d:02d}" for d in range(5, 11)]
    nj, ns = random.randint(0, 4), random.randint(0, 2)
    data = {}
    put(data, random.choice(["TransportaionDetailDtos", "TransportationDetailDtos", "transportaionDetailDtos"]),
        as_list([saved_journey(random.choice(days)) for _ in range(nj)] + ([None, 7] if random.random() < 0.1 else [])))
    put(data, random.choice(["AccomodationDetailDto", "AccommodationDetailDto"]),
        as_list([saved_stay(random.choice(days)) for _ in range(ns)]))
    if random.random() < 0.5:
        put(data, "LeaveAvailedDto", as_list([saved_leave() for _ in range(random.randint(0, 2))]))
    put(data, "transportationFileReferences", as_list(refs(nj)))
    put(data, "accomodationFileReferences", as_list(refs(ns)))
    r = random.random()
    if r < 0.6:
        return {"draftId": 1, random.choice(["draftData", "DraftData"]): data}
    if r < 0.9:
        return data
    return random.choice([None, "x", 5, [], {"draftData": None}, {"draftData": "[]"}])


def bill_rows():
    state, _ = tour_scenario()
    return {"transportationRows": [r for r in state["transportationRows"] if random.random() < 0.7],
            "accommodationRows": [r for r in state["accommodationRows"] if random.random() < 0.7]}


cases = []


def add(fn, *args):
    cases.append({"fn": fn, "args": list(args)})


def n(k):
    return int(k * SCALE)


for _ in range(n(4000)):
    add("reviewClaim", review_input())
for _ in range(n(1500)):
    add("short", review_input(), random.sample(["askLeave", "amountMissing", "formOnly", "oddPlace", "noReturn"],
                                               random.randint(0, 3)))
for _ in range(n(800)):
    add("loop", review_input(), [random.choice(["confirm", "later"]) for _ in range(random.randint(0, 40))])
for _ in range(n(800)):
    add("notes", review_input(), random.random() < 0.5, random.choice(["Claim.", "", "x\ny"]))
for _ in range(n(1500)):
    add("claimStateFromDraft", draft())
for _ in range(n(1200)):
    add("savedFlow", draft(), bill_rows(), tour_scenario()[1], random.choice(["Noida → Pune", "T"]))
for _ in range(n(1200)):
    st, t = tour_scenario()
    o = {}
    put(o, "lastSaved", random.choice(["2026-10-27T10:00:00", "", None, "garbage", "27/10/2026", M]))
    put(o, "savedInChat", random.choice([True, False, 1, M]))
    put(o, "tourDates", random.choice(["05 Oct – 09 Oct", "", M]))
    add("savedDraftSummary", t, st, o, random.randint(0, 3))
for _ in range(n(600)):
    add("savedDraftSummaryFromDraft", tour_scenario()[1], draft())
for _ in range(n(600)):
    ops = []
    for _ in range(random.randint(1, 8)):
        r = random.random()
        now = random.choice([1_000_000, 1790000000000, 1790000000000 + 14 * 86400000, 1_000_000 + 14 * 86400000])
        if r < 0.4:
            rep = {"scope": random.choice(["page", "tour-4600"]), "text": random.choice(["A", "B …"]),
                   "tourIds": random.sample([4600, 7], random.randint(0, 2))}
            if random.random() < 0.5:
                rep["tone"] = random.choice(["error", "success"])
            ops.append(["keep", random.choice([1001, None, "x"]), rep, now])
        elif r < 0.55:
            ops.append(["drop", random.choice([1001, None]), random.choice([[], ["1000000-4fzzzx"],
                                                                             ["1790000000000-4fzzzx"]]), now])
        elif r < 0.75:
            ops.append(["for", random.choice([1001, None]), random.choice(["page", "tour-4600"]), now])
        elif r < 0.9:
            ops.append(["read", random.choice([1001, None, "x"]), now])
        else:
            ops.append(["set", random.choice([1001, None]), random.choice([
                "{not json", "[]", "null", "5", '[{"text":"t","scope":"page","at":1000000}]',
                '[{"text":"t","scope":"page","at":"1e6","id":"q"}, null, 3, {"scope":"page"}]',
                '{"text":"t"}', '[{"text":"t","scope":"page","at":1.5e12}]', ""])])
    add("kept", ops)
json.dump(strip(cases), open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
print(len(cases))
