"""The Claim Assistant chat on the Within Jurisdiction claim form: port of Tour_React
src/components/claimChatbot/WithinClaimChatbot.tsx (and the texts of WithinClaimAssistantLauncher.tsx).

The employee uploads their monthly Tour Plan in the chat; the Tour OCR reads it, every date becomes a tour on the
form, and the assistant asks for what the plan cannot give (distance for a personal vehicle, a missing mode...) one
question at a time. It also ends tours, answers questions, applies typed corrections, saves the draft, and submits
once the employee has agreed to the declarations in the chat. The claim itself lives in the form: the assistant reads
it from the form snapshot sent with every request (`form.read()` = `{tours}`) and changes it through the bridge
(`write {tours}`, `check`, `info`, `saveDraft`, `submit`; frontend requirements §5.3, Spec D §3).

Every message the employee reads is written by the AI model: this flow does the work and tells the narrator what
happened, and every typed message is read by the model, which picks one of the actions on offer.

The component's state (useState / useRef) is `self.state` (JSON); its callbacks (askYN.yes / no) are named
continuations; its effects are `@effect`s; its render is `chips()`, `composer()`, `status()`, `bills()`.
Line references (`:123`) are WithinClaimChatbot.tsx lines.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any, Final

from tourclaim_agent.claimchat.ai.ai_assistant import ChatAction, narrator_prompt, prompts, understand
from tourclaim_agent.claimchat.ai.assistant_actions import (
    TALK_NOTE,
    WITHIN_DOMAIN,
    WITHIN_START_OVER_ASK,
    WithinChatState,
    within_actions,
    within_open_question,
)
from tourclaim_agent.claimchat.intents import MAX_MESSAGE_CHARS, check_files, clean_message
from tourclaim_agent.claimchat.js_compat import JS_WS, js_len, js_str, js_trim, js_truthy
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
from tourclaim_agent.claimchat.runtime.ocr import is_in_flight
from tourclaim_agent.claimchat.runtime.session import Continuation, cont
from tourclaim_agent.claimchat.runtime.turn import IncomingFile
from tourclaim_agent.claimchat.within_assistant import (
    WITHIN_DA_RULES,
    Gap,
    answer_gap,
    apply_within_edits,
    blank_tour,
    find_gaps,
    has_tour_data,
    progress_text,
    read_within_edits,
    snapshot,
    totals_text,
    tour_discrepancies,
    tour_summary_rows,
)
from tourclaim_agent.claimchat.within_form import DECLARATION_TEXTS
from tourclaim_agent.claimchat.within_tour_plan import WITHIN_TRAVEL_MODES, TourPlanError, map_tour_plan_to_tours

MODE: Final = "within_form"

# Reference of the tour plans this assistant sends (OCR_Frontend_Integration.md) (:70)
PLAN_REF: Final = "within-plan"

# WithinClaimAssistantLauncher.tsx:11: the bubble on the closed launcher
LAUNCHER_GREETING: Final = "Hi, I'm ARYAAN! Upload your monthly tour plan and I'll create every tour for you."

THINKING: Final = "Thinking…"

_SPACES = re.compile(JS_WS + "+")


def plural(n: int, word: str) -> str:
    """:83"""
    return f"{n} {word}{'' if n == 1 else 's'}"


def gap_key(g: Gap) -> str:
    """:84"""
    return f"{js_str(g['tourId'])}:{js_str(g['leg'])}:{g['kind']}"


def _slice(value: str, end: int) -> str:
    """`value.slice(0, end)`, counted in UTF-16 units."""
    if js_len(value) <= end:
        return value
    return value.encode("utf-16-le", "surrogatepass")[: 2 * end].decode("utf-16-le", "surrogatepass")


def _message_of(error: BaseException) -> str:
    """`error?.message` (empty when there is none)."""
    message = getattr(error, "message", None)
    if isinstance(message, str):
        return message
    return str(error)


def _or_message(error: BaseException, fallback: str) -> str:
    """`error?.message || fallback`"""
    return _message_of(error) or fallback


def _leg_text(gap: Gap) -> str:
    return f", leg {gap['leg']}" if gap["leg"] > 0 else ""


def _mode_label(m: str) -> str:
    """:682-685"""
    if m == "Rail":
        return "🚆 Rail"
    if m == "Company Provided Vehicle":
        return "🏢 Company vehicle"
    if m == "Personal Vehicle":
        return "🚗 Personal vehicle"
    if m == "Public Transport":
        return "🚌 Public transport"
    return "🚌 Public transport (beyond entitled)"


MODE_CHOICES: Final = tuple({"label": _mode_label(m), "value": m} for m in WITHIN_TRAVEL_MODES)


class WithinChat(ChatFlow):
    """WithinClaimChatbot.tsx on the server."""

    mode = MODE

    @classmethod
    def initial_state(cls, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "busy": None,
            "greeted": False,  # greetedRef
            "step": "start",  # 'start' | 'gaps' | 'ready' | 'declare'
            "pending": None,  # Gap (pendingRef)
            # the tour plan that could not be read (or is waiting for "replace the tours?"): {key, name, type, size}
            "planFile": None,
            "undo": None,  # undoRef (one level); canUndo = undo is not None
            "skipped": [],  # skippedRef: questions the employee chose to leave for the form
            "askYN": None,  # {question, yes, no, yesLabel, noLabel}: yes / no are continuations
            "resetAsk": False,  # resetAsk / resetAskedRef
            "letGo": [],  # plans this chat let go of (stopped, discarded, or waiting after a failure)
            "agreed": [],  # plans sent with the employee's go-ahead to replace the tours (or to an empty form)
            "heldPlan": None,  # a plan taken and not in the form yet (waiting for "replace the tours?")
            "failedPlan": None,
            "taking": False,  # takingRef
            "nextFile": 1,  # (staged file keys)
        }

    # ── the form (:185-206) ──
    def tours(self) -> list[Any]:
        """form.read(): the tours as they are on the form right now (the snapshot sent with the request)."""
        form = self.rt.form
        tours = form.get("tours") if isinstance(form, dict) else None
        return tours if isinstance(tours, list) else []

    def _set_tours(self, tours: list[Any]) -> None:
        """What the old ref saw straight after `write` (the browser's answer replaces it with the form's state)."""
        form = self.rt.form
        self.rt.session.form = {**(form if isinstance(form, dict) else {}), "tours": tours}

    async def _write(self, tours: list[Any]) -> None:
        """form.write(tours) through the bridge. The old write could not fail: a failed call leaves the form as it
        was and raises (01_deviations.md W1)."""
        before = self.rt.session.form
        self._set_tours(tours)
        try:
            await self.rt.bridge.call("write", {"tours": tours})
        except BridgeError:
            self.rt.session.form = before
            raise

    async def info(self) -> dict[str, Any]:
        """form.info(): {selfApprove, approverChosen}."""
        result = await self.rt.bridge.call("info")
        return result if isinstance(result, dict) else {}

    async def commit(self, next_tours: list[Any]) -> None:
        """every change to the form goes through here, so "undo" can bring the tours back (:188-192)"""
        previous = self.state["undo"]
        self.state["undo"] = self.tours()
        try:
            await self._write(next_tours)
        except BridgeError:
            self.state["undo"] = previous
            raise

    @continuation
    async def undo_last(self) -> None:
        """:194-206"""
        if self.state["undo"] is None:
            self.say("There's nothing to undo.")
            return
        await self._write(self.state["undo"])
        self.state["undo"] = None
        self.state["skipped"] = []
        self.state["pending"] = None
        self.say("Undone. The tours are back as they were.")

    # ── narrator ──
    def tell(self, note: str, tone: str | None = None, *, data: Any = None, has_data: bool = False) -> None:
        """what happened, for the AI model to tell the employee (:140-143)"""
        if has_data:
            self.rt.narrator.tell(note, data=data, tone=tone)
        else:
            self.rt.narrator.tell(note, tone=tone)

    def say(self, text: str, tone: str | None = None) -> None:
        """:146 (also worded by the model)"""
        self.rt.narrator.say(text, tone)

    def ask_yes_no(
        self, text: str, yes: Continuation, no: Continuation, yes_label: str = "Yes", no_label: str = "No"
    ) -> None:
        """a plain yes / no question: shown with buttons, and a typed yes / no works too (:162-167)"""
        self.state["askYN"] = {"question": text, "yes": yes, "no": no, "yesLabel": yes_label, "noLabel": no_label}
        self.tell(text)

    def clear_ask_yn(self) -> None:
        self.state["askYN"] = None

    def system_prompt(self) -> str:
        return narrator_prompt(WITHIN_DOMAIN)

    def narrator_view(self) -> NarratorView:
        """:136"""
        return {
            "openQuestion": self.open_question(),
            "buttons": [c.label for c in self.chips()],
            "claim": snapshot(self.tours()),
        }

    # ── greeting (:209-219) ──
    @effect
    async def effect_greet(self) -> bool:
        if self.state["greeted"]:
            return False
        self.state["greeted"] = True
        current = self.tours()
        name = js_trim(self.rt.user_name or "")
        hello = f"Hello{f', {name}' if name else ''}! 👋\nI'm Aryaan, your Claim Assistant."
        text = (
            f"{hello} Your form has **{plural(len(current), 'tour')}**. Tell me what to change, or upload a new "
            "tour plan."
            if has_tour_data(current)
            else f"{hello} Please upload your monthly tour plan (a PDF or a photo)."
        )
        self.say(text)
        return True

    # ── asking what the plan could not give (:222-244) ──
    async def ask_next(self) -> None:
        skipped = set(self.state["skipped"])
        gap = next((g for g in find_gaps(self.tours()) if gap_key(g) not in skipped), None)
        if gap is None:
            self.state["pending"] = None
            p = progress_text(self.tours(), await self.info())  # type: ignore[arg-type]
            self.state["step"] = "ready"
            self.tell(p["text"], "success" if p["ready"] else None)
            return
        self.state["pending"] = gap
        self.state["step"] = "gaps"
        self.tell(gap["question"])

    @continuation
    async def skip_pending(self) -> None:
        gap = self.state["pending"]
        if not gap:
            return
        key = gap_key(gap)
        if key not in self.state["skipped"]:
            self.state["skipped"].append(key)
        self.say("Okay, I'll leave that one. You can fill it on the form.")
        await self.ask_next()

    # ── reading the tour plan (:249-431) ──
    # The plan is uploaded to the Tour API and read there in the background (it can take minutes), so the employee
    # can close the chat or the browser. Once it is read and the chat is open and free, its tours go into the form.
    def my_plans(self) -> list[dict[str, Any]]:
        let_go = set(self.state["letGo"])
        return [d for d in self.rt.ocr.documents if d.get("reference") == PLAN_REF and d["documentId"] not in let_go]

    def reading_plans(self) -> list[dict[str, Any]]:
        return [d for d in self.my_plans() if is_in_flight(d)]

    def _let_go(self, ids: list[int]) -> None:
        for i in ids:
            if i not in self.state["letGo"]:
                self.state["letGo"].append(i)

    async def on_open(self) -> None:
        # useOcrDocuments: the list is read when the chat is shown
        await self.rt.ocr.refresh()

    @continuation
    async def stop_reading(self) -> None:
        """:269-282"""
        ids = [d["documentId"] for d in self.reading_plans()]
        if not ids:
            return
        self.rt.set_busy("Stopping…")
        try:
            await self.rt.ocr.discard_ocr(ids)
            self._let_go(ids)
            self.say("Stopped reading. Nothing was added. Attach the tour plan again whenever you want me to read it.")
        except Exception as error:  # noqa: BLE001 - as the old catch
            self.say(_or_message(error, "I couldn't stop the reading just now.") + " Please try again.", "error")
        finally:
            self.rt.set_busy(None)

    @continuation
    async def read_plan(self, file: dict[str, Any]) -> None:
        """:284-304 (the old offline check has no server equivalent: 01_deviations.md W2)"""
        self.rt.set_busy("Processing your tour plan…")
        try:
            stored = await self.rt.files.get(file["key"])
            if stored is None:
                raise ValueError("")
            [*docs] = await self.rt.ocr.upload_for_ocr([(stored.name, stored.data, stored.type)], PLAN_REF)
            if docs and docs[0]["documentId"] not in self.state["agreed"]:
                self.state["agreed"].append(docs[0]["documentId"])
            self.state["planFile"] = None
            await self.rt.files.delete(file["key"])
            self.tell(
                f"{file['name']} was sent for reading. In two short lines, tell the employee: reading a tour plan "
                "takes a few minutes on the server, and they don't have to wait: they can close this chat or even the "
                "browser. You'll create the tours as soon as it is read, here or the next time they open the "
                "assistant on this form."
            )
        except Exception as error:  # noqa: BLE001 - as the old catch
            self.state["planFile"] = file
            self.say(
                _or_message(error, "I couldn't send the tour plan just now.") + " Your file is still here, so please "
                "try again.",
                "error",
            )
        finally:
            self.rt.set_busy(None)

    @continuation
    async def apply_plan(self, id: int, raw: Any) -> None:  # noqa: A002 - the old name
        """the read plan's tours go into the form (its result is then settled: it is in the form) (:307-324)"""
        self.state["heldPlan"] = None
        try:
            mapped = map_tour_plan_to_tours(raw)
            plan_tours, warnings = mapped["tours"], mapped["warnings"]
            await self.commit(plan_tours)
            self.state["skipped"] = []
            self.tell(
                f"I read your tour plan and created {plural(len(plan_tours), 'tour')}, one for each date:",
                "success",
                data=tour_summary_rows(plan_tours),
                has_data=True,
            )
            for w in warnings:
                self.tell(w, "error")
            purposes = {js_str(t["tourDetails"]["purpose"]) for t in plan_tours}
            if len(plan_tours) > 1 and len(purposes) == 1:
                self.say(
                    "I couldn't tell which purpose belongs to which date, so every tour has the whole text. Please "
                    "shorten each tour's purpose on the form."
                )
            await self.ask_next()
        except Exception as error:  # noqa: BLE001 - as the old catch
            self.tell(
                str(error)
                if isinstance(error, TourPlanError)
                else "I couldn't make tours from that plan. Please attach a clearer copy.",
                "error",
            )
        finally:
            if id not in self.rt.ocr.held:
                # given back while the chat was idle (runtime R5): taken again, so it does not come back
                with contextlib.suppress(Exception):  # it is in the form either way
                    await self.rt.ocr.take_ocr_results([id])
            await self.rt.ocr.settle_ocr_results([id])

    @continuation
    async def keep_tours_for_plan(self, id: int) -> None:  # noqa: A002 - the old name
        """ "No, keep them" for a plan read while away (:352-357)"""
        self.state["heldPlan"] = None
        self._let_go([id])
        try:
            await self.rt.ocr.discard_ocr([id])
        except Exception:  # noqa: BLE001 - `.catch(() => settleOcrResults([id]))`
            await self.rt.ocr.settle_ocr_results([id])
        self.say("Okay, I left your tours as they are.")

    async def take_plan(self, batch: dict[str, Any]) -> None:
        """a read plan: taken (only this chat gets it), then put into the form, after asking if the form has tours
        now (:327-373)"""
        self.state["taking"] = True
        self.rt.set_busy("Going through your tour plan…")
        mine: list[int] = []
        try:
            mine = await self.rt.ocr.take_ocr_results([d["documentId"] for d in batch["documents"]])
            if not mine:
                return
            [taken, *_] = await self.rt.ocr.read_taken_documents(
                [d for d in batch["documents"] if d["documentId"] in mine], with_files=False
            )
            id_ = taken["doc"]["documentId"]
            result = taken["result"]
            if result["failed"] or not js_truthy(result["response"]):
                await self.rt.ocr.settle_ocr_results([id_])
                self.state["failedPlan"] = id_
                self.say(
                    f"I couldn't read the tour plan {result['fileName']}. Please try again, or attach a clearer copy.",
                    "error",
                )
                return
            raw = result["response"]
            existing = self.tours()
            if id_ not in self.state["agreed"] and has_tour_data(existing):
                self.state["heldPlan"] = id_
                self.ask_yes_no(
                    f"The tour plan {result['fileName']}, sent earlier, has been read. Your form has "
                    f"{plural(len(existing), 'tour')} now, and its tours would replace them. Shall I go ahead? (You "
                    'can say "undo" afterwards.)',
                    cont("apply_plan", id=id_, raw=raw),
                    cont("keep_tours_for_plan", id=id_),
                    "Yes, replace them",
                    "No, keep them",
                )
                return
            if not batch["local"]:
                self.tell(
                    "The tour plan the employee sent earlier has been read on the server while they were away. Say so "
                    "in one line before what follows."
                )
            await self.apply_plan(id_, raw)
        except Exception as error:  # noqa: BLE001 - as the old catch
            self._let_go(mine)
            await self.rt.ocr.give_back_ocr_results([i for i in mine if i in self.rt.ocr.held])
            self.say(
                _or_message(error, "I couldn't go through the tour plan just now.") + " It is kept on the server; "
                "I'll go through it the next time you open the assistant.",
                "error",
            )
        finally:
            self.state["taking"] = False
            self.rt.set_busy(None)

    @effect
    async def effect_read_plan(self) -> bool:
        """the newest read plan is gone through once the chat is open and free (an older one would only be replaced
        by it) (:376-389)"""
        s = self.state
        if (
            not self.rt.active
            or s["busy"]
            or self.rt.narrator.writing
            or s["taking"]
            or s["askYN"]
            or s["resetAsk"]
            or s["step"] == "declare"
        ):
            return False
        done = [b for b in self.rt.ocr.batches(self.my_plans()) if b["done"]]
        if not done:
            return False
        newest = done[-1]
        older = [d["documentId"] for b in done[:-1] for d in b["documents"]]
        if older:
            self._let_go(older)
            with contextlib.suppress(Exception):  # `.catch(() => undefined)`
                await self.rt.ocr.discard_ocr(older)
        await self.take_plan(newest)
        return True

    @continuation
    async def retry_plan(self) -> None:
        """:391-406"""
        id_ = self.state["failedPlan"]
        if id_ is None:
            return
        self.rt.set_busy("Processing it again…")
        try:
            await self.rt.ocr.api.retry_ocr(id_)
            self.state["failedPlan"] = None
            if id_ not in self.state["agreed"]:
                self.state["agreed"].append(id_)
            await self.rt.ocr.unhide([id_])
            self.say("Your tour plan is being processed again. I'll create the tours when it's done.")
        except Exception as error:  # noqa: BLE001 - as the old catch
            self.say(
                _or_message(error, "I couldn't send it again just now.") + " Please try again, or attach a clearer "
                "copy.",
                "error",
            )
        finally:
            self.rt.set_busy(None)

    async def on_files(self, files: list[IncomingFile]) -> None:
        """addFiles (:408-431)"""
        given = [f.as_file() for f in files]
        verdict = check_files(given)
        for p in verdict["problems"]:
            self.tell(p, "error")
        if not verdict["accepted"]:
            return
        first = verdict["accepted"][0]
        incoming = files[next(i for i, f in enumerate(given) if f is first)]
        key = f"plan-{self.state['nextFile']}"
        self.state["nextFile"] += 1
        await self.rt.files.put(key, incoming.name, incoming.type, incoming.data)
        file = {"key": key, "name": incoming.name, "type": incoming.type, "size": incoming.size}
        if len(verdict["accepted"]) > 1:
            self.say(
                f"A tour plan is one file, so I'll read {file['name']}. If your plan has several pages, please send it "
                "as a single PDF."
            )
        existing = self.tours()
        if has_tour_data(existing):
            self.state["planFile"] = file
            self.ask_yes_no(
                f"Your form already has {plural(len(existing), 'tour')}. Reading {file['name']} will replace them. "
                'Shall I go ahead? (You can say "undo" afterwards.)',
                cont("read_plan", file=file),
                cont("keep_tours_for_file"),
                "Yes, replace them",
                "No, keep them",
            )
            return
        await self.read_plan(file)

    @continuation
    def keep_tours_for_file(self) -> None:
        """:421-424"""
        self.state["planFile"] = None
        self.say("Okay, I left your tours as they are.")

    @continuation
    def remove_plan_file(self) -> None:
        """the ✕ on the pending plan file (:786)"""
        self.state["planFile"] = None

    # ── ending tours: all at once, and only when no tour has anything left to resolve (:434-467) ──
    @continuation
    async def end_tours(self) -> None:
        current = self.tours()
        open_ = [(t, i + 1) for i, t in enumerate(current) if not js_truthy(t["tourDetails"].get("tourEnd"))]
        if not open_:
            self.tell(
                "Every tour is already ended."
                if current
                else "There are no tours yet. Attach your monthly tour plan first."
            )
            return
        problems = tour_discrepancies(current)
        if problems:
            shown = [f"• {p}" for p in problems[:8]]
            more = f"\n…and {len(problems) - 8} more" if len(problems) > 8 else ""
            self.tell(
                f"I can't end the tours yet. Please resolve these first:\n{chr(10).join(shown)}{more}\n\nOnce every "
                "tour is complete I can end them all at once.",
                "error",
            )
            # the answerable ones (distance, mode...) are asked here, one at a time
            self.state["skipped"] = []
            if find_gaps(current):
                await self.ask_next()
            return
        n = len(open_)
        names = ", ".join(str(no) for _, no in open_)
        self.ask_yes_no(
            f"No discrepancies found. Shall I end {f'all {n} tours' if n > 1 else 'the tour'} ({names}) now? DA is "
            "worked out once a tour is ended, and an ended tour can't be changed from the chat (untick End Tour on the "
            "form to reopen it).",
            cont("end_tours_confirmed", ids=[t["id"] for t, _ in open_]),
            cont("leave_tours_open"),
            "Yes, end all tours",
            "Not yet",
        )

    @continuation
    async def end_tours_confirmed(self, ids: list[Any]) -> None:
        latest = self.tours()
        wanted = set(ids)
        n = len(ids)
        # a tour's DA is worked out by its DA table, which only exists while the tour is open on screen: so the ended
        # tours are opened
        await self.commit(
            [
                {**t, "isExpanded": True, "tourDetails": {**t["tourDetails"], "tourEnd": True}}
                if t.get("id") in wanted
                else t
                for t in latest
            ]
        )
        self.tell(
            f"Ended {f'all {n} tours' if n > 1 else 'the tour'}. DA is now calculated, and I've opened "
            f"{'the tours' if n > 1 else 'it'} so you can check it. To add hotel stays or travel bills, use Add "
            "Accommodation and Add Travel Costs on each tour.",
            "success",
        )

    @continuation
    def leave_tours_open(self) -> None:
        self.say("Okay, I left the tours open.")

    # ── progress, summary, draft, submit (:470-537) ──
    @continuation
    async def show_progress(self) -> None:
        p = progress_text(self.tours(), await self.info())  # type: ignore[arg-type]
        self.tell(p["text"], "success" if p["ready"] else None)

    @continuation
    def show_tours(self) -> None:
        current = self.tours()
        if not has_tour_data(current):
            self.say("There are no tours yet. Attach your monthly tour plan and I will create them.")
            return
        self.tell(f"Your claim has {plural(len(current), 'tour')}:", data=tour_summary_rows(current), has_data=True)
        self.tell(totals_text(current))

    @continuation
    async def save_draft_here(self) -> None:
        self.rt.set_busy("Saving your draft…")
        try:
            await self.rt.bridge.call("saveDraft")
            self.say(
                "Draft save finished. If no error appeared, it is in the Draft tab and you can resume it any time."
            )
        except Exception:  # noqa: BLE001 - as the old catch
            self.say("I couldn't save the draft. Please try the Save Draft button.", "error")
        finally:
            self.rt.set_busy(None)

    @continuation
    async def start_submit(self) -> None:
        if not has_tour_data(self.tours()):
            self.say("There is nothing to submit yet. Attach your monthly tour plan first.")
            return
        result = await self.rt.bridge.call("check")
        issues = [js_str(i) for i in result] if isinstance(result, list) else []
        if issues:
            shown = [f"• {i}" for i in issues[:8]]
            more = f"\n…and {len(issues) - 8} more" if len(issues) > 8 else ""
            self.tell(f"I can't submit yet:\n{chr(10).join(shown)}{more}", "error")
            return
        self_approve = js_truthy((await self.info()).get("selfApprove"))
        self.state["step"] = "declare"
        declarations = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(DECLARATION_TEXTS))
        self.tell(
            f"{totals_text(self.tours())}\n\nPlease read these declarations:\n{declarations}\n\nDo you agree to all of "
            f"them and want me to {'self-approve and submit' if self_approve else 'submit'} the claim now?"
        )

    @continuation
    async def do_submit(self) -> None:
        """The form's submission; it reports its own problems (a modal), so the chat always says it started
        (Spec D §3.5). The old `aliveRef` check (the page navigated away) has no server equivalent."""
        self.rt.set_busy("Submitting…")
        try:
            await self.rt.bridge.call("submit")
            self.say("I started the submission. If something is missing, a list will appear on the form.")
            self.state["step"] = "ready"
        except Exception:  # noqa: BLE001 - as the old catch
            self.say("The claim couldn't be submitted. Please check the form and try again.", "error")
            self.state["step"] = "ready"
        finally:
            self.rt.set_busy(None)

    async def reset_claim(self) -> None:
        """:529-537"""
        self.state["resetAsk"] = False
        self.state["skipped"] = []
        self.state["pending"] = None
        await self.commit([blank_tour()])
        self.state["step"] = "start"
        self.say('Cleared. Attach your tour plan whenever you are ready. Say "undo" if you change your mind.')

    # ── where the chat is right now, as the AI model is told it (:540-553) ──
    def chat_state(self) -> WithinChatState:
        yn = self.state["askYN"]
        pending = self.state["pending"]
        return {
            "yesNo": {"question": yn["question"], "yesLabel": yn["yesLabel"], "noLabel": yn["noLabel"]} if yn else None,
            "resetAsk": bool(self.state["resetAsk"]),
            "declare": self.state["step"] == "declare",
            "pendingQuestion": pending["question"] if pending else None,
            "canUndo": self.state["undo"] is not None,
        }

    def open_question(self) -> str | None:
        return within_open_question(self.chat_state())

    def recent_chat_text(self) -> str:
        messages = [m for m in self.rt.session.messages if not m.get("system")][-6:]
        return "\n".join(
            f"{'Assistant' if m.get('role') == 'bot' else 'Employee'}: "
            f"{_slice(_SPACES.sub(' ', js_str(m.get('text') or '')), 300)}"
            for m in messages
        )

    # ── what each action the AI model can pick does (:556-627) ──
    async def run_action(self, name: str, args: dict[str, Any], edits: list[Any]) -> None:
        yn = self.state["askYN"]
        gap: Gap | None = self.state["pending"]
        if name == "yes" or name == "no":
            self.clear_ask_yn()
            if yn:
                await self.rt.run(yn[name])
        elif name == "confirm_start_over":
            await self.reset_claim()
        elif name == "keep_tours":
            self.tell("The employee chose to keep the tours. Nothing was cleared.")
        elif name == "agree_and_submit":
            await self.do_submit()
        elif name == "dont_submit_yet":
            self.state["step"] = "ready"
            self.tell("The employee does not want to submit yet. Nothing has been submitted. Ask what to change.")
        elif name == "answer_question":
            if not gap:
                return
            value = js_trim(js_str(args.get("value") if args.get("value") is not None else ""))
            result = (
                answer_gap(self.tours(), gap, value)
                if value
                else {"tours": self.tours(), "error": "No answer could be read from the message."}
            )
            if result.get("error"):
                self.tell(
                    f'The employee\'s answer to "{gap["question"]}" could not be used: {result["error"]} Ask the '
                    "question again.",
                    "error",
                    data={"answer": value},
                    has_data=True,
                )
                return
            await self.commit(result["tours"])
            self.tell(
                f'Saved the employee\'s answer to "{gap["question"]}" on the form.',
                "success",
                data={"answer": value},
                has_data=True,
            )
            await self.ask_next()
        elif name == "skip_question":
            await self.skip_pending()
        elif name == "edit":
            valid = read_within_edits(edits)
            if not valid:
                self.tell(
                    "The employee seemed to give tour details, but none could be read. Ask them to give the detail "
                    "again, more plainly."
                )
                return
            applied = apply_within_edits(self.tours(), valid)
            if applied["applied"]:
                await self.commit(applied["tours"])
            self.tell(
                "\n".join(
                    part
                    for part in (
                        "Changed on the form:\n" + "\n".join(f"• {a}" for a in applied["applied"])
                        if applied["applied"]
                        else "",
                        "Could not change:\n" + "\n".join(f"• {r}" for r in applied["rejected"])
                        if applied["rejected"]
                        else "",
                    )
                    if part
                ),
                "error"
                if applied["rejected"] and not applied["applied"]
                else ("success" if applied["applied"] else None),
            )
            # carry on with the open question, if there is one
            if self.state["pending"] or find_gaps(self.tours()):
                await self.ask_next()
        elif name == "show_progress":
            await self.show_progress()
        elif name == "show_tours":
            self.show_tours()
        elif name == "end_tours":
            await self.end_tours()
        elif name == "check_and_submit":
            await self.start_submit()
        elif name == "hold_submit":
            self.tell("The employee does not want to submit yet. Nothing has been submitted.")
        elif name == "save_draft":
            await self.save_draft_here()
        elif name == "explain_da":
            self.tell(f"The employee asked how DA is worked out. The rules:\n{WITHIN_DA_RULES}")
        elif name == "upload_help":
            self.tell(
                "The employee wants to upload. Tell them to tap the 📎 button and attach their tour plan for one month "
                "(a PDF, or a photo or scan: JPG or PNG). Each date becomes a tour; hotel stays and travel bills are "
                "added on the form."
            )
        elif name == "start_over":
            if not has_tour_data(self.tours()):
                self.tell(
                    "The employee asked to start over, but the form has no tours yet, so there is nothing to clear."
                )
                return
            self.state["resetAsk"] = True
            self.tell(WITHIN_START_OVER_ASK)
        elif name == "undo":
            await self.undo_last()
        else:  # talk, and any name without a runner
            self.tell(TALK_NOTE)

    # ── what the employee types: the AI model reads it and picks what to do (:630-679) ──
    async def on_message(self, text: str) -> None:
        await self.send(text, show=False)

    @continuation
    async def send_words(self, text: str) -> None:
        """a chip that sends words (`send('yes')`): the user bubble is drawn here (runtime R4)"""
        await self.send(text, show=True)

    async def send(self, raw: str, *, show: bool) -> None:
        text = clean_message(raw)
        if not text or self.state["busy"] or self.rt.narrator.writing:
            return
        self.rt.narrator.resume()
        # the old recent chat was read before the new bubble was rendered
        recent = self.recent_chat_text()
        too_long = js_len(text) > MAX_MESSAGE_CHARS
        self.rt.user_message(f"{_slice(text, 200)}…" if too_long else text, show=show)
        if too_long:
            self.rt.narrator.heard(f"{_slice(text, 300)}…")
            self.tell(
                f"The employee's message is {js_len(text)} characters long, more than the {MAX_MESSAGE_CHARS} that "
                "can be read, so it was not read. Ask them to send it in shorter pieces.",
                "error",
            )
            return
        await self.interpret(text, recent)

    @continuation
    async def interpret_again(self, text: str) -> None:
        """ "Try again" after the model could not be reached: the same message is read again."""
        await self.interpret(text, self.recent_chat_text())

    async def interpret(self, text: str, recent: str) -> None:
        self.rt.narrator.heard(text)
        actions = within_actions(self.chat_state())
        self.rt.set_busy(THINKING)
        try:
            gap = self.state["pending"]
            context = "\n".join(
                part
                for part in (
                    f"The open question is about Tour {gap['tourNo']}{_leg_text(gap)}: {gap['kind']}." if gap else "",
                    f"CURRENT TOURS:\n{snapshot(self.tours())}",
                )
                if part
            )
            understood = await understand(
                self.rt.llm,
                system_prompt=prompts()["understand_within"],
                message=text,
                actions=[ChatAction(a.name, a.description, a.args) for a in actions],
                open_question=self.open_question(),
                context=context,
                recent_chat=recent,
            )
            chosen = next((a for a in actions if a.name == understood.action), actions[0])
            if self.state["askYN"] and chosen.name not in ("yes", "no"):
                self.clear_ask_yn()
            if self.state["resetAsk"] and chosen.name not in ("confirm_start_over", "start_over"):
                self.state["resetAsk"] = False
            await self.run_action(chosen.name, understood.args, understood.edits)
        except Exception:  # noqa: BLE001 - every failure shows the fixed notice, as before
            self.rt.narrator.heard(None)
            self.rt.narrator.show_unavailable(cont("interpret_again", text=text))
        finally:
            self.rt.set_busy(None)

    # ── buttons (:682-754) ──
    @continuation
    def tell_da_rules(self) -> None:
        """the "How is DA worked out?" chip: the rules themselves are the note (unlike explain_da)"""
        self.tell(WITHIN_DA_RULES)

    @continuation
    async def yes_no(self, answer: str) -> None:
        """a yes / no chip: runs the question's own yes / no, without the model"""
        yn = self.state["askYN"]
        self.clear_ask_yn()
        if yn:
            await self.rt.run(yn[answer])

    def chips_hidden(self) -> bool:
        return self.rt.narrator.writing or self.state["busy"] == THINKING

    def chips(self) -> list[Chip]:
        s = self.state
        busy, ask_yn, reset_ask, step, pending = s["busy"], s["askYN"], s["resetAsk"], s["step"], s["pending"]
        plan_file = s["planFile"]
        tours = self.tours()
        has_data = has_tour_data(tours)
        current = tours if has_data else []
        has_open_tours = any(not js_truthy(t["tourDetails"].get("tourEnd")) for t in current)
        all_ready_to_end = has_open_tours and len(tour_discrepancies(current)) == 0

        if ask_yn:
            ask_chips = [
                Chip(ask_yn["yesLabel"], cont("yes_no", answer="yes"), primary=True),
                Chip(ask_yn["noLabel"], cont("yes_no", answer="no")),
            ]
        elif reset_ask:
            ask_chips = [
                Chip("Yes, start over", cont("send_words", text="yes")),
                Chip("No, keep my tours", cont("send_words", text="no"), primary=True),
            ]
        elif step == "declare":
            ask_chips = [
                Chip("Yes, I agree — submit", cont("do_submit"), primary=True),
                Chip("Not yet", cont("send_words", text="no")),
            ]
        else:
            ask_chips = []

        gap_chips: list[Chip] = []
        if step == "gaps" and pending and not ask_yn:
            if pending["kind"] == "mode":
                gap_chips += [
                    Chip(m["label"], cont("send_words", text=m["value"]), primary=i == 0)
                    for i, m in enumerate(MODE_CHOICES)
                ]
            if pending["kind"] == "vehicle":
                gap_chips += [
                    Chip("🛵 Two wheeler", cont("send_words", text="Two Wheeler"), primary=True),
                    Chip("🚗 Four wheeler", cont("send_words", text="Four Wheeler")),
                ]
            gap_chips.append(Chip("Skip this one", cont("skip_pending")))

        chips: list[Chip] = []
        if plan_file and not busy and not ask_yn:
            chips.append(Chip("Read again", cont("read_plan", file=plan_file), primary=True))
        if self.reading_plans() and not ask_yn:
            chips.append(Chip("⏹ Stop processing", cont("stop_reading")))
        if s["failedPlan"] is not None and not ask_yn:
            chips.append(Chip("↻ Read it again", cont("retry_plan"), primary=True))
        chips += ask_chips
        chips += gap_chips
        if not has_data and not ask_yn and not plan_file:
            chips.append(Chip("📎 Upload tour plan", kind="upload", primary=True))
        if step != "declare" and not ask_yn and not reset_ask and has_data:
            if has_open_tours and step != "gaps":
                chips.append(Chip("End all tours", cont("end_tours"), primary=all_ready_to_end))
            chips += [
                Chip("What's left?", cont("show_progress")),
                Chip("Show my tours", cont("show_tours")),
                Chip("Check & submit", cont("start_submit"), primary=step == "ready" and not all_ready_to_end),
                Chip("Save draft", cont("save_draft_here")),
                Chip("How is DA worked out?", cont("tell_da_rules")),
            ]
        if s["undo"] is not None and step != "declare":
            chips.append(Chip("↩ Undo last change", cont("undo_last")))
        return chips

    def composer(self) -> dict[str, Any]:
        """:835-845. `highlightUpload`: the 📎 button is highlighted (no tours, nothing typed yet)."""
        typed = any(m.get("role") == "user" for m in self.rt.session.messages)
        return {
            "placeholder": "Type your answer…"
            if self.state["pending"]
            else "Type a detail, a correction or a question…",
            "canType": True,
            "canAttach": True,
            "highlightUpload": not has_tour_data(self.tours()) and not typed,
        }

    def status(self) -> dict[str, Any]:
        """WithinClaimAssistantLauncher.tsx:20, :34 (the header says "Thinking…" while anything runs: thinking =
        !!busy). `greeting` is the closed launcher's resting bubble (:11)."""
        my_plans = self.my_plans()
        reading = len([d for d in my_plans if is_in_flight(d)])
        ready = len(my_plans) - reading
        busy = self.state.get("busy")
        return {
            "header": THINKING
            if busy
            else ("Processing your tour plan…" if reading > 0 else "Upload your tour plan to begin"),
            "busy": busy,
            "launcher": "Your tour plan is read. Open me to create the tours."
            if ready > 0
            else ("Processing your tour plan…" if reading > 0 else None),
            "greeting": LAUNCHER_GREETING,
        }

    def bills(self) -> dict[str, list[FilePill]]:
        """the pending plan file (with ✕) and the plans being read (:779-808)"""
        plan_file = self.state["planFile"]
        return {
            "staged": [FilePill(plan_file["name"], remove=cont("remove_plan_file"))] if plan_file else [],
            "inFlight": [FilePill(d["fileName"], d["documentId"]) for d in self.reading_plans()],
        }


register_flow(WithinChat)
