"""Tour matching (BIL-30..37). The first four cases are ported from Tour_React tests/claimChatbot/tourMatch.test.ts."""

from __future__ import annotations

from datetime import date

from tourclaim_agent.domain.claim import Origin, StayRow, TransportRow
from tourclaim_agent.domain.models import TourSummary
from tourclaim_agent.drafting.tour_match import (
    VerdictKind,
    candidates_for,
    judge,
    norm,
    rank_tours,
    same_place,
    tour_window,
    triage,
)


def make_tour(tour_id: int, source: str, destination: str, start: str | None, end: str | None = None, days: int = 0):
    return TourSummary(
        tourId=tour_id,
        source=source,
        destination=destination,
        tourStartDate=start,
        tantetiveEndDate=end,
        totalDays=days,
        statusId=2,
    )


def ticket(source: str, destination: str, day: str | None) -> TransportRow:
    d = date.fromisoformat(day) if day else None
    return TransportRow(
        key="t", origin=Origin.BILL, source=source, destination=destination, depart_date=d, arrive_date=d
    )


def stay(location: str, check_in: str | None, check_out: str | None) -> StayRow:
    return StayRow(
        key="s",
        origin=Origin.BILL,
        location=location,
        check_in_date=date.fromisoformat(check_in) if check_in else None,
        check_out_date=date.fromisoformat(check_out) if check_out else None,
    )


# The tour from the React report: New Delhi → Pune, 16–17 Jan 2026.
PUNE = make_tour(5, "New Delhi, Delhi", "Pune", "2026-01-16", "2026-01-17", 2)


class TestPortedFromReact:
    def test_ticket_to_another_city_the_day_after_the_tour_is_not_this_tour(self):
        t = triage([ticket("New Delhi (DEL)", "Jaipur (JAI)", "2026-01-18")], [PUNE])
        assert t.groups == {} and len(t.no_tour) == 1

    def test_a_day_either_side_counts_for_the_trip_itself(self):
        out = triage([ticket("New Delhi (DEL)", "Pune (PNQ)", "2026-01-15")], [PUNE])
        assert list(out.groups) == [5]
        back = triage([ticket("Pune (PNQ)", "New Delhi (DEL)", "2026-01-18")], [PUNE])
        assert list(back.groups) == [5]

    def test_a_bill_inside_the_tour_dates_fits_even_to_another_city(self):
        assert list(triage([ticket("Pune", "Mumbai", "2026-01-17")], [PUNE]).groups) == [5]

    def test_hotel_in_another_city_starting_the_day_after_the_tour_is_not_this_tour(self):
        t = triage([stay("Jaipur, Rajasthan", "2026-01-18", "2026-01-20")], [PUNE])
        assert t.groups == {}


class TestBasics:
    def test_window_uses_end_date_or_number_of_days(self):
        assert tour_window(PUNE) == (date(2026, 1, 16), date(2026, 1, 17))
        assert tour_window(make_tour(1, "A", "B", "2026-03-01", None, 4)) == (date(2026, 3, 1), date(2026, 3, 4))
        assert tour_window(make_tour(1, "A", "B", "2026-03-01", None, 0)) == (date(2026, 3, 1), date(2026, 3, 1))
        assert tour_window(make_tour(1, "A", "B", None)) is None

    def test_places(self):
        assert norm("  New-Delhi (DEL) ") == "new delhi del"
        assert same_place("New Delhi", "delhi") and same_place("delhi", "New Delhi")
        assert not same_place("Goa", "Go") and not same_place("Pune", "Mumbai")

    def test_score_is_date_fit_twice_plus_route_hits(self):
        [match] = rank_tours([ticket("New Delhi", "Pune", "2026-01-16")], [PUNE])
        assert match.date_fit == 1.0 and match.score == 4.0


class TestJudging:
    MUMBAI_A = make_tour(1, "Noida", "Mumbai", "2026-10-05", "2026-10-08")
    PUNE_B = make_tour(2, "Delhi", "Pune", "2026-10-20", "2026-10-22")
    MUMBAI_C = make_tour(3, "Noida", "Mumbai", "2026-10-06", "2026-10-09")

    def test_no_dates_and_no_places_is_neutral(self):
        assert judge(ticket("", "", None), [self.MUMBAI_A, self.PUNE_B]).kind is VerdictKind.NEUTRAL

    def test_clear_fit_is_assigned(self):
        verdict = judge(ticket("Delhi", "Pune", "2026-10-20"), [self.MUMBAI_A, self.PUNE_B])
        assert verdict.kind is VerdictKind.ASSIGNED and verdict.tour.tour_id == 2

    def test_dates_outside_every_tour_mean_no_tour(self):
        verdict = judge(ticket("Noida", "Mumbai", "2026-07-05"), [self.MUMBAI_A, self.PUNE_B])
        assert verdict.kind is VerdictKind.NO_TOUR

    def test_two_tours_fitting_equally_is_neutral(self):
        verdict = judge(ticket("Mumbai", "Mumbai Airport", None), [self.MUMBAI_A, self.MUMBAI_C])
        assert verdict.kind is VerdictKind.NEUTRAL

    def test_tour_without_dates_does_not_stop_dates_ruling_out(self):
        undated = make_tour(9, "Agra", "Jaipur", None)
        verdict = judge(ticket("Noida", "Mumbai", "2026-07-05"), [self.MUMBAI_A, undated])
        assert verdict.kind is VerdictKind.NO_TOUR


class TestTriage:
    A = TestJudging.MUMBAI_A
    B = TestJudging.PUNE_B
    C = TestJudging.MUMBAI_C

    def test_mixed_upload_goes_to_two_tours(self):
        rows = [ticket("Noida", "Mumbai", "2026-10-05"), stay("Mumbai", "2026-10-05", "2026-10-08")]
        rows.append(ticket("Delhi", "Pune", "2026-10-20"))
        t = triage(rows, [self.A, self.B])
        assert {k: len(v) for k, v in t.groups.items()} == {1: 2, 2: 1}

    def test_neutral_rows_follow_the_single_other_tour(self):
        taxi = ticket("", "", None)
        t = triage([ticket("Delhi", "Pune", "2026-10-20"), taxi], [self.A, self.B])
        assert t.tour_of(taxi) == 2 and t.unsure == []

    def test_neutral_rows_go_to_the_claim_in_progress(self):
        taxi = ticket("", "", None)
        assert triage([taxi], [self.A, self.B], claim_tour_id=1).tour_of(taxi) == 1

    def test_neutral_rows_go_to_the_only_claimable_tour(self):
        taxi = ticket("", "", None)
        assert triage([taxi], [self.A]).tour_of(taxi) == 1

    def test_otherwise_neutral_rows_are_unsure(self):
        taxi = ticket("Mumbai", "Mumbai Airport", None)
        t = triage([taxi], [self.A, self.C])
        assert t.unsure == [taxi] and t.groups == {}
        assert {tour.tour_id for tour in candidates_for(t.unsure, [self.A, self.B, self.C])} == {1, 2, 3}

    def test_claim_in_progress_does_not_narrow_matching(self):
        t = triage([ticket("Delhi", "Pune", "2026-10-20")], [self.A, self.B], claim_tour_id=1)
        assert list(t.groups) == [2]

    def test_no_claimable_tours(self):
        t = triage([ticket("Delhi", "Pune", "2026-10-20")], [])
        assert t.groups == {} and len(t.no_tour) + len(t.unsure) == 1
