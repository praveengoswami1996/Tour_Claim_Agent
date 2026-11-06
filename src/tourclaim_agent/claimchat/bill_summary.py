"""What was found in each bill, one short line per file, and what became of it.

Port of Tour_React src/lib/claimChatbot/billSummary.ts: saved into a tour's draft, already there, fits no tour...
Pure, so the wording can be tested. A file's fate is a dict {"kind": ..., "tour"/"reason": ...}; the TypeScript Map
of fates by file name is a dict here (insertion-ordered, as the Map was).
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal, NotRequired, TypedDict

from .js_compat import UNDEFINED, jget, js_lookup, js_number, js_or, js_str, js_trim, js_truthy, nullish
from .ocr_mapping import to_iso_date


class ReadRow(TypedDict, total=False):
    """A journey or a stay read from a bill, with the files behind it."""

    ModeOfTravel: str
    source: str
    destination: str
    DateOfTravelSource: str
    location: str
    checkInDate: str
    checkOutDate: str
    uploadedFiles: list[dict[str, Any]]
    billFiles: list[dict[str, Any]]


FateKind = Literal[
    "saved",
    "alreadySaved",
    "saveFailed",
    "problem",
    "unsure",
    "noTour",
    "nothingFound",
    "unreadable",
    "duplicate",
    # turned away before any reading (not a PDF or photo, too large, empty), with the reason
    "rejected",
]


class FileFate(TypedDict):
    kind: FateKind
    # saved, alreadySaved, saveFailed
    tour: NotRequired[str]
    # problem, rejected
    reason: NotRequired[str]


# when a file has rows with different fates, the one said is the first of these
_RANK: Final = [
    "saved",
    "alreadySaved",
    "saveFailed",
    "problem",
    "unsure",
    "noTour",
    "nothingFound",
    "unreadable",
    "duplicate",
    "rejected",
]


def _rank(kind: Any) -> int:
    return _RANK.index(kind) if isinstance(kind, str) and kind in _RANK else -1


def stronger_fate(a: Any, b: FileFate) -> Any:
    """billSummary.ts:strongerFate: the fate that is said for a file with two."""
    return b if not js_truthy(a) or _rank(jget(b, "kind")) < _rank(jget(a, "kind")) else a


_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _parts(value: Any) -> tuple[int, int, int] | None:
    iso = to_iso_date(value)
    if not iso:
        return None
    y, m, d = (int(js_number(p)) for p in iso.split("-"))
    return y, m, d


def day_text(value: Any = UNDEFINED) -> str:
    """billSummary.ts:dayText: '18 Jan 2026' (September is 'Sep' here), or ''."""
    p = _parts(value)
    return f"{p[2]} {_MONTHS[p[1] - 1]} {p[0]}" if p else ""


def range_text(from_: Any = UNDEFINED, to: Any = UNDEFINED) -> str:
    """billSummary.ts:rangeText: "18–20 Jan 2026", "30 Jan – 2 Feb 2026", "30 Dec 2025 – 2 Jan 2026"."""
    a = _parts(from_)
    b = _parts(to)
    if not a or not b:
        return day_text(from_) or day_text(to)
    if a[0] == b[0] and a[1] == b[1]:
        return day_text(from_) if a[2] == b[2] else f"{a[2]}–{b[2]} {_MONTHS[a[1] - 1]} {a[0]}"
    if a[0] == b[0]:
        return f"{a[2]} {_MONTHS[a[1] - 1]} – {b[2]} {_MONTHS[b[1] - 1]} {b[0]}"
    return f"{day_text(from_)} – {day_text(to)}"


def _place(v: Any) -> str:
    text = js_or(v, "")
    if not isinstance(text, str):
        raise TypeError("(v || '').split is not a function")
    return js_trim(text.split(",")[0]) or "?"


_MODE_WORD: Final = {
    "air": "Flight",
    "rail": "Train",
    "public-transport": "Cab or bus",
    "personal-vehicle": "Own vehicle",
    "official-entitled": "Office vehicle",
    "official-beyond": "Office vehicle",
    "official-dedicated": "Office vehicle",
}


def describe_row(row: ReadRow | dict[str, Any]) -> str:
    """billSummary.ts:describeRow.

    "Train, Delhi → Jaipur, **18 Jan 2026**" or "Hotel stay in Jaipur, **18–20 Jan 2026**".
    """
    if "checkInDate" in row or "location" in row:
        days = range_text(jget(row, "checkInDate"), jget(row, "checkOutDate"))
        return f"Hotel stay in {_place(jget(row, 'location'))}{f', **{days}**' if days else ''}"
    day = day_text(jget(row, "DateOfTravelSource"))
    word = js_str(nullish(js_lookup(_MODE_WORD, nullish(jget(row, "ModeOfTravel"), "")), "Journey"))
    route = f"{_place(jget(row, 'source'))} → {_place(jget(row, 'destination'))}"
    return f"{word}, {route}{f', **{day}**' if day else ''}"


def _files_of(row: Any) -> list[Any]:
    return [jget(f, "name") for f in [*nullish(jget(row, "uploadedFiles"), []), *nullish(jget(row, "billFiles"), [])]]


def findings_by_file(rows: list[Any]) -> dict[Any, list[str]]:
    """billSummary.ts:findingsByFile: what was found in each file, by file name."""
    found: dict[Any, list[str]] = {}
    for row in rows:
        for name in _files_of(row):
            items = found.get(name, [])
            text = describe_row(row)
            if text not in items:
                items.append(text)
            found[name] = items
    return found


def _sentence(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def file_line(name: Any, found: list[str], fate: Any, opts: dict[str, Any]) -> str:
    """billSummary.ts:fileLine: what was found in a file, and what became of it when that is not the plain "saved"."""
    what = f"{'; '.join(found[:3])}; and {len(found) - 3} more" if len(found) > 3 else "; ".join(found)

    def lead(text: str) -> str:
        return f"• **{js_str(name)}**: {text}"

    kind = jget(fate, "kind")
    in_claim = js_truthy(jget(opts, "inClaim"))
    if kind == "unreadable":
        return lead("⚠️ couldn't be read")
    if kind == "nothingFound":
        return lead("no journey or hotel stay found")
    if kind == "duplicate":
        return lead("same file as one already uploaded, skipped")
    if kind == "rejected":
        return lead(f"**not uploaded**: {js_str(jget(fate, 'reason'))}")
    if kind == "saved":
        tag = f" → **{js_str(jget(fate, 'tour'))}**" if js_truthy(jget(opts, "tagSaved")) else ""
        return lead(f"{what}{tag}")
    if kind == "alreadySaved":
        where = (
            "already in this claim, not added again"
            if in_claim
            else f"already in your {js_str(jget(fate, 'tour'))} draft"
        )
        return lead(f"{what} ({where})")
    if kind == "saveFailed":
        return lead(f"{what} (**{'could not be added' if in_claim else 'not saved yet'}**)")
    if kind == "problem":
        return lead(f"{what or 'hotel bill'} (**not added**: {js_str(jget(fate, 'reason'))})")
    if kind == "unsure":
        return lead(f"{what} (**couldn't tell which tour it's for**)")
    if kind == "noTour":
        why = "doesn't belong to this claim, not added" if in_claim else "doesn't match any of your approved tours"
        return lead(f"{what} (**{why}**)")
    return lead(what or "no journey or hotel stay found")


class ClaimAdded(TypedDict):
    journeys: int
    stays: int
    saved: bool


class ReadSummary(TypedDict):
    # the first line: "Your 3 bills have been read:"
    header: str
    # every file, in upload order
    files: list[str]
    rows: list[ReadRow]
    fates: dict[str, FileFate]
    # the tours whose drafts were saved now, as the employee knows them
    savedTours: list[str]
    # extra lines after the list (a bill the server no longer has...)
    notes: NotRequired[list[str]]
    # inside one claim form: what was added to it (the footer then says that, not where drafts were saved)
    claim: NotRequired[ClaimAdded]


# The three groups every upload is sorted into:
# - new: read and added (or waiting to be saved)
# - duplicate: the same bill as one already here, or a journey or stay the claim already has
# - not relevant: not used for the claim (fits no tour, nothing in it, a problem on the bill, could not be read or
#   told apart, or turned away before reading)
FileGroup = Literal["new", "duplicate", "notRelevant"]

GROUP_TITLE: Final[dict[str, str]] = {"new": "✅ New", "duplicate": "↩ Duplicate", "notRelevant": "🚫 Not relevant"}
_GROUP_ORDER: Final = ("new", "duplicate", "notRelevant")


def group_of(fate: Any, found: list[str]) -> FileGroup:
    """billSummary.ts:groupOf: which of the three groups a file goes in."""
    kind = jget(fate, "kind")
    if kind in ("saved", "saveFailed"):
        return "new"
    if kind in ("alreadySaved", "duplicate"):
        return "duplicate"
    if kind is UNDEFINED:
        return "new" if len(found) > 0 else "notRelevant"
    return "notRelevant"


def _grouped_lines(
    files: list[Any], fates: dict[Any, Any], found: dict[Any, list[str]], opts: dict[str, Any]
) -> list[str]:
    """groupedLines: the files in their groups, in upload order: a bold heading with the count, then one line each."""
    out: list[str] = []
    for group in _GROUP_ORDER:
        names = [name for name in files if group_of(fates.get(name), found.get(name, [])) == group]
        if len(names) == 0:
            continue
        if len(out) > 0:
            out.append("")
        out.append(f"**{GROUP_TITLE[group]} ({len(names)})**")
        out.extend(file_line(name, found.get(name, []), fates.get(name), opts) for name in names)
    return out


_BLANK_LINES = re.compile(r"\n{3,}")


def _tidy(lines: list[str]) -> str:
    return js_trim(_BLANK_LINES.sub("\n\n", "\n".join(lines)))


def upload_sorted_text(files: list[Any], fates: dict[Any, Any]) -> str:
    """billSummary.ts:uploadSortedText.

    The reply to an upload that has nothing to read (every file is a duplicate or was turned away): the same groups,
    and no list of what was found, since nothing was read.
    """
    lines = _grouped_lines(files, fates, {}, {"tagSaved": False, "inClaim": False})
    any_new = any(group_of(fates.get(name), []) == "new" for name in files)
    return _tidy(
        [
            "Here is how I sorted your upload:",
            "",
            *lines,
            *([] if any_new else ["", "Nothing new to read, so nothing was added."]),
        ]
    )


def read_summary_text(s: ReadSummary) -> str:
    """billSummary.ts:readSummaryText: the header, the files in their three groups, and where the bills were saved."""
    found = findings_by_file(s["rows"])
    saved_tours = s["savedTours"]
    tag_saved = len(saved_tours) > 1
    claim = jget(s, "claim")
    lines = _grouped_lines(s["files"], s["fates"], found, {"tagSaved": tag_saved, "inClaim": js_truthy(claim)})
    fates = list(s["fates"].values())
    notes = nullish(jget(s, "notes"), [])
    if js_truthy(claim):
        return _tidy([s["header"], "", *lines, *notes, "", claim_footer(claim)])
    footer: list[str] = []
    if len(saved_tours) == 1:
        footer.append(f"✅ Saved to your draft for **{js_str(saved_tours[0])}**. Nothing is submitted.")
    elif len(saved_tours) > 1:
        footer.append(f"✅ Saved to your drafts for **{len(saved_tours)} tours**. Nothing is submitted.")
    if any(jget(f, "kind") == "saveFailed" for f in fates):
        footer.append("⚠️ I couldn't save some of them just now. I'll try again the next time you open the assistant.")
    if len(saved_tours) == 0 and not any(jget(f, "kind") in ("saveFailed", "alreadySaved") for f in fates):
        footer.append("⚠️ **Nothing was saved.**")
    return _tidy([s["header"], "", *lines, *notes, "", *footer])


def _count(n: Any, word: str) -> str:
    return f"{js_str(n)} {word}{'' if n == 1 and not isinstance(n, bool) else 's'}"


def claim_footer(c: ClaimAdded | dict[str, Any]) -> str:
    """billSummary.ts:claimFooter: "✅ Added **2 journeys** and **1 stay** to this claim. The draft is saved."."""
    journeys, stays = jget(c, "journeys"), jget(c, "stays")
    parts = [
        p
        for p in (js_truthy(journeys) and _count(journeys, "journey"), js_truthy(stays) and _count(stays, "stay"))
        if p
    ]
    if len(parts) == 0:
        return "Nothing new was added to this claim."
    saved = (
        " The draft is saved."
        if js_truthy(jget(c, "saved"))
        else " ⚠️ The draft could not be saved yet; it is saved again when you leave the form."
    )
    return f"✅ Added **{'** and **'.join(parts)}** to this claim.{saved}"


def read_header(count: Any, opts: dict[str, Any]) -> str:
    """billSummary.ts:readHeader.

    "Your bill has been read:", "Hi Anil, the 3 bills you uploaded earlier have been read:".
    """
    one = count == 1 and not isinstance(count, bool)
    name = jget(opts, "name")
    hi = f"Hi{f' {js_str(name)}' if js_truthy(name) else ''}, " if name is not UNDEFINED else ""
    if js_truthy(jget(opts, "earlier")):
        text = (
            "the bill you uploaded earlier has been read:"
            if one
            else f"the {js_str(count)} bills you uploaded earlier have been read:"
        )
    else:
        text = "your bill has been read:" if one else f"your {js_str(count)} bills have been read:"
    return f"{hi}{text}" if hi else _sentence(text)


__all__ = [
    "GROUP_TITLE",
    "ClaimAdded",
    "FileFate",
    "FileGroup",
    "ReadRow",
    "ReadSummary",
    "claim_footer",
    "day_text",
    "describe_row",
    "file_line",
    "findings_by_file",
    "group_of",
    "range_text",
    "read_header",
    "read_summary_text",
    "stronger_fate",
    "upload_sorted_text",
]
