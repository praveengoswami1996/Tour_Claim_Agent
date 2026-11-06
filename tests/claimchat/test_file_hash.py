"""Port of the hashFile test of Tour_React tests/claimChatbot/billDuplicates.test.ts."""

from __future__ import annotations

import re

from tourclaim_agent.claimchat.file_hash import hash_file


# TS: billDuplicates.test.ts 'hashFile: the same bytes under another name are the same bill; different bytes are not'
def test_hash_file_same_bytes_are_the_same_bill_different_bytes_are_not():
    a = hash_file(b"same bytes")  # ticket.pdf
    b = hash_file(b"same bytes")  # scan (2).jpg
    c = hash_file(b"other bytes")  # ticket.pdf
    assert a and re.fullmatch(r"[0-9a-f]{64}", a)
    assert a == b
    assert a != c


# ── our own ──


def test_hash_file_known_digest_and_unreadable_bytes():
    assert hash_file(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert hash_file(None) is None
    assert hash_file("text, not bytes") is None  # type: ignore[arg-type]
