"""Decides what happens to each bill the employee attaches, by its bytes and never by its name.

Port of Tour_React src/lib/claimChatbot/billOutcome.ts. A bill goes to the OCR only when it is new. The functions
here are pure, so the rules can be tested without the browser.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class KnownBills(TypedDict):
    """What the chat already knows, by SHA-256 of the bytes."""

    # bills already read in this chat and still in the claim
    read: set[str]
    # bills attached but not read yet
    staged: set[str]


class UploadVerdict(TypedDict):
    kind: Literal["new", "duplicate"]


def upload_verdict(hash_: str | None, known: KnownBills, in_upload: set[str]) -> UploadVerdict:
    """billOutcome.ts:uploadVerdict.

    A newly attached file: new, or a duplicate of a bill the chat has, is about to read, or has in this upload.
    Bytes that could not be read cannot be compared, so the file is treated as new.
    """
    if not hash_:
        return {"kind": "new"}
    if hash_ in known["read"] or hash_ in known["staged"] or hash_ in in_upload:
        return {"kind": "duplicate"}
    return {"kind": "new"}


class ReadOutcome(TypedDict):
    added: list[str]
    alreadySaved: list[str]
    inClaim: list[str]
    failed: list[str]
    noDetails: list[str]
    notBelonging: list[str]


def read_outcome(
    bills: list[str],
    added: list[str],
    failed: list[str],
    already_saved: list[str] | None = None,
    empty: list[str] | None = None,
    in_claim: list[str] | None = None,
) -> ReadOutcome:
    """billOutcome.ts:readOutcome.

    After the OCR: each bill is added to the claim, already in the saved claim, already in the claim (added earlier in
    this chat, or on the form), failed to read, read with no journey or stay found in it (`empty`), or is not part of
    any claim (no approved tour fits it). A bill with one new row and one known row counts as added.
    """
    added_set, saved_set, in_claim_set = set(added), set(already_saved or []), set(in_claim or [])
    failed_set, empty_set = set(failed), set(empty or [])

    def is_added(n: str) -> bool:
        return n in added_set

    def is_saved(n: str) -> bool:
        return not is_added(n) and n in saved_set

    def is_in_claim(n: str) -> bool:
        return not is_added(n) and not is_saved(n) and n in in_claim_set

    def known(n: str) -> bool:
        return is_added(n) or is_saved(n) or is_in_claim(n)

    def is_failed(n: str) -> bool:
        return not known(n) and n in failed_set

    def is_empty(n: str) -> bool:
        return not known(n) and not is_failed(n) and n in empty_set

    return {
        "added": [n for n in bills if is_added(n)],
        "alreadySaved": [n for n in bills if is_saved(n)],
        "inClaim": [n for n in bills if is_in_claim(n)],
        "failed": [n for n in bills if is_failed(n)],
        "noDetails": [n for n in bills if is_empty(n)],
        "notBelonging": [n for n in bills if not known(n) and not is_failed(n) and not is_empty(n)],
    }
