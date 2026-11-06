"""Port of Tour_React tests/claimChatbot/claimLink.test.ts (src/lib/claimLink.ts), plus checks of our own.

`new URL(url).search` is the part from '?' on. A key the TypeScript left undefined is left out of the dict here.
"""

from __future__ import annotations

from tourclaim_agent.claimchat.claim_link import claim_link, state_from_claim_link


def search_of(url: str) -> str:
    return url[url.index("?") :]


# TS: 'claimLink: the complete address of the saved claim, which the claim page reads back'
def test_claim_link_the_complete_address_of_the_saved_claim():
    url = claim_link({"tourId": 4522, "statusId": 3, "totalDays": 6}, "https://tour.example")
    assert url == "https://tour.example/raise-claim-approver?tourId=4522&isDraft=true&statusId=3&totalDays=6"
    assert state_from_claim_link(search_of(url)) == {"tourId": 4522, "statusId": 3, "totalDays": 6, "isDraft": True}


# TS: 'claimLink: a tour without status or days still gives the tour'
def test_claim_link_a_tour_without_status_or_days_still_gives_the_tour():
    url = claim_link({"tourId": 7, "statusId": None}, "https://tour.example")
    assert url == "https://tour.example/raise-claim-approver?tourId=7&isDraft=true"
    assert state_from_claim_link(search_of(url)) == {"tourId": 7, "isDraft": True}


# TS: 'stateFromClaimLink: an address without a tour, or with a bad one, is not a claim link'
def test_state_from_claim_link_without_a_tour_or_with_a_bad_one():
    assert state_from_claim_link("") is None
    assert state_from_claim_link("?statusId=3") is None
    assert state_from_claim_link("?tourId=abc") is None
    assert state_from_claim_link("?tourId=-4") is None


# TS: 'stateFromClaimLink: without isDraft=true the claim is not treated as a draft'
def test_state_from_claim_link_without_is_draft_true_not_a_draft():
    assert state_from_claim_link("?tourId=9")["isDraft"] is False


# ── our own (expected values from the TypeScript) ──


def test_url_search_params_encoding_and_decoding():
    assert claim_link({"tourId": "12 a~*", "statusId": 2.5}, "") == (
        "/raise-claim-approver?tourId=12+a%7E*&isDraft=true&statusId=2.5"
    )
    assert state_from_claim_link("?tourId=%37&statusId=0x10&isDraft=tr%75e") == {
        "tourId": 7,
        "statusId": 16,
        "isDraft": True,
    }
    assert state_from_claim_link("tourId=1e3&statusId=&totalDays=x") == {
        "tourId": 1000,
        "statusId": 0,
        "isDraft": False,
    }
    assert state_from_claim_link("?tourId=1.5") is None
