"""Port of Tour_React tests/claimChatbot/ocrReferences.test.ts."""

from __future__ import annotations

from tourclaim_agent.claimchat.ocr_references import (
    RAISE_CLAIM_CHAT_REF,
    claim_chat_ref,
    claim_form_ref,
    claim_tour_of,
    is_claim_chat_ref,
    is_raise_claim_chat_ref,
)


# TS: 'Raise Claim page (positive): goes through only what was sent from the Raise Claim page'
def test_raise_claim_page_positive():
    assert is_raise_claim_chat_ref(RAISE_CLAIM_CHAT_REF) is True


# TS: 'Raise Claim page (negative): never what was sent from inside a claim, old or new tag'
def test_raise_claim_page_negative():
    for ref in [claim_chat_ref(4600), "outside-chat:tour-4600", claim_form_ref(4600), "within-plan", None, ""]:
        assert is_raise_claim_chat_ref(ref) is False, ref


# TS: 'claim form (positive): its chat goes through what was sent from this claim, old tag too'
def test_claim_form_positive():
    assert is_claim_chat_ref(claim_chat_ref(4600), 4600) is True
    assert is_claim_chat_ref("outside-chat:tour-4600", 4600) is True


# TS: "claim form (negative): never another claim, the Raise Claim page, or the form's own upload"
def test_claim_form_negative():
    for ref in [
        claim_chat_ref(4601),
        "outside-chat:tour-4601",
        RAISE_CLAIM_CHAT_REF,
        claim_form_ref(4600),
        "within-plan",
        None,
    ]:
        assert is_claim_chat_ref(ref, 4600) is False, ref


# TS: 'claimTourOf: the claim an upload belongs to'
def test_claim_tour_of():
    assert claim_tour_of(claim_chat_ref(12)) == 12
    assert claim_tour_of("outside-chat:tour-12") == 12
    assert claim_tour_of(claim_form_ref(12)) == 12
    assert claim_tour_of(RAISE_CLAIM_CHAT_REF) is None
    assert claim_tour_of("within-plan") is None


# ── our own ──


def test_references_end_exactly_and_take_a_string_tour_id():
    assert claim_tour_of("claim-chat:tour-12\n") is None  # JavaScript `$` is the very end
    assert claim_tour_of("claim-form:tour-0012") == 12
    assert is_claim_chat_ref("claim-chat:tour-7", "7") is True
