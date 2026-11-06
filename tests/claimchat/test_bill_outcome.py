"""Port of the billOutcome.ts tests: billDuplicates.test.ts (uploadVerdict, readOutcome), and the readOutcome tests of
savedClaim.test.ts and staysOnly.test.ts. The hashFile test is in test_file_hash.py.
"""

from __future__ import annotations

from tourclaim_agent.claimchat.bill_outcome import read_outcome, upload_verdict


def none():
    # the TS test also passes a `saved` map, which uploadVerdict ignores
    return {"saved": {}, "read": set(), "staged": set()}


# TS: billDuplicates.test.ts 'uploadVerdict: a bill already read, already waiting, or repeated in this upload is a
# duplicate'
def test_upload_verdict_a_bill_already_read_waiting_or_repeated_is_a_duplicate():
    assert upload_verdict("r", {**none(), "read": {"r"}}, set()) == {"kind": "duplicate"}
    assert upload_verdict("s", {**none(), "staged": {"s"}}, set()) == {"kind": "duplicate"}
    assert upload_verdict("u", none(), {"u"}) == {"kind": "duplicate"}


# TS: billDuplicates.test.ts 'uploadVerdict: a new bill, or one whose bytes could not be read, goes to the OCR'
def test_upload_verdict_a_new_bill_or_unreadable_bytes_go_to_the_ocr():
    assert upload_verdict("n", none(), set()) == {"kind": "new"}
    assert upload_verdict(None, {**none(), "saved": {"h": "x"}}, set()) == {"kind": "new"}


# TS: billDuplicates.test.ts 'readOutcome: each bill is added, failed, or not part of any claim'
def test_read_outcome_each_bill_is_added_failed_or_not_part_of_any_claim():
    r = read_outcome(["a.pdf", "b.pdf", "c.pdf", "d.pdf"], ["a.pdf"], ["d.pdf"])
    assert r == {
        "added": ["a.pdf"],
        "alreadySaved": [],
        "inClaim": [],
        "failed": ["d.pdf"],
        "noDetails": [],
        "notBelonging": ["b.pdf", "c.pdf"],
    }


# TS: billDuplicates.test.ts 'readOutcome: a bill whose journey the claim already has is "already in your claim", not
# new and not unreadable'
def test_read_outcome_a_bill_the_claim_already_has_is_in_claim():
    r = read_outcome(["again.pdf", "saved.pdf"], [], [], ["saved.pdf"], [], ["again.pdf"])
    assert r == {
        "added": [],
        "alreadySaved": ["saved.pdf"],
        "inClaim": ["again.pdf"],
        "failed": [],
        "noDetails": [],
        "notBelonging": [],
    }


# TS: savedClaim.test.ts 'readOutcome: a bill the saved claim holds is reported as already saved'
def test_read_outcome_a_bill_the_saved_claim_holds_is_already_saved():
    r = read_outcome(["new.pdf", "saved.pdf", "other.pdf"], ["new.pdf"], [], ["saved.pdf"])
    assert r == {
        "added": ["new.pdf"],
        "alreadySaved": ["saved.pdf"],
        "inClaim": [],
        "failed": [],
        "noDetails": [],
        "notBelonging": ["other.pdf"],
    }


# TS: staysOnly.test.ts 'readOutcome: a bill with no journey or stay in it is reported as such, not as "not part of any
# claim"'
def test_read_outcome_a_bill_with_nothing_in_it_is_no_details():
    r = read_outcome(["a.jpeg", "empty.jpeg", "nov.jpeg"], ["a.jpeg"], [], [], ["empty.jpeg"])
    assert r == {
        "added": ["a.jpeg"],
        "alreadySaved": [],
        "inClaim": [],
        "failed": [],
        "noDetails": ["empty.jpeg"],
        "notBelonging": ["nov.jpeg"],
    }


# ── our own ──


def test_read_outcome_precedence():
    # added beats saved beats in-claim beats failed beats empty
    r = read_outcome(["x", "y", "z", "w"], ["x"], ["x", "y", "z", "w"], ["x", "y"], ["z", "w"], ["y", "z"])
    assert r == {
        "added": ["x"],
        "alreadySaved": ["y"],
        "inClaim": ["z"],
        "failed": ["w"],
        "noDetails": [],
        "notBelonging": [],
    }
    assert upload_verdict("", {"read": {""}, "staged": set()}, set()) == {"kind": "new"}
