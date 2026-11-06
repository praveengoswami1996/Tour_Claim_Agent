"""Port of Tour_React tests/claimChatbot/parsers.test.ts and the ocrMapping-only tests of staysOnly.test.ts.

Not ported here (they need tourMatcher.ts, next phase): staysOnly.test.ts 'hotel bills only: each stay goes to the
tour its dates fit; a stay no tour fits is left out' and 'attachUnmatched: once the claim is known, the empty bill goes
to its first stay' (its stays come from triageRows). The bills' File objects are file dicts here.
"""

from __future__ import annotations

import pytest

from tourclaim_agent.claimchat.js_compat import use_runtime
from tourclaim_agent.claimchat.ocr_mapping import (
    accommodation_type_fields,
    attach_unmatched,
    file_key,
    is_real_day,
    map_ocr_to_rows,
    map_travel_mode,
    to_iso_date,
    to_time,
)


# TS: parsers.test.ts 'toIsoDate / toTime refuse impossible values'
def test_to_iso_date_to_time_refuse_impossible_values():
    for v in ["2026-02-30", "31/02/2026", "2026-13-01", "00/00/2026", "garbage", ""]:
        assert to_iso_date(v) == "", v
    assert to_iso_date("2026-10-05") == "2026-10-05"
    for v in ["24:00", "10:60", "abc", ""]:
        assert to_time(v) == "", v
    assert to_time("9.05 PM") == "21:05"


# ── staysOnly.test.ts ──

FILES = [
    "ACC_06NOV2025_07NOV2025.jpeg",
    "ACC_10SEP2026_12SEP2026.jpeg",
    "ACC_18JAN2026_20JAN2026.jpeg",
    "ACC2_10SEP2026_12SEP2026.jpeg",
    "ACC2_18JAN2026_20JAN2026.jpeg",
    "ACC3_10SEP2026_12SEP2026.jpeg",
]
BILLS = [{"file": {"name": name, "size": 1, "type": "image/jpeg"}, "hash": name} for name in FILES]


def stay(id_: str, location: str, check_in: str, check_out: str | None, source_file_name: str) -> dict:
    return {
        "id": id_,
        "location": location,
        "cityClass": "X",
        "checkInDate": check_in,
        "checkInTime": None,
        "checkOutDate": check_out,
        "checkOutTime": None,
        "accommodationType": "hotel",
        "hotelName": "Hotel",
        "sourceFileName": source_file_name,
        "invoice": {
            "invoiceNo": None,
            "invoiceDate": None,
            "billedAmount": 1000,
            "taxStructure": "NOT_APPLICABLE",
            "claimedAmount": 1000,
        },
    }


# the OCR response for six hotel bills: no journeys, one bill read as "unknown" with no entry
RESULT = {
    "meta": {
        "per_file_results": [
            {
                "filename": name,
                "success": True,
                "bill_type": "unknown" if name.startswith("ACC2_10SEP") else "accommodation_bill",
            }
            for name in FILES
        ]
    },
    "tourDetails": {"tourLegs": []},
    "accommodationDetails": {
        "entries": [
            stay("acc_1", "Delhi, Delhi", "2025-11-06", "2025-11-07", "ACC_06NOV2025_07NOV2025.jpeg"),
            stay("acc_2", "Jaipur, Rajasthan", "2026-01-18", "2026-01-20", "ACC_18JAN2026_20JAN2026.jpeg"),
            stay("acc_3", "Jaipur, Rajasthan", "2026-01-20", "2026-01-20", "ACC2_18JAN2026_20JAN2026.jpeg"),
            stay("acc_4", "Delhi, Delhi", "2026-09-10", "2026-09-12", "ACC_10SEP2026_12SEP2026.jpeg"),
            stay("acc_5", "Delhi, Delhi", "2026-09-10", None, "ACC3_10SEP2026_12SEP2026.jpeg"),
        ]
    },
}


# TS: staysOnly.test.ts 'hotel bills only: every stay is read, and the bill with nothing in it is not glued to another
# row'
def test_hotel_bills_only_every_stay_is_read_and_the_empty_bill_is_not_glued_to_another_row():
    mapped = map_ocr_to_rows(RESULT, BILLS)
    assert len(mapped["transportationRows"]) == 0
    assert len(mapped["accommodationRows"]) == 5
    assert mapped["unmatchedFiles"] == ["ACC2_10SEP2026_12SEP2026.jpeg"]
    assert all(len(r["billFiles"]) == 1 for r in mapped["accommodationRows"])


# ── our own ──


def test_map_ocr_to_rows_row_fields_and_ids():
    result = {
        "tourDetails": {
            "from": "Noida",
            "to": "Pune",
            "startDate": "05/10/2026",
            "startTime": "9.05 pm",
            "endDate": "Oct 6, 2026",
            "endTime": "0610",
            "modeOfTravel": "Express Train",
            "distance": 12.0,
            "amountDetails": {
                "billedAmount": 1200.5,
                "totalClaimedAmount": None,
                "claimedAmount": 1100,
                "invoiceDate": "5th Oct 2026",
                "taxStructure": "IGST",
                "igst": 0,
                "sourceFileName": "t.pdf",
            },
            "tourLegs": [{"from": "  ", "to": None}],
        },
        "accommodationDetails": {
            "entries": [
                {
                    "location": "Pune",
                    "checkInDate": "2026-10-06",
                    "accommodationType": "Company paid guest house",
                    "invoice": {"cgst": 45, "sgst": "45", "claimedAmountWithTax": 1090, "sourceFileName": "h.pdf"},
                }
            ]
        },
        "meta": {"per_file_results": [{"filename": "bad.pdf", "success": False}]},
    }
    bills = [
        {"file": {"name": n, "size": 3, "type": "application/pdf"}} for n in ("t.pdf", "h.pdf", "bad.pdf", "x.pdf")
    ]
    with use_runtime(now_ms=lambda: 1790000000000, random=lambda: 0.25):
        mapped = map_ocr_to_rows(result, bills)
    (row,) = mapped["transportationRows"]
    assert row["id"] == "transport-ocr-main-1790000000000"
    assert (row["DateOfTravelSource"], row["TimeOfTravelSource"], row["DateOfTravelDestination"]) == (
        "2026-10-05",
        "21:05",
        "2026-10-06",
    )
    assert (row["TimeOfTravelDestiNation"], row["ModeOfTravel"], row["distanceKm"]) == ("06:10", "rail", "12")
    assert (row["travelCost"], row["claimedAmount"], row["invoiceDate"], row["taxStructure"], row["igst"]) == (
        "1200.5",
        "1100",
        "2026-10-05",
        "IGST",
        "0",
    )
    assert row["includeTravelCosts"] is True
    assert row["uploadedFiles"] == [
        {
            "id": "file-1790000000000-0.25",
            "name": "t.pdf",
            "size": 3,
            "type": "application/pdf",
            "uploadDate": "2026-09-21T14:13:20.000Z",
        }
    ]
    (acc,) = mapped["accommodationRows"]
    assert (acc["id"], acc["selectedType"], acc["companyPaid"], acc["classOfCity"]) == (
        "accom-ocr-0-1790000000000",
        "company_paid",
        "Yes",
        "Select",
    )
    # cgst + sgst + igst with JavaScript `+`: 45 + "45" is "4545", then + 0 is "45450"
    assert (acc["tax"], acc["claimed"], acc["billed"], acc["ClaimedSGSTAmount"], acc["ClaimedIGSTAmount"]) == (
        "45450",
        "1090",
        "0",
        "45",
        0,
    )
    assert mapped["failedFiles"] == ["bad.pdf"]
    assert mapped["unmatchedFiles"] == ["x.pdf"]


def test_attach_unmatched_goes_to_the_first_row_of_its_kind():
    rows = {
        "transportationRows": [{"id": "t1", "uploadedFiles": []}],
        "accommodationRows": [{"id": "a1", "billFiles": []}],
    }
    bills = [
        {"file": {"name": "hotel.jpg", "size": 1, "type": "image/jpeg"}, "category": "accommodation"},
        {"file": {"name": "cab.jpg", "size": 1, "type": "image/jpeg"}},
    ]
    r = attach_unmatched(rows, ["hotel.jpg", "cab.jpg", "missing.jpg"], bills)
    assert r["attached"] == ["hotel.jpg", "cab.jpg"]
    assert [f["name"] for f in r["accommodationRows"][0]["billFiles"]] == ["hotel.jpg"]
    assert [f["name"] for f in r["transportationRows"][0]["uploadedFiles"]] == ["cab.jpg"]
    assert rows["accommodationRows"][0]["billFiles"] == []  # the input rows are not changed
    assert attach_unmatched({"transportationRows": [], "accommodationRows": []}, ["cab.jpg"], bills)["attached"] == []


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("05/10/2026", "2026-10-05"),
        ("05-10-26", "2026-10-05"),
        ("5.10.2026", "2026-10-05"),
        ("2026-10-05T00:00:00", "2026-10-05"),
        ("2026-1-5", "2026-01-05"),
        ("0099-01-01", ""),
        ("05 Oct", ""),
        ("October 5, 2026", "2026-10-05"),
        ("5th October 2026", "2026-10-05"),
        ("Mon, 05 Oct 2026", "2026-10-05"),
        ("05-Oct-2026", "2026-10-05"),
        ("2026/10/05", "2026-10-05"),
        ("05 Oct 2026 10:30", "2026-10-05"),
        ("Sept 5 2026", "2026-09-05"),
        ("5 Sept 2026", "2026-09-05"),
        ("Oct 2026", "2026-10-01"),
        ("2026", "2026-01-01"),
        ("31 Feb 2026", "2026-03-03"),
        ("5 Oct 2026 PNR 2034", ""),
        ("5 Oct 2026 10:30 IST", ""),
        ("5 Oct 2026 23:30 GMT", "2026-10-06"),
        ("10.05.2026x", ""),
        ("12/31/2026", ""),
    ],
)
def test_to_iso_date_reads_like_the_browser(text, iso):
    assert to_iso_date(text) == iso


@pytest.mark.parametrize(
    ("text", "hhmm"),
    [
        ("9:5", "09:05"),
        ("09:05:00", "09:05"),
        ("10 AM", "10:00"),
        ("12 am", "00:00"),
        ("12 pm", "12:00"),
        ("12:30 a.m.", "00:30"),
        ("1030", "10:30"),
        ("930", ""),
        ("10", ""),
        ("10:30 hrs", "10:30"),
        ("10:30hours", "10:30"),
        ("NOON", "12:00"),
        ("midnight", "00:00"),
        ("13 pm", ""),
        ("10:30 pm", "22:30"),
    ],
)
def test_to_time_reads_the_bill_formats(text, hhmm):
    assert to_time(text) == hhmm


def test_small_mapping_helpers():
    assert file_key({"name": "image.jpg"}) == "image.jpg|0"
    assert file_key({"name": "image.jpg", "size": 1024}) == "image.jpg|1024"
    assert is_real_day(2028, 2, 29) and not is_real_day(2026, 2, 29) and not is_real_day(99, 1, 1)
    assert [map_travel_mode(m) for m in ["Business class", "chair car", "town", "office car", None]] == [
        "public-transport",
        "air",
        "personal-vehicle",
        "select",
        "select",
    ]
    assert accommodation_type_fields("company_free")["selectedType"] == "company_free"
    assert accommodation_type_fields(None) == {
        "selectedType": "hotel",
        "ownArrangement": "No",
        "freeProvidedByCompany": "No",
        "companyPaid": "No",
    }
