"""Port of Tour_React tests/claimChatbot/placeCheck.test.ts."""

from __future__ import annotations

import re

from tourclaim_agent.claimchat.place_check import place_problem, same_place_both_ends


# TS: 'real places, stations and airports pass'
def test_real_places_stations_and_airports_pass():
    for p in [
        "Noida",
        "New Delhi",
        "Mumbai CST",
        "Hazrat Nizamuddin (NZM)",
        "Indira Gandhi International Airport, New Delhi",
        "Terminal 3, IGI Airport",
        "T2 Chhatrapati Shivaji Maharaj International Airport",
        "Sector 62, Noida",
        "Netaji Subhas Chandra Bose International Airport",
        "Billimora Junction",
        "Bengaluru (SBC)",
        "Pune",
        "DFCCIL Office, Pragati Maidan",
        "Hotel Sea View, Juhu",
        "Ahmedabad Jn",
        "Kanpur Central",
    ]:
        assert place_problem(p) is None, p


# TS: 'an empty place is not judged here (it is reported as missing)'
def test_an_empty_place_is_not_judged_here():
    assert place_problem("") is None
    assert place_problem("   ") is None
    assert place_problem(None) is None


# TS: 'bill labels in a place field are flagged'
def test_bill_labels_in_a_place_field_are_flagged():
    for p in [
        "Total Net Amount",
        "Grand Total",
        "Net Payable",
        "Base Fare",
        "GST",
        "CGST 9%",
        "Invoice No",
        "PNR",
        "Booking ID",
        "Passenger Details",
        "Convenience Fee",
        "Taxes and Charges",
        "Seat 14B",
        "Paid via UPI",
    ]:
        assert re.search(r"label from the bill|amount|mostly numbers", place_problem(p) or ""), p


# TS: 'amounts, dates, times, numbers and contacts in a place field are flagged'
def test_amounts_dates_times_numbers_and_contacts_are_flagged():
    assert re.search(r"amount", place_problem("₹1,240.00") or "")
    assert re.search(r"amount", place_problem("Rs 450") or "")
    assert re.search(r"amount", place_problem("1240.50") or "")
    assert re.search(r"date or time", place_problem("05/10/2026") or "")
    assert re.search(r"date or time", place_problem("2026-10-05") or "")
    assert re.search(r"date or time", place_problem("10:30 AM") or "")
    assert re.search(r"no letters|mostly numbers", place_problem("8812345670") or "")
    assert re.search(r"mostly numbers", place_problem("PN 88123456") or "")
    assert re.search(r"email or web", place_problem("care@irctc.co.in") or "")
    assert re.search(r"email or web", place_problem("www.makemytrip.com") or "")


# TS: 'placeholders, symbols and walls of text are flagged'
def test_placeholders_symbols_and_walls_of_text_are_flagged():
    for p in ["N/A", "NA", "null", "undefined", "Unknown", "-", "?", "...", "Select"]:
        assert re.search(r"placeholder", place_problem(p) or ""), p
    assert re.search(r"no letters", place_problem("###") or "")
    wall = (
        "Please carry a valid photo identity card in original while travelling with this ticket otherwise you will be "
        "treated as without ticket"
    )
    assert re.search(r"too long", place_problem(wall) or "")
    assert re.search(r"placeholder|too short", place_problem("X") or "")


# TS: 'the same place at both ends is spotted, whatever the case or punctuation'
def test_the_same_place_at_both_ends_is_spotted():
    assert same_place_both_ends("Mumbai", "mumbai") is True
    assert same_place_both_ends("New Delhi", "New-Delhi") is True
    assert same_place_both_ends("Noida", "Mumbai") is False
    assert same_place_both_ends("", "Mumbai") is False


# ── our own: JavaScript regex semantics ──


def test_place_problem_word_boundaries_and_lengths_are_javascript_ones():
    # \b is ASCII-only in JavaScript: "é" is not a word character, so "éamount" has a word boundary before "amount"
    assert place_problem("éamount") == "it looks like a label from the bill, not a place"
    assert place_problem("Café Coffee Day") is None
    assert place_problem("10:30 am") == "it looks like a date or time from the bill"
    # an emoji is two UTF-16 units: one emoji is not "too short"
    assert place_problem("😀") == "it has no letters, so it is not a place name"
    assert place_problem("a" * 79 + "😀") == "it is too long to be a place name"
    assert same_place_both_ends("É", "e") is False  # "É" is not a-z, so both normalise differently
