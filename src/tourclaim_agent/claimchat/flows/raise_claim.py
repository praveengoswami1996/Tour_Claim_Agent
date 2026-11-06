"""The Raise Claim chat (outside jurisdiction): port of Tour_React src/components/claimChatbot/ClaimChatbot.tsx.

One component, two places (Spec A §0): the Raise Claim list page (`raise_page`, no form) and inside one claim form
(`raise_form`, the form reached through the bridge). The old component's logic is kept line for line on the runtime
(runtime design, Project-Context/agent/old_assistant_parity/02_runtime_design.md):

- every `useState` / `useRef` is a key of `self.state` (plain JSON; `Set`s are lists, `Map`s are dicts keyed by the
  number as text), callbacks it held (`askYN.yes`, a reply's `onShown`, "Try again") are named continuations;
- the event handlers are `async` methods; the `useEffect`s that do work are the `@effect`s, in the order they are
  declared in the TSX (greeting, drafts changed, settle on claim change, the read effect, the auto upload, the
  in-form start);
- the render (chips, composer placeholder, typing label) is `chips()`, `composer()`, `status()`, `bills()`, and
  ClaimAssistantLauncher's header status and launcher notice are `status()`.

Every text the employee reads is still written by the AI model from the notes queued here (`say` / `tell`), word for
word the old notes. CC:<line> refers to ClaimChatbot.tsx, LAU:<line> to ClaimAssistantLauncher.tsx.

Dead code of the TSX that no live path reaches (Spec A §8) is left out: the bulk / split / upload-choice machinery
(saveBulk, declineBulk, workOnOneTour, chooseSplit, chooseUpload, declineUpload, finishRead, dropTaken, offerUpload),
announceGaps and the steps gaps / submit (startSubmit, doSubmit), the outside branch of afterRead and noMoreFiles,
and the "Choose a tour myself" chip (noMatch is never set). Their actions are never offered by outsideActions on a
live path; if one were, it would run "talk", as the TSX's `runs[spec.name] ?? runs.talk`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from typing import TYPE_CHECKING, Any, ClassVar, Final

from tourclaim_agent.claimchat.ai.ai_assistant import prompts, understand
from tourclaim_agent.claimchat.ai.assistant_actions import (
    FORM_READY_REPLY,
    FORM_TALK_NOTE,
    OUTSIDE_START_OVER_ASK,
    TALK_NOTE,
    outside_actions,
    outside_open_question,
)
from tourclaim_agent.claimchat.bill_outcome import upload_verdict
from tourclaim_agent.claimchat.bill_summary import read_header, read_summary_text, stronger_fate, upload_sorted_text
from tourclaim_agent.claimchat.claim_assistant import (
    DA_RULES,
    apply_edits,
    describe_missing,
    find_form_only_issues,
    find_missing_fields,
    initial_claim_state,
    is_empty_accommodation,
    is_empty_leave,
    is_empty_transport,
    journey_key,
    merge_ocr_rows,
    read_claim_edits,
    snapshot,
    stay_key,
    summarize_journeys,
    summarize_leave,
    summarize_stays,
)
from tourclaim_agent.claimchat.claim_link import claim_link
from tourclaim_agent.claimchat.claim_payload import js_json_stringify
from tourclaim_agent.claimchat.claim_progress import (
    build_progress,
    covered_text,
    find_claim_issues,
    find_relation_issues,
    progress_text,
    stay_bill_problem,
)
from tourclaim_agent.claimchat.file_hash import hash_file
from tourclaim_agent.claimchat.intents import MAX_FILES_PER_READ, MAX_MESSAGE_CHARS, check_files, clean_message
from tourclaim_agent.claimchat.js_compat import (
    JS_WS,
    UNDEFINED,
    defined,
    jget,
    js_len,
    js_number,
    js_or,
    js_str,
    js_trim,
    js_truthy,
    local_ymd,
    now_ms,
    nullish,
    utc_iso_string,
    v8_date_parse,
)
from tourclaim_agent.claimchat.kept_reports import drop_reports, keep_report, reports_for, reports_key
from tourclaim_agent.claimchat.ocr_mapping import file_key, map_ocr_to_rows
from tourclaim_agent.claimchat.ocr_references import (
    RAISE_CLAIM_CHAT_REF,
    claim_chat_ref,
    is_claim_chat_ref,
    is_raise_claim_chat_ref,
)
from tourclaim_agent.claimchat.ocr_results import combine_ocr_results
from tourclaim_agent.claimchat.row_order import ids_of, in_stored_order, journeys_in_date_order, stays_in_date_order
from tourclaim_agent.claimchat.runtime.bridge import BridgeError
from tourclaim_agent.claimchat.runtime.flow import (
    ChatFlow,
    Chip,
    FilePill,
    NarratorView,
    continuation,
    effect,
    register_flow,
)
from tourclaim_agent.claimchat.runtime.ocr import OcrApiError, is_in_flight
from tourclaim_agent.claimchat.runtime.session import Continuation, cont
from tourclaim_agent.claimchat.saved_claim import (
    add_to_saved,
    claim_state_from_draft,
    compare_with_saved,
    draft_for_model,
    rows_count,
    saved_claim_text,
    saved_draft_summary,
)
from tourclaim_agent.claimchat.tour_matcher import match_tours, tour_window, triage_rows
from tourclaim_agent.claimchat.tour_review import (
    missing_in_short,
    next_review_step,
    review_blocked_note,
    review_claim,
    review_done_note,
    review_question_note,
    review_settled_note,
    settle,
)

if TYPE_CHECKING:
    from tourclaim_agent.claimchat.runtime.turn import IncomingFile

log = logging.getLogger(__name__)

# CC:204-205
NARRATOR_PROMPT: Final = prompts()["narrate_outside"]
UNDERSTAND_PROMPT: Final = prompts()["understand_outside"]

# CC:127: steps where a question is waiting for its answer: bills read meanwhile are gone through once it is answered
WAIT_STEPS: Final = ("confirmTour", "pickTour", "bulkDrafts", "splitTours", "submit")
# CC:2345
TOUR_STEPS: Final = ("uploadChoice", "confirmTour", "pickTour", "splitTours", "bulkDrafts")
# CC:1821: on the Raise Claim page the details, stay and leave questions come first
LIST_PAGE_SKIPS: Final = frozenset(("missingField", "amountMissing", "formOnly", "askLeave", "noJourneys"))

SAVE_DRAFT_LABEL: Final = "Save as draft"
OPEN_FILE_PICKER: Final = {"type": "open_file_picker"}

_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SPACES: Final = re.compile(JS_WS + "+")
_EXTENSION: Final = re.compile(r"(\.[^.]*)?\Z")
_NO_DATA: Final = object()


# ─── formatting helpers (CC:172-184) ─────────────────────────────────────────


def day_month(iso: Any) -> str:
    """dayMonth: safeFormatDate(iso, 'dd MMM yyyy') (`new Date(iso)` shown in local time), or ''."""
    if not js_truthy(iso):
        return ""
    ms = v8_date_parse(js_str(iso))
    if ms is None:
        return ""
    y, m, d = local_ymd(ms)
    return f"{d:02d} {_MONTHS[m - 1]} {y:04d}"


def tour_dates(t: Any) -> str:
    w = tour_window(t)
    return f"{day_month(w['start'])} – {day_month(w['end'])}" if w else ""


def tour_label(t: Any) -> str:
    dates = tour_dates(t)
    return (
        f"{js_str(js_or(jget(t, 'source'), '?'))} → {js_str(js_or(jget(t, 'destination'), '?'))}"
        f"{f' · {dates}' if dates else ''}"
    )


def short_range(t: Any) -> str:
    w = tour_window(t)
    if not w:
        return ""
    return day_month(w["start"]) if w["start"] == w["end"] else f"{day_month(w['start'])} – {day_month(w['end'])}"


def place_name(v: Any) -> str:
    return js_trim(js_str(js_or(v, "?")).split(",")[0])


def has_claim_rows(s: dict[str, Any]) -> bool:
    """CC:188-189"""
    return any(not is_empty_transport(r) for r in s["transportationRows"]) or (
        js_truthy(s["includeAccommodation"]) and any(not is_empty_accommodation(r) for r in s["accommodationRows"])
    )


def _is_new_file(v: Any) -> bool:
    """A bill attached in this chat (the old `File` object): it has the server's documentId and no saved fileId."""
    return isinstance(v, dict) and "documentId" in v and "name" in v and "fileId" not in v


def claim_key(s: dict[str, Any]) -> str:
    """CC:191-199: what a claim holds, to tell whether it changed since it was saved (bill files count by name)."""

    def replace(v: Any) -> Any:
        if isinstance(v, dict):
            if _is_new_file(v):
                return v["name"]
            return {k: replace(x) for k, x in v.items() if k not in ("fileId", "tempFilePath", "isRetained", "_saved")}
        if isinstance(v, list):
            return [replace(x) for x in v]
        return v

    value = [
        [r for r in s["transportationRows"] if not is_empty_transport(r)],
        [r for r in s["accommodationRows"] if not is_empty_accommodation(r)]
        if js_truthy(s["includeAccommodation"])
        else [],
        [r for r in s["leaveRows"] if not is_empty_leave(r)] if js_truthy(s["includeLeave"]) else [],
    ]
    return js_json_stringify(replace(value))


def slot_key(m: dict[str, Any]) -> str:
    return f"{js_str(m['section'])}:{js_str(m['row'])}:{js_str(m['slot'])}"


def row_file_names(rows: list[Any]) -> list[str]:
    """CC:154-155"""
    return [f["name"] for r in rows for f in [*(jget(r, "uploadedFiles") or []), *(jget(r, "billFiles") or [])]]


def count_of(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def review_text(s: dict[str, Any]) -> str:
    """CC:610-611"""
    return (
        f"Your claim:\n\nJourneys\n{summarize_journeys(s)}\n\nStays\n{summarize_stays(s)}"
        f"\n\nLeave\n{summarize_leave(s)}"
    )


def _js_slice(text: str, n: int) -> str:
    """`text.slice(0, n)` (UTF-16 units; a cut surrogate pair is dropped)."""
    data = text.encode("utf-16-le", "surrogatepass")[: 2 * n]
    return data.decode("utf-16-le", "ignore")


def _file_doc_ids(value: Any, out: set[int] | None = None, depth: int = 0) -> set[int]:
    """collectFiles (CC:130-138): every bill attached in this chat somewhere in `value`, by its documentId."""
    out = set() if out is None else out
    if value is None or depth > 8:
        return out
    if isinstance(value, dict):
        if _is_new_file(value):
            doc = value.get("documentId")
            if isinstance(doc, int | float) and not isinstance(doc, bool):
                out.add(int(doc))
            return out
        for v in value.values():
            _file_doc_ids(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _file_doc_ids(v, out, depth + 1)
    return out


def _error_message(error: BaseException) -> str:
    """`error?.message` of what the old code caught: the API's and the form's own messages."""
    if isinstance(error, OcrApiError):
        return error.message
    if isinstance(error, BridgeError):
        return str(error)
    return ""


class RaiseClaimChat(ChatFlow):
    """ClaimChatbot on the Raise Claim page (no form)."""

    mode = "raise_page"
    IN_FORM: ClassVar[bool] = False

    @classmethod
    def initial_state(cls, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "busy": None,
            # staged bills (CC:235): {file: {name, size, type}, hash, key (bytes in rt.files)}
            "bills": [],
            "stageSeq": 0,
            "step": "start",
            "pending": None,
            "claim": initial_claim_state(),
            # the claim changed (setState) -> settleTaken (CC:1241)
            "claimRev": 0,
            "settledRev": 0,
            "picked": None,
            "suggested": None,
            "ranked": [],
            "undo": None,
            "savedTours": [],
            "asked": {"stay": False, "leave": False},
            "skipped": [],
            "hinted": False,
            "resetAsked": False,
            "resetAsk": False,
            "askYN": None,
            "greeted": False,
            "welcome": None,
            "readFiles": [],
            "readHashes": {},
            "doneUploading": False,
            "savedCache": {},
            "lastSaved": {},
            "saveOffered": False,
            "savedHere": [],
            "draftDetails": {},
            "storedIds": {"t": [], "a": []},
            "discarded": [],
            "sentHashes": {},
            "taken": {},
            "taking": False,
            "failedDocs": [],
            "pendingSkip": {"duplicate": [], "rejected": []},
            "tried": [],
            "acknowledged": [],
            "deferred": [],
            "reviewMode": "check",
            "reviewItem": None,
            "reviewActive": False,
            "reviewPassed": False,
            "formStarted": False,
            "formTours": None,
            "lastDraftsKey": "",
        }

    # ── shortcuts ──
    @property
    def s(self) -> dict[str, Any]:
        return self.state

    @property
    def in_form(self) -> bool:
        return self.IN_FORM

    def say(self, text: str, tone: str | None = None, on_shown: Continuation | None = None) -> None:
        """sayExactly / narrator.say: a NOTE for the model (CC:450)."""
        self.rt.narrator.say(text, tone, on_shown)

    def tell(self, note: str, tone: str | None = None, data: Any = _NO_DATA) -> None:
        """tell(note, tone, data) (CC:326-329)."""
        if data is _NO_DATA:
            self.rt.narrator.tell(note, tone=tone)
        else:
            self.rt.narrator.tell(note, data=data, tone=tone)

    def set_busy(self, label: str | None) -> None:
        self.rt.set_busy(label)

    # ── the page's props ──
    @property
    def tours(self) -> list[Any]:
        if self.in_form:
            return list(self.s.get("formTours") or [])
        return self.rt.page.tours

    @property
    def tours_loading(self) -> bool:
        if self.in_form:
            return self.s.get("formTours") is None
        return self.rt.page.tours_loading

    @property
    def no_tours(self) -> bool:
        return not self.tours_loading and len(self.tours) == 0

    @property
    def drafts_loading(self) -> bool:
        return False if self.in_form else self.rt.page.drafts_loading

    @property
    def draft_tour_ids(self) -> list[Any]:
        if self.in_form:
            # ApproverPage: [tourId] when the form loaded a saved draft
            tour_id = self.form_tour_id
            return [tour_id] if tour_id is not None and tour_id in self.rt.page.draft_tour_ids else []
        return self.rt.page.draft_tour_ids

    @property
    def drafts_info(self) -> list[dict[str, Any]]:
        return [] if self.in_form else self.rt.page.drafts_info

    @property
    def page_info(self) -> str | None:
        return None if self.in_form else self.rt.page.page_info

    @property
    def form_tour_id(self) -> int | None:
        """formTourId (CC:430): the form's tour."""
        if not self.in_form:
            return None
        tour_id = self.rt.session.params.get("tourId")
        if tour_id is None:
            return None
        number = js_number(tour_id)
        return int(number) if number == number else None

    async def load_tour(self, row: Any) -> dict[str, Any] | None:
        return await self.rt.page.load_tour(row)

    async def on_load_draft(self, tour_id: Any) -> Any:
        return await self.rt.page.load_draft(tour_id)

    async def on_save_draft(self, row: Any, draft: dict[str, Any], *, overwrite: bool) -> str:
        """onSaveDraft. Inside the form it is ApproverPage's assistantSaveDraft: 'failed' (the form saves itself)."""
        if self.in_form:
            return "failed"
        result = await self.rt.page.save_draft(row, draft, overwrite=overwrite)
        if result == "saved":
            # the page fetched the drafts again: the drafts-changed effect runs now, while the save is still busy
            self._drafts_changed()
        return result

    async def on_fill_form(self, row: Any, draft: dict[str, Any]) -> bool:
        if self.in_form:
            # ApproverPage's assistantFillForm (unreachable: "fill the form" is never offered in the form)
            return True
        return await self.rt.page.fill_form(row, draft)

    async def on_open_claim(self, row: Any) -> bool:
        if self.in_form:
            return True
        return await self.rt.page.open_claim(row)

    async def _ensure_loaded(self) -> None:
        """What the page had loaded before the chat was used: the tours, the drafts, the bill list."""
        await self.rt.page.load()
        if self.in_form and self.s.get("formTours") is None:
            await self._load_form_tour()
        if not self.rt.ocr.loaded:
            await self.rt.ocr.refresh()
        # the page's drafts arrived (a prop change): the drafts-changed effect saw it when it happened, i.e. before
        # anything this request does (on page load, before the window was opened, it only records them)
        self._drafts_changed()

    async def _load_form_tour(self) -> None:
        """ApproverPage's assistantTour: the form's tour as a ClaimableTour (one-element `tours`)."""
        tour_id = self.form_tour_id
        if tour_id is None:
            self.s["formTours"] = []
            return
        loaded = await self.rt.page.load_tour({"tourId": tour_id})
        if loaded is None:
            return  # still "loading": tried again with the next request
        details = loaded["tour"]
        listed = self.rt.page.tour_row(tour_id)
        row = defined(
            tourId=tour_id,
            source=jget(details, "source"),
            destination=jget(details, "destination"),
            tourStartDate=jget(details, "startDate"),
            statusId=jget(listed, "statusId"),
            totalDays=jget(listed, "totalDays"),
        )
        self.s["formTours"] = [row]

    # ── entry points ──
    def system_prompt(self) -> str:
        return NARRATOR_PROMPT

    async def on_open(self) -> None:
        await self._ensure_loaded()
        # CC:440-446: opening the chat asks for the bill list again
        await self.rt.ocr.refresh()

    async def on_launcher(self) -> None:
        await self._ensure_loaded()
        await self.rt.ocr.refresh()

    async def on_poll(self) -> None:
        await self._ensure_loaded()
        await self.rt.ocr.refresh()

    async def on_message(self, text: str) -> None:
        await self._ensure_loaded()
        await self.send(text, show=False)

    async def on_files(self, files: list[IncomingFile]) -> None:
        await self._ensure_loaded()
        await self.add_bills(files)

    async def on_retry(self) -> None:
        await self._ensure_loaded()
        await self.rt.narrator.retry()

    # ── the claim (CC:570-611) ──
    @property
    def claim(self) -> dict[str, Any]:
        return self.s["claim"]

    @staticmethod
    def as_seen(s: dict[str, Any]) -> dict[str, Any]:
        return {
            **s,
            "transportationRows": journeys_in_date_order(s["transportationRows"]),
            "accommodationRows": stays_in_date_order(s["accommodationRows"]),
        }

    def as_stored(self, s: dict[str, Any]) -> dict[str, Any]:
        ids = self.s["storedIds"]
        return {
            **s,
            "transportationRows": in_stored_order(s["transportationRows"], ids["t"]),
            "accommodationRows": in_stored_order(s["accommodationRows"], ids["a"]),
        }

    async def commit(self, nxt: dict[str, Any], from_form: bool = False, replace: bool = False) -> None:
        if replace:
            self.s["storedIds"] = {"t": [], "a": []}
        seen = self.as_seen(nxt)
        self.s["claim"] = seen
        self.s["claimRev"] = int(self.s.get("claimRev") or 0) + 1
        # inside a claim form every change goes straight into the form, in its stored order
        if not from_form and self.in_form:
            try:
                await self.rt.bridge.call("write", {"state": self.as_stored(seen), "replace": replace})
            except BridgeError:
                log.warning("the claim form did not take the assistant's write", exc_info=True)

    def _form_snapshot(self) -> dict[str, Any] | None:
        snap = self.rt.bridge.read()
        if not isinstance(snap, dict) or not isinstance(snap.get("transportationRows"), list):
            return None
        snap = copy.deepcopy(snap)
        return {
            "transportationRows": snap.get("transportationRows") or [],
            "accommodationRows": snap.get("accommodationRows") or [],
            "leaveRows": snap.get("leaveRows") or [],
            "includeAccommodation": bool(snap.get("includeAccommodation")),
            "includeLeave": bool(snap.get("includeLeave")),
        }

    async def sync_from_form(self) -> dict[str, Any]:
        """CC:599-606"""
        if not self.in_form:
            return self.claim
        stored = self._form_snapshot()
        if stored is None:
            return self.claim
        self.s["storedIds"] = {"t": ids_of(stored["transportationRows"]), "a": ids_of(stored["accommodationRows"])}
        await self.commit(stored, True)
        return self.claim

    def open_missing(self, s: dict[str, Any]) -> list[dict[str, Any]]:
        skipped = set(self.s["skipped"])
        return [m for m in find_missing_fields(s) if slot_key(m) not in skipped]

    # ── yes / no (CC:331-362) ──
    def clear_ask_yn(self) -> None:
        self.s["askYN"] = None

    def ask_yes_no(
        self, text: str, yes: Continuation, no: Continuation, yes_label: str = "Yes", no_label: str = "No"
    ) -> None:
        self.s["askYN"] = {"question": text, "yes": yes, "no": no, "yesLabel": yes_label, "noLabel": no_label}
        self.say(text)

    @continuation
    async def ask_answer(self, which: str) -> None:
        """A yes / no chip: the question is closed, then its answer runs."""
        ask = self.s.get("askYN")
        self.clear_ask_yn()
        if ask:
            await self.rt.run(ask[which])

    # ── drafts saved by this chat (CC:378-422) ──
    @property
    def _chat_drafts_key(self) -> str:
        return f"aryaan.chatDrafts.v1.{js_str(nullish(self.rt.emp_id, 'anon'))}"

    def _chat_drafts(self) -> dict[str, Any]:
        try:
            value = json.loads(self.rt.storage.get(self._chat_drafts_key) or "{}")
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    def chat_saved_tours(self) -> set[float]:
        return {js_number(k) for k in self._chat_drafts()}

    def mark_saved_in_chat(self, tour_id: Any) -> None:
        drafts = self._chat_drafts()
        drafts[js_str(tour_id)] = utc_iso_string(now_ms())
        self.rt.storage[self._chat_drafts_key] = js_json_stringify(drafts)

    def forget_saved_in_chat(self, gone: Any) -> None:
        drafts = self._chat_drafts()
        kept = {k: v for k, v in drafts.items() if not gone(js_number(k))}
        if len(kept) != len(drafts):
            self.rt.storage[self._chat_drafts_key] = js_json_stringify(kept)

    def remember_draft(self, row: Any, saved: dict[str, Any]) -> None:
        tour_id = jget(row, "tourId")
        info = next((d for d in self.drafts_info if d["tourId"] == tour_id), None)
        self.s["draftDetails"][js_str(tour_id)] = saved_draft_summary(
            row,
            saved,
            {
                "lastSaved": nullish(jget(info, "lastSaved"), utc_iso_string(now_ms())),
                "savedInChat": js_number(tour_id) in self.chat_saved_tours(),
                "tourDates": tour_dates(row),
            },
        )

    def draft_details_text(self) -> str:
        details = list(self.s["draftDetails"].values())
        if not details:
            return ""
        listed = js_json_stringify([draft_for_model(d) for d in details])
        return f"SAVED DRAFTS IN DETAIL (what each saved claim holds):\n{listed}"

    # ── kept reports (CC:452-481) ──
    @property
    def _kept_key(self) -> str:
        return reports_key(self.rt.emp_id)

    @property
    def report_scope(self) -> str:
        return f"tour-{js_str(self.form_tour_id)}" if self.in_form else "page"

    def say_report(self, text: str, tone: str | None, tour_ids: list[Any]) -> None:
        report = {"scope": self.report_scope, "text": text, "tourIds": tour_ids}
        if tone is not None:
            report = {"scope": self.report_scope, "text": text, "tone": tone, "tourIds": tour_ids}
        report_id = keep_report(self.rt.storage, self._kept_key, report)
        self.say(text, tone, cont("drop_kept_reports", ids=[report_id]))

    def say_kept_reports(self, lead: str | None = None) -> bool:
        kept = reports_for(self.rt.storage, self._kept_key, self.report_scope)
        if not kept:
            return False
        if any(r.get("tone") == "error" for r in kept):
            tone: str | None = "error"
        elif all(r.get("tone") == "success" for r in kept):
            tone = "success"
        else:
            tone = None
        text = "\n\n".join(t for t in [lead, *(r["text"] for r in kept)] if t)
        self.say(text, tone, cont("drop_kept_reports", ids=[r["id"] for r in kept]))
        if not self.in_form:
            ids = {i for r in kept for i in r.get("tourIds") or []}
            self.s["savedTours"] = [t for t in self.tours if jget(t, "tourId") in ids]
        return True

    @continuation
    def drop_kept_reports(self, ids: list[str]) -> None:
        drop_reports(self.rt.storage, self._kept_key, ids)

    def hello(self) -> str:
        """CC:483-486"""
        name = js_trim(self.rt.user_name or "")
        return f"Hello{f', {name}' if name else ''}! 👋\nI'm Aryaan, your Claim Assistant."

    # ── the employee's bills on the server (CC:424-437) ──
    def my_docs(self) -> list[dict[str, Any]]:
        discarded = set(self.s["discarded"])
        form_tour = self.form_tour_id
        return [
            d
            for d in self.rt.ocr.documents
            if (
                is_claim_chat_ref(d.get("reference"), form_tour)
                if form_tour is not None
                else is_raise_claim_chat_ref(d.get("reference"))
            )
            and d.get("documentId") not in discarded
        ]

    def in_flight_docs(self) -> list[dict[str, Any]]:
        return [d for d in self.my_docs() if is_in_flight(d)]

    def ocr_reference(self) -> str:
        return claim_chat_ref(js_str(self.form_tour_id)) if self.in_form else RAISE_CLAIM_CHAT_REF

    # ── effects, in the order the TSX declares them ──
    @effect(max_runs=3)
    async def effect_greeting(self) -> bool:
        """CC:490-538"""
        s = self.s
        if s["greeted"] or self.tours_loading or self.drafts_loading:
            return False
        if not self.rt.active and not self.in_form:
            return False
        if not self.in_form and not self.rt.ocr.loaded:
            return False
        s["greeted"] = True
        tours = self.tours
        if len(tours) == 0:
            self.say(
                f"{self.hello()} You have no approved tours waiting for a claim yet. Once one is approved, I can help "
                "with it."
            )
            return True
        if self.in_form:
            return True  # inside a claim the assistant greets once the tour is loaded
        if self.say_kept_reports(self.hello()):
            return True
        waiting = self.my_docs()
        if waiting:
            name = js_trim(self.rt.user_name or "")
            if any(not is_in_flight(d) for d in waiting):
                s["welcome"] = name
                return True
            n = len(waiting)
            which = "the bill you uploaded is" if n == 1 else f"the {n} bills you uploaded are"
            done = "it's" if n == 1 else "they're"
            self.say(
                f"Hi{f' {name}' if name else ''}, {which} still being read. I'll show what I find and save it to your "
                f"drafts as soon as {done} done. You can close this window meanwhile."
            )
            return True
        draft_ids = self.draft_tour_ids
        with_draft = [t for t in tours if jget(t, "tourId") in draft_ids]
        if not with_draft:
            self.say('Say word for word: "Upload your tour tickets/bills/receipts"')
            return True
        # read what each saved draft holds, for questions about them later
        self.set_busy("Looking at your saved drafts…")
        try:
            shown = with_draft[:10]
            loaded = await asyncio.gather(*(self.load_saved(jget(t, "tourId")) for t in shown))
            for t, saved in zip(shown, loaded, strict=True):
                if saved:
                    self.remember_draft(t, saved)
            s["savedTours"] = shown
            n = len(with_draft)
            self.say(
                f"{self.hello()} You have **{n} saved draft{'' if n == 1 else 's'}**. You can continue one, or upload "
                "new bills."
            )
        finally:
            self.set_busy(None)
        return True

    def drafts_key(self) -> str:
        ids = sorted({js_number(i) for i in self.draft_tour_ids})
        return ",".join(js_str(i) for i in ids)

    @effect(max_runs=3)
    async def effect_drafts_changed(self) -> bool:
        """CC:543-568"""
        if self.drafts_loading or self.drafts_key() == self.s["lastDraftsKey"]:
            return False
        self._drafts_changed()
        return True

    def _drafts_changed(self) -> None:
        """The body of the drafts-changed effect (also run straight after a save, when the old page re-fetched)."""
        s = self.s
        key = self.drafts_key()
        if self.drafts_loading or key == s["lastDraftsKey"]:
            return
        s["lastDraftsKey"] = key
        now = {js_number(i) for i in self.draft_tour_ids}

        def gone(tour_id: Any) -> bool:
            return js_number(tour_id) not in now

        for name in ("savedCache", "draftDetails", "lastSaved"):
            s[name] = {k: v for k, v in s[name].items() if not gone(k)}
        s["savedHere"] = [i for i in s["savedHere"] if not gone(i)]
        self.forget_saved_in_chat(gone)
        s["savedTours"] = [t for t in s["savedTours"] if not gone(jget(t, "tourId"))]
        if self.in_form or not s["greeted"]:
            return
        # only the greeting so far (nothing typed, nothing going on): it is said again, with the drafts as they are now
        untouched = (
            not any(m.get("role") == "user" for m in self.rt.session.messages)
            and s["step"] == "start"
            and not s["busy"]
            and not s["askYN"]
            and len(s["bills"]) == 0
        )
        if untouched:
            self.rt.clear_messages()
            s["greeted"] = False

    @effect(max_runs=20)
    async def effect_settle_on_change(self) -> bool:
        """CC:1241-1244: every change of the claim settles what is in no unsaved claim any more."""
        if self.s["claimRev"] == self.s["settledRev"]:
            return False
        self.s["settledRev"] = self.s["claimRev"]
        await self.settle_taken()
        return True

    @effect(max_runs=1)
    async def effect_read(self) -> bool:
        """CC:1395-1408: read bills are gone through once the chat is open, free, and no question is waiting."""
        s = self.s
        if (
            not self.rt.active
            or s["busy"]
            or self.rt.narrator.writing
            or s["taking"]
            or self.tours_loading
            or self.no_tours
            or not s["greeted"]
        ):
            return False
        if self.in_form and not s["picked"]:
            return False
        if s["askYN"] or s["resetAsked"] or s["step"] in WAIT_STEPS:
            return False
        done = [b for b in self.rt.ocr.batches(self.my_docs()) if b["done"]]
        if not done:
            return False
        await self.take_results(
            {
                **done[0],
                "documents": [d for b in done for d in b["documents"]],
                "local": all(b["local"] for b in done),
            }
        )
        return True

    @effect(max_runs=3)
    async def effect_auto_upload(self) -> bool:
        """CC:1698-1707: bills are read as soon as they are attached (each file is tried automatically once)."""
        s = self.s
        if not s["bills"]:
            s["tried"] = []
        if s["busy"] or not s["bills"]:
            return False
        tried = set(s["tried"])
        fresh = [b for b in s["bills"] if file_key(b["file"]) not in tried]
        if not fresh:
            return False
        s["tried"] = [*s["tried"], *(file_key(b["file"]) for b in s["bills"] if file_key(b["file"]) not in tried)]
        await self.upload_bills()
        return True

    @effect(max_runs=1)
    async def effect_form_start(self) -> bool:
        """CC:1754-1790: inside a claim form the tour is known, so start by looking at where the claim stands."""
        s = self.s
        if not self.in_form or self.tours_loading or len(self.tours) == 0 or s["formStarted"]:
            return False
        s["formStarted"] = True
        row = self.tours[0]
        self.set_busy("Looking at your claim…")
        try:
            loaded = await self.load_tour(row)
            if not loaded:
                s["formStarted"] = False
                self.say("I couldn't load this tour just now. Please close and reopen me in a moment.", "error")
                return True
            s["picked"] = {"row": row, "tour": loaded["tour"], "rules": loaded["rules"]}
            current = await self.sync_from_form()
            has_claim = has_claim_rows(current)
            rng = short_range(row)
            claim_for = (
                f"**{place_name(jget(row, 'source'))} → {place_name(jget(row, 'destination'))}**"
                f"{f' ({rng})' if rng else ''}"
            )
            # bills read into this claim whose report was never seen come first (it ends with "more bills to upload?")
            if self.say_kept_reports(
                f"{self.hello()} Let's {'finish' if has_claim else 'do'} your claim for {claim_for}."
            ):
                s["step"] = "moreFiles"
                s["pending"] = None
            elif not has_claim:
                self.say(f"{self.hello()} Let's do your claim for {claim_for}. Please upload your tickets and bills.")
            else:
                self.say(f"{self.hello()} Let's finish your claim for {claim_for}.")
                s["doneUploading"] = False
                self.after_read(current)
        finally:
            self.set_busy(None)
        return True

    # ── which tour is this for? (CC:613-659) ──
    def show_tour_list(self, tours: list[Any], text: str) -> None:
        self.s["suggested"] = None
        self.s["ranked"] = tours
        self.s["step"] = "pickTour"
        self.s["pending"] = None
        self.say(text)

    def ask_tour(self, s: dict[str, Any]) -> None:
        tours = self.tours
        if len(tours) == 0:
            self.s["step"] = "start"
            self.say(
                "You have no approved tours to claim for right now. Once a tour is approved, I can help you with its "
                "claim.",
                "error",
            )
            return
        matched = match_tours(s, tours)
        order = matched["ranked"]
        confident = matched["confident"]
        ranked = [m["tour"] for m in order]
        # typed details that fit none of the tours (bills that fit none are already left out when they are read)
        if matched["hasClues"] and order[0]["score"] == 0:
            self.show_tour_list(
                ranked,
                "I couldn't match what you've told me to any of your approved tours. Which tour is this claim for?",
            )
            return
        guess = confident["tour"] if confident else (tours[0] if len(tours) == 1 else None)
        if guess is None:
            self.show_tour_list(
                ranked,
                "I couldn't tell for sure which tour these belong to. Which one is it?"
                if order and order[0]["score"] > 0
                else "Which tour is this claim for?",
            )
            return
        self.s["suggested"] = guess
        self.s["ranked"] = ranked
        self.s["step"] = "confirmTour"
        self.s["pending"] = None
        rng = short_range(guess)
        which = (
            f"**{place_name(jget(guess, 'source'))} → {place_name(jget(guess, 'destination'))}**"
            f"{f' ({rng})' if rng else ''}, tour #{js_str(jget(guess, 'tourId'))}"
        )
        self.say(
            f"These bills look like they belong to {which}. Is that right?"
            if confident
            else f"Is this claim for {which}?"
        )

    # ── what to ask next (CC:662-719) ──
    async def advance(self, s: dict[str, Any]) -> None:
        st = self.s
        picked = st["picked"]
        if not picked:
            self.ask_tour(s)
            return
        # inside the claim form the chat never asks about what the claim is missing: it only reads bills into it
        if self.in_form:
            st["pending"] = None
            return
        missing = self.open_missing(s)
        if missing:
            st["step"] = "details"
            st["pending"] = missing[0]
            more = f"\n\n({len(missing) - 1} more after this one)" if len(missing) > 1 else ""
            hint = (
                ""
                if st["hinted"]
                else '\n\n(If you would rather fill something on the form yourself, just say "skip".)'
            )
            st["hinted"] = True
            self.say(missing[0]["question"] + more + hint)
            return
        st["pending"] = None
        has_stay = js_truthy(s["includeAccommodation"]) and any(
            js_truthy(jget(r, "location")) or js_truthy(jget(r, "checkInDate")) for r in s["accommodationRows"]
        )
        if not st["asked"]["stay"] and not has_stay:
            st["asked"]["stay"] = True
            st["step"] = "stay"
            self.say(
                'Did you stay anywhere overnight? You can upload the hotel bill or just tell me, like "guest house in '
                'Ahmedabad, 5th to 8th". Say "no" if you didn\'t.'
            )
            return
        if not st["asked"]["leave"] and not js_truthy(s["includeLeave"]):
            st["asked"]["leave"] = True
            st["step"] = "leave"
            self.say('Were you on leave on any day of the tour? For example, "leave on 7th". Say "no" if not.')
            return
        # before the read-out: the route, cabs and taxis, nights and bills (asked one at a time, like inside the form)
        if not st["reviewPassed"]:
            await self.run_review("check")
            return
        st["reviewPassed"] = False
        st["step"] = "confirm"
        heads = [*find_form_only_issues(s), *find_claim_issues(s, picked["rules"])]
        notes = find_relation_issues(s)
        text = review_text(s)
        if notes:
            text += "\n\n👀 Worth a check:\n" + "\n".join(f"• {n}" for n in notes)
        if heads:
            text += (
                "\n\n⚠️ The form will flag these:\n"
                + "\n".join(f"• {h}" for h in heads)
                + "\nYou can sort them out with me or on the form."
            )
        text += (
            "\n\nIf anything looks off, tell me and I'll fix it. Amounts and bill files are added in the form itself. "
            'When you\'re happy, just say "submit".'
            if self.in_form
            else "\n\nIf anything looks off, tell me and I'll fix it. When you're happy, say \"fill the form\"."
        )
        self.say(text, None if heads else "success")

    # ── bills skipped at upload (CC:722-736) ──
    def take_skipped(self) -> dict[str, list[Any]]:
        skipped = self.s["pendingSkip"]
        self.s["pendingSkip"] = {"duplicate": [], "rejected": []}
        return skipped

    def say_upload_only(self, skipped: dict[str, list[Any]]) -> None:
        fates: dict[str, Any] = {}
        for name in skipped["duplicate"]:
            fates[name] = {"kind": "duplicate"}
        for r in skipped["rejected"]:
            fates[r["name"]] = {"kind": "rejected", "reason": r["reason"]}
        self.say(upload_sorted_text(list(fates), fates), "error" if skipped["rejected"] else None)

    # ── a tour's saved draft (CC:738-803) ──
    async def load_saved(self, tour_id: Any, fresh: bool = False) -> dict[str, Any] | None:
        cache = self.s["savedCache"]
        key = js_str(tour_id)
        if not fresh and not self.in_form and key in cache:
            return cache[key]
        if not fresh and tour_id not in self.draft_tour_ids:
            return None
        try:
            draft = await self.on_load_draft(tour_id)
        except Exception:  # noqa: BLE001 - `.catch(() => null)`
            draft = None
        if not js_truthy(draft):
            return None
        saved = claim_state_from_draft(draft)
        cache[key] = saved
        return saved

    async def save_to_draft(self, row: Any) -> str:
        tour_id = jget(row, "tourId")
        key = js_str(tour_id)
        result = await self.on_save_draft(
            row,
            {"tourId": tour_id, "state": self.as_stored(self.claim)},
            overwrite=key in self.s["savedCache"],
        )
        if result == "saved":
            self.mark_saved_in_chat(tour_id)
            self.s["saveOffered"] = False
            self.s["lastSaved"][key] = claim_key(self.claim)
            # the bills just saved are the server's files now: read the draft back so they are not uploaded again
            saved = await self.load_saved(tour_id, True)
            if saved:
                self.s["storedIds"] = {
                    "t": ids_of(saved["transportationRows"]),
                    "a": ids_of(saved["accommodationRows"]),
                }
                self.s["lastSaved"][key] = claim_key(self.as_seen(saved))
                await self.commit(saved)
                self.remember_draft(row, saved)
        return result

    def offer_draft_save(self, row: Any, s: dict[str, Any]) -> None:
        if (
            self.in_form
            or self.s["saveOffered"]
            or not has_claim_rows(s)
            or claim_key(s) == self.s["lastSaved"].get(js_str(jget(row, "tourId")))
        ):
            return
        self.s["saveOffered"] = True
        self.tell(
            f"The bills are now in a claim for {tour_label(row)}, but it is not saved yet. In one short line, say it "
            "is not saved as a draft yet."
        )

    def offer_save(self, row: Any) -> None:
        """offerSave(row, carryOn) (CC:783-803): `then` is always carryOn = advance (justReadRef is never set)."""
        self.ask_yes_no(
            f"Shall I save the new data into your saved claim for {tour_label(row)}? Nothing is submitted.",
            cont("offer_save_yes", row=row),
            cont("offer_save_no"),
            "Yes, save it",
            "Not now",
        )

    @continuation
    async def offer_save_yes(self, row: Any) -> None:
        self.set_busy("Saving your draft…")
        try:
            result = await self.save_to_draft(row)
            if result == "saved":
                self.say("Saved to your claim. It stays in the Raise Claim tab until you submit it.", "success")
            else:
                self.say('I couldn\'t save it just now. Say "save draft" to try again.', "error")
        finally:
            self.set_busy(None)
        await self.advance(self.claim)

    @continuation
    async def offer_save_no(self) -> None:
        self.say('Okay, nothing is saved. Say "save draft" whenever you want to keep it.')
        await self.advance(self.claim)

    # ── attaching files (CC:804-863) ──
    async def add_bills(self, files: list[IncomingFile]) -> None:
        if not files:
            return
        s = self.s
        if self.no_tours:
            self.say("There is no approved tour to claim for right now.", "error")
            return
        self.clear_ask_yn()
        # files turned away are sorted into the reply with the next read (or at once, when nothing is left to read)
        as_files = [f.as_file() for f in files]
        verdict = check_files(as_files)
        s["pendingSkip"]["rejected"] = [*s["pendingSkip"]["rejected"], *verdict["rejected"]]
        accepted = [f for f, d in zip(files, as_files, strict=True) if any(a is d for a in verdict["accepted"])]
        # the bytes decide what is the same file: a bill that a saved claim holds is never sent to the OCR again
        hashes = [hash_file(f.data) for f in accepted]
        read_files = set(s["readFiles"])
        in_flight_hashes = [s["sentHashes"].get(js_str(d["documentId"])) for d in self.in_flight_docs()]
        known = {
            "read": {h for h, v in s["readHashes"].items() if v["key"] in read_files},
            "staged": {*(b["hash"] for b in s["bills"] if b.get("hash")), *(h for h in in_flight_hashes if h)},
        }
        in_upload: set[str] = set()
        fresh: list[tuple[dict[str, Any], IncomingFile]] = []
        taken = {b["file"]["name"] for b in s["bills"]}
        for f, h in zip(accepted, hashes, strict=True):
            if upload_verdict(h, known, in_upload)["kind"] == "duplicate":
                s["pendingSkip"]["duplicate"] = [*s["pendingSkip"]["duplicate"], f.name]
                continue
            if h:
                in_upload.add(h)
            # two different files can share a name: the bill reader matches results to files by name
            name = f.name
            n = 2
            while name in taken:
                name = _EXTENSION.sub(lambda m, n=n: f" ({n}){m.group(1) or ''}", f.name, count=1)
                n += 1
            taken.add(name)
            fresh.append(({"file": {"name": name, "size": f.size, "type": f.type}, "hash": h}, f))
        # the bill reader takes a limited number of files at a time: the rest wait for the next go
        keep = max(0, MAX_FILES_PER_READ - len(s["bills"]))
        fresh, later = fresh[:keep], fresh[keep:]
        if later:
            shown = [f"• {b['file']['name']}" for b, _ in later[:10]]
            if len(later) > len(shown):
                shown.append(f"• and {len(later) - len(shown)} more")
            self.tell(
                f"I can read up to {MAX_FILES_PER_READ} bills at a time, so I'm reading "
                f"{f'the first {len(fresh)}' if fresh else 'none of these yet'} now. "
                f"Please attach the other {len(later)} after this, in groups of up to {MAX_FILES_PER_READ}:\n"
                + "\n".join(shown)
            )
        if not fresh:
            self.say_upload_only(self.take_skipped())
            return
        staged = []
        for bill, f in fresh:
            s["stageSeq"] = int(s.get("stageSeq") or 0) + 1
            key = f"stage-{s['stageSeq']}"
            await self.rt.files.put(key, bill["file"]["name"], f.type, f.data)
            staged.append({**bill, "key": key})
        s["savedTours"] = []
        s["bills"] = [*s["bills"], *staged]
        self.rt.user_files([b["file"]["name"] for b in staged])

    @continuation
    async def remove_bill(self, file_name: str) -> None:
        """The ✕ on a staged bill (CC:2494)."""
        gone = [b for b in self.s["bills"] if b["file"]["name"] == file_name]
        self.s["bills"] = [b for b in self.s["bills"] if b["file"]["name"] != file_name]
        await self.rt.files.delete(*(b["key"] for b in gone))

    def enforce_air_rule(self, s: dict[str, Any]) -> dict[str, Any]:
        """CC:866-871"""
        picked = self.s["picked"]
        allow_air = picked["rules"].get("allowAir", True) if picked else True
        if allow_air is None:
            allow_air = True
        if allow_air or not any(r.get("ModeOfTravel") == "air" for r in s["transportationRows"]):
            return s
        self.say("One bill shows air travel, but this tour has no air leg, so I left the travel mode empty.", "error")
        return {
            **s,
            "transportationRows": [
                {**r, "ModeOfTravel": "select"} if r.get("ModeOfTravel") == "air" else r
                for r in s["transportationRows"]
            ],
        }

    # ── the employee confirmed (or chose) the tour (CC:897-940) ──
    @continuation
    async def choose_tour(self, row: Any) -> None:
        st = self.s
        self.set_busy("Getting your tour details…")
        try:
            loaded, saved = await asyncio.gather(self.load_tour(row), self.load_saved(jget(row, "tourId")))
            if not loaded:
                self.show_tour_list(
                    st["ranked"] if st["ranked"] else self.tours,
                    "I couldn't load that tour just now. Please try again, or pick another.",
                )
                return
            # a saved claim stays as it is: what was read before the tour was known is added after it
            s = self.claim
            chat_rows = {
                "transportationRows": [r for r in s["transportationRows"] if not is_empty_transport(r)],
                "accommodationRows": [r for r in s["accommodationRows"] if not is_empty_accommodation(r)]
                if js_truthy(s["includeAccommodation"])
                else [],
            }
            vs_saved = compare_with_saved(saved, chat_rows) if saved else None
            st["picked"] = {"row": row, "tour": loaded["tour"], "rules": loaded["rules"]}
            if saved:
                st["storedIds"] = {"t": ids_of(saved["transportationRows"]), "a": ids_of(saved["accommodationRows"])}
            nxt = self.enforce_air_rule(add_to_saved(saved, vs_saved["added"]) if saved and vs_saved else s)
            st["suggested"] = None
            await self.commit(nxt)
            dates = tour_dates(row)
            self.tell(
                f"Claim for {js_str(js_or(jget(row, 'source'), '?'))} → "
                f"{js_str(js_or(jget(row, 'destination'), '?'))}.",
                "success",
                [
                    {"label": "Tour", "value": f"#{js_str(jget(row, 'tourId'))}"},
                    *([{"label": "Dates", "value": dates}] if dates else []),
                ],
            )
            key = js_str(jget(row, "tourId"))
            # a saved draft the bills add nothing to is the claim as it is now
            if saved:
                st["lastSaved"][key] = claim_key(
                    self.as_seen(saved if vs_saved and rows_count(vs_saved["added"]) > 0 else nxt)
                )
            st["saveOffered"] = False
            if saved and vs_saved:
                self.tell(
                    saved_claim_text({"claim": tour_label(row), "saved": saved, **vs_saved, "merged": nxt, "tour": row})
                )
                if rows_count(vs_saved["added"]) > 0:
                    self.offer_save(row)
                    return
            # a new claim for this tour (no saved draft yet): it can be saved as a draft straight away
            if not saved:
                self.offer_draft_save(row, self.claim)
            await self.advance(self.claim)
        finally:
            self.set_busy(None)

    # ── save what we have as a draft (CC:942-996) ──
    @continuation
    async def save_draft_here(self) -> None:
        st = self.s
        if self.in_form:
            form_claim = await self.sync_from_form()
            self.set_busy("Saving your draft…")
            try:
                if await self.rt.bridge.call("saveDraft") is False:
                    raise BridgeError("not saved")
                await self.settle_files_of(form_claim)
                self.say(
                    "Your draft is saved. Nothing has been submitted yet, so you can carry on or come back later.",
                    "success",
                )
            except Exception:  # noqa: BLE001 - as the old catch-all
                self.say("I couldn't save the draft just now. Please try again.", "error")
            finally:
                self.set_busy(None)
            return
        row = st["picked"]["row"] if st["picked"] else None
        s = self.claim
        if not has_claim_rows(s):
            self.say("There's nothing to save yet. Add your tickets or bills, or tell me about the trip first.")
            return
        if not row:
            self.say("Which tour is this for?")
            self.ask_tour(s)
            return
        self.set_busy("Saving your draft…")
        try:
            result = await self.save_to_draft(row)
            if result == "saved":
                self.say(
                    f"Draft saved for {tour_label(row)}. It stays in the Raise Claim tab until you submit it.",
                    "success",
                )
                self.ask_yes_no(
                    "Would you like to open the claim now?",
                    cont("open_saved_claim", row=row),
                    cont("say_text", text="Okay, we can keep working here."),
                    "Yes, open it",
                    "No, keep working here",
                )
            elif result == "exists":
                self.say("This tour has a saved draft I couldn't read just now, so I left it as it is.", "error")
                self.ask_yes_no(
                    "Would you like to open that claim now?",
                    cont("open_saved_claim", row=row),
                    cont("say_text", text="Okay, I left it as it is."),
                    "Yes, open it",
                    "No, not now",
                )
            else:
                self.say("I couldn't save the draft just now. Please try again.", "error")
        finally:
            self.set_busy(None)

    @continuation
    async def open_saved_claim(self, row: Any) -> None:
        self.set_busy("Opening the claim…")
        try:
            if not await self.on_open_claim(row):
                self.say("I couldn't open the claim just now. Please try again.", "error")
        finally:
            self.set_busy(None)

    @continuation
    def change_tour(self) -> None:
        """CC:998-1004"""
        self.s["step"] = "pickTour"
        self.s["suggested"] = None
        self.s["ranked"] = self.tours
        self.s["pending"] = None
        self.say("Okay, which tour is this claim for?")

    # ── results taken, settled, given back (CC:1213-1284) ──
    def _taken(self) -> dict[str, Any]:
        """takenRef, without what the runtime gave back meanwhile (an idle chat's results go back to the server)."""
        held = {js_str(i) for i in self.rt.ocr.held}
        taken = {k: v for k, v in self.s["taken"].items() if k in held}
        self.s["taken"] = taken
        return taken

    async def settle_taken(self, all_: bool = False) -> None:
        taken = self._taken()
        # while a read is gone through, its rows are on their way into the claim: it settles them itself at the end
        if not taken or (self.s["taking"] and not all_):
            return
        in_play = set() if all_ else _file_doc_ids(self.claim)
        done = [
            k
            for k, t in taken.items()
            if all_ or not t.get("file") or int(js_number(t["file"].get("documentId"))) not in in_play
        ]
        for k in done:
            taken.pop(k, None)
        await self.rt.ocr.settle_ocr_results([int(k) for k in done])

    def _taken_ids_of(self, value: Any) -> list[str]:
        files = _file_doc_ids(value)
        return [
            k
            for k, t in self._taken().items()
            if t.get("file") and int(js_number(t["file"].get("documentId"))) in files
        ]

    async def settle_files_of(self, value: Any) -> None:
        ids = self._taken_ids_of(value)
        for k in ids:
            self.s["taken"].pop(k, None)
        await self.rt.ocr.settle_ocr_results([int(k) for k in ids])

    async def give_back_files_of(self, value: Any) -> None:
        ids = self._taken_ids_of(value)
        for k in ids:
            self.s["taken"].pop(k, None)
            if int(k) not in self.s["discarded"]:
                self.s["discarded"].append(int(k))
        await self.rt.ocr.give_back_ocr_results([int(k) for k in ids])

    # ── stop, send, take (CC:1288-1391) ──
    @continuation
    async def stop_reading(self) -> None:
        ids = [d["documentId"] for d in self.in_flight_docs()]
        if not ids:
            return
        self.set_busy("Stopping…")
        try:
            await self.rt.ocr.discard_ocr(ids)
            for i in ids:
                if i not in self.s["discarded"]:
                    self.s["discarded"].append(i)
                self.s["sentHashes"].pop(js_str(i), None)
            self.say("Stopped reading. Nothing was added. Attach the bills again whenever you want me to read them.")
        except Exception as error:  # noqa: BLE001
            fallback = "I couldn't stop the reading just now."
            self.say(f"{_error_message(error) or fallback} Please try again.", "error")
        finally:
            self.set_busy(None)

    @continuation
    async def upload_bills(self) -> None:
        """CC:1306-1340: the staged bills go to the server to be read; nothing here waits for the reading."""
        sending = list(self.s["bills"])
        if not sending:
            return
        n = len(sending)
        self.set_busy(f"Processing {n} bill{'s' if n > 1 else ''}…")
        try:
            files: list[tuple[str, bytes, str]] = []
            for b in sending:
                stored = await self.rt.files.get(b["key"])
                if stored is None:
                    raise RuntimeError("a staged bill is gone")
                files.append((b["file"]["name"], stored.data, b["file"]["type"]))
            docs = await self.rt.ocr.upload_for_ocr(files, self.ocr_reference())
            for d in docs:
                index = d.get("index")
                bill = sending[index] if isinstance(index, int) and 0 <= index < n else None
                if bill and bill.get("hash"):
                    self.s["sentHashes"][js_str(d["documentId"])] = bill["hash"]
            keys = {b["key"] for b in sending}
            self.s["bills"] = [b for b in self.s["bills"] if b["key"] not in keys]
            await self.rt.files.delete(*keys)
            self.say(
                f"{'Your bill is' if n == 1 else f'Your {n} bills are'} being read. This takes a few minutes, and you "
                "can close this window or the browser meanwhile. "
                + (
                    "I'll add what I find to this claim."
                    if self.in_form
                    else "I'll show what I find and save it to your drafts."
                )
            )
        except OcrApiError as error:
            if error.invalid:
                k = len(error.invalid)
                lines = "\n".join(f"• {x['fileName']}: {x['reason']}" for x in error.invalid)
                self.tell(
                    f"The bills were not sent: the server turned away {'this file' if k == 1 else 'these files'}, and "
                    f"then it takes none of them:\n{lines}\n\nRemove {'it' if k == 1 else 'them'} and try again.",
                    "error",
                )
            else:
                fallback = "I couldn't send the bills just now."
                self.say(f"{error.message or fallback} Your files are still here, so please try again.", "error")
        except Exception:  # noqa: BLE001
            log.warning("could not send the staged bills", exc_info=True)
            self.say("I couldn't send the bills just now. Your files are still here, so please try again.", "error")
        finally:
            self.set_busy(None)

    async def take_results(self, batch: dict[str, Any]) -> None:
        """CC:1343-1391: a read upload: its results are taken, its bills fetched, and it is gone through."""
        st = self.s
        st["taking"] = True
        ids = [d["documentId"] for d in batch["documents"]]
        self.set_busy(f"Going through {len(ids)} read bill{'s' if len(ids) > 1 else ''}…")
        mine: list[int] = []
        try:
            mine = await self.rt.ocr.take_ocr_results(ids)
            if not mine:
                # another tab or device has them: the greeting that was waiting for them is said on its own
                if st["welcome"] is not None:
                    st["welcome"] = None
                    st["greeted"] = False
                return
            docs = [d for d in batch["documents"] if d["documentId"] in mine]
            claim = self.claim
            taken = await self.rt.ocr.read_taken_documents(
                docs,
                with_files=True,
                taken_names=row_file_names([*claim["transportationRows"], *claim["accommodationRows"]]),
            )
            for t in taken:
                st["taken"][js_str(t["doc"]["documentId"])] = {
                    "file": t["file"],
                    "key": file_key(t["file"]) if t["file"] else None,
                }
            staged = []
            for t in taken:
                if not t["file"]:
                    continue
                h = st["sentHashes"].get(js_str(t["doc"]["documentId"]))
                if h is None:
                    try:
                        data, _ = await self.rt.ocr.file_bytes(t["file"])
                        h = hash_file(data)
                    except Exception:  # noqa: BLE001 - hashFile resolves to null when the bytes cannot be read
                        h = None
                staged.append({"file": t["file"], "hash": h})
            lost = [t["result"]["fileName"] for t in taken if not t["file"] and not t["result"]["failed"]]
            st["failedDocs"] = [t["doc"]["documentId"] for t in taken if t["result"]["failed"]]
            welcome = st["welcome"]
            st["welcome"] = None
            lost_note = (
                f"⚠️ The server no longer has {', '.join(lost)}, so please attach {'it' if len(lost) == 1 else 'them'} "
                "again on the claim form."
                if lost
                else ""
            )
            if lost_note and self.in_form:
                self.tell(lost_note, "error")
            header_opts: dict[str, Any] = {"earlier": not batch["local"]}
            if welcome is not None:
                header_opts["name"] = welcome
            await self.process_read(
                combine_ocr_results([t["result"] for t in taken]),
                staged,
                {
                    "fileNames": [t["file"]["name"] if t["file"] else t["result"]["fileName"] for t in taken],
                    "header": read_header(len(taken), header_opts),
                    "notes": [lost_note] if lost_note else [],
                },
            )
        except Exception as error:  # noqa: BLE001 - nothing was gone through: the results are given back
            log.warning("could not go through the read bills", exc_info=True)
            for i in mine:
                st["taken"].pop(js_str(i), None)
                if i not in st["discarded"]:
                    st["discarded"].append(i)
            await self.rt.ocr.give_back_ocr_results(mine)
            fallback = "I couldn't go through the read bills just now."
            self.say(
                f"{_error_message(error) or fallback} They are kept on the server; I'll go through them the next time "
                "you open the assistant.",
                "error",
            )
        finally:
            st["taking"] = False
            self.set_busy(None)
            await self.settle_taken()

    @continuation
    async def retry_failed(self) -> None:
        """CC:1414-1428: failed bills are read again on the server, without a new upload."""
        ids = list(self.s["failedDocs"])
        if not ids:
            return
        self.set_busy("Processing them again…")
        try:
            await asyncio.gather(*(self.rt.ocr.api.retry_ocr(i) for i in ids))
            self.s["failedDocs"] = []
            await self.rt.ocr.unhide(ids)
            one = len(ids) == 1
            when = "it's" if one else "they're"
            self.say(
                f"{'That bill is' if one else 'Those bills are'} being processed again. I'll go through "
                f"{'it' if one else 'them'} when {when} done."
            )
        except Exception as error:  # noqa: BLE001
            fallback = "I couldn't send them again just now."
            self.say(f"{_error_message(error) or fallback} Please try again, or attach a clearer copy.", "error")
        finally:
            self.set_busy(None)

    # ── the Raise Claim page: what was read goes straight into the drafts (CC:1430-1556) ──
    def _mark(self, fates: dict[str, Any], files: list[str], fate: dict[str, Any]) -> None:
        for n in files:
            fates[n] = stronger_fate(fates.get(n), fate)

    def _read_names(
        self, read: dict[str, Any] | None, bills: list[dict[str, Any]], skipped: dict[str, Any]
    ) -> list[str]:
        names = list(read["fileNames"]) if read else [b["file"]["name"] for b in bills]
        out: list[str] = []
        for n in [*names, *skipped["duplicate"], *(r["name"] for r in skipped["rejected"])]:
            if n not in out:
                out.append(n)
        return out

    def _remember_read(self, bills: list[dict[str, Any]], fates: dict[str, Any]) -> None:
        """A bill now in a draft (or the claim) is not read again when it is attached again."""
        for b in bills:
            kind = jget(fates.get(b["file"]["name"]), "kind")
            if kind not in ("saved", "alreadySaved"):
                continue
            key = file_key(b["file"])
            if key not in self.s["readFiles"]:
                self.s["readFiles"].append(key)
            if b.get("hash"):
                self.s["readHashes"][b["hash"]] = {"key": key, "name": b["file"]["name"]}

    async def auto_save_read(
        self, result: dict[str, Any], bills: list[dict[str, Any]], read: dict[str, Any] | None
    ) -> None:
        st = self.s
        mapped = map_ocr_to_rows(result, bills)
        all_rows = [*mapped["transportationRows"], *mapped["accommodationRows"]]
        skipped = self.take_skipped()
        names = self._read_names(read, bills, skipped)
        fates: dict[str, Any] = {}
        self._mark(fates, mapped["failedFiles"], {"kind": "unreadable"})
        self._mark(fates, mapped["unmatchedFiles"], {"kind": "nothingFound"})
        self._mark(fates, skipped["duplicate"], {"kind": "duplicate"})
        for r in skipped["rejected"]:
            self._mark(fates, [r["name"]], {"kind": "rejected", "reason": r["reason"]})
        # a hotel bill with a problem on the bill itself (check-out before check-in) is not added
        kept_stays = []
        for row in mapped["accommodationRows"]:
            problem = stay_bill_problem(row, None, [])
            if problem:
                self._mark(fates, row_file_names([row]), {"kind": "problem", "reason": problem})
            else:
                kept_stays.append(row)
        mapped = {**mapped, "accommodationRows": kept_stays}

        # which tour each bill belongs to (all approved tours count, whatever the chat was doing)
        tours = self.tours
        triage = triage_rows(mapped, tours)
        for d in triage["dropped"]:
            self._mark(fates, [k[: k.rfind("|")] for k in d["files"]], {"kind": "noTour"})
        groups = [{**g, "journeys": list(g["journeys"]), "stays": list(g["stays"])} for g in triage["groups"]]
        neutral = [*triage["neutral"]["journeys"], *triage["neutral"]["stays"]]
        if neutral:
            # bills that do not say which tour: they go with the one tour the rest is for, the tour of this chat, or
            # the only tour
            picked_row = st["picked"]["row"] if st["picked"] else None
            if len(groups) == 1:
                home = groups[0]["tour"]
            elif len(groups) == 0:
                home = picked_row if picked_row is not None else (tours[0] if len(tours) == 1 else None)
            else:
                home = None
            if home is not None:
                g = next((x for x in groups if jget(x["tour"], "tourId") == jget(home, "tourId")), None)
                if g is None:
                    g = {"tour": home, "journeys": [], "stays": []}
                    groups.append(g)
                g["journeys"].extend(triage["neutral"]["journeys"])
                g["stays"].extend(triage["neutral"]["stays"])
            else:
                self._mark(fates, row_file_names(neutral), {"kind": "unsure"})

        def short_name(t: Any) -> str:
            return f"{place_name(jget(t, 'source'))} → {place_name(jget(t, 'destination'))}"

        def full_name(t: Any) -> str:
            rng = short_range(t)
            return f"{short_name(t)}{f' ({rng})' if rng else ''}"

        saved_now: list[Any] = []
        with_draft: list[Any] = []
        not_saved: list[Any] = []
        self.set_busy("Saving to your drafts…")
        for g in groups:
            rows = {"transportationRows": g["journeys"], "accommodationRows": g["stays"]}
            files = row_file_names([*g["journeys"], *g["stays"]])
            tour = short_name(g["tour"])
            tour_id = jget(g["tour"], "tourId")
            # the claim being built in this chat is for this tour: the bills go into it, and it is saved
            if st["picked"] and jget(st["picked"]["row"], "tourId") == tour_id:
                merged = merge_ocr_rows(self.claim, rows)
                if merged["addedJourneys"] + merged["addedStays"] == 0:
                    self._mark(fates, files, {"kind": "alreadySaved", "tour": tour})
                    if tour_id in self.draft_tour_ids:
                        with_draft.append(g["tour"])
                    continue
                await self.commit(self.enforce_air_rule(merged["state"]))
                result = await self.save_to_draft(g["tour"])
                if result == "saved":
                    saved_now.append(g["tour"])
                    self._mark(fates, files, {"kind": "saved", "tour": tour})
                else:
                    self._mark(fates, files, {"kind": "saveFailed", "tour": tour})
                continue
            # read fresh from the server: a draft saved a moment ago in this chat counts too
            has_draft = tour_id in self.draft_tour_ids or tour_id in st["savedHere"]
            saved = await self.load_saved(tour_id, True)
            if has_draft and not saved:
                # a saved draft that could not be read is never written over: tried again next time
                self._mark(fates, files, {"kind": "saveFailed", "tour": tour})
                not_saved.append(rows)
                continue
            vs = compare_with_saved(saved, rows) if saved else None
            added = vs["added"] if vs else rows
            if vs:
                self._mark(
                    fates,
                    row_file_names(
                        [*vs["alreadySaved"]["transportationRows"], *vs["alreadySaved"]["accommodationRows"]]
                    ),
                    {"kind": "alreadySaved", "tour": tour},
                )
            if rows_count(added) == 0:
                with_draft.append(g["tour"])
                continue
            state = add_to_saved(saved, added) if saved else merge_ocr_rows(initial_claim_state(), added)["state"]
            result = await self.on_save_draft(g["tour"], {"tourId": tour_id, "state": state}, overwrite=bool(saved))
            added_files = row_file_names([*added["transportationRows"], *added["accommodationRows"]])
            if result == "saved":
                saved_now.append(g["tour"])
                if tour_id not in st["savedHere"]:
                    st["savedHere"].append(tour_id)
                self.mark_saved_in_chat(tour_id)
                st["draftDetails"][js_str(tour_id)] = saved_draft_summary(
                    g["tour"],
                    state,
                    {"lastSaved": utc_iso_string(now_ms()), "savedInChat": True, "tourDates": tour_dates(g["tour"])},
                )
                st["savedCache"].pop(js_str(tour_id), None)  # read again next time: its new files are the server's now
                self._mark(fates, added_files, {"kind": "saved", "tour": tour})
            else:
                self._mark(fates, added_files, {"kind": "saveFailed", "tour": tour})
                not_saved.append(added)
        # results that did not get into a draft are given back, to be gone through the next time the assistant opens
        await self.give_back_files_of(not_saved)
        self._remember_read(bills, fates)

        offered: list[Any] = []
        for t in [*saved_now, *with_draft]:
            if not any(jget(x, "tourId") == jget(t, "tourId") for x in offered):
                offered.append(t)
        self.clear_ask_yn()
        st["pending"] = None
        st["step"] = "start"
        st["savedTours"] = offered
        kinds = [jget(f, "kind") for f in fates.values()]
        bad = any(k in ("saveFailed", "unreadable", "noTour", "problem", "rejected") for k in kinds)
        summary: dict[str, Any] = {
            "header": read["header"] if read and read.get("header") is not None else read_header(len(names), {}),
            "files": names,
            "rows": all_rows,
            "fates": fates,
            "savedTours": [full_name(t) for t in saved_now],
        }
        if read is not None and read.get("notes") is not None:
            summary["notes"] = read["notes"]
        self.say_report(
            read_summary_text(summary),  # type: ignore[arg-type]
            "error" if bad else ("success" if saved_now else None),
            [jget(t, "tourId") for t in offered],
        )

    # ── inside a claim form: what was read goes straight into this claim (CC:1563-1638) ──
    async def form_read(self, result: dict[str, Any], bills: list[dict[str, Any]], read: dict[str, Any] | None) -> None:
        st = self.s
        row = st["picked"]["row"] if st["picked"] else (self.tours[0] if self.tours else None)
        mapped = map_ocr_to_rows(result, bills)
        all_rows = [*mapped["transportationRows"], *mapped["accommodationRows"]]
        skipped = self.take_skipped()
        names = self._read_names(read, bills, skipped)
        fates: dict[str, Any] = {}
        tour = f"{place_name(jget(row, 'source'))} → {place_name(jget(row, 'destination'))}" if row else "this claim"
        self._mark(fates, mapped["failedFiles"], {"kind": "unreadable"})
        self._mark(fates, mapped["unmatchedFiles"], {"kind": "nothingFound"})
        self._mark(fates, skipped["duplicate"], {"kind": "duplicate"})
        for r in skipped["rejected"]:
            self._mark(fates, [r["name"]], {"kind": "rejected", "reason": r["reason"]})
        # a hotel bill with a problem on the bill itself (check-out before check-in, dates outside this tour)
        rules = st["picked"]["rules"] if st["picked"] else None
        kept_stays = []
        for r in mapped["accommodationRows"]:
            problem = stay_bill_problem(r, rules, [])
            if problem:
                self._mark(fates, row_file_names([r]), {"kind": "problem", "reason": problem})
            else:
                kept_stays.append(r)
        mapped = {**mapped, "accommodationRows": kept_stays}
        # bills whose dates are not within this tour do not belong to this claim
        triage = triage_rows(mapped, self.tours, row)
        for d in triage["dropped"]:
            self._mark(fates, [k[: k.rfind("|")] for k in d["files"]], {"kind": "noTour"})
        journeys = [*(r for g in triage["groups"] for r in g["journeys"]), *triage["neutral"]["journeys"]]
        stays = [*(r for g in triage["groups"] for r in g["stays"]), *triage["neutral"]["stays"]]

        # what the claim already has is not added again
        current = await self.sync_from_form()
        have_journeys = {journey_key(r) for r in current["transportationRows"] if not is_empty_transport(r)}
        have_stays = {
            stay_key(r)
            for r in (current["accommodationRows"] if js_truthy(current["includeAccommodation"]) else [])
            if not is_empty_accommodation(r)
        }
        new_journeys = [r for r in journeys if journey_key(r) not in have_journeys]
        new_stays = [r for r in stays if stay_key(r) not in have_stays]
        self._mark(
            fates,
            row_file_names(
                [
                    *(r for r in journeys if journey_key(r) in have_journeys),
                    *(r for r in stays if stay_key(r) in have_stays),
                ]
            ),
            {"kind": "alreadySaved", "tour": tour},
        )

        merged = merge_ocr_rows(current, {"transportationRows": new_journeys, "accommodationRows": new_stays})
        added = {"journeys": merged["addedJourneys"], "stays": merged["addedStays"]}
        notes = list(read.get("notes") or []) if read else []
        saved = True
        if added["journeys"] + added["stays"] > 0:
            allow_air = rules.get("allowAir", True) if rules else True
            if allow_air is None:
                allow_air = True
            nxt = merged["state"]
            if not allow_air and any(r.get("ModeOfTravel") == "air" for r in nxt["transportationRows"]):
                nxt = {
                    **nxt,
                    "transportationRows": [
                        {**r, "ModeOfTravel": "select"} if r.get("ModeOfTravel") == "air" else r
                        for r in nxt["transportationRows"]
                    ],
                }
                notes.append("⚠️ This tour has no air travel, so the travel mode of the flight was left empty.")
            await self.commit(nxt)
            self._mark(fates, row_file_names([*new_journeys, *new_stays]), {"kind": "saved", "tour": tour})
            try:
                saved = await self.rt.bridge.call("saveDraft") is not False
            except Exception:  # noqa: BLE001 - `.catch(() => false)`
                saved = False
            if saved:
                await self.settle_files_of(nxt)
        self._remember_read(bills, fates)

        self.clear_ask_yn()
        st["pending"] = None
        st["step"] = "moreFiles"
        kinds = [jget(f, "kind") for f in fates.values()]
        bad = not saved or any(k in ("unreadable", "noTour", "problem", "rejected") for k in kinds)
        text = read_summary_text(
            {
                "header": read["header"] if read and read.get("header") is not None else read_header(len(names), {}),
                "files": names,
                "rows": all_rows,
                "fates": fates,
                "savedTours": [],
                "notes": notes,
                "claim": {**added, "saved": saved},
            }  # type: ignore[arg-type]
        )
        self.say_report(
            text + "\n\nDo you have more tickets or bills to upload for this claim?",
            "error" if bad else ("success" if added["journeys"] + added["stays"] > 0 else None),
            [jget(row, "tourId")] if row else [],
        )

    async def process_read(
        self, result: dict[str, Any], bills: list[dict[str, Any]], read: dict[str, Any] | None
    ) -> None:
        """CC:1641-1644"""
        if self.in_form:
            await self.form_read(result, bills, read)
        else:
            await self.auto_save_read(result, bills, read)

    def claim_holds(self, s: dict[str, Any]) -> str:
        """CC:1647-1655"""
        journeys = [r for r in s["transportationRows"] if not is_empty_transport(r)]
        stays = (
            [r for r in s["accommodationRows"] if not is_empty_accommodation(r)]
            if js_truthy(s["includeAccommodation"])
            else []
        )
        bills = len(row_file_names([*journeys, *stays]))
        parts = [
            p for p in (journeys and count_of(len(journeys), "journey"), stays and count_of(len(stays), "stay")) if p
        ]
        if not parts:
            return "Your claim has no journeys or stays yet."
        covered = covered_text(s)
        attached = f", with {count_of(bills, 'bill')} attached" if bills else ""
        return (
            f"Your claim has **{'** and **'.join(parts)}**{f', covering **{covered}**' if covered else ''}{attached}."
        )

    def after_read(self, s: dict[str, Any]) -> None:
        """CC:1658-1695. Only its in-form branch is reachable (Spec A §8): outside, chooseTour goes on with advance."""
        self.s["step"] = "moreFiles"
        self.s["pending"] = None
        self.say(f"{self.claim_holds(s)}\n\nDo you have more tickets or bills to upload for this claim?")

    # ── more files (CC:1709-1726) ──
    @continuation
    def has_more_files(self) -> None:
        self.s["step"] = "start"
        self.say("Okay, attach them whenever you are ready. Everything already in your claim stays as it is.")
        # composerRef.current?.pickFiles(): the browser opens its file picker
        self.rt.emit(dict(OPEN_FILE_PICKER))

    @continuation
    def no_more_files(self) -> None:
        self.s["doneUploading"] = True
        # inside a claim form there is nothing more to ask; outside, this step is never reached (Spec A §8)
        self.s["step"] = "start"
        self.s["pending"] = None
        self.say("Okay. Everything I read is in your claim.")

    # ── what is done, what is left (CC:1729-1751) ──
    @continuation
    async def show_progress(self) -> None:
        st = self.s
        s = await self.sync_from_form()
        if self.in_form and st["picked"]:
            items = await self.review_items(s)
            acknowledged, deferred = set(st["acknowledged"]), set(st["deferred"])
            if not items:
                note = f"Nothing is left: the tour is complete and the form's checks pass. {review_text(s)}"
            else:
                lines = "\n".join(
                    f"• {'[must fix] ' if i.get('blocking') else ''}{i['fact']}"
                    + (
                        " (confirmed as it is)"
                        if i["id"] in acknowledged
                        else (" (to fix on the form)" if i["id"] in deferred else "")
                    )
                    for i in items
                )
                note = (
                    f"What is left before the claim can be submitted ({len(items)}). In one sentence, to be said word "
                    f'for word: "{missing_in_short(items) or "Some details need checking."}"\n{lines}'
                )
            self.tell(note, "error" if any(i.get("blocking") for i in items) else ("success" if not items else None))
            return
        has_stay = js_truthy(s["includeAccommodation"]) and any(
            js_truthy(jget(r, "location")) or js_truthy(jget(r, "checkInDate")) for r in s["accommodationRows"]
        )
        progress: dict[str, Any] = {
            "state": s,
            "tour": st["picked"]["row"] if st["picked"] else None,
            "staysAnswered": has_stay or st["asked"]["stay"],
            "leaveAnswered": js_truthy(s["includeLeave"]) or st["asked"]["leave"],
            "doneUploading": st["doneUploading"],
        }
        if st["picked"]:
            progress["rules"] = st["picked"]["rules"]
        report = build_progress(progress)  # type: ignore[arg-type]
        self.tell(progress_text(report), "success" if report["ready"] and not report["heads"] else None)

    # ── handing the claim to the form (CC:1793-1808) ──
    @continuation
    async def do_fill(self) -> None:
        self.set_busy("Opening your claim form…")
        try:
            picked = self.s["picked"]
            if not picked:
                raise RuntimeError("no tour")
            row = picked["row"]
            handed = self.as_stored(self.claim)
            opened = await self.on_fill_form(row, {"tourId": jget(row, "tourId"), "state": handed})
            # the bills handed to the form are the form's now (it saves them to the draft)
            if opened:
                await self.settle_files_of(handed)
                self.tell(
                    "The claim form was filled with the claim and opened. Ask the employee to review each section, "
                    "attach anything missing (amounts and bill files go on the form), then submit.",
                    "success",
                )
            else:
                self.say("I couldn't open the form just now. Your claim is still here, so please try again.", "error")
        except Exception:  # noqa: BLE001
            self.say("I couldn't open the form just now. Your claim is still here, so please try again.", "error")
        finally:
            self.set_busy(None)

    # ── review (CC:1810-1899) ──
    async def review_items(self, s: dict[str, Any]) -> list[dict[str, Any]]:
        st = self.s
        form_problems: list[Any] = []
        if self.in_form:
            try:
                form_problems = list(await self.rt.bridge.call("check") or [])
            except BridgeError:
                form_problems = []
        inp: dict[str, Any] = {
            "state": s,
            "tour": st["picked"]["row"] if st["picked"] else None,
            "formProblems": form_problems,
            "leaveAnswered": "askLeave" in st["acknowledged"]
            or (st["asked"]["leave"] and not js_truthy(s["includeLeave"])),
        }
        if st["picked"]:
            inp["rules"] = st["picked"]["rules"]
        items = review_claim(inp)  # type: ignore[arg-type]
        return items if self.in_form else [i for i in items if i["kind"] not in LIST_PAGE_SKIPS]

    async def run_review(self, mode: str) -> None:
        st = self.s
        st["reviewMode"] = mode
        s = await self.sync_from_form()
        items = await self.review_items(s)
        acknowledged, deferred = set(st["acknowledged"]), set(st["deferred"])
        nxt = next_review_step(items, acknowledged, deferred)  # type: ignore[arg-type]
        asked = st["reviewItem"]
        if nxt["type"] == "ask":
            item, open_ = nxt["item"], nxt["open"]
            st["reviewActive"] = True
            st["reviewItem"] = item
            st["pending"] = None
            st["step"] = "review"
            self.tell(
                review_question_note(item, open_, bool(asked) and asked["id"] == item["id"]),
                "error" if item.get("blocking") else None,
                {"kind": item["kind"], "mustFix": item.get("blocking")},
            )
            return
        st["reviewItem"] = None
        st["reviewActive"] = False
        # what the employee chose to fix on the form, and the form still rejects
        if nxt["type"] == "blocked":
            st["step"] = "confirm"
            self.tell(review_blocked_note(nxt["blocking"]), "error")
            return
        if not self.in_form:
            st["reviewPassed"] = True
            await self.advance(s)
            return
        if mode == "submit":
            st["step"] = "submit"
            st["pending"] = None
            self.tell(review_done_note(review_text(s), True), "success")
            return
        st["step"] = "confirm"
        self.tell(review_done_note(review_text(s), False), "success")

    @continuation
    async def settle_review(self, how: str) -> None:
        """The employee settled the review question: confirmed it, will fix it on the form, or removed the entry."""
        st = self.s
        item = st["reviewItem"]
        if not item:
            await self.run_review(st["reviewMode"])
            return
        if how == "upload":
            self.tell(review_settled_note(item, "upload"))
            return
        if how == "remove" and item.get("section") and item.get("row") and item.get("section") != "leave":
            await self.finish_edits(
                self.claim, [{"section": item["section"], "row": item["row"], "field": "remove", "value": "yes"}], ""
            )
            return
        if how in ("confirm", "later"):
            acknowledged, deferred = set(st["acknowledged"]), set(st["deferred"])
            settle(item, how, acknowledged, deferred)  # type: ignore[arg-type]
            st["acknowledged"] = [*st["acknowledged"], *(i for i in acknowledged if i not in st["acknowledged"])]
            st["deferred"] = [*st["deferred"], *(i for i in deferred if i not in st["deferred"])]
            self.tell(review_settled_note(item, how))  # type: ignore[arg-type]
        await self.run_review(st["reviewMode"])

    # ── "fill the form" (CC:1927-1985) ──
    @continuation
    async def request_fill(self) -> None:
        st = self.s
        if self.in_form:
            self.say("Your form is already up to date as we go, so there's nothing more to fill.")
            await self.show_progress()
            return
        s = self.claim
        if not has_claim_rows(s):
            self.say(
                "There's nothing to fill yet. Add your tickets or hotel bills, or tell me about the journey first."
            )
            return
        if not st["picked"]:
            self.say("Which tour is this for?")
            self.ask_tour(s)
            return
        missing = find_missing_fields(s)
        form_only = [*find_form_only_issues(s), *find_claim_issues(s, st["picked"]["rules"])]
        if not missing and not form_only:
            await self.do_fill()
            return
        st["step"] = "validate"
        st["pending"] = None
        lines = "\n".join(
            x for x in [describe_missing(s, missing) if missing else "", *(f"• {i}" for i in form_only)] if x
        )
        self.tell(
            f"Quick heads-up, the form will flag these when you submit:\n{lines}\n\n"
            + (
                "Would you like to sort them out here with me, or open the form as it is?"
                if missing
                else "Some of these can only be fixed on the form itself. Shall I open it now?"
            )
        )

    @continuation
    async def choose_here(self) -> None:
        st = self.s
        if not find_missing_fields(self.claim):
            await self.do_fill()
            return
        st["skipped"] = []
        st["hinted"] = False
        self.say("Understood, let's sort them out.")
        await self.advance(self.claim)

    @continuation
    async def choose_form(self) -> None:
        await self.do_fill()

    @continuation
    async def skip_pending(self) -> None:
        pending = self.s["pending"]
        if not pending:
            return
        if slot_key(pending) not in self.s["skipped"]:
            self.s["skipped"].append(slot_key(pending))
        self.say("Understood, left for the form.")
        await self.advance(self.claim)

    # ── where the chat is, for the AI model (CC:1995-2027) ──
    def chat_state(self) -> dict[str, Any]:
        st = self.s
        ask = st["askYN"]
        item = st["reviewItem"]
        return {
            "step": st["step"],
            "inForm": self.in_form,
            "noTours": self.no_tours,
            "yesNo": {"question": ask["question"], "yesLabel": ask["yesLabel"], "noLabel": ask["noLabel"]}
            if ask
            else None,
            "resetAsk": st["resetAsked"],
            "suggestedTourId": jget(st["suggested"], "tourId") if st["suggested"] else None,
            "hasTour": bool(st["picked"]),
            "hasBulk": False,
            "hasSplit": False,
            "hasMissing": len(find_missing_fields(self.claim)) > 0,
            "pendingQuestion": st["pending"]["question"] if st["pending"] else None,
            "canUndo": bool(st["undo"]),
            "hasSavedTours": len(st["savedTours"]) > 0,
            "review": {
                "question": item["question"],
                "blocking": item.get("blocking"),
                "resolutions": item.get("resolutions"),
                "yes": item.get("yes"),
                "no": item.get("no"),
                "newJourney": item.get("newJourney"),
                "oddPlace": item.get("kind") == "oddPlace",
            }
            if item
            else None,
        }

    def open_question(self) -> str | None:
        return outside_open_question(self.chat_state())  # type: ignore[arg-type]

    def recent_chat_text(self, messages: list[dict[str, Any]] | None = None) -> str:
        msgs = [m for m in (self.rt.session.messages if messages is None else messages) if not m.get("system")][-6:]
        return "\n".join(
            f"{'Assistant' if m.get('role') == 'bot' else 'Employee'}: "
            + _js_slice(_SPACES.sub(" ", js_str(m.get("text") or "")), 280)
            for m in msgs
        )

    def understand_context(self, s: dict[str, Any]) -> str:
        """CC:2135-2156"""
        st = self.s
        tour = st["picked"]["row"] if st["picked"] else None
        step = st["step"]
        offered = (st["ranked"] if st["ranked"] else self.tours) if step in ("confirmTour", "pickTour") else []
        pend = st["pending"] if step == "details" else None
        tour_text = f"#{js_str(jget(tour, 'tourId'))} {tour_label(tour)}" if tour else "not chosen yet"
        parts = [
            f"Where the chat is: {'inside the claim form' if self.in_form else 'the Raise Claim page'}. Tour: "
            f"{tour_text}.",
            "TOURS ON OFFER:\n" + "\n".join(f"#{js_str(jget(t, 'tourId'))}: {tour_label(t)}" for t in offered)
            if offered
            else "",
            "SAVED CLAIMS:\n" + "\n".join(f"#{js_str(jget(t, 'tourId'))}: {tour_label(t)}" for t in st["savedTours"])
            if st["savedTours"]
            else "",
            f"The open question fills {js_str(pend['section'])} row {js_str(pend['row'])}: {js_str(pend['label'])}."
            if pend
            else "",
            f"CURRENT CLAIM:\n{snapshot(s, st['picked']['tour'] if st['picked'] else None)}",
        ]
        return "\n".join(p for p in parts if p)

    # ── typed messages (CC:2159-2208) ──
    @continuation
    async def send_words(self, text: str) -> None:
        """A chip whose old onClick sent words (`send('yes')`, the travel modes)."""
        await self.send(text, show=True)

    async def send(self, raw: str, *, show: bool) -> None:
        text = clean_message(raw)
        if not text or self.s["busy"] or self.rt.narrator.writing:
            return
        self.rt.narrator.resume()
        before = list(self.rt.session.messages)
        length = js_len(text)
        self.rt.user_message(f"{_js_slice(text, 200)}…" if length > MAX_MESSAGE_CHARS else text, show=show)
        if length > MAX_MESSAGE_CHARS:
            self.rt.narrator.heard(f"{_js_slice(text, 300)}…")
            self.tell(
                f"The employee's message is {length} characters long, more than the {MAX_MESSAGE_CHARS} that can be "
                "read, so it was not read. Ask them to send it in shorter pieces: one journey or stay at a time.",
                "error",
            )
            return
        # the bubble just added is not in the recent chat yet (the old messagesRef was the last render's)
        await self.interpret(text, recent=before)

    @continuation
    async def interpret_again(self, text: str) -> None:
        """ "Try again" after the model could not read the message: the whole interpretation again."""
        await self.interpret(text)

    async def interpret(self, text: str, recent: list[dict[str, Any]] | None = None) -> None:
        st = self.s
        self.rt.narrator.heard(text)
        current = await self.sync_from_form()
        specs = outside_actions(self.chat_state())  # type: ignore[arg-type]
        # what the actions were built with (the render's values, CC:2030-2133)
        captured = {
            "yn": st["askYN"],
            "suggested": st["suggested"],
            "ranked": st["ranked"],
            "savedTours": st["savedTours"],
            "step": st["step"],
            "undo": st["undo"],
            "pending": st["pending"],
        }
        self.set_busy("Thinking…")
        try:
            understood = await understand(
                self.rt.llm,
                system_prompt=UNDERSTAND_PROMPT,
                message=text,
                actions=specs,
                open_question=self.open_question(),
                context=self.understand_context(current),
                recent_chat=self.recent_chat_text(recent) or None,
            )
            chosen = next((a.name for a in specs if a.name == understood.action), specs[0].name)
            # an open question the employee moved on from is closed (as when they tap something else)
            if st["askYN"] and chosen not in ("yes", "no"):
                self.clear_ask_yn()
            if st["resetAsked"] and chosen not in ("confirm_start_over", "start_over"):
                st["resetAsked"] = False
                st["resetAsk"] = False
            await self.run_action(chosen, understood.args, understood.edits, captured)
        except Exception:  # noqa: BLE001 - the model could not read the message: "Try again" sends it again
            log.warning("could not understand the message", exc_info=True)
            self.rt.narrator.heard(None)
            self.rt.narrator.show_unavailable(cont("interpret_again", text=text))
        finally:
            self.set_busy(None)

    def _tour_by_id(self, args: dict[str, Any], tours: list[Any]) -> Any:
        wanted = re.sub(r"^#", "", js_str(nullish(jget(args, "tourId"), "")), count=1)
        return next((t for t in tours if js_str(jget(t, "tourId")) == wanted), None)

    def _not_listed(self) -> None:
        self.tell(
            "The tour the employee named is not one of the tours on offer. Ask them to pick one of the tours shown."
        )

    async def run_action(self, name: str, args: dict[str, Any], edits: list[Any], c: dict[str, Any]) -> None:
        """CC:2030-2131: what each action the AI model can pick does."""
        st = self.s
        if name == "talk":
            self.tell(FORM_TALK_NOTE if self.in_form else TALK_NOTE)
        elif name == "ask_if_ready":
            self.say(FORM_READY_REPLY)
        elif name in ("yes", "no"):
            self.clear_ask_yn()
            if c["yn"]:
                await self.rt.run(c["yn"][name])
        elif name == "confirm_start_over":
            await self.reset_claim()
        elif name == "keep_claim":
            self.tell("The employee chose to keep the claim. Nothing was cleared.")
        elif name == "confirm_tour":
            if c["suggested"]:
                await self.choose_tour(c["suggested"])
            else:
                self._not_listed()
        elif name in ("choose_another_tour", "change_tour"):
            self.change_tour()
        elif name == "pick_tour":
            t = self._tour_by_id(args, c["ranked"] if c["ranked"] else self.tours)
            if t is not None:
                await self.choose_tour(t)
            else:
                self._not_listed()
        elif name == "have_more_files":
            self.has_more_files()
        elif name == "no_more_files":
            self.no_more_files()
        elif name == "fill_here":
            await self.choose_here()
        elif name == "fill_on_form":
            await self.choose_form()
        elif name == "open_form_anyway":
            await self.do_fill()
        elif name == "yes_have_it":
            await self.settle_review("upload")
        elif name in ("yes_keep_it", "no_keep_as_is"):
            await self.settle_review("confirm")
        elif name == "no_remove_it":
            await self.settle_review("remove")
        elif name == "fixed_it":
            await self.settle_review("fixed")
        elif name == "fix_on_form_later":
            await self.settle_review("later")
        elif name in ("no_stay", "no_leave"):
            await self.advance(self.claim)
        elif name == "skip_question":
            await self.skip_pending()
        elif name == "edit":
            valid = read_claim_edits(edits)
            if not valid:
                self.tell(
                    "The employee seemed to give claim details, but none could be read. Ask them to give the detail "
                    "again, more plainly."
                )
                return
            # answering "how did you get to the airport?": the ride is a new journey, so the journeys already in the
            # claim are not written over
            item = st["reviewItem"] if st["step"] == "review" else None
            locked = (
                [i + 1 for i, r in enumerate(self.claim["transportationRows"]) if not is_empty_transport(r)]
                if item and item.get("newJourney")
                else None
            )
            await self.finish_edits(self.claim, valid, "", locked, pending=c["pending"])
        elif name == "show_progress":
            await self.show_progress()
        elif name == "show_claim":
            self.tell(f"The employee asked to see the claim. {review_text(await self.sync_from_form())}")
        elif name == "explain_da":
            self.tell(f"The employee asked how DA is worked out. The rules:\n{DA_RULES}")
        elif name == "tour_info":
            row = st["picked"]["row"] if st["picked"] else None
            self.tell(
                f"The claim is for {tour_label(row)} (tour #{js_str(jget(row, 'tourId'))})."
                if row
                else "No tour is chosen yet. It is found from the bills, or the employee can say which tour it is."
            )
        elif name == "upload_help":
            self.tell(
                "The employee wants to upload bills. Tell them to tap the 📎 button and attach PDFs or photos of their "
                "tickets and hotel bills (JPG or PNG), several at once if they like, up to 20 at a time. Reading takes "
                "a few minutes, and they do not have to wait: the bills are read on the server even if they close the "
                "chat or the browser."
            )
        elif name == "save_draft":
            await self.save_draft_here()
        elif name == "start_over":
            if not has_claim_rows(self.claim):
                self.tell(
                    "The employee asked to start over, but the claim is still empty, so there is nothing to clear."
                )
                return
            st["resetAsked"] = True
            st["resetAsk"] = True
            self.tell(OUTSIDE_START_OVER_ASK)
        elif name == "undo":
            await self.undo_last(c["undo"])
        elif name == "fill_form":
            if c["step"] == "validate":
                await self.do_fill()
            else:
                await self.request_fill()
        elif name == "open_saved_claim":
            t = self._tour_by_id(args, c["savedTours"])
            if t is None:
                self._not_listed()
                return
            self.ask_yes_no(
                f"Open the saved claim for {tour_label(t)} (tour #{js_str(jget(t, 'tourId'))})?",
                cont("open_saved_claim", row=t),
                cont(
                    "tell_note",
                    note="The employee did not want to open that claim. Ask which of the saved claims they want, or "
                    "whether they want to upload more bills.",
                ),
                "Yes, open it",
                "No",
            )
        else:
            # an action of the dead machinery (never offered on a live path): `runs[spec.name] ?? runs.talk`
            self.tell(FORM_TALK_NOTE if self.in_form else TALK_NOTE)

    # ── start over / undo / edits (CC:2211-2257) ──
    async def reset_claim(self) -> None:
        st = self.s
        before = self.claim
        st["resetAsk"] = False  # (resetAskedRef stays set: Spec A §8 quirk 1)
        st["skipped"] = []
        st["hinted"] = False
        st["doneUploading"] = False
        st["asked"] = {"stay": False, "leave": False}
        st["readFiles"] = []
        st["pending"] = None
        staged = [b["key"] for b in st["bills"]]
        st["bills"] = []
        await self.rt.files.delete(*staged)
        await self.commit(initial_claim_state(), False, True)
        st["undo"] = before
        st["step"] = "start"
        self.say(
            'Cleared. Attach your tickets and bills, or tell me about the trip. Say "undo" if you change your mind.'
        )

    @continuation
    async def undo_last(self, undo: Any = UNDEFINED) -> None:
        st = self.s
        previous = st["undo"] if undo is UNDEFINED else undo
        if not previous:
            self.say("There's nothing to undo.")
            return
        await self.commit(previous)
        st["undo"] = None
        self.say("Last change undone.")
        await self.advance(previous)

    async def finish_edits(
        self,
        base: dict[str, Any],
        edits: list[Any],
        answer: str,
        locked: list[int] | None = None,
        *,
        pending: Any = UNDEFINED,
    ) -> None:
        st = self.s
        picked = st["picked"]
        rules = dict(picked["rules"]) if picked else {"allowAir": True}
        if locked is not None:
            rules["lockedJourneys"] = locked
        result = apply_edits(base, edits, rules)  # type: ignore[arg-type]
        nxt, applied, rejected = result["state"], result["applied"], result["rejected"]
        await self.commit(nxt)
        if applied:
            st["undo"] = base
        lines: list[str] = []
        if answer:
            lines.append(answer)
        if applied:
            lines.append("Updated:\n" + "\n".join(f"• {a}" for a in applied))
        if rejected:
            lines.append("I couldn't use these:\n" + "\n".join(f"• {r}" for r in rejected))
        # a change that makes the claim contradict itself is pointed out at once
        if applied:
            claim_rules = picked["rules"] if picked else None
            before = set(find_claim_issues(base, claim_rules))
            fresh = [i for i in find_claim_issues(nxt, claim_rules) if i not in before]
            if fresh:
                lines.append("⚠️ Please check:\n" + "\n".join(f"• {i}" for i in fresh))
        self.tell(
            "\n\n".join(lines) or "There was nothing to change there.", "error" if rejected and not applied else None
        )
        open_pending = st["pending"] if pending is UNDEFINED else pending
        if applied:
            await self.advance(nxt)
        elif open_pending:
            self.tell(open_pending["question"])

    # ── small chip actions ──
    @continuation
    def say_text(self, text: str) -> None:
        self.say(text)

    @continuation
    def tell_note(self, note: str) -> None:
        self.tell(note)

    @continuation
    def saved_not_now(self) -> None:
        self.s["savedTours"] = []
        self.say("Okay. Upload bills or tell me about a trip whenever you are ready.")

    @continuation
    async def show_my_claim(self) -> None:
        self.say(review_text(await self.sync_from_form()))

    # ── the render (CC:2259-2568) ──
    def chips_hidden(self) -> bool:
        return self.no_tours or self.tours_loading or self.rt.narrator.writing or self.s["busy"] == "Thinking…"

    def chips(self) -> list[Chip]:
        st = self.s
        step = st["step"]
        picked = st["picked"]
        ask = st["askYN"]
        bills = st["bills"]
        saved_tours = st["savedTours"]
        claim = self.claim
        in_form = self.in_form
        disabled = bool(st["busy"]) or self.rt.narrator.writing

        def chip(label: str, run: Continuation | None = None, *, primary: bool = False, kind: str = "action") -> Chip:
            return Chip(label, run, kind=kind, primary=primary, disabled=disabled)

        out: list[Chip] = []
        # reviewChips
        item = st["reviewItem"]
        if step == "review" and item:
            resolutions = item.get("resolutions") or []
            if "upload" in resolutions:
                out.append(chip("📎 Upload it", kind="upload", primary=True))
            if not item.get("blocking") and "confirm" in resolutions:
                label = (
                    "Yes, it's right"
                    if item.get("yes") == "confirm"
                    else ("No leave" if item.get("kind") == "askLeave" else "I don't have it / not needed")
                )
                out.append(chip(label, cont("settle_review", how="confirm"), primary="upload" not in resolutions))
            if item.get("no") == "remove":
                out.append(chip("No, remove it", cont("settle_review", how="remove")))
            if item.get("blocking") or "form" in resolutions:
                out.append(
                    chip(
                        "Done, I've fixed it",
                        cont("settle_review", how="fixed"),
                        primary="upload" not in resolutions and "chat" not in resolutions,
                    )
                )
                out.append(chip("I'll fix it on the form", cont("settle_review", how="later")))
        # Read again
        if bills and not st["busy"]:
            out.append(chip("Read again", cont("upload_bills"), primary=True))
        # askChips
        pending = st["pending"]
        if ask:
            out.append(chip(ask["yesLabel"], cont("ask_answer", which="yes"), primary=True))
            out.append(chip(ask["noLabel"], cont("ask_answer", which="no")))
        elif st["resetAsk"]:
            out.append(chip("Yes, start over", cont("send_words", text="yes")))
            out.append(chip("No, keep my claim", cont("send_words", text="no"), primary=True))
        elif step == "stay":
            out.append(chip("📎 Yes, upload hotel bill", kind="upload", primary=True))
            out.append(
                chip(
                    "✍️ Yes, I'll type it",
                    cont("say_text", text='Tell me the city and dates, like "guest house in Ahmedabad, 5th to 8th".'),
                )
            )
            out.append(chip("No stay", cont("send_words", text="no")))
        elif step == "leave":
            out.append(
                chip(
                    "Yes, I was on leave",
                    cont("say_text", text='Which day or days? For example, "leave on 7th" or "leave 7th to 9th".'),
                    primary=True,
                )
            )
            out.append(chip("No leave", cont("send_words", text="no")))
        elif step == "details" and pending:
            if pending.get("slot") == "mode":
                modes = [
                    *([] if picked and picked["rules"].get("allowAir") is False else [("✈️ Air", "air")]),
                    ("🚆 Rail", "rail"),
                    ("🚌 Bus / taxi", "bus or taxi"),
                    ("🚗 Personal vehicle", "personal vehicle"),
                    ("🏢 Official vehicle", "official vehicle"),
                ]
                for i, (label, value) in enumerate(modes):
                    out.append(chip(label, cont("send_words", text=value), primary=i == 0))
            out.append(chip("Skip this one", cont("skip_pending")))
            if not in_form:
                out.append(chip("I'll fill the rest on the form", cont("choose_form")))
        # tourChips
        if step == "confirmTour" and st["suggested"]:
            out.append(chip("Yes, that's right", cont("choose_tour", row=st["suggested"]), primary=True))
            out.append(chip("No, choose another", cont("change_tour")))
        elif step == "pickTour":
            for i, t in enumerate(st["ranked"] if st["ranked"] else self.tours):
                out.append(chip(tour_label(t), cont("choose_tour", row=t), primary=i == 0))
        # moreChips
        if step == "moreFiles":
            out.append(chip("Yes, I'll upload more", cont("has_more_files"), primary=True))
            out.append(chip("No, that's everything", cont("no_more_files")))
        # startChips
        if step == "start" and not bills and not saved_tours and not ask:
            out.append(
                chip(
                    "✍️ I'll type the tour details",
                    cont(
                        "say_text",
                        text="Tell me about your journey, for example "
                        '"Left Noida 5 Oct 10:30 am by air, back on 8th by train".',
                    ),
                )
            )
        # savedChips
        if step == "start" and saved_tours and not ask:
            for t in saved_tours:
                link = claim_link(t, self.rt.origin)
                out.append(Chip(link, None, kind="link", href=link))
            out.append(chip("📎 Upload other bills", kind="upload"))
            out.append(chip("Not now", cont("saved_not_now")))
        # choiceChips (validate)
        if step == "validate":
            missing = len(find_missing_fields(claim)) > 0
            if missing:
                out.append(chip("Sort them out here", cont("choose_here"), primary=True))
            out.append(chip("Open the form anyway", cont("do_fill"), primary=not missing))
        in_tour_step = step in TOUR_STEPS
        unsaved = (
            not in_form
            and bool(picked)
            and not in_tour_step
            and has_claim_rows(claim)
            and claim_key(claim) != st["lastSaved"].get(js_str(jget(picked["row"], "tourId")))
        )
        if unsaved and not ask and step != "submit":
            out.append(chip(SAVE_DRAFT_LABEL, cont("save_draft_here")))
        if not in_form and not in_tour_step and step not in ("gaps", "validate", "start"):
            out.append(chip("Fill the form", cont("request_fill"), primary=step == "confirm"))
        if not in_tour_step and (step != "start" or in_form):
            if not in_form:
                out.append(chip("What's left?", cont("show_progress")))
            out.append(chip("Show my claim", cont("show_my_claim")))
        if not in_tour_step and picked:
            out.append(chip("How is DA worked out?", cont("tell_note", note=DA_RULES)))
        if picked and not in_form and not unsaved and not in_tour_step and step not in ("start", "gaps", "validate"):
            out.append(chip(SAVE_DRAFT_LABEL, cont("save_draft_here")))
        if st["undo"] and not in_tour_step:
            out.append(chip("↩ Undo last change", cont("undo_last")))
        if picked and not in_tour_step and not in_form:
            out.append(chip("↩ Different tour", cont("change_tour")))
        if self.in_flight_docs():
            out.append(chip("⏹ Stop processing", cont("stop_reading")))
        failed = st["failedDocs"]
        if failed:
            out.append(chip(f"↻ Read the unreadable bill{'s' if len(failed) > 1 else ''} again", cont("retry_failed")))
        return out

    def narrator_view(self) -> NarratorView:
        """CC:322: what the narrator reads when it writes."""
        buttons = [] if self.no_tours or self.tours_loading else [c.label for c in self.chips()]
        picked = self.s["picked"]
        page = "\n\n".join(p for p in (self.page_info, self.draft_details_text()) if p)
        return {
            "openQuestion": self.open_question(),
            "buttons": buttons,
            "claim": snapshot(self.claim, picked["tour"] if picked else None),
            "page": page or None,
        }

    def composer(self) -> dict[str, Any]:
        """CC:2550-2567"""
        no_tours, loading = self.no_tours, self.tours_loading
        if no_tours:
            placeholder = "No approved tours to claim for yet"
        elif loading:
            placeholder = "Checking your tours…"
        elif self.s["pending"]:
            placeholder = "Type your answer…"
        else:
            placeholder = "Type a detail, a correction or a question…"
        enabled = not (self.s["busy"] or self.rt.narrator.writing or no_tours or loading)
        return {"placeholder": placeholder, "canType": enabled, "canAttach": enabled}

    def status(self) -> dict[str, Any]:
        """ClaimAssistantLauncher's status line and launcher notice (LAU:60-77), and the typing label (CC:2483)."""
        docs = self.my_docs()
        reading = len([d for d in docs if is_in_flight(d)])
        ready = len(docs) - reading
        if ready > 0:
            notice: str | None = (
                f"Your {'bill is' if ready == 1 else f'{ready} bills are'} read. Open me to go through "
                f"{'it' if ready == 1 else 'them'}."
            )
        elif reading > 0:
            notice = f"Processing {'your bill' if reading == 1 else f'{reading} bills'}…"
        else:
            notice = None
        busy = self.s["busy"]
        picked = self.s["picked"]
        if busy:
            header = "Thinking…"
        elif reading > 0 and not picked:
            header = f"Processing {'1 bill' if reading == 1 else f'{reading} bills'}…"
        elif picked:
            tour_id = js_str(jget(picked["row"], "tourId"))
            header = (
                f"Tour #{tour_id} · guiding your claim" if self.in_form else f"Tour #{tour_id} · you review and submit"
            )
        elif not self.tours_loading and len(self.tours) == 0:
            header = "No approved tours to claim for"
        else:
            header = "Upload your bills to begin"
        typing = busy if busy else ("Checking your tours…" if self.tours_loading else None)
        return {"header": header, "busy": typing, "launcher": notice}

    def bills(self) -> dict[str, list[FilePill]]:
        return {
            "staged": [
                FilePill(b["file"]["name"], remove=cont("remove_bill", file_name=b["file"]["name"]))
                for b in self.s["bills"]
            ],
            "inFlight": [FilePill(js_str(d.get("fileName")), d.get("documentId")) for d in self.in_flight_docs()],
        }


class RaiseClaimFormChat(RaiseClaimChat):
    """ClaimChatbot inside one claim form (ApproverPage): the form is read and written through the bridge."""

    mode = "raise_form"
    IN_FORM: ClassVar[bool] = True


register_flow(RaiseClaimChat)
register_flow(RaiseClaimFormChat)
