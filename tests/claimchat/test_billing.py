"""claimchat/billing.py: bill amounts and the claim form's totals of a saved draft (owner requirement 2026-11-06)."""

from __future__ import annotations

from decimal import Decimal

from tests.fakes import raise_draft
from tourclaim_agent.claimchat.billing import bill_amount, draft_amounts, money, new_ledger, record_read


def test_bill_amount_is_claimed_amount_with_tax_or_nothing():
    assert bill_amount(6810.5) == Decimal("6810.5")
    assert bill_amount("₹ 10,080.00") == Decimal("10080.00")
    assert bill_amount(None) is None
    assert bill_amount("") is None
    assert bill_amount("n/a") is None
    assert bill_amount(0) is None
    assert bill_amount(-5) is None
    assert bill_amount(True) is None


def test_money_is_en_in_rupees():
    assert money(Decimal("1234567.5")) == "₹12,34,567.50"
    assert money(Decimal("0.005")) == "₹0.01"


def test_draft_totals_as_the_form_works_them_out():
    draft = raise_draft(
        504,
        journeys=[{"ClaimedAmount": 1200.555}, {"ClaimedAmount": "1,234"}, {"ClaimedAmount": None}, {}],
        stays=[{"ClaimedAmount": "5000.10"}, {"ClaimedAmount": "abc"}],
        da=[
            {"Date": "2026-09-01", "Amount": 1000, "IsApplicable": True},
            {"Date": "2026-09-02T00:00:00", "Amount": "700", "IsApplicable": True},
            {"Date": "2026-09-03", "Amount": 300, "IsApplicable": False},
            {"Date": "2026-09-04", "Amount": 300, "IsApplicable": "true"},  # not `=== true`
        ],
        leave=[
            # the leave section works the deduction out from the DA rows (any row in the range, applicable or not)
            {"LeaveStartDate": "2026-09-03T00:00:00", "LeaveEndDate": "2026-09-02", "DADeductable": True, "Amount": 1},
            {"LeaveStartDate": "2026-09-01", "LeaveEndDate": "2026-09-01", "DADeductable": False, "Amount": 999},
        ],
    )
    # parseFloat("1,234") = 1 (the form's own reading); 1200.555 + 1 = 1201.555 -> toFixed(2)
    assert draft_amounts(draft) == {
        "transportation": Decimal("1201.56"),
        "accommodation": Decimal("5000.10"),
        "da": Decimal("1700.00"),
        "leave": Decimal("1000.00"),
        "totalWithDa": Decimal("6901.66"),
        "totalWithoutDa": Decimal("6201.66"),
    }


def test_without_da_rows_the_agent_does_not_work_out_da():
    draft = raise_draft(
        504,
        journeys=[{"ClaimedAmount": 100}],
        leave=[{"LeaveStartDate": "2026-09-02", "LeaveEndDate": "2026-09-02", "DADeductable": True}],
    )
    amounts = draft_amounts(draft)
    assert amounts["da"] is None and amounts["leave"] is None and amounts["totalWithDa"] is None
    assert amounts["totalWithoutDa"] == Decimal("100.00")
    # no leave to deduct: the total with DA is known as soon as the draft has DA rows
    plain = draft_amounts(
        raise_draft(504, journeys=[{"ClaimedAmount": 100}], da=[{"Amount": 50, "IsApplicable": True}])
    )
    assert plain["leave"] == Decimal(0) and plain["totalWithDa"] == Decimal("150.00")


def test_a_multi_leg_ticket_is_one_bill():
    result = {
        "tourDetails": {
            "from": "Noida",
            "to": "Pune",
            "startDate": "2026-08-20",
            "amountDetails": {"claimedAmountWithTax": 4200, "sourceFileName": "ticket.pdf"},
            "tourLegs": [
                {
                    "from": "Pune",
                    "to": "Goa",
                    "departureDate": "2026-08-21",
                    "amountDetails": {"claimedAmountWithTax": 4200, "sourceFileName": "ticket.pdf"},
                }
            ],
        },
        "accommodationDetails": {"entries": []},
    }
    ledger = new_ledger()
    record_read(
        ledger,
        result=result,
        bills=[{"file": {"name": "ticket.pdf", "documentId": 7}, "hash": "h"}],
        rows=[],
        names=["ticket.pdf"],
        fates={"ticket.pdf": {"kind": "saved"}},
        tour_of=lambda name: None,
        tour_name=str,
        skipped_hashes={},
    )
    (bill,) = ledger["bills"]
    assert (bill["kind"], bill["what"], bill["amount"], bill["documentId"]) == ("journey", "Noida → Goa", "4200", 7)
