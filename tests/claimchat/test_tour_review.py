"""Port of Tour_React tests/claimChatbot/tourReview.test.ts (all of it) and of the tourReview test of
timeOverlap.test.ts ('the review asks about the overlap as a must-fix, and the short summary names it'), plus checks
of our own (expected values taken from the TypeScript, run under Node)."""

from __future__ import annotations

import re

from tests.claimchat.test_row_order import ambala_trip
from tourclaim_agent.claimchat.claim_assistant import initial_claim_state, new_accommodation_row, new_transport_row
from tourclaim_agent.claimchat.tour_review import (
    missing_in_short,
    next_review_step,
    review_blocked_note,
    review_claim,
    review_done_note,
    review_question_note,
    review_settled_note,
    settle,
)

tour = {
    "tourId": 4521,
    "source": "Noida",
    "destination": "Mumbai",
    "tourStartDate": "2026-10-05",
    "tantetiveEndDate": "2026-10-09",
    "totalDays": 5,
    "statusId": 2,
}
rules = {"allowAir": True, "minDate": "2026-10-05", "maxDate": "2026-10-10"}
file = {"name": "ticket.pdf"}


def j(from_, to, dep, dep_t, arr, arr_t, mode="air", **extra):
    return {
        **new_transport_row(),
        "source": from_,
        "destination": to,
        "DateOfTravelSource": dep,
        "TimeOfTravelSource": dep_t,
        "DateOfTravelDestination": arr,
        "TimeOfTravelDestiNation": arr_t,
        "ModeOfTravel": mode,
        "uploadedFiles": [file],
        "claimedAmount": "4500",
        **extra,
    }


def s(city, inn, inn_t, out, out_t, **extra):
    return {
        **new_accommodation_row(),
        "location": city,
        "checkInDate": inn,
        "checkInTime": inn_t,
        "checkOutDate": out,
        "checkOutTime": out_t,
        "selectedType": "hotel",
        "hotelName": "Hotel Sea View",
        "invoiceNo": "INV-1",
        "classOfCity": "X",
        "billFiles": [file],
        "claimed": "5000",
        "billed": "5000",
        **extra,
    }


def claim(journeys, stays=(), leave=None):
    return {
        **initial_claim_state(),
        "transportationRows": list(journeys) if journeys else initial_claim_state()["transportationRows"],
        "accommodationRows": list(stays) if stays else initial_claim_state()["accommodationRows"],
        "includeAccommodation": len(stays) > 0,
        "includeLeave": bool(leave),
        "leaveRows": [{"id": "l1", "daDeductable": "Yes", "amount": "0", **leave}]
        if leave
        else initial_claim_state()["leaveRows"],
    }


def outbound():
    return j("Noida", "Mumbai", "2026-10-05", "10:30", "2026-10-05", "13:00")


def back():
    return j("Mumbai", "Noida", "2026-10-09", "18:00", "2026-10-09", "20:10")


def hotel():
    return s("Mumbai", "2026-10-05", "14:00", "2026-10-09", "12:00")


def cab(from_, to, d, dep, arr, **extra):
    return j(from_, to, d, dep, d, arr, "public-transport", **{"claimedAmount": "450", **extra})


def mumbai_cabs():
    """the cabs a complete Noida → Mumbai → Noida tour has: home → airport, airport → hotel, around town,
    hotel → airport, airport → home"""
    return [
        cab("Noida", "Noida Airport", "2026-10-05", "08:00", "09:30"),
        cab("Mumbai Airport", "Mumbai Hotel", "2026-10-05", "13:20", "14:00"),
        cab("Mumbai Hotel", "Mumbai Office", "2026-10-07", "09:00", "09:40"),
        cab("Mumbai Hotel", "Mumbai Airport", "2026-10-09", "16:00", "17:00"),
        cab("Noida Airport", "Noida", "2026-10-09", "20:30", "21:30"),
    ]


def review(c, t=tour, **opts):
    return review_claim({"state": c, "tour": t, "rules": rules, "leaveAnswered": True, **opts})


def kinds(c, leave_answered=True, form_problems=None):
    inp = {"state": c, "tour": tour, "rules": rules, "leaveAnswered": leave_answered}
    if form_problems is not None:
        inp["formProblems"] = form_problems
    return [i["kind"] for i in review_claim(inp)]


def find(items, kind):
    return next((i for i in items if i["kind"] == kind), None)


# ── a complete tour asks nothing ──


# TS: 'complete tour: there and back, a stay every night, bills attached: nothing to ask'
def test_complete_tour_nothing_to_ask():
    assert kinds(claim([outbound(), back(), *mumbai_cabs()], [hotel()])) == []


# TS: 'complete tour with a stay for each night in different cities: nothing to ask'
def test_complete_tour_with_a_stay_each_night_in_different_cities():
    c = claim(
        [
            outbound(),
            j("Mumbai", "Pune", "2026-10-07", "09:00", "2026-10-07", "12:00", "rail"),
            j("Pune", "Noida", "2026-10-09", "18:00", "2026-10-09", "20:10"),
            cab("Noida", "Noida Airport", "2026-10-05", "08:00", "09:30"),
            cab("Mumbai Airport", "Mumbai Hotel", "2026-10-05", "13:20", "14:00"),
            cab("Mumbai Hotel", "Mumbai Office", "2026-10-06", "09:00", "09:40"),
            cab("Mumbai Hotel", "Mumbai Station", "2026-10-07", "07:30", "08:30"),
            cab("Pune Station", "Pune Hotel", "2026-10-07", "12:20", "12:50"),
            cab("Pune Hotel", "Pune Site", "2026-10-08", "09:00", "09:30"),
            cab("Pune Hotel", "Pune Airport", "2026-10-09", "16:00", "17:00"),
            cab("Noida Airport", "Noida", "2026-10-09", "20:30", "21:30"),
        ],
        [
            s("Mumbai", "2026-10-05", "14:00", "2026-10-07", "08:00"),
            s("Pune", "2026-10-07", "13:00", "2026-10-09", "12:00"),
        ],
    )
    assert kinds(c) == []


# TS: 'a round trip from a nearby station (Noida tour, flights from Delhi and back) is complete'
def test_round_trip_from_a_nearby_station_is_complete():
    c = claim(
        [
            j("Delhi", "Mumbai", "2026-10-05", "10:30", "2026-10-05", "13:00"),
            j("Mumbai", "Delhi", "2026-10-09", "18:00", "2026-10-09", "20:10"),
        ],
        [hotel()],
    )
    assert "noOutbound" not in kinds(c)
    assert "noReturn" not in kinds(c)


# TS: 'a station written differently ("Noida, Uttar Pradesh") is still the starting point'
def test_a_station_written_differently_is_still_the_starting_point():
    c = claim(
        [outbound(), j("Mumbai", "Noida, Uttar Pradesh", "2026-10-09", "18:00", "2026-10-09", "20:10"), *mumbai_cabs()],
        [hotel()],
    )
    assert kinds(c) == []


# ── the route ──


# TS: 'no journeys at all is a blocking question'
def test_no_journeys_at_all_is_blocking():
    items = review(claim([]))
    assert items[0]["kind"] == "noJourneys"
    assert items[0]["blocking"] is True


# TS: 'one-way claim: "There are no return journey tickets/bills uploaded. Do you have them?"'
def test_one_way_claim_asks_for_the_return():
    items = review(claim([outbound()], [hotel()]))
    ret = find(items, "noReturn")
    assert ret
    assert re.search(
        r"There are no return journey tickets/bills uploaded \(Mumbai → Noida\)\. Do you have them\?", ret["question"]
    )
    assert ret["blocking"] is False
    assert ret["yes"] == "upload"
    # the days after the last journey are part of the same question, not asked again
    assert not any(i["kind"] == "uncoveredDays" for i in items)
    assert not any(i["kind"] == "stayOutsideTravel" for i in items)


# TS: 'first journey not from the starting point: the onward ticket is asked for'
def test_first_journey_not_from_the_starting_point():
    c = claim([j("Delhi", "Mumbai", "2026-10-05", "10:30", "2026-10-05", "13:00"), back()], [hotel()])
    out = find(review(c), "noOutbound")
    assert out
    assert re.search(r"onward journey ticket from Noida to Delhi", out["question"])


# TS: 'a break in the route: "Is a ticket missing between Mumbai and Pune?"'
def test_a_break_in_the_route():
    c = claim([outbound(), j("Pune", "Noida", "2026-10-09", "18:00", "2026-10-09", "20:10")], [hotel()])
    br = find(review(c), "routeBreak")
    assert br
    assert re.search(r"between Mumbai and Pune", br["question"])


# TS: 'a journey that leaves before the previous one arrives is blocking'
def test_a_journey_leaving_before_the_previous_one_arrives_is_blocking():
    c = claim([outbound(), j("Mumbai", "Noida", "2026-10-05", "12:00", "2026-10-05", "14:00")])
    ov = find(review(c), "overlap")
    assert ov
    assert ov["blocking"] is True


# ── details and dates ──


# TS: "missing details are blocking, one question each, with the form's own wording"
def test_missing_details_are_blocking_with_the_forms_wording():
    c = claim([j("Noida", "Mumbai", "2026-10-05", "", "2026-10-05", "13:00"), back()], [hotel()])
    items = [i for i in review(c) if i["kind"] == "missingField"]
    assert len(items) == 1
    assert items[0]["blocking"] is True
    assert re.search(r"What time did you leave for journey 1", items[0]["question"])


# TS: 'a hotel stay without a hotel name or invoice number asks for both'
def test_a_hotel_stay_without_name_or_invoice_number_asks_for_both():
    c = claim(
        [outbound(), back()], [s("Mumbai", "2026-10-05", "14:00", "2026-10-09", "12:00", hotelName="", invoiceNo="")]
    )
    assert [i["id"] for i in review(c) if i["kind"] == "missingField"] == [
        "missing:accommodation:1:hotelName",
        "missing:accommodation:1:invoiceNo",
    ]


# TS: 'dates outside the tour, arrivals before departures and check-outs before check-ins are blocking'
def test_bad_dates_are_blocking():
    c = claim(
        [
            j("Noida", "Mumbai", "2026-10-05", "10:30", "2026-10-05", "09:00"),
            j("Mumbai", "Noida", "2026-10-20", "18:00", "2026-10-20", "20:10"),
        ],
        [s("Mumbai", "2026-10-05", "14:00", "2026-10-04", "12:00")],
    )
    bad = [i for i in review(c) if i["kind"] == "badDates"]
    assert len(bad) == 4  # the stay is also partly before the tour
    assert all(i["blocking"] for i in bad)


# ── days and nights ──


# TS: 'tour days nothing covers are asked about once'
def test_tour_days_nothing_covers_are_asked_once():
    c = claim([outbound(), back()], [s("Mumbai", "2026-10-05", "14:00", "2026-10-06", "12:00")])
    gap = find(review(c), "uncoveredDays")
    assert gap
    assert re.search(r"07 Oct 2026 – 08 Oct 2026", gap["question"])


# TS: 'nights away with no stay: "Where did you stay on the nights of ... in Mumbai?"'
def test_nights_away_with_no_stay():
    c = claim([outbound(), back()], [s("Mumbai", "2026-10-05", "14:00", "2026-10-07", "12:00")])
    n = [i for i in review(c) if i["kind"] == "nightsWithoutStay"]
    assert len(n) == 1
    assert re.search(r"nights of 07 Oct 2026 – 08 Oct 2026 in Mumbai", n[0]["question"])


# TS: 'an overnight train covers its night, and a leave day covers its night'
def test_an_overnight_train_and_a_leave_day_cover_their_nights():
    train = claim(
        [
            j("Noida", "Mumbai", "2026-10-05", "22:00", "2026-10-06", "07:00", "rail"),
            j("Mumbai", "Noida", "2026-10-06", "20:00", "2026-10-07", "06:00", "rail"),
        ]
    )
    assert not any(i["kind"] == "nightsWithoutStay" for i in review(train, {**tour, "tantetiveEndDate": "2026-10-07"}))
    with_leave = claim(
        [outbound(), j("Mumbai", "Noida", "2026-10-06", "18:00", "2026-10-06", "20:10")],
        [],
        {"startDate": "2026-10-05", "endDate": "2026-10-05"},
    )
    assert not any(
        i["kind"] == "nightsWithoutStay" for i in review(with_leave, {**tour, "tantetiveEndDate": "2026-10-06"})
    )


# TS: 'a same-day trip has no nights to ask about'
def test_a_same_day_trip_has_no_nights():
    c = claim([outbound(), j("Mumbai", "Noida", "2026-10-05", "18:00", "2026-10-05", "20:10")])
    assert not any(i["kind"] == "nightsWithoutStay" for i in review(c, {**tour, "tantetiveEndDate": "2026-10-05"}))


# TS: 'a stay in a city no journey goes to, and a stay outside the travel dates, are questioned (yes = keep, no =
# remove)'
def test_stays_off_the_route_or_outside_the_travel_dates_are_questioned():
    goa = find(
        review(claim([outbound(), back()], [hotel(), s("Goa", "2026-10-06", "14:00", "2026-10-07", "12:00")])),
        "stayNotOnRoute",
    )
    assert goa
    assert goa["no"] == "remove"
    early = review(
        claim(
            [j("Noida", "Mumbai", "2026-10-08", "10:30", "2026-10-08", "13:00"), back()],
            [s("Mumbai", "2026-10-05", "14:00", "2026-10-09", "12:00")],
        )
    )
    assert any(i["kind"] == "stayOutsideTravel" for i in early)


# ── bills and amounts ──


# TS: 'a ticketed journey with no ticket attached is asked about; personal or official vehicles and "bill not
# required" are not'
def test_a_ticketed_journey_with_no_ticket_is_asked_about():
    c = claim([r if i != 1 else {**r, "uploadedFiles": []} for i, r in enumerate([outbound(), back()])], [hotel()])
    assert "ticketMissing" in kinds(c)
    car = claim([outbound(), {**back(), "uploadedFiles": [], "ModeOfTravel": "personal-vehicle"}], [hotel()])
    assert "ticketMissing" not in kinds(car)
    not_required = claim([outbound(), {**back(), "uploadedFiles": [], "billNotRequired": True}], [hotel()])
    assert "ticketMissing" not in kinds(not_required)


# TS: 'a hotel stay with no bill attached is asked about; own arrangement and free company lodging are not'
def test_a_hotel_stay_with_no_bill_is_asked_about():
    assert "hotelBillMissing" in kinds(claim([outbound(), back()], [{**hotel(), "billFiles": []}]))
    own = {**hotel(), "billFiles": [], "selectedType": "own_arrangement"}
    assert "hotelBillMissing" not in kinds(claim([outbound(), back()], [own]))
    free = {**hotel(), "billFiles": [], "selectedType": "company_free", "hotelName": "", "invoiceNo": ""}
    assert "hotelBillMissing" not in kinds(claim([outbound(), back()], [free]))


# TS: 'a bill with no amount read is left for the form'
def test_a_bill_with_no_amount_read_is_left_for_the_form():
    amt = find(review(claim([{**outbound(), "claimedAmount": "0"}, back()], [hotel()])), "amountMissing")
    assert amt
    assert amt["resolutions"] == ["form", "confirm"]


# TS: "the form's own errors are blocking; its missing-detail errors are not repeated (they are asked as details)"
def test_the_forms_own_errors_are_blocking():
    form_problems = [
        {
            "kind": "overEntitlement",
            "section": "accommodation",
            "row": 1,
            "message": "Claimed base amount (₹9000.00) exceeds your entitled limit (₹7500.00) in Accommodation 1.",
        },
        {"kind": "field", "section": "transport", "row": 1, "message": "Departure Time is missing for Main Tour"},
    ]
    items = review(claim([outbound(), back(), *mumbai_cabs()], [hotel()]), formProblems=form_problems)
    assert [i["kind"] for i in items] == ["formOnly"]
    assert items[0]["blocking"] is True
    assert re.search(r"entitled limit", items[0]["question"])


# ── leave ──


# TS: 'the leave question is asked until answered, and not when leave is already in the claim'
def test_the_leave_question():
    assert "askLeave" in kinds(claim([outbound(), back()], [hotel()]), leave_answered=False)
    with_leave = claim([outbound(), back()], [hotel()], {"startDate": "2026-10-07", "endDate": "2026-10-07"})
    assert "askLeave" not in kinds(with_leave, leave_answered=False)


# ── order and identity ──


# TS: 'the route comes first, then details, dates, days and nights, bills, the form, and leave last'
def test_the_order_of_the_questions():
    c = claim([j("Noida", "Mumbai", "2026-10-05", "", "2026-10-05", "13:00", "air", uploadedFiles=[])], [])
    order = kinds(c, leave_answered=False)
    assert order[0] == "noReturn"
    assert order.index("missingField") < order.index("ticketMissing")
    assert order[-1] == "askLeave"


# TS: 'ids stay the same while that part of the claim does not change, and an item goes once it is fixed'
def test_ids_are_stable_and_a_fixed_item_goes():
    one = claim([outbound()], [hotel()])
    a = [i["id"] for i in review(one)]
    b = [i["id"] for i in review(one)]
    assert a == b
    fixed = review(claim([outbound(), back()], [hotel()]))
    assert not any(i["kind"] == "noReturn" for i in fixed)


# ── going through the review ──


# TS: 'review loop: asks each item in turn; confirmed items go; a blocking item cannot be confirmed away'
def test_review_loop_asks_each_item_in_turn():
    c = claim([j("Noida", "Mumbai", "2026-10-05", "", "2026-10-05", "13:00")], [hotel()])
    items = review(c, leaveAnswered=False)
    confirmed: set[str] = set()
    deferred: set[str] = set()
    first = next_review_step(items, confirmed, deferred)
    assert first["type"] == "ask"
    assert first["item"]["kind"] == "noReturn"
    # "no, I came back by office car"
    settle(items[0], "confirm", confirmed, deferred)
    second = next_review_step(items, confirmed, deferred)
    assert second["type"] == "ask" and second["item"]["kind"] == "missingField"
    # a missing detail can't be confirmed away: still asked
    settle(second["item"], "confirm", confirmed, deferred)
    assert next_review_step(items, confirmed, deferred)["item"]["kind"] == "missingField"
    # "I'll fill it on the form": no longer asked, but the claim is blocked until the form has it
    settle(second["item"], "later", confirmed, deferred)
    step = next_review_step(items, confirmed, deferred)
    while step["type"] == "ask":
        settle(step["item"], "confirm", confirmed, deferred)
        step = next_review_step(items, confirmed, deferred)
    assert step["type"] == "blocked"
    assert step["blocking"][0]["kind"] == "missingField"


# TS: 'review loop: once everything is fixed or confirmed, the review is done'
def test_review_loop_done():
    items = review(claim([outbound()], [hotel()]), leaveAnswered=False)
    confirmed: set[str] = set()
    deferred: set[str] = set()
    for i in items:
        settle(i, "confirm", confirmed, deferred)
    assert next_review_step(items, confirmed, deferred)["type"] == "done"
    # the form no longer reports a fixed problem, so a deferred item that is fixed lets the claim through
    assert next_review_step([], set(), {"form:overEntitlement:accommodation:1"})["type"] == "done"


# ── cabs, taxis and other conveyance around the main journeys ──


def conveyance(c):
    return [i for i in review(c) if i["kind"].startswith("conveyance") or i["kind"] == "localConveyance"]


# TS: 'no cabs at all: asked for home → airport, airport → hotel, getting around, hotel → airport and airport → home'
def test_no_cabs_at_all():
    items = conveyance(claim([outbound(), back()], [hotel()]))
    assert [i["kind"] for i in items] == [
        "conveyanceBefore",
        "conveyanceAfter",
        "conveyanceBefore",
        "conveyanceAfter",
        "localConveyance",
    ]
    assert re.search(
        r"How did you get from home to the airport in Noida for your flight on 05 Oct 2026\? If you took a cab, taxi "
        r"or auto, do you have the bill\?",
        items[0]["question"],
    )
    assert re.search(
        r"After your flight reached Mumbai on 05 Oct 2026, how did you get to your hotel\?", items[1]["question"]
    )
    assert re.search(r"from your hotel to the airport in Mumbai", items[2]["question"])
    assert re.search(r"how did you get to home\?", items[3]["question"])
    assert re.search(
        r"While you were in Mumbai \(05 Oct 2026 – 09 Oct 2026\), did you take any cabs, taxis or autos to get around",
        items[4]["question"],
    )
    assert all(not i["blocking"] and i["yes"] == "upload" and i["no"] == "confirm" for i in items)


# TS: 'every cab in the claim answers its own question, and only that one'
def test_every_cab_answers_its_own_question():
    all_cabs = mumbai_cabs()
    assert conveyance(claim([outbound(), back(), *all_cabs], [hotel()])) == []
    # drop each cab in turn: exactly the question it answered comes back
    expected = ["conveyanceBefore", "conveyanceAfter", "localConveyance", "conveyanceBefore", "conveyanceAfter"]
    for k in range(len(all_cabs)):
        left = conveyance(claim([outbound(), back(), *(c for x, c in enumerate(all_cabs) if x != k)], [hotel()]))
        assert [i["kind"] for i in left] == [expected[k]], f"without cab {k + 1}"


# TS: 'a cab typed in later (added at the end of the form) still counts where its time puts it'
def test_a_cab_typed_in_later_counts_where_its_time_puts_it():
    c = claim([outbound(), back(), cab("Noida Airport", "Noida", "2026-10-09", "20:30", "21:30")], [hotel()])
    assert not any(i["kind"] == "conveyanceAfter" and re.search("home", i["question"]) for i in conveyance(c))
    assert not any(i["kind"] in ("routeBreak", "noReturn") for i in review(c))


# TS: 'an office car, own car or auto counts as conveyance too (any local mode), with no bill needed'
def test_an_office_car_counts_as_conveyance():
    office_car = j(
        "Noida", "Noida Airport", "2026-10-05", "08:00", "2026-10-05", "09:30", "official-entitled", uploadedFiles=[]
    )
    c = claim([outbound(), back(), office_car], [hotel()])
    assert not any(i["kind"] == "conveyanceBefore" and re.search("home", i["question"]) for i in conveyance(c))
    assert not any(i["kind"] == "ticketMissing" and i.get("row") == 3 for i in review(c))


# TS: 'a cab with no bill attached is asked about like any ticket'
def test_a_cab_with_no_bill_is_asked_about():
    cabs = [{**r, "uploadedFiles": []} if k == 0 else r for k, r in enumerate(mumbai_cabs())]
    t = find(review(claim([outbound(), back(), *cabs], [hotel()])), "ticketMissing")
    assert t
    assert re.search(r"Noida → Noida Airport", t["question"])


# TS: 'a cab hours away from the flight does not count as getting to the airport'
def test_a_cab_hours_away_from_the_flight_does_not_count():
    c = claim([outbound(), back(), cab("Noida", "Noida Market", "2026-10-04", "18:00", "18:30")], [hotel()])
    assert any(i["kind"] == "conveyanceBefore" and re.search("from home", i["question"]) for i in conveyance(c))


# TS: 'a change of station or airport between two main journeys is one transfer question'
def test_a_change_of_station_is_one_transfer_question():
    c = claim(
        [
            j("Noida", "Mumbai", "2026-10-05", "06:00", "2026-10-05", "08:00"),
            j("Mumbai CST", "Pune", "2026-10-05", "11:00", "2026-10-05", "14:00", "rail"),
            j("Pune", "Noida", "2026-10-05", "20:00", "2026-10-05", "22:00"),
        ]
    )
    items = conveyance(c)
    transfers = [i for i in items if i["kind"] == "conveyanceTransfer"]
    assert len(transfers) == 2
    assert re.search(
        r"from the airport in Mumbai to the railway station in Mumbai CST for your next train", transfers[0]["question"]
    )
    # the transfer is not asked again as "after the flight" and "before the train"
    assert not any(i["kind"] == "conveyanceAfter" and i["row"] == 1 for i in items)
    assert not any(i["kind"] == "conveyanceBefore" and i["row"] == 2 for i in items)


# TS: 'a tight connection at the same airport needs no conveyance; a cab between two journeys answers the transfer'
def test_a_tight_connection_needs_no_conveyance():
    connection = claim(
        [
            j("Noida", "Mumbai", "2026-10-05", "06:00", "2026-10-05", "08:00"),
            j("Mumbai", "Goa", "2026-10-05", "09:30", "2026-10-05", "10:45"),
            j("Goa", "Noida", "2026-10-05", "20:00", "2026-10-05", "22:30"),
        ]
    )
    assert not any(i["kind"] == "conveyanceTransfer" and i["row"] == 2 for i in conveyance(connection))
    with_cab = claim(
        [
            j("Noida", "Mumbai", "2026-10-05", "06:00", "2026-10-05", "08:00"),
            cab("Mumbai Airport", "Mumbai CST", "2026-10-05", "08:30", "09:45"),
            j("Mumbai CST", "Pune", "2026-10-05", "11:00", "2026-10-05", "14:00", "rail"),
        ]
    )
    assert not any(i["kind"] == "conveyanceTransfer" for i in conveyance(with_cab))


# TS: 'a same-day trip has no "getting around" question; a tour made only of taxi rides has no airport questions'
def test_same_day_trip_and_taxi_only_tour():
    same_day = claim([outbound(), j("Mumbai", "Noida", "2026-10-05", "18:00", "2026-10-05", "20:10")])
    assert not any(i["kind"] == "localConveyance" for i in conveyance(same_day))
    by_taxi = claim(
        [
            j("Noida", "Agra", "2026-10-05", "07:00", "2026-10-05", "11:00", "public-transport"),
            j("Agra", "Noida", "2026-10-06", "15:00", "2026-10-06", "19:00", "public-transport"),
        ],
        [s("Agra", "2026-10-05", "12:00", "2026-10-06", "11:00")],
    )
    assert conveyance(by_taxi) == []


# TS: 'a one-way claim asks for the first cab and the ride to the hotel, and leaves the way back to the return
# question'
def test_a_one_way_claim_asks_for_the_first_cab_and_the_ride_to_the_hotel():
    items = review(claim([outbound()], [hotel()]))
    assert any(i["kind"] == "noReturn" for i in items)
    assert [i["kind"] for i in conveyance(claim([outbound()], [hotel()]))] == ["conveyanceBefore", "conveyanceAfter"]


# TS: 'a train arriving the next morning: the ride onward is asked for the arrival day'
def test_a_train_arriving_the_next_morning():
    c = claim(
        [
            j("Noida", "Mumbai", "2026-10-05", "20:00", "2026-10-06", "08:00", "rail"),
            j("Mumbai", "Noida", "2026-10-09", "18:00", "2026-10-10", "06:00", "rail"),
        ],
        [s("Mumbai", "2026-10-06", "09:00", "2026-10-09", "12:00")],
    )
    after = find(conveyance(c), "conveyanceAfter")
    assert re.search(
        r"After your train reached Mumbai on 06 Oct 2026, how did you get to your hotel\?", after["question"]
    )


# ── what is missing, in one plain sentence ──


def short(c, t=tour):
    return missing_in_short([i for i in review(c, t) if i["kind"] not in ("askLeave", "amountMissing", "formOnly")])


# TS: 'one-way draft with no cabs: "Missing Return journey ticket, cabs/taxis and other conveyance bills for both
# onward and return journeys."'
def test_short_one_way_draft_with_no_cabs():
    assert (
        short(claim([outbound()], [hotel()]))
        == "Missing Return journey ticket, cabs/taxis and other conveyance bills for both onward and return journeys."
    )


# TS: 'round trip with no cabs: both onward and return, plus local travel'
def test_short_round_trip_with_no_cabs():
    assert short(claim([outbound(), back()], [hotel()])) == (
        "Missing Cabs/taxis and other conveyance bills for both onward and return journeys, cab/taxi bills for local "
        "travel in Mumbai."
    )


# TS: 'cabs only missing on one side say which side'
def test_short_cabs_missing_on_one_side():
    cabs = mumbai_cabs()
    onward_only = claim([outbound(), back(), cabs[2], cabs[3], cabs[4]], [hotel()])
    assert short(onward_only) == "Missing Cabs/taxis and other conveyance bills for the onward journey."
    return_only = claim([outbound(), back(), cabs[0], cabs[1], cabs[2]], [hotel()])
    assert short(return_only) == "Missing Cabs/taxis and other conveyance bills for the return journey."


# TS: 'one-way draft whose onward cabs are in: still needs the return ticket and its cabs'
def test_short_one_way_draft_with_onward_cabs():
    cabs = mumbai_cabs()
    assert (
        short(claim([outbound(), cabs[0], cabs[1]], [hotel()]))
        == "Missing Return journey ticket, cabs/taxis and other conveyance bills for the return journey."
    )


# TS: 'a complete tour has nothing missing (empty sentence)'
def test_short_complete_tour():
    assert short(claim([outbound(), back(), *mumbai_cabs()], [hotel()])) == ""


# TS: 'the sentence names transfers, stays, days, files and details in plain words'
def test_short_names_transfers_stays_days_files_and_details():
    transfer = claim(
        [
            j("Noida", "Mumbai", "2026-10-05", "06:00", "2026-10-05", "08:00"),
            j("Mumbai CST", "Pune", "2026-10-05", "11:00", "2026-10-05", "14:00", "rail"),
            j("Pune", "Noida", "2026-10-05", "20:00", "2026-10-05", "22:00"),
        ]
    )
    assert re.search(r"cab/taxi bills for going between stations and airports", short(transfer))
    gaps = claim(
        [{**outbound(), "uploadedFiles": []}, {**back(), "TimeOfTravelSource": ""}, *mumbai_cabs()],
        [s("Mumbai", "2026-10-05", "14:00", "2026-10-07", "12:00", billFiles=[])],
    )
    text = short(gaps)
    assert re.search(r"hotel stay for the nights of 07 Oct 2026 – 08 Oct 2026 in Mumbai", text)
    assert re.search(r"ticket or bill file for journey 1 \(Noida → Mumbai\)", text)
    assert re.search(r"hotel bill for stay 1 \(Mumbai\)", text)
    assert re.search(r"1 detail \(dates, times or places\) to fill in", text)
    # never the old shorthand
    assert not re.search(r"both ends|airport cabs|\bleg\b", text, re.IGNORECASE)


# ── places that are not places ──


# TS: 'a bill label in From or To is the first thing asked, and names the value and why'
def test_a_bill_label_in_from_or_to_is_asked_first():
    c = claim([{**outbound(), "destination": "Total Net Amount"}, back(), *mumbai_cabs()], [hotel()])
    items = review(c)
    assert items[0]["kind"] == "oddPlace"
    assert re.search(
        r"Journey 1 has “Total Net Amount” as its destination, which doesn't look like a place \(it looks like a label "
        r"from the bill, not a place\)\. Where did journey 1 end\?",
        items[0]["question"],
    )
    assert items[0]["blocking"] is False
    assert "newJourney" not in items[0]
    assert re.match(
        r"Missing A place name to correct \(misread from the bill\)",
        missing_in_short([i for i in items if i["kind"] != "amountMissing"]),
    )


# TS: 'an amount as the starting point, the same place at both ends, and an odd stay city are all asked about'
def test_amounts_same_places_and_odd_stay_cities_are_asked_about():
    amount = find(review(claim([{**outbound(), "source": "₹1,240.00"}, back()])), "oddPlace")
    assert re.search(
        r"starting point, which doesn't look like a place \(it looks like an amount from the bill\)\. Where did "
        r"journey 1 start from\?",
        amount["question"],
    )
    same = find(review(claim([{**outbound(), "destination": "Noida"}, back()])), "oddPlace")
    assert re.search(r"goes from Noida to Noida, the same place", same["question"])
    city = find(review(claim([outbound(), back()], [{**hotel(), "location": "GSTIN 07AAACD1234F1Z5"}])), "oddPlace")
    assert re.search(r"Stay 1 has “GSTIN 07AAACD1234F1Z5” as its city", city["question"])


# TS: 'real station and airport names are never questioned'
def test_real_station_and_airport_names_are_never_questioned():
    c = claim(
        [
            {
                **outbound(),
                "source": "Hazrat Nizamuddin (NZM)",
                "destination": "T2 Chhatrapati Shivaji Maharaj International Airport",
            },
            back(),
        ],
        [hotel()],
    )
    assert not any(i["kind"] == "oddPlace" for i in review(c))


# ── timeOverlap.test.ts ──


# TS: timeOverlap.test.ts 'the review asks about the overlap as a must-fix, and the short summary names it'
def test_the_review_asks_about_the_overlap_as_a_must_fix():
    items = review_claim({"state": {**initial_claim_state(), "transportationRows": ambala_trip()}, "tour": None,
                          "leaveAnswered": True})  # fmt: skip
    ov = [i for i in items if i["kind"] == "overlap"]
    assert len(ov) == 1
    assert ov[0]["blocking"] is True
    assert ov[0]["row"] == 4
    assert re.search(r"journeys 3 and 4 \(they overlap\)", missing_in_short(items))


# ── our own: exact texts (checked against the TypeScript under Node) ──


def test_exact_items_of_a_one_way_claim():
    items = review(claim([outbound()], [hotel()]))
    assert items == [
        {
            "id": "noReturn:mumbai",
            "kind": "noReturn",
            "newJourney": True,
            "blocking": False,
            "resolutions": ["upload", "chat", "confirm"],
            "yes": "upload",
            "no": "confirm",
            "section": "transport",
            "fact": "The last journey in the claim ends in Mumbai, and the tour started from Noida: there is no "
            "journey back.",
            "question": "There are no return journey tickets/bills uploaded (Mumbai → Noida). Do you have them?",
        },
        {
            "id": "conveyanceBefore:0:noida:2026-10-05",
            "kind": "conveyanceBefore",
            "blocking": False,
            "resolutions": ["upload", "chat", "confirm"],
            "yes": "upload",
            "no": "confirm",
            "section": "transport",
            "row": 1,
            "fact": "There is no cab, taxi or other conveyance in the claim for getting to the airport in Noida for "
            "journey 1 (Noida → Mumbai) by air on 05 Oct 2026.",
            "question": "How did you get from home to the airport in Noida for your flight on 05 Oct 2026? If you took "
            "a cab, taxi or auto, do you have the bill?",
            "newJourney": True,
            "side": "onward",
        },
        {
            "id": "conveyanceAfter:0:mumbai:2026-10-05",
            "kind": "conveyanceAfter",
            "blocking": False,
            "resolutions": ["upload", "chat", "confirm"],
            "yes": "upload",
            "no": "confirm",
            "section": "transport",
            "row": 1,
            "fact": "There is no cab, taxi or other conveyance in the claim for getting from the airport in Mumbai to "
            "the hotel or office after journey 1 (Noida → Mumbai) by air on 05 Oct 2026.",
            "question": "After your flight reached Mumbai on 05 Oct 2026, how did you get to your hotel? If you took a "
            "cab, taxi or auto, do you have the bill?",
            "newJourney": True,
            "side": "onward",
        },
    ]


def test_notes_for_the_ai_model():
    items = review(claim([j("Noida", "Mumbai", "2026-10-05", "", "2026-10-05", "13:00")], [hotel()]))
    note = review_question_note(items[1], items[1:], again=True)
    assert note.startswith("This is still not sorted out. Before the claim can be submitted, it is checked for ")
    assert f"{len(items) - 1} things are left to check (1 of them must be fixed before submitting).\n" in note
    assert "This must be fixed before the claim can be submitted. How it can be settled: they can tell you the " in note
    assert note.endswith(f"Ask the employee exactly this question, and only this one: {items[1]['question']}")
    assert "This is the last thing to check." in review_question_note(items[0], [items[0]])
    assert "or they can confirm the claim is right as it is" in review_question_note(items[0], [items[0]])
    assert review_blocked_note([items[1]]) == (
        "Everything else is checked, but the claim form will not accept the claim until these are fixed on the form:\n"
        f'• {items[1]["fact"]}\nAsk the employee to fix them on the form, then say "submit" again.'
    )
    assert review_done_note("C.", True).endswith("Ask whether to submit the claim now.")
    assert review_done_note("C.", False).endswith('they can say "submit" when they are ready.')
    assert review_settled_note(items[1], "later").endswith(" The claim cannot be submitted until it is fixed.")
    assert review_settled_note(items[0], "confirm") == (
        f"The employee confirmed this is right as it is: {items[0]['fact']} It is left as it is."
    )
    assert review_settled_note(items[0], "upload").startswith("The employee has it. Ask them to attach it with the 📎")


def test_a_cab_typed_in_later_is_judged_in_time_order():
    """the journeys are taken in time order, rows with no date last, ties by their place in the form"""
    late_cab = cab("Noida", "Noida Airport", "2026-10-05", "08:00", "09:30")
    no_date = j("Pune", "Goa", "", "", "", "", "rail")
    items = review(claim([outbound(), back(), no_date, late_cab], [hotel()]))
    # the cab answers the first flight's "getting to the airport", even though it is the last row
    assert not any(i["id"].startswith("conveyanceBefore:0:") for i in items)
    # the dateless rail journey is last in time: the route breaks after the return, and the trip ends in Goa
    assert find(items, "routeBreak")["id"] == "routeBreak:2:noida:pune"
    assert find(items, "noReturn")["id"] == "noReturn:goa"
