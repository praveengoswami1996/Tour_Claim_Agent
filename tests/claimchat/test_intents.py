"""Port of Tour_React tests/claimChatbot/intents.test.ts. Files are dicts {"name", "type", "size"}."""

from __future__ import annotations

from tourclaim_agent.claimchat.intents import check_files, clean_message


def file(name: str, type_: str, size: int = 10) -> dict:
    return {"name": name, "type": type_, "size": size}


# TS: 'cleanMessage strips control and zero-width characters'
def test_clean_message_strips_control_and_zero_width_characters():
    assert clean_message("  hello​   world\u0007\n") == "hello world"
    assert clean_message("\u0000\u0001") == ""


# TS: 'checkFiles: positive and negative'
def test_check_files_positive_and_negative():
    ok = check_files([file("ticket.pdf", "application/pdf"), file("bill.jpg", "image/jpeg")])
    assert len(ok["accepted"]) == 2
    assert len(ok["problems"]) == 0

    bad = check_files(
        [
            file("notes.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            file("photo.heic", "image/heic"),
            file("empty.pdf", "application/pdf", 0),
            file("huge.pdf", "application/pdf", 50 * 1024 * 1024),
            file("mystery", ""),
            file("data.zip", "application/zip"),
        ]
    )
    assert len(bad["accepted"]) == 0
    assert len(bad["problems"]) == 6
    # the same turned-away files, with the name and reason apart, for the report
    assert len(bad["rejected"]) == 6
    assert bad["rejected"][0] == {
        "name": "notes.docx",
        "reason": "I can only read PDFs and photos or scans (JPG, PNG), not this kind of file.",
    }
    assert (
        bad["problems"][0] == "notes.docx: I can only read PDFs and photos or scans (JPG, PNG), not this kind of file."
    )
    assert bad["rejected"][4]["name"] == "mystery"

    # how many go to the bill reader at once is decided by the chat, after duplicates are left out
    many = check_files([file(f"b{i}.pdf", "application/pdf") for i in range(20)])
    assert len(many["accepted"]) == 20
    assert len(many["problems"]) == 0


# ── our own ──


def test_check_files_reasons_word_for_word():
    r = check_files(
        [
            file("photo.HEIC", ""),
            file("scan.png", "image/heif"),
            file("a.pdf", "text/plain"),
            file("big.pdf", "", int(21.5 * 1024 * 1024)),
            file("", ""),
        ]
    )
    assert r["problems"] == [
        "photo.HEIC: HEIC photos can't be read. Please send it as a JPG or PNG, or a PDF.",
        "scan.png: HEIC photos can't be read. Please send it as a JPG or PNG, or a PDF.",
        "a.pdf: I can only read PDFs and JPG or PNG images.",
        "big.pdf: it is 22 MB, which is too large (the limit is 20 MB). Please send a smaller copy.",
        "This file: I can only read PDFs and JPG or PNG images.",
    ]


def test_clean_message_uses_javascript_whitespace():
    assert clean_message("a b c﻿d") == "a b cd"
    # \x1c is a control character (removed); \x85 is not whitespace in JavaScript (kept)
    assert clean_message("x\u001cy a\u0085b") == "xy a\u0085b"
