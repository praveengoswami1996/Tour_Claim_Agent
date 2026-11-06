"""Billing questions and "nothing about missing information" (owner requirements 2026-11-06), through HTTP.

The amounts are worked out in code (claimchat/billing.py) and given to the model as the BILLS section of a typed
turn's narrate message: the tests check the exact BILLS sent to the fake gateway. Also checked: nothing the chat sends
the model (notes, data, open questions, PAGE) on the Raise Claim page or inside the form is about missing information.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.claimchat.test_flow_raise_form import form_chat
from tests.claimchat.test_flow_raise_page import HELLO, brief, finish, gap_words, page
from tests.fakes import (
    FLIGHT_BACK,
    FLIGHT_OUT,
    GATEWAY_BASE,
    HOTEL,
    PDF,
    FakeBackend,
    raise_draft,
    renamed,
    tour,
    with_claimed,
)
from tourclaim_agent.claimchat.flows.raise_claim import BILLS_RULE, UNDERSTAND_RULES
from tourclaim_agent.main import create_app

HEAD = (
    "All amounts are in ₹. A bill's amount is its total with tax as read from the bill; each bill is counted once "
    "(duplicates are left out)."
)
DRAFTS_HEAD = (
    "SAVED DRAFTS (each claim's totals as the claim form works them out from the saved draft: Total Transportation "
    "Amount + Total Accommodation Amount + Total DA Amount, less the DA of leave days marked DA deductable; DA as "
    "saved in the draft):"
)
MUMBAI = "tour #503 Noida → Mumbai (20 Aug 2026 – 22 Aug 2026)"
CHENNAI = "tour #504 Noida → Chennai (01 Sep 2026 – 03 Sep 2026)"
# the draft the chat saves for tour 503 from the bills below (the claim form's amounts: the flights' ClaimedAmount is
# the OCR's totalClaimedAmount / claimedAmount, which these bills do not give; the hotel's is claimedAmountWithTax)
MUMBAI_DRAFT = (
    f"• {MUMBAI}: transportation ₹0.00, accommodation ₹10,080.00, DA could not be determined (no DA saved in the "
    "draft), total with DA could not be determined, total without DA ₹10,080.00."
)
CHENNAI_DRAFT = (
    f"• {CHENNAI}: transportation ₹2,000.50, accommodation ₹5,000.00, DA ₹1,700.00, leave deduction ₹700.00, total "
    "with DA ₹8,000.50, total without DA ₹7,000.50."
)


@pytest.fixture
def client(settings, backend: FakeBackend):
    settings.chat_gateway_url = GATEWAY_BASE
    settings = settings.model_copy(update={"enabled_write_endpoints": ["Claim/SaveRaiseClaimDraft"]})
    with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
        yield test_client


def bills_of(message: str) -> str:
    """The BILLS section of a typed turn's narrate message."""
    start = message.index("\n\nBILLS:\n") + len("\n\nBILLS:\n")
    end = message.index("\n\nNow reply to EMPLOYEE SAID only", start)
    return message[start:end]


def chennai_draft(backend: FakeBackend) -> None:
    """Tour 504 (Chennai) with a draft saved from the claim form: two journeys, one stay, DA for three days (one not
    applicable) and a day of leave with its DA deducted."""
    backend.outside_tours[1001].append(
        tour(504, 1001, kind_dest="Chennai", status_id=2, status="Tour Approved", start="2026-09-01")
    )
    backend.drafts[504] = raise_draft(
        504,
        journeys=[
            {"Source": "Noida", "Destination": "Chennai", "DateOfTravelSource": "2026-09-01", "ClaimedAmount": 1200.5},
            {"Source": "Chennai", "Destination": "Noida", "DateOfTravelSource": "2026-09-03", "ClaimedAmount": "800"},
        ],
        stays=[{"Location": "Chennai", "DateOfCheckIn": "2026-09-01", "ClaimedAmount": 5000}],
        da=[
            {"Date": "2026-09-01", "Amount": 1000, "IsApplicable": True},
            {"Date": "2026-09-02", "Amount": 700, "IsApplicable": True},
            {"Date": "2026-09-03", "Amount": 300, "IsApplicable": False},
        ],
        leave=[{"LeaveStartDate": "2026-09-02", "LeaveEndDate": "2026-09-02", "DADeductable": True, "Amount": 0}],
    )


def no_gaps(backend: FakeBackend) -> None:
    """Nothing sent to the model (but the understand calls, which only pick an action) is about missing information."""
    for call in backend.gateway_calls:
        if "\n\nEMPLOYEE'S MESSAGE:\n" not in call["message"]:
            assert not gap_words(call["message"]), call["message"]


def read_three_then_duplicates(chat, backend: FakeBackend) -> None:
    chat.attach(("out.pdf", PDF + b"1"), ("back.pdf", PDF + b"2"), ("hotel.pdf", PDF + b"h"))
    finish(backend, "out.pdf", with_claimed(FLIGHT_OUT, 6810.5))
    finish(backend, "back.pdf", FLIGHT_BACK)  # no claimedAmountWithTax: its amount can't be determined
    finish(backend, "hotel.pdf", with_claimed(HOTEL, 10080))
    chat.poll()
    # the next upload: an exact copy of out.pdf (skipped at upload) and another scan of the same flight
    chat.attach(("out-copy.pdf", PDF + b"1"), ("scan.pdf", PDF + b"3"))
    finish(backend, "scan.pdf", with_claimed(renamed(FLIGHT_OUT, "scan.pdf"), 6810.5))
    chat.poll()


BILLS_READ = (
    "BILLS READ IN THIS CHAT, by upload (oldest first):\n"
    "Upload 1:\n"
    f"• out.pdf (journey Delhi (Noida) → Mumbai, 20 Aug 2026, invoice AI-123, {MUMBAI}): ₹6,810.50\n"
    f"• back.pdf (journey Mumbai → Delhi (Noida), 22 Aug 2026, invoice AI-456, {MUMBAI}): amount could not be "
    "determined\n"
    f"• hotel.pdf (stay Harbour Inn in Mumbai, 20 Aug 2026, invoice HI-77, {MUMBAI}): ₹10,080.00\n"
    "Upload 2:\n"
    "• scan.pdf: duplicate of out.pdf, not counted\n"
    "• out-copy.pdf: duplicate of out.pdf, not counted\n"
    "LATEST UPLOAD (upload 2): 0 unique bills; no bill to count; 2 duplicates left out.\n"
    "ALL BILLS READ IN THIS CHAT: 3 unique bills; total ₹16,890.50 (of the 2 with an amount); 1 bill whose amount "
    "could not be determined, not included; 2 duplicates left out.\n"
    "HIGHEST BILL: ₹10,080.00 (hotel.pdf).\n"
    "LOWEST BILL: ₹6,810.50 (out.pdf).\n"
    "BY TOUR (bills read in this chat only, not the whole draft):\n"
    f"• {MUMBAI}: 3 unique bills; total ₹16,890.50 (of the 2 with an amount); 1 bill whose amount could not be "
    "determined, not included; 1 duplicate left out."
)


class TestOnThePage:
    def test_bill_totals_count_each_bill_once(self, client, backend):
        chat = page(client, backend)
        chat.open()
        read_three_then_duplicates(chat, backend)

        chat.say("What is the total of these uploaded bills?", {"action": "talk"})
        understand, narrate = chat.sent
        assert understand["system_prompt"].endswith(UNDERSTAND_RULES)
        assert '"What is the total of these uploaded bills?" -> talk' in understand["system_prompt"]
        assert narrate["system_prompt"].endswith(BILLS_RULE)
        assert bills_of(narrate["message"]) == "\n".join(
            [
                HEAD,
                BILLS_READ,
                DRAFTS_HEAD,
                MUMBAI_DRAFT,
                "ALL 1 DRAFT TOGETHER: transportation ₹0.00, accommodation ₹10,080.00, DA could not be determined, "
                "total with DA could not be determined, total without DA ₹10,080.00.",
            ]
        )
        assert narrate["message"].endswith(
            "nothing about the claim. A question about an amount or a total is answered only with the figures "
            "written in BILLS, exactly as written; never add, subtract or estimate a number yourself, and if the "
            "figure asked for is not in BILLS, say it could not be determined."
        )
        # the report itself stays the old ✅ New / ↩ Duplicate one, with nothing about missing information
        report = backend.gateway_calls[-3]["message"]
        assert report.startswith("NOTES:\n1. Your bill has been read:\n\n**↩ Duplicate (2)**\n• **scan.pdf**: ")
        assert "\n• **out-copy.pdf**: same file as one already uploaded, skipped" in report
        no_gaps(backend)

    def test_bills_are_given_only_with_a_question(self, client, backend):
        chat = page(client, backend)
        chat.open()
        read_three_then_duplicates(chat, backend)
        chat.say("thanks", {"action": "talk"})  # "thanks" is not about the claim: no CLAIM, PAGE or BILLS
        assert "\n\nBILLS:\n" not in chat.sent[-1]["message"]
        chat.say("start over", {"action": "start_over"})  # not "talk": nothing about amounts was asked
        assert "\n\nBILLS:\n" not in chat.sent[-1]["message"]

    def test_drafts_totals_with_and_without_da(self, client, backend):
        chennai_draft(backend)
        backend.drafts[503] = raise_draft(
            503,
            draft_id=8002,
            journeys=[{"Source": "Noida", "Destination": "Mumbai", "ClaimedAmount": 4500.25}],
        )
        chat = page(client, backend)
        events = chat.open()
        # the greeting with saved drafts: nothing about what they lack
        assert chat.sent[-1]["message"] == brief(
            f"{HELLO} You have **2 saved drafts**. You can continue one, or upload new bills."
        )
        chat.say("What is the total claim amount of all my drafts?", {"action": "talk"})
        typed = chat.sent[-1]["message"]
        mumbai = (
            f"• {MUMBAI}: transportation ₹4,500.25, accommodation ₹0.00, DA could not be determined (no DA saved in "
            "the draft), total with DA could not be determined, total without DA ₹4,500.25."
        )
        assert bills_of(typed) == "\n".join(
            [
                HEAD,
                "BILLS READ IN THIS CHAT: none yet.",
                DRAFTS_HEAD,
                CHENNAI_DRAFT,
                mumbai,
                "ALL 2 DRAFTS TOGETHER: transportation ₹6,500.75, accommodation ₹5,000.00, DA could not be "
                "determined, total with DA could not be determined, total without DA ₹11,500.75.",
            ]
        )
        # what the drafts hold goes to the model without what they lack
        assert '"route":"Noida → Chennai"' in typed and '"missing"' not in typed
        # each draft is read once per turn
        reads = [c for c in backend.calls if c.endswith("Claim/GetRaiseClaimDraft")]
        chat.say("total for my Chennai tour without DA", {"action": "talk"})
        assert CHENNAI_DRAFT in bills_of(chat.sent[-1]["message"])
        assert len([c for c in backend.calls if c.endswith("Claim/GetRaiseClaimDraft")]) == len(reads) + 2
        assert events  # the chips of the greeting
        no_gaps(backend)

    def test_a_draft_that_cannot_be_read(self, client, backend):
        chennai_draft(backend)
        chat = page(client, backend)
        chat.open()
        backend.fail_next("Claim/GetRaiseClaimDraft", 500)
        chat.say("total of my drafts?", {"action": "talk"})
        assert bills_of(chat.sent[-1]["message"]).endswith(
            f"{DRAFTS_HEAD}\n• {CHENNAI}: the draft could not be read, so its amounts could not be determined."
        )


class TestInTheForm:
    def test_highest_bill_and_totals_in_the_form(self, client, backend):
        chennai_draft(backend)
        chat = form_chat(client, backend)
        chat.open()
        chat.attach(("out.pdf", PDF + b"1"), ("hotel.pdf", PDF + b"h"))
        finish(backend, "out.pdf", with_claimed(FLIGHT_OUT, "6810.50"))
        finish(backend, "hotel.pdf", with_claimed(HOTEL, 0))  # 0: not a usable amount
        chat.poll()
        chat.say("Which bill has the highest amount?", {"action": "talk"})
        narrate = chat.sent[-1]
        assert narrate["system_prompt"].endswith(BILLS_RULE)
        assert bills_of(narrate["message"]) == "\n".join(
            [
                HEAD,
                "BILLS READ IN THIS CHAT, by upload (oldest first):",
                "Upload 1:",
                f"• out.pdf (journey Delhi (Noida) → Mumbai, 20 Aug 2026, invoice AI-123, {MUMBAI}): ₹6,810.50",
                f"• hotel.pdf (stay Harbour Inn in Mumbai, 20 Aug 2026, invoice HI-77, {MUMBAI}): amount could not "
                "be determined",
                "LATEST UPLOAD (upload 1): 2 unique bills; total ₹6,810.50 (of the 1 with an amount); 1 bill whose "
                "amount could not be determined, not included; 0 duplicates left out.",
                "ALL BILLS READ IN THIS CHAT: 2 unique bills; total ₹6,810.50 (of the 1 with an amount); 1 bill whose "
                "amount could not be determined, not included; 0 duplicates left out.",
                "HIGHEST BILL: ₹6,810.50 (out.pdf).",
                "LOWEST BILL: ₹6,810.50 (out.pdf).",
                "BY TOUR (bills read in this chat only, not the whole draft):",
                f"• {MUMBAI}: 2 unique bills; total ₹6,810.50 (of the 1 with an amount); 1 bill whose amount could not "
                "be determined, not included; 0 duplicates left out.",
                DRAFTS_HEAD,
                CHENNAI_DRAFT,
                "ALL 1 DRAFT TOGETHER: transportation ₹2,000.50, accommodation ₹5,000.00, DA ₹1,700.00, total with "
                "DA ₹8,000.50, total without DA ₹7,000.50.",
            ]
        )
        no_gaps(backend)

    def test_same_invoice_and_amount_is_one_bill(self, client, backend):
        chat = form_chat(client, backend)
        chat.open()
        # the same hotel invoice scanned twice, the second read with other dates: one bill
        other = with_claimed(renamed(HOTEL, "hotel-2.pdf"), 10080)
        other["accommodationDetails"]["entries"][0]["checkInDate"] = "21-08-2026"
        chat.attach(("hotel.pdf", PDF + b"h"), ("hotel-2.pdf", PDF + b"i"))
        finish(backend, "hotel.pdf", with_claimed(HOTEL, 10080))
        finish(backend, "hotel-2.pdf", other)
        chat.poll()
        chat.say("what is the total of these bills?", {"action": "talk"})
        bills = bills_of(chat.sent[-1]["message"])
        assert "• hotel-2.pdf: duplicate of hotel.pdf, not counted" in bills
        assert (
            "LATEST UPLOAD (upload 1): 1 unique bill; total ₹10,080.00 (of the 1 with an amount); 1 duplicate left "
            "out." in bills
        )
