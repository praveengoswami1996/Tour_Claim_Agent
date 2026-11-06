import json
import random

random.seed(5)
d_in = json.load(open("../fuzz/dates_in.json", encoding="utf-8"))
extra = ["2026-02-30", "31/02/2026", "2026-13-01", "00/00/2026", "garbage", "", "2026-10-05", "05/10/26", "5-10-2026",
         "05.10.2026", "0099-01-01", "2026-1-5", " 2026-10-05 ", "2026-10-05abc", "Sept 5 2026", "5th October 2026",
         "Oct 5, 2026", "1st Jan 2027", "22nd Feb 2026", "23rd March 2026", "12/31/2026", "31/12/2026", None, 5, 20261005,
         True, "2026", "Oct 5 0100 2026"]
times = ["9:5", "09:05:00", "9.05 PM", "10 AM", "1030", "10:30 hrs", "noon", "midnight", "24:00", "10:60", "abc", "", "930",
         "12 am", "12 pm", "12:30 a.m.", "1 p.m", "13 pm", "10:30hrs", "10:30 h.", "10:30 hours", "7.5", "10", "10:30:61",
         "NOON", " 10:30 ", "10 : 30", "0:0", "23:59", "10:30 pm", "10:30 pm hrs", None, 1030, 9.5]
cases = [{"fn": "toIsoDate", "args": [v]} for v in d_in + extra] + [{"fn": "toTime", "args": [v]} for v in times + d_in[:300]]
modes = ["Air", "flight", "Business class", "automobile", "chair car", "town bus", "Train", "office car", "teleport", "", None,
         "Official Vehicle Entitled", "beyond", "dedicated", "RAIL", "personal-vehicle", "cab"]
cases += [{"fn": "mapTravelMode", "args": [m]} for m in modes]
places = ["Noida", "New Delhi", "Mumbai CST", "Hazrat Nizamuddin (NZM)", "Total Net Amount", "₹1,240.00", "Rs 450", "1240.50",
          "05/10/2026", "2026-10-05", "10:30 AM", "8812345670", "PN 88123456", "care@irctc.co.in", "www.makemytrip.com", "N/A",
          "NA", "null", "-", "?", "...", "Select", "###", "X", "", "   ", None, "Café Coffee Day", "Seat 14B",
          "GSTIN 07AAACD1234F1Z5", "Hotel Amount", "a" * 81, "a" * 79 + "😀",
          "one two three four five six seven eight nine ten eleven twelve thirteen", "😀😀", "😀", "Paid via UPI",
          "Réservation", "Rs.450", "INR 1,200", "1,23,456", "Terminal 3, IGI Airport", "K", "xx", "Sector 62", "éamount",
          "10:30 am", "rs  5"]
cases += [{"fn": "placeProblem", "args": [p]} for p in places]
pairs = [("Mumbai", "mumbai"), ("New Delhi", "New-Delhi"), ("Noida", "Mumbai"), ("", "Mumbai"), (None, "x"), ("  ", "  "),
         ("É", "e"), ("A.B", "a b")]
cases += [{"fn": "samePlaceBothEnds", "args": list(p)} for p in pairs]
msgs = ["  hello​   world\u0007\n", "\u0000\u0001", "a b c﻿d", "tab\there", "x\u001cy", "‪‮z", "  ",
        "a\u0085b"]
cases += [{"fn": "cleanMessage", "args": [m]} for m in msgs]
fl = [{"name": n, "type": t, "size": s} for n, t, s in [
    ("t.pdf", "application/pdf", 10), ("b.JPG", "image/jpeg", 10), ("n.docx", "x", 10), ("p.heic", "image/heic", 10),
    ("x.png", "image/heif", 10), ("e.pdf", "application/pdf", 0), ("h.pdf", "", 50 * 1024 * 1024),
    ("h2.pdf", "", 20 * 1024 * 1024 + 1), ("h3.pdf", "", int(21.5 * 1024 * 1024)), ("mystery", "", 10), ("d.zip", "", 10),
    ("", "", 10), ("a.jpeg", "image/png", 10), ("a.pdf", "text/plain", 10)]]
cases.append({"fn": "checkFiles", "args": [fl]})
nums = ["", " ", "12", " 12 ", "1e3", "1.", ".5", ".", "0x1F", "0b101", "0o17", "-0x1", "Infinity", "-Infinity", "inf", "nan",
        "1_000", "12abc", "+5", "--5", "1e", "1e+2", " 5 ", "0.1", "1,000", None, True, False, 5, -0.0, 1e21,
        123456789012345680000, 0.000001, 1e-7, 1.5e300, 2**53 + 1, 0.1 + 0.2, 100, 1e20, 1.23e-5, [], [5], [1, 2], {},
        "\u0085 5"]
cases += [{"fn": f, "args": [v]} for f in ("number", "parseFloat", "string") for v in nums]
money = [1.005, 2.675, 1.125, 0.125, -1234.5, 1234567.5, 9000, 1e21, 123456789012.345, -0.004, 0.005, 1e-7, 0, "abc",
         "12.5", 99999.995, 1e15 + 0.3]
cases += [{"fn": "inr", "args": [v]} for v in money]
cases += [{"fn": "radix", "args": [random.random()]} for _ in range(300)]
cases += [{"fn": "radix", "args": [v]} for v in (0.5, 0.123456789, 1e-10, 0.9999999999999999)]
cases += [{"fn": "showDate", "args": [v]} for v in ["2026-09-05", "2026-01-31", "0999-09-05", "100-10-05", "5 Oct 2026",
                                                    "garbage", "Sept 5 2026", "2026-12-31T10:00:00", "Oct 5 0100 2026"]]
cases += [{"fn": "claimTourOf", "args": [v]} for v in ["claim-chat:tour-12", "outside-chat:tour-12", "claim-form:tour-0012",
                                                       "within-plan", None, "claim-chat:tour-", "claim-chat:tour-12\n"]]
cases.append({"fn": "uniquelyNamed", "args": [["ticket.pdf", "ticket.pdf", "bill.jpg", "a.b.c", "noext", "noext", ".hidden",
                                               "x.", "x.", "ticket (2).pdf"], ["bill.jpg"]]})


def amt():
    a = {}
    for k in ("billedAmount", "claimedAmount", "totalClaimedAmount", "claimedAmountWithTax", "cgst", "sgst", "igst",
              "billCGST", "billSGST", "billIGST"):
        if random.random() < 0.5:
            a[k] = random.choice([0, 100, 12.5, None, "9", ""])
    for k, pool in (("invoiceNo", ["INV", "", None]), ("invoiceDate", ["2026-10-05", "05/10/2026", "bad", None]),
                    ("taxStructure", ["IGST", "NOT_APPLICABLE", "SGST_CGST", "x", None]),
                    ("sourceFileName", ["a.pdf", "b.pdf", "zz.pdf", None, ""])):
        if random.random() < 0.6:
            a[k] = random.choice(pool)
    return a


def leg(main=False):
    one = {}
    ks = ("startDate", "startTime", "endDate", "endTime") if main else ("departureDate", "departureTime", "arrivalDate",
                                                                         "arrivalTime")
    for k in ks:
        if random.random() < 0.7:
            one[k] = random.choice(["2026-10-05", "10:30", "", "5 Oct 2026", None, "9 pm"])
    for k, pool in (("from", ["Noida", "", "  ", None]), ("to", ["Pune", "", None]),
                    ("modeOfTravel", ["Rail", "Flight", None, "bus"]), ("distance", [0, 12.5, None, "30"])):
        if random.random() < 0.7:
            one[k] = random.choice(pool)
    if random.random() < 0.8:
        one["amountDetails"] = amt()
    return one


def stay():
    s = {}
    for k, pool in (("location", ["Pune", "", " ", None]), ("cityClass", ["X", "", None]),
                    ("checkInDate", ["2026-10-05", "", "bad", None]), ("checkInTime", ["14:00", None]),
                    ("checkOutDate", ["2026-10-07", "", None]), ("checkOutTime", ["11 am", None]),
                    ("accommodationType", ["Hotel", "own", "Company Free", "paid", None]), ("hotelName", ["H", None, ""]),
                    ("sourceFileName", ["c.pdf", None, "a.pdf"])):
        if random.random() < 0.7:
            s[k] = random.choice(pool)
    if random.random() < 0.8:
        s["invoice"] = random.choice([amt(), amt(), None])
    return s


def result():
    r = {}
    if random.random() < 0.8:
        t = leg(True)
        if random.random() < 0.7:
            t["tourLegs"] = [leg() for _ in range(random.randint(0, 3))]
        r["tourDetails"] = t
    if random.random() < 0.8:
        r["accommodationDetails"] = {"entries": [stay() for _ in range(random.randint(0, 3))]}
    if random.random() < 0.8:
        names = random.sample(["a.pdf", "b.pdf", "c.pdf", "d.pdf"], random.randint(0, 3))
        r["meta"] = {"per_file_results": [{"filename": n, "success": random.random() < 0.8, "bill_type": "x"} for n in names]}
    return r


bills = [{"file": {"name": n, "size": i + 1, "type": "application/pdf"}} for i, n in enumerate(["a.pdf", "b.pdf", "c.pdf", "d.pdf"])]
for _ in range(400):
    cases.append({"fn": "mapOcr", "args": [result(), random.sample(bills, random.randint(0, 4))]})
for _ in range(400):
    files = [{"fileName": n, "failed": random.random() < 0.15, "response": result() if random.random() < 0.9 else None}
             for n in random.sample(["a.pdf", "b.pdf", "c.pdf", "d.pdf"], random.randint(0, 4))]
    cases.append({"fn": "combine", "args": [files]})
for _ in range(300):
    files = [{"fileName": n, "failed": False, "response": result()} for n in random.sample(["a.pdf", "b.pdf", "c.pdf"], 3)]
    cases.append({"fn": "combineMap", "args": [files, bills]})
json.dump(cases, open("cases2.json", "w", encoding="utf-8"), ensure_ascii=False)
print(len(cases))
