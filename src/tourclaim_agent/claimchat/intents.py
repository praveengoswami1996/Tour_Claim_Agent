"""Checks on what the employee sends before it reaches the AI model or the bill reader.

Port of Tour_React src/lib/claimChatbot/intents.ts: control characters are stripped from typed text, and files the
bill reader cannot take are turned away (each with the reason, which the AI model passes on). What the employee
MEANS is always read by the AI model, never here. A file is a dict {"name", "size", "type", ...}.
"""

from __future__ import annotations

import re
from typing import Any, Final, TypedDict

from .js_compat import JS_WS, jget, js_number, js_or, js_round, js_str, js_trim

# A message longer than this is not a claim detail: ask for a shorter one instead of sending a wall of text on.
MAX_MESSAGE_CHARS: Final = 1000
# Per-file limit of the Tour API's bill upload (a larger file turns the whole upload away).
MAX_FILE_BYTES: Final = 20 * 1024 * 1024
# The Tour API takes at most this many files in one upload (more turns the whole upload away).
MAX_FILES_PER_READ: Final = 20

_CONTROL = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏‪-‮⁠﻿]")
_SPACES = re.compile(JS_WS + "+")


def clean_message(raw: str) -> str:
    """intents.ts:cleanMessage: strips control and zero-width characters and collapses whitespace."""
    return js_trim(_SPACES.sub(" ", _CONTROL.sub("", raw)))


# ─── Files ────────────────────────────────────────────────────────────────────


class RejectedFile(TypedDict):
    name: str
    reason: str


class FileVerdict(TypedDict):
    accepted: list[dict[str, Any]]
    # one line per file turned away, for the employee ("name: reason")
    problems: list[str]
    # the same files turned away, with the name and the reason apart (for sorting them in the report)
    rejected: list[RejectedFile]


_HEIC_NAME = re.compile(r"\.(heic|heif)\Z", re.IGNORECASE | re.ASCII)
_HEIC_TYPE = re.compile(r"heic|heif", re.IGNORECASE | re.ASCII)
_OFFICE_NAME = re.compile(r"\.(docx?|xlsx?|pptx?|txt|csv|zip|rar)\Z", re.IGNORECASE | re.ASCII)
_READABLE_NAME = re.compile(r"\.(pdf|jpe?g|png)\Z", re.IGNORECASE | re.ASCII)


def _readable(f: dict[str, Any]) -> bool:
    """the Tour API takes only these (one other file turns the whole upload away)"""
    type_ = jget(f, "type")
    return bool(_READABLE_NAME.search(js_str(jget(f, "name")))) and (
        type_ == "" or type_ in ("application/pdf", "image/jpeg", "image/png")
    )


def _mb(size: float) -> str:
    return f"{js_str(js_round(size / 1024 / 1024))} MB"


def check_files(files: list[dict[str, Any]]) -> FileVerdict:
    """intents.ts:checkFiles: which of the chosen files can be sent to the bill reader, and why the others cannot."""
    accepted: list[dict[str, Any]] = []
    rejected: list[RejectedFile] = []
    for f in files:
        name = js_or(jget(f, "name"), "This file")
        file_name, file_type, size = js_str(jget(f, "name")), js_str(jget(f, "type")), jget(f, "size")
        reason: str | None = None
        if _HEIC_NAME.search(file_name) or _HEIC_TYPE.search(file_type):
            reason = "HEIC photos can't be read. Please send it as a JPG or PNG, or a PDF."
        elif _OFFICE_NAME.search(file_name):
            reason = "I can only read PDFs and photos or scans (JPG, PNG), not this kind of file."
        elif not _readable(f):
            reason = "I can only read PDFs and JPG or PNG images."
        elif isinstance(size, int | float) and not isinstance(size, bool) and size == 0:
            reason = "the file is empty. Please attach it again."
        elif js_number(size) > MAX_FILE_BYTES:
            reason = (
                f"it is {_mb(js_number(size))}, which is too large (the limit is {_mb(MAX_FILE_BYTES)}). "
                "Please send a smaller copy."
            )
        if reason is None:
            accepted.append(f)
        else:
            rejected.append({"name": name, "reason": reason})
    return {
        "accepted": accepted,
        "problems": [f"{js_str(r['name'])}: {r['reason']}" for r in rejected],
        "rejected": rejected,
    }
