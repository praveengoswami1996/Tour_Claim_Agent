"""Port of Tour_React tests/claimChatbot/ocrResults.test.ts.

The browser File objects of the TypeScript test are file dicts here; a file on a row is told apart by the
`documentId` it carries over from the bill.
"""

from __future__ import annotations

from tourclaim_agent.claimchat.ocr_mapping import map_ocr_to_rows
from tourclaim_agent.claimchat.ocr_results import combine_ocr_results, uniquely_named


# one result per file, as the server keeps them; sourceFileName is often left empty by the OCR
def ticket(from_: str, to: str, date: str, amount: float) -> dict:
    return {
        "tourDetails": {
            "from": from_,
            "to": to,
            "startDate": date,
            "startTime": "08:00",
            "endDate": date,
            "endTime": "12:00",
            "modeOfTravel": "Rail",
            "amountDetails": {"billedAmount": amount, "sourceFileName": None},
        },
        "accommodationDetails": {"entries": []},
        "meta": {"per_file_results": [{"filename": "x", "success": True, "bill_type": "ticket"}]},
    }


def hotel(location: str, check_in: str, check_out: str) -> dict:
    return {
        "accommodationDetails": {
            "entries": [
                {
                    "location": location,
                    "checkInDate": check_in,
                    "checkOutDate": check_out,
                    "invoice": {"billedAmount": 3000, "invoiceNo": "H1"},
                }
            ]
        },
        "meta": {"per_file_results": [{"filename": "x", "success": True}]},
    }


# TS: 'combineOcrResults: the first file is the main journey, the others legs, stays one after another'
def test_combine_ocr_results_first_file_is_the_main_journey_the_others_legs():
    combined = combine_ocr_results(
        [
            {"fileName": "out.pdf", "failed": False, "response": ticket("Delhi", "Mumbai", "2026-10-01", 1200)},
            {"fileName": "hotel.pdf", "failed": False, "response": hotel("Mumbai", "2026-10-01", "2026-10-03")},
            {"fileName": "back.pdf", "failed": False, "response": ticket("Mumbai", "Delhi", "2026-10-03", 1300)},
        ]
    )
    tour = combined["tourDetails"]
    assert tour["from"] == "Delhi"
    assert tour["amountDetails"]["sourceFileName"] == "out.pdf"
    assert len(tour["tourLegs"]) == 1
    assert tour["tourLegs"][0]["from"] == "Mumbai"
    assert tour["tourLegs"][0]["departureDate"] == "2026-10-03"
    assert tour["tourLegs"][0]["amountDetails"]["sourceFileName"] == "back.pdf"
    assert combined["accommodationDetails"]["entries"][0]["sourceFileName"] == "hotel.pdf"
    assert [[r["filename"], r["success"]] for r in combined["meta"]["per_file_results"]] == [
        ["out.pdf", True],
        ["hotel.pdf", True],
        ["back.pdf", True],
    ]


# TS: 'combineOcrResults: a failed file (on the server, or by the OCR itself) is reported as failed and adds nothing'
def test_combine_ocr_results_a_failed_file_is_reported_as_failed_and_adds_nothing():
    combined = combine_ocr_results(
        [
            {"fileName": "bad.pdf", "failed": True, "response": None},
            {
                "fileName": "blank.pdf",
                "failed": False,
                "response": {"meta": {"per_file_results": [{"filename": "blank.pdf", "success": False}]}},
            },
            {"fileName": "hotel.pdf", "failed": False, "response": hotel("Pune", "2026-10-05", "2026-10-06")},
        ]
    )
    assert "tourDetails" not in combined
    assert len(combined["accommodationDetails"]["entries"]) == 1
    assert [r["filename"] for r in combined["meta"]["per_file_results"] if not r["success"]] == ["bad.pdf", "blank.pdf"]


# TS: 'combineOcrResults: only legs in the files -> the first leg leads'
def test_combine_ocr_results_only_legs_the_first_leg_leads():
    combined = combine_ocr_results(
        [
            {
                "fileName": "legs.pdf",
                "failed": False,
                "response": {
                    "tourDetails": {
                        "tourLegs": [
                            {"from": "A", "to": "B", "departureDate": "2026-10-01"},
                            {"from": "B", "to": "C", "departureDate": "2026-10-02"},
                        ]
                    }
                },
            }
        ]
    )
    tour = combined["tourDetails"]
    assert tour["from"] == "A"
    assert tour["startDate"] == "2026-10-01"
    assert len(tour["tourLegs"]) == 1
    assert tour["amountDetails"]["sourceFileName"] == "legs.pdf"


# TS: 'mapOcrToRows on combined results: every journey and stay keeps its own bill file'
def test_map_ocr_to_rows_on_combined_results_every_journey_and_stay_keeps_its_own_bill_file():
    out, stay, back = (
        {"name": "out.pdf", "size": 1, "type": "application/pdf", "documentId": "doc-out"},
        {"name": "hotel.pdf", "size": 1, "type": "application/pdf", "documentId": "doc-hotel"},
        {"name": "back.pdf", "size": 1, "type": "application/pdf", "documentId": "doc-back"},
    )
    combined = combine_ocr_results(
        [
            {"fileName": out["name"], "failed": False, "response": ticket("Delhi", "Mumbai", "2026-10-01", 1200)},
            {"fileName": stay["name"], "failed": False, "response": hotel("Mumbai", "2026-10-01", "2026-10-03")},
            {"fileName": back["name"], "failed": False, "response": ticket("Mumbai", "Delhi", "2026-10-03", 1300)},
        ]
    )
    mapped = map_ocr_to_rows(combined, [{"file": out}, {"file": stay}, {"file": back}])
    assert [[f["documentId"] for f in r["uploadedFiles"]] for r in mapped["transportationRows"]] == [
        ["doc-out"],
        ["doc-back"],
    ]
    assert [[f["documentId"] for f in r["billFiles"]] for r in mapped["accommodationRows"]] == [["doc-hotel"]]
    assert mapped["failedFiles"] == []
    assert mapped["unmatchedFiles"] == []


# TS: 'uniquelyNamed: a second file of the same name is told apart, also from names already in the claim'
def test_uniquely_named_a_second_file_of_the_same_name_is_told_apart():
    named = uniquely_named([{"name": "ticket.pdf"}, {"name": "ticket.pdf"}, {"name": "bill.jpg"}], ["bill.jpg"])
    assert [f["name"] for f in named] == ["ticket.pdf", "ticket (2).pdf", "bill (2).jpg"]


# ── our own ──


def test_uniquely_named_follows_the_javascript_regex_for_odd_names():
    named = uniquely_named([{"name": n} for n in ["a.b.c", "a.b.c", "noext", "noext", "x.", "x."]])
    assert [f["name"] for f in named] == ["a.b.c", "a.b (2).c", "noext", "noext (2)", "x.", "x (2)."]
