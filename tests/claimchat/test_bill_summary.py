"""Port of Tour_React tests/claimChatbot/billSummary.test.ts, plus checks of our own (expected texts taken from the
TypeScript, run under Node). The TypeScript Map of fates is a dict here."""

from __future__ import annotations

import re

import pytest

from tourclaim_agent.claimchat.bill_summary import (
    claim_footer,
    describe_row,
    file_line,
    group_of,
    range_text,
    read_header,
    read_summary_text,
    stronger_fate,
    upload_sorted_text,
)

TRAIN = {
    "ModeOfTravel": "rail",
    "source": "New Delhi, Delhi",
    "destination": "Jaipur",
    "DateOfTravelSource": "2026-01-18",
    "uploadedFiles": [{"name": "ticket.pdf"}],
}
STAY = {
    "location": "Jaipur",
    "checkInDate": "2026-01-18",
    "checkOutDate": "2026-01-20",
    "billFiles": [{"name": "hotel.pdf"}],
}


# TS: 'describeRow: a journey and a hotel stay in a few words'
def test_describe_row_a_journey_and_a_hotel_stay_in_a_few_words():
    assert describe_row(TRAIN) == "Train, New Delhi → Jaipur, **18 Jan 2026**"
    assert describe_row(STAY) == "Hotel stay in Jaipur, **18–20 Jan 2026**"


# TS: 'rangeText: shortens dates in the same month or year'
def test_range_text_shortens_dates_in_the_same_month_or_year():
    assert range_text("2026-01-30", "2026-02-02") == "30 Jan – 2 Feb 2026"
    assert range_text("2025-12-30", "2026-01-02") == "30 Dec 2025 – 2 Jan 2026"
    assert range_text("2026-01-18", "2026-01-18") == "18 Jan 2026"


# TS: 'readHeader: greets by name for bills sent earlier'
def test_read_header_greets_by_name_for_bills_sent_earlier():
    assert (
        read_header(3, {"earlier": True, "name": "Anil"}) == "Hi Anil, the 3 bills you uploaded earlier have been read:"
    )
    assert read_header(1, {}) == "Your bill has been read:"


# TS: 'strongerFate: a file with one saved row counts as saved'
def test_stronger_fate_a_file_with_one_saved_row_counts_as_saved():
    saved = {"kind": "saved", "tour": "Delhi → Jaipur"}
    assert stronger_fate({"kind": "noTour"}, saved) is saved
    assert stronger_fate(saved, {"kind": "unreadable"}) is saved


# TS: 'readSummaryText: one line per file, then where they were saved'
def test_read_summary_text_one_line_per_file_then_where_they_were_saved():
    fates = {
        "ticket.pdf": {"kind": "saved", "tour": "Delhi → Jaipur"},
        "hotel.pdf": {"kind": "saved", "tour": "Delhi → Jaipur"},
        "blur.jpg": {"kind": "unreadable"},
    }
    text = read_summary_text(
        {
            "header": "Your 3 bills have been read:",
            "files": ["ticket.pdf", "hotel.pdf", "blur.jpg"],
            "rows": [TRAIN, STAY],
            "fates": fates,
            "savedTours": ["Delhi → Jaipur (16–20 Jan 2026)"],
        }
    )
    assert text == "\n".join(
        [
            "Your 3 bills have been read:",
            "",
            "**✅ New (2)**",
            "• **ticket.pdf**: Train, New Delhi → Jaipur, **18 Jan 2026**",
            "• **hotel.pdf**: Hotel stay in Jaipur, **18–20 Jan 2026**",
            "",
            "**🚫 Not relevant (1)**",
            "• **blur.jpg**: ⚠️ couldn't be read",
            "",
            "✅ Saved to your draft for **Delhi → Jaipur (16–20 Jan 2026)**. Nothing is submitted.",
        ]
    )


# TS: 'groupOf: saved and failed-to-save are new; already there and repeated are duplicates; the rest are not
# relevant'
def test_group_of():
    assert group_of({"kind": "saved", "tour": "t"}, ["x"]) == "new"
    assert group_of({"kind": "saveFailed", "tour": "t"}, ["x"]) == "new"
    assert group_of({"kind": "alreadySaved", "tour": "t"}, ["x"]) == "duplicate"
    assert group_of({"kind": "duplicate"}, []) == "duplicate"
    for fate in [
        {"kind": "noTour"},
        {"kind": "nothingFound"},
        {"kind": "unreadable"},
        {"kind": "unsure"},
        {"kind": "problem", "reason": "r"},
        {"kind": "rejected", "reason": "r"},
    ]:
        assert group_of(fate, []) == "notRelevant", fate["kind"]
    assert group_of(None, ["a journey"]) == "new"
    assert group_of(None, []) == "notRelevant"


# TS: 'readSummaryText: files sorted into New, Duplicate and Not relevant, each group with its count'
def test_read_summary_text_files_sorted_into_groups_each_with_its_count():
    fates = {
        "ticket.pdf": {"kind": "saved", "tour": "Delhi → Jaipur"},
        "again.pdf": {"kind": "duplicate"},
        "other.pdf": {"kind": "noTour"},
        "notes.docx": {
            "kind": "rejected",
            "reason": "I can only read PDFs and photos or scans (JPG, PNG), not this kind of file.",
        },
    }
    text = read_summary_text(
        {
            "header": "h",
            "files": ["ticket.pdf", "again.pdf", "other.pdf", "notes.docx"],
            "rows": [TRAIN],
            "fates": fates,
            "savedTours": ["Delhi → Jaipur"],
        }
    )
    assert re.search(r"\*\*✅ New \(1\)\*\*\n• \*\*ticket\.pdf\*\*", text)
    assert re.search(
        r"\*\*↩ Duplicate \(1\)\*\*\n• \*\*again\.pdf\*\*: same file as one already uploaded, skipped", text
    )
    assert re.search(
        r"\*\*🚫 Not relevant \(2\)\*\*\n• \*\*other\.pdf\*\*:.*doesn't match any of your approved tours\*\*\)\n"
        r"• \*\*notes\.docx\*\*: \*\*not uploaded\*\*: I can only read PDFs",
        text,
    )
    assert text.index("New (1)") < text.index("Duplicate (1)") < text.index("Not relevant (2)")


# TS: 'readSummaryText: a group with no files is not shown'
def test_read_summary_text_a_group_with_no_files_is_not_shown():
    text = read_summary_text(
        {
            "header": "h",
            "files": ["hotel.pdf"],
            "rows": [STAY],
            "fates": {"hotel.pdf": {"kind": "saved", "tour": "Delhi → Jaipur"}},
            "savedTours": ["Delhi → Jaipur"],
        }
    )
    assert not re.search(r"Duplicate|Not relevant", text)


# TS: 'uploadSortedText: an upload with nothing to read is sorted the same way, and says nothing was added'
def test_upload_sorted_text_an_upload_with_nothing_to_read():
    fates = {
        "same.pdf": {"kind": "duplicate"},
        "blank.pdf": {"kind": "rejected", "reason": "the file is empty. Please attach it again."},
    }
    assert upload_sorted_text(["same.pdf", "blank.pdf"], fates) == "\n".join(
        [
            "Here is how I sorted your upload:",
            "",
            "**↩ Duplicate (1)**",
            "• **same.pdf**: same file as one already uploaded, skipped",
            "",
            "**🚫 Not relevant (1)**",
            "• **blank.pdf**: **not uploaded**: the file is empty. Please attach it again.",
            "",
            "Nothing new to read, so nothing was added.",
        ]
    )


# TS: 'readSummaryText: several tours name the tour on each line; nothing saved is said'
def test_read_summary_text_several_tours_name_the_tour_on_each_line_nothing_saved_is_said():
    fates = {
        "ticket.pdf": {"kind": "saved", "tour": "Delhi → Jaipur"},
        "hotel.pdf": {"kind": "saved", "tour": "Delhi → Pune"},
    }
    text = read_summary_text(
        {
            "header": "h",
            "files": ["ticket.pdf", "hotel.pdf"],
            "rows": [TRAIN, STAY],
            "fates": fates,
            "savedTours": ["A", "B"],
        }
    )
    assert re.search(
        r"\*\*ticket\.pdf\*\*: Train, New Delhi → Jaipur, \*\*18 Jan 2026\*\* → \*\*Delhi → Jaipur\*\*", text
    )
    assert re.search(r"Saved to your drafts for \*\*2 tours\*\*", text)
    none = read_summary_text(
        {
            "header": "h",
            "files": ["ticket.pdf"],
            "rows": [TRAIN],
            "fates": {"ticket.pdf": {"kind": "noTour"}},
            "savedTours": [],
        }
    )
    assert re.search(r"doesn't match any of your approved tours", none)
    assert re.search(r"Nothing was saved\.", none)


# ── inside a claim form: what was found, added, already there, not added, not belonging; never what is missing ──


def in_claim(fates, added=None, files=None):
    added = added if added is not None else {"journeys": 1, "stays": 1, "saved": True}
    return read_summary_text(
        {
            "header": "Your bills have been read:",
            "files": files if files is not None else [f for f, _ in fates],
            "rows": [TRAIN, STAY],
            "fates": dict(fates),
            "savedTours": [],
            "claim": added,
        }
    )


# TS: 'in a claim (positive): added bills, and what was added to this claim'
def test_in_a_claim_added_bills_and_what_was_added():
    text = in_claim(
        [
            ("ticket.pdf", {"kind": "saved", "tour": "Delhi → Jaipur"}),
            ("hotel.pdf", {"kind": "saved", "tour": "Delhi → Jaipur"}),
        ]
    )
    assert re.search(r"\*\*ticket\.pdf\*\*: Train, New Delhi → Jaipur, \*\*18 Jan 2026\*\*$", text, re.MULTILINE)
    assert re.search(r"Added \*\*1 journey\*\* and \*\*1 stay\*\* to this claim\. The draft is saved\.", text)


# TS: 'in a claim (positive): already there, does not belong, could not be read, not added'
def test_in_a_claim_already_there_does_not_belong_could_not_be_read_not_added():
    text = in_claim(
        [
            ("ticket.pdf", {"kind": "alreadySaved", "tour": "Delhi → Jaipur"}),
            ("hotel.pdf", {"kind": "noTour"}),
            ("blur.jpg", {"kind": "unreadable"}),
            ("bad.pdf", {"kind": "problem", "reason": "the check-out date is before the check-in date"}),
        ],
        {"journeys": 0, "stays": 0, "saved": True},
    )
    assert re.search(r"ticket\.pdf.*already in this claim, not added again", text)
    assert re.search(r"hotel\.pdf.*doesn't belong to this claim, not added", text)
    assert re.search(r"blur\.jpg.*couldn't be read", text)
    assert re.search(r"bad\.pdf.*not added\*\*: the check-out date is before the check-in date", text)
    assert re.search(r"Nothing new was added to this claim\.", text)


# TS: 'in a claim (negative): the report never says what the claim is missing or suggests uploads'
@pytest.mark.parametrize(
    "fate",
    [
        {"kind": "saved", "tour": "x"},
        {"kind": "alreadySaved", "tour": "x"},
        {"kind": "noTour"},
        {"kind": "unreadable"},
        {"kind": "nothingFound"},
        {"kind": "problem", "reason": "r"},
        {"kind": "duplicate"},
    ],
)
def test_in_a_claim_the_report_never_says_what_is_missing(fate):
    text = in_claim([("ticket.pdf", fate)])
    assert not re.search(r"missing|you should|please upload|do you have|approved tours|draft for", text, re.IGNORECASE)


# TS: 'claimFooter: a draft that could not be saved is said'
def test_claim_footer_a_draft_that_could_not_be_saved_is_said():
    assert re.search(
        r"Added \*\*2 journeys\*\*.*could not be saved yet", claim_footer({"journeys": 2, "stays": 0, "saved": False})
    )


# ── our own (expected texts from the TypeScript) ──


def test_file_lines_word_for_word():
    found = ["A", "B", "C", "D", "E"]
    assert file_line("x.pdf", found, {"kind": "saved", "tour": "T"}, {"tagSaved": True}) == (
        "• **x.pdf**: A; B; C; and 2 more → **T**"
    )
    assert file_line("x.pdf", [], {"kind": "problem", "reason": "r"}, {"tagSaved": False}) == (
        "• **x.pdf**: hotel bill (**not added**: r)"
    )
    assert file_line("x.pdf", ["A"], {"kind": "saveFailed", "tour": "T"}, {"tagSaved": False}) == (
        "• **x.pdf**: A (**not saved yet**)"
    )
    assert file_line("x.pdf", ["A"], {"kind": "alreadySaved", "tour": "T"}, {"tagSaved": False}) == (
        "• **x.pdf**: A (already in your T draft)"
    )
    assert file_line("x.pdf", ["A"], {"kind": "unsure"}, {"tagSaved": False}) == (
        "• **x.pdf**: A (**couldn't tell which tour it's for**)"
    )
    assert file_line("x.pdf", [], None, {"tagSaved": False}) == "• **x.pdf**: no journey or hotel stay found"


def test_describe_row_odd_rows():
    assert describe_row({"ModeOfTravel": "toString", "source": "", "destination": "Pune"}) == (
        "function toString() { [native code] }, ? → Pune"
    )
    assert describe_row({"location": None, "checkInDate": "2026-09-05", "checkOutDate": "garbage"}) == (
        "Hotel stay in ?, **5 Sep 2026**"
    )


def test_read_summary_footer_save_failed_and_headers():
    text = read_summary_text(
        {
            "header": "h",
            "files": ["a.pdf"],
            "rows": [],
            "fates": {"a.pdf": {"kind": "saveFailed", "tour": "T"}},
            "savedTours": [],
            "notes": ["", "", "note"],
        }
    )
    assert text == (
        "h\n\n**✅ New (1)**\n• **a.pdf**:  (**not saved yet**)\n\nnote\n\n⚠️ I couldn't save some of them just now. "
        "I'll try again the next time you open the assistant."
    )
    assert read_header(2, {"name": None}) == "Hi, your 2 bills have been read:"
    assert read_header(1, {"earlier": True}) == "The bill you uploaded earlier has been read:"
    assert claim_footer({"journeys": 0, "stays": 1, "saved": True}) == (
        "✅ Added **1 stay** to this claim. The draft is saved."
    )
