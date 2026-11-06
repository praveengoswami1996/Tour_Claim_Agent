"""Turns what the flow did into the assistant's reply, written by the AI model: port of useNarrator.ts.

Runtime design §4. `tell()` records an event; once nothing else is running (no OCR, no API call, no reply on its way)
everything told since the last reply goes to the model in one request, and its reply streams into the chat as one
message (`reply_start`, `reply_delta` with the whole text so far, `reply`).

The hook's refs and state live in the session (`session.narrator`), so they survive between requests:
`events` (eventsRef), `said` (saidRef), `failed` (failedRef), `retry` (retryRef, a named continuation),
`unavailable`, `conversationId` (conversationRef), `shown` (shownRef). `writing` is only true while a reply is being
written inside a turn.

What the server cannot reproduce:
- The old reply was aborted when the window closed while it was on its way (AbortError: kept silently, written when
  the window opens again). Here a reply that started is written to the end within the turn; `active` only changes
  with the next request. With `active` false nothing is written: the queue waits (`held`).
- A reply that fails after part of it was shown had its bubble removed; the runtime sends
  `{"type": "reply_remove", "id": ...}` for that (an event the 0.4 contract does not list yet).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from tourclaim_agent.claimchat.ai.ai_assistant import (
    SYSTEM_NOTICE,
    AssistantEvent,
    drop_button_lines,
    is_acknowledgement,
    narrate,
)
from tourclaim_agent.claimchat.runtime.session import Continuation, cont

if TYPE_CHECKING:
    from tourclaim_agent.claimchat.runtime.turn import Turn

# "Try again" after a reply failed: write again (the old `showUnavailable(() => setTick((t) => t + 1))`)
TICK: Final = "narrator.tick"

_NO_DATA: Final = object()


def _tone_of(events: list[dict[str, Any]]) -> str | None:
    """useNarrator.ts toneOf"""
    if any(e.get("tone") == "error" for e in events):
        return "error"
    if events and all(e.get("tone") == "success" for e in events):
        return "success"
    return None


class Narrator:
    """useNarrator for one chat, on top of ai_assistant.narrate."""

    def __init__(self, turn: Turn) -> None:
        self._turn = turn
        self.writing = False

    @property
    def _s(self) -> dict[str, Any]:
        return self._turn.session.narrator

    # ── what the flow uses ──
    @property
    def unavailable(self) -> bool:
        """The model could not be reached: the fixed notice is shown and the only chip is "Try again"."""
        return bool(self._s.get("unavailable"))

    @property
    def has_pending(self) -> bool:
        """Something is waiting to be written (events, a heard message, or a failed reply)."""
        return bool(self._s["events"]) or bool(self._s.get("said")) or self._s.get("failed") is not None

    def tell(self, note: str, data: Any = _NO_DATA, tone: str | None = None) -> None:
        """Something happened that the employee should hear about (`tell(note, {data, tone})`)."""
        event: dict[str, Any] = {"note": note}
        if data is not _NO_DATA:
            event["data"] = data
            event["hasData"] = True
        if tone is not None:
            event["tone"] = tone
        self._s["events"].append(event)

    def say(self, text: str, tone: str | None = None, on_shown: Continuation | None = None) -> None:
        """Something to tell the employee, worded by the model. `on_shown` runs once the reply carrying it is shown."""
        event: dict[str, Any] = {"note": text}
        if tone is not None:
            event["tone"] = tone
        if on_shown is not None:
            event["onShown"] = on_shown
        self._s["events"].append(event)

    def heard(self, text: str | None) -> None:
        """The employee's message: the next reply responds to it."""
        self._s["said"] = text

    def show_unavailable(self, retry: Continuation) -> None:
        """The model could not be reached: the fixed notice, and what "Try again" does."""
        self._s["retry"] = retry
        self._s["unavailable"] = True
        self._turn.add_message(
            {"id": self._turn.session.new_id(), "role": "bot", "text": SYSTEM_NOTICE, "system": True}
        )
        self._turn.emit({"type": "system_notice", "text": SYSTEM_NOTICE})

    async def retry(self) -> None:
        """The "Try again" chip."""
        run = self._s.get("retry")
        self._s["retry"] = None
        self._s["unavailable"] = False
        if run:
            await self._turn.run(run)

    def resume(self) -> None:
        """The employee carried on (typed or tapped something): what could not be said goes out with the next reply."""
        if not self._s.get("retry"):
            return
        self._s["retry"] = None
        self._s["unavailable"] = False

    # ── writing ──
    def ready(self) -> bool:
        """The old write effect's condition: idle, the window open, not writing, not unavailable, something to say."""
        if not self._turn.flow.idle() or not self._turn.session.active or self.writing or self._s.get("retry"):
            return False
        return bool(self._s["events"]) or bool(self._s.get("said")) or self._s.get("failed") is not None

    async def maybe_write(self) -> bool:
        """Writes the reply if the old effect would have; returns whether it tried."""
        if not self.ready():
            return False
        await self.write()
        return True

    async def write(self) -> None:
        """useNarrator.ts write()"""
        if self.writing:
            return
        failed = self._s.get("failed")
        self._s["failed"] = None
        events = [*(failed["events"] if failed else []), *self._s["events"]]
        self._s["events"] = []
        said = self._s.get("said")
        if said is None:
            said = failed.get("said") if failed else None
        self._s["said"] = None
        if not events and not said:
            return

        # what the employee sees, as the last render had it (before "writing" hides the chips)
        view = self._turn.flow.narrator_view()
        self.writing = True
        self._s["unavailable"] = False
        self._s["retry"] = None
        self._turn.emit_status()
        try:
            await self._narrate_run(events, said, view)
        except Exception:  # noqa: BLE001 - every failure shows the fixed notice
            # what the model could not write is sent again with the next reply, or with "Try again"
            self._s["failed"] = {"events": events, "said": said}
            self.show_unavailable(cont(TICK))
        finally:
            self.writing = False

    async def _narrate_run(self, events: list[dict[str, Any]], employee_said: str | None, view: dict[str, Any]) -> None:
        turn = self._turn
        buttons = list(view.get("buttons") or [])
        tone = _tone_of(events)
        message: dict[str, Any] | None = None

        def show(raw: str, streaming: bool) -> None:
            nonlocal message
            text = drop_button_lines(raw, buttons)
            if message is None:
                if not text:
                    return
                message = {"id": turn.session.new_id(), "role": "bot", "text": text}
                if tone is not None:
                    message["tone"] = tone
                turn.add_message(message)
                start: dict[str, Any] = {"type": "reply_start", "id": message["id"]}
                if tone is not None:
                    start["tone"] = tone
                turn.emit(start)
            else:
                message["text"] = text
            if streaming:
                turn.emit({"type": "reply_delta", "id": message["id"], "text": text})
            else:
                final: dict[str, Any] = {"type": "reply", "id": message["id"], "text": text}
                if tone is not None:
                    final["tone"] = tone
                turn.emit(final)

        about_claim = bool(employee_said) and not is_acknowledgement(employee_said or "")
        shown = list(self._s.get("shown") or [])
        try:
            result = await narrate(
                turn.llm,
                system_prompt=turn.flow.system_prompt(),
                events=[
                    AssistantEvent(e["note"], e.get("data"), e.get("tone"), has_data=bool(e.get("hasData")))
                    for e in events
                ],
                conversation_id=self._s.get("conversationId"),
                employee_said=employee_said,
                open_question=view.get("openQuestion"),
                already_shown=shown if about_claim and shown else None,
                # the claim and the page only go with a reply to the employee's typed message (they may ask about
                # them); "ok", "thanks" is not about the claim: it gets neither
                claim=view.get("claim") if about_claim else None,
                page=view.get("page") if about_claim else None,
                bills=view.get("bills") if about_claim else None,
                brief_prompt=turn.flow.brief_prompt(),
                on_text=lambda text: show(text, True) if text else None,
            )
            reply = result.reply
            if employee_said:
                # a typed turn carries the conversation on; what was shown since goes with the next typed turn only
                self._s["conversationId"] = result.conversation_id
                self._s["shown"] = []
            elif reply:
                self._s["shown"] = [*shown, drop_button_lines(reply, buttons)][-4:]
            if not reply:
                raise ValueError("The model sent an empty reply.")
            show(reply, False)
            # the employee has the reply that carries these: what they told is done (e.g. a kept report is dropped)
            for e in events:
                if e.get("onShown"):
                    await turn.run(e["onShown"])
        except Exception:
            if message is not None:
                turn.remove_message(message["id"])
            raise
