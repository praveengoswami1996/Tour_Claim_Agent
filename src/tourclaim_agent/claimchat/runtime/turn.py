"""A request = a turn: the engine that runs the ported chat components on the server.

Runtime design §2-§6. Each HTTP call (`open`, `messages`, `actions`, `attachments`, `poll`, `retry`, `bridge`) is
one turn, run as an asyncio task:

1. load the session (under the conversation's turn lock); set `active` and replace the form snapshot when sent;
2. run the entry point (the old handler for that input);
3. settle the effects: run the flow's `@effect`s in declared order, then the narrator (useNarrator's write effect),
   again and again until nothing fires;
4. emit the derived UI state: `chips`, `composer`, `status` (+ `held`, `draftsChanged`), `bills`;
5. save the session (and the employee's chat storage).

Events go to the HTTP response as they are produced (SSE). A bridge call ends the response; the task waits for
`POST /bridge/{id}`, whose response carries the rest of the turn. Events produced while no response is listening
(after a bridge call timed out) are kept in the session's outbox and sent first with the next request.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from tourclaim_agent.auth.identity import Employee
from tourclaim_agent.claimchat.ai.llm_client import LlmClient
from tourclaim_agent.claimchat.js_compat import now_ms
from tourclaim_agent.claimchat.runtime.bridge import Bridge, BridgeCalls, PendingCall, answer
from tourclaim_agent.claimchat.runtime.flow import ChatFlow, Chip, FilePill, flow_for
from tourclaim_agent.claimchat.runtime.narrator import TICK, Narrator
from tourclaim_agent.claimchat.runtime.ocr import OcrApi, OcrSession
from tourclaim_agent.claimchat.runtime.page import PageServices
from tourclaim_agent.claimchat.runtime.session import (
    ABANDONED,
    IDLE_SECONDS,
    ChatSession,
    Continuation,
    EmployeeStorage,
    Locks,
    SessionStore,
    WorkflowFiles,
    give_back_idle_holdings,
)
from tourclaim_agent.clients.allowlist import Service
from tourclaim_agent.clients.errors import BackendUnauthorized
from tourclaim_agent.clients.http import UserScopedClient
from tourclaim_agent.clients.ocr_api import OcrApiClient
from tourclaim_agent.service import WorkflowNotFound

log = logging.getLogger(__name__)

# the "Try again" chip (both old chats: the only chip while the narrator is unavailable)
TRY_AGAIN_ID: Final = "try-again"
RETRY: Final = "narrator.retry"
# how many effect rounds a turn may take before it is stopped (a flow that loops is a bug)
MAX_ROUNDS: Final = 100
# events that are worked out again at the end of every turn: never kept in the outbox
UI_EVENTS: Final = frozenset({"chips", "composer", "status", "bills"})
ENTRIES: Final = ("open", "message", "chip", "files", "poll", "retry")

_UNSET: Final = object()


class UnknownMode(Exception):
    """POST /workflows or /sessions with a mode no flow serves."""


@dataclass(frozen=True)
class ChatUser:
    """The authenticated caller of a chat request. The token lives only for the request (and its turn)."""

    token: str
    employee: Employee
    # the old Redux `user.unique_name` (the token's), else the employee's name from the Organisation service
    name: str = ""
    # the page's origin (window.location.origin), for the saved-claim link chips
    origin: str = ""


@dataclass
class IncomingFile:
    """A file attached in the browser (POST /attachments): the old `File` (name, type, size) with its bytes."""

    name: str
    type: str
    size: int
    data: bytes = field(repr=False)

    def as_file(self) -> dict[str, Any]:
        """The file as the ported code reads a File: {name, size, type}."""
        return {"name": self.name, "size": self.size, "type": self.type}


@dataclass
class TurnInput:
    entry: str
    active: bool | None = None
    form: Any = _UNSET
    text: str | None = None
    chip: str | None = None
    files: list[IncomingFile] = field(default_factory=list)


class _Failed:
    """A step of the turn failed (reported already)."""

    def __bool__(self) -> bool:
        return True


FAILED: Final = _Failed()


class _Pause:
    """The response ends here (a bridge call is waiting for the browser)."""


class _End:
    """The turn is over."""


@dataclass
class TurnFailed:
    """The turn failed before it sent anything: the HTTP response becomes this error."""

    error: BaseException


PAUSE: Final = _Pause()
END: Final = _End()

Channel = asyncio.Queue
"""Events of a turn on their way to one HTTP response: dicts, then PAUSE or END (or TurnFailed)."""


class Turn:
    """One request on one chat: what the flow sees as `self.rt`."""

    def __init__(
        self,
        runtime: ChatRuntime,
        session: ChatSession,
        user: ChatUser,
        flow_cls: type[ChatFlow],
        storage: EmployeeStorage,
    ) -> None:
        self.runtime = runtime
        self.session = session
        self.user = user
        self.storage = storage
        self.files = WorkflowFiles(runtime.store, session.workflow_id)
        client = runtime.tour_client(user.token)
        self.ocr = OcrSession(
            OcrApi(OcrApiClient(client), read_only=user.employee.read_only),
            session,
            self.files,
            runtime.locks.ocr(session.emp_code),
        )
        self.page = PageServices(self, client, user.employee.emp_id)
        self.bridge = Bridge(self, runtime.bridges, runtime.bridge_timeout)
        self.narrator = Narrator(self)
        self.llm = runtime.llm
        self.flow = flow_cls(self)
        self.drafts_changed = False
        self.task: asyncio.Task[Any] | None = None
        self._channel: Channel | None = None
        self._detached: list[dict[str, Any]] = []
        self.emitted = False

    # ── who and where ──
    @property
    def employee(self) -> Employee:
        return self.user.employee

    @property
    def emp_id(self) -> int:
        return self.user.employee.emp_id

    @property
    def read_only(self) -> bool:
        return self.user.employee.read_only

    @property
    def user_name(self) -> str:
        return self.user.name

    @property
    def origin(self) -> str:
        return self.user.origin

    @property
    def mode(self) -> str:
        return self.session.mode

    @property
    def state(self) -> dict[str, Any]:
        return self.session.state

    @property
    def active(self) -> bool:
        return self.session.active

    @property
    def form(self) -> Any:
        return self.session.form

    @staticmethod
    def now_ms() -> int:
        return now_ms()

    # ── events ──
    def emit(self, event: dict[str, Any]) -> None:
        """Sends an event to the browser (now, or with the next response if no one is listening)."""
        self.emitted = True
        if self._channel is not None:
            self._channel.put_nowait(event)
        else:
            self._detached.append(event)

    def pause(self) -> None:
        """Ends the current HTTP response (a bridge call was sent)."""
        if self._channel is not None:
            self._channel.put_nowait(PAUSE)
            self._channel = None

    def attach(self, channel: Channel) -> None:
        """A response is listening again (the bridge answer)."""
        self._channel = channel
        detached, self._detached = self._detached, []
        for event in detached:
            channel.put_nowait(event)

    @property
    def channel(self) -> Channel | None:
        """The response listening now (None while a bridge call waits for the browser)."""
        return self._channel

    def keep_undelivered(self) -> None:
        """Events no response listened to (a bridge call timed out) go out with the next request."""
        self.session.data["outbox"] = [
            *self.session.data.get("outbox", []),
            *(e for e in self._detached if e.get("type") not in UI_EVENTS),
        ]
        self._detached = []

    def close(self) -> None:
        """The turn is over: the listening response ends."""
        self.keep_undelivered()
        if self._channel is not None:
            self._channel.put_nowait(END)
            self._channel = None

    # ── bubbles ──
    def add_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Adds a bubble to the chat's messages (for the model's recent chat); emits nothing."""
        return self.session.add_message(message)

    def remove_message(self, message_id: str) -> None:
        self.session.data["messages"] = [m for m in self.session.messages if m.get("id") != message_id]
        self.emit({"type": "reply_remove", "id": message_id})

    def clear_messages(self) -> None:
        """The old `setMessages([])` (the chat is greeted again from scratch)."""
        self.session.data["messages"] = []
        self.emit({"type": "messages_cleared"})

    def user_message(self, text: str, *, show: bool) -> dict[str, Any]:
        """A user bubble. `show` = the browser has not drawn it (a chip that sends words), so it is sent to it."""
        message = self.add_message({"id": self.session.new_id(), "role": "user", "text": text})
        if show:
            self.emit({"type": "user_message", "text": text})
        return message

    def user_files(self, names: list[str]) -> None:
        """One `📎 name` user bubble per accepted file."""
        for name in names:
            self.add_message({"id": self.session.new_id(), "role": "user", "text": f"📎 {name}"})
        if names:
            self.emit({"type": "user_files", "names": list(names)})

    # ── busy, status ──
    def set_busy(self, label: str | None) -> None:
        """The old setBusy(label): the typing label while it runs (`status.busy`)."""
        self.state["busy"] = label
        self.emit_status()

    def status_event(self) -> dict[str, Any]:
        event: dict[str, Any] = {"type": "status", **self.flow.status()}
        event["held"] = not self.session.active and self.narrator.has_pending
        if self.drafts_changed:
            event["draftsChanged"] = True
        return event

    def emit_status(self) -> None:
        self.emit(self.status_event())

    # ── continuations ──
    async def run(self, cont: Continuation) -> Any:
        """Runs a named continuation: [name, {args}]."""
        name = cont[0]
        args = cont[1] if len(cont) > 1 and isinstance(cont[1], dict) else {}
        if name == TICK:
            return None
        if name == RETRY:
            await self.narrator.retry()
            return None
        method = self.flow.continuations().get(name)
        if method is None:
            log.warning("chat %s: no continuation %r", self.session.mode, name)
            return None
        result = getattr(self.flow, method)(**args)
        if inspect.isawaitable(result):
            result = await result
        return result

    # ── the turn ──
    async def execute(self, data: TurnInput) -> None:
        """Runs the whole turn (the caller holds the turn lock)."""
        session = self.session
        if data.active is not None:
            session.active = data.active
        if data.form is not _UNSET:
            session.form = data.form
        outbox, session.data["outbox"] = session.data.get("outbox", []), []
        for event in outbox:
            self.emit(event)

        await self._guarded(lambda: self._entry(data))
        await self._settle()
        self._emit_ui()
        self.keep_undelivered()
        # saved before the response ends, so the next request sees this turn's state
        await self.runtime.store.save(session)
        await self.runtime.store.save_storage(session.emp_code, self.storage)
        self.close()

    async def launcher(self) -> dict[str, Any]:
        """POST /sessions with a mode: the launcher's state before the window is opened (nothing is saved)."""
        await self._guarded(self.flow.on_launcher)
        return {"status": self.status_event(), "bills": self._bills_event({})}

    async def _entry(self, data: TurnInput) -> None:
        flow = self.flow
        if data.entry == "open":
            await flow.on_open()
        elif data.entry == "message":
            await flow.on_message(data.text or "")
        elif data.entry == "files":
            await flow.on_files(data.files)
        elif data.entry == "poll":
            await flow.on_poll()
        elif data.entry == "retry":
            await flow.on_retry()
        elif data.entry == "chip":
            await self._chip(data.chip or "")
        elif data.entry == "close":
            await flow.on_close()
        else:
            raise ValueError(f"unknown entry {data.entry!r}")

    async def _chip(self, chip_id: str) -> None:
        """A chip tap: what that chip on screen does. Every chip first resumes the narrator; "Try again" retries."""
        cont = (self.session.data.get("chipActions") or {}).get(chip_id)
        if chip_id == TRY_AGAIN_ID or (cont and cont[0] == RETRY):
            await self.narrator.retry()
            return
        if not cont:
            log.info("chat %s: chip %s is not on screen any more", self.session.mode, chip_id)
            return
        self.narrator.resume()
        await self.run(cont)

    async def _guarded(self, step: Callable[[], Awaitable[Any]]) -> Any:
        """Runs a step of the turn. A failure is logged and reported; before anything was sent it fails the request
        (so an expired session is a 401)."""
        try:
            return await step()
        except asyncio.CancelledError:
            raise
        except BackendUnauthorized:
            if not self.emitted:
                raise
            self.emit(
                {
                    "type": "error",
                    "error": "session_expired",
                    "message": "Your session has expired. Please sign in again.",
                }
            )
            return FAILED
        except Exception:  # noqa: BLE001 - the old handlers' unhandled rejections left the chat as it was
            log.exception("chat %s: a step of the turn failed", self.session.mode)
            self.state["busy"] = None
            self.emit({"type": "error", "error": "turn_failed", "message": "Something went wrong. Please try again."})
        return FAILED

    async def _settle(self) -> None:
        """Effects until quiet, the narrator in between (design §3, §4)."""
        runs: dict[str, int] = {}
        for _ in range(MAX_ROUNDS):
            fired = False
            for name, max_runs in self.flow.effects():
                if runs.get(name, 0) >= max_runs:
                    continue
                did = await self._guarded(getattr(self.flow, name))
                if did:  # (a failed run counts too: it is not tried again in this turn)
                    runs[name] = runs.get(name, 0) + 1
                    fired = True
            if await self._guarded(self.narrator.maybe_write) is True:
                fired = True
            if not fired:
                return
        log.error("chat %s: effects did not settle in %d rounds", self.session.mode, MAX_ROUNDS)

    def _emit_ui(self) -> None:
        actions: dict[str, Continuation] = {}
        if self.flow.chips_hidden():
            chips: list[Chip] = []
        elif self.narrator.unavailable:
            chips = [Chip("Try again", [RETRY, {}], primary=True, id=TRY_AGAIN_ID)]
        else:
            chips = self.flow.chips()
        items = []
        for chip in chips:
            event = chip.event()
            if chip.run is not None and chip.kind == "action":
                actions[event["id"]] = chip.run
            items.append(event)
        self.emit({"type": "chips", "items": items})
        self.emit({"type": "composer", **self.flow.composer()})
        self.emit(self.status_event())
        self.emit(self._bills_event(actions))
        self.session.data["chipActions"] = actions

    def _bills_event(self, actions: dict[str, Continuation]) -> dict[str, Any]:
        bills = self.flow.bills()

        def pill(p: FilePill) -> dict[str, Any]:
            out: dict[str, Any] = {"name": p.name, **p.extra}
            if p.document_id is not None:
                out["documentId"] = p.document_id
            if p.remove is not None:
                chip_id = Chip(f"remove {p.name}", p.remove, id=None).chip_id()
                actions[chip_id] = p.remove
                out["removeChip"] = chip_id
            return out

        return {
            "type": "bills",
            "staged": [pill(p) for p in bills.get("staged", [])],
            "inFlight": [pill(p) for p in bills.get("inFlight", [])],
        }


class ChatRuntime:
    """The chats of the 0.4 contract: sessions, turns, bridge answers, launcher state, Start over."""

    def __init__(
        self,
        *,
        store: SessionStore,
        http: httpx.AsyncClient,
        llm: LlmClient,
        tour_api_base_url: str,
        device_type: str,
        enabled_writes: frozenset[str] = frozenset(),
        bridge_timeout: float = 60.0,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        self.store = store
        self.http = http
        self.llm = llm
        self._tour_api_base_url = tour_api_base_url
        self._device_type = device_type
        self._enabled_writes = enabled_writes
        self.bridge_timeout = bridge_timeout
        self.idle_seconds = idle_seconds
        self.locks = Locks()
        self.bridges = BridgeCalls()
        self._tasks: set[asyncio.Task[Any]] = set()

    def tour_client(self, token: str) -> UserScopedClient:
        return UserScopedClient(
            self.http,
            service=Service.TOUR,
            base_url=self._tour_api_base_url,
            token=token,
            device_type=self._device_type,
            enabled_writes=self._enabled_writes,
        )

    # ── housekeeping ──
    async def release_idle(self, user: ChatUser) -> list[str]:
        """Gives back the OCR results held by the caller's chats not touched for 15 minutes (the chats stay)."""

        async def release(session: ChatSession) -> bool:
            ocr = OcrSession(
                OcrApi(OcrApiClient(self.tour_client(user.token)), read_only=user.employee.read_only),
                session,
                WorkflowFiles(self.store, session.workflow_id),
                self.locks.ocr(session.emp_code),
            )
            return await ocr.give_back_all()

        return await give_back_idle_holdings(
            self.store, self.locks, user.employee.emp_code, release, idle_seconds=self.idle_seconds
        )

    async def owned(self, user: ChatUser, workflow_id: str) -> ChatSession:
        session = await self.store.get_owned(workflow_id, user.employee.emp_code)
        if session is None or flow_for(session.mode) is None:
            raise WorkflowNotFound(workflow_id)
        return session

    # ── conversations ──
    async def create(self, user: ChatUser, mode: str, params: dict[str, Any]) -> ChatSession:
        """POST /workflows {mode, tourId?, draftId?}: a new conversation (a freshly mounted chat)."""
        flow_cls = flow_for(mode)
        if flow_cls is None:
            raise UnknownMode(mode)
        await self.release_idle(user)
        session = ChatSession.new(user.employee.emp_code, mode, params, flow_cls.initial_state(params))
        await self.store.create(session)
        return session

    async def launcher(self, user: ChatUser, mode: str, params: dict[str, Any]) -> dict[str, Any]:
        """POST /sessions {mode, ...}: the launcher's state (status, bills) before the window is opened."""
        flow_cls = flow_for(mode)
        if flow_cls is None:
            raise UnknownMode(mode)
        await self.release_idle(user)
        session = ChatSession.new(user.employee.emp_code, mode, params, flow_cls.initial_state(params))
        storage = await self.store.load_storage(user.employee.emp_code)
        turn = Turn(self, session, user, flow_cls, storage)
        try:
            return await turn.launcher()
        finally:
            await self.store.delete_files(session.workflow_id)

    async def abandon(self, user: ChatUser, workflow_id: str) -> None:
        """DELETE /workflows/{id} (Start over): the chat unmounts; what it took is given back."""
        session = await self.owned(user, workflow_id)
        for call in self.bridges.for_workflow(workflow_id):
            if call.turn.task is not None:
                call.turn.task.cancel()
        async with self.locks.turn(workflow_id):
            session = await self.store.get_owned(workflow_id, user.employee.emp_code) or session
            flow_cls = flow_for(session.mode)
            assert flow_cls is not None
            storage = await self.store.load_storage(session.emp_code)
            turn = Turn(self, session, user, flow_cls, storage)
            try:
                await turn.flow.on_close()
            except Exception:  # noqa: BLE001 - the chat is closed anyway
                log.warning("chat %s: closing failed", session.mode, exc_info=True)
            session.status = ABANDONED
            await self.store.save(session)
            await self.store.save_storage(session.emp_code, storage)
            await self.store.delete_files(workflow_id)

    # ── turns ──
    async def start_turn(self, user: ChatUser, workflow_id: str, data: TurnInput) -> Channel:
        """Starts a turn; returns the channel its events arrive on (the first part of the HTTP response)."""
        await self.release_idle(user)
        await self.owned(user, workflow_id)
        channel: Channel = asyncio.Queue()
        task = asyncio.create_task(self._run_turn(user, workflow_id, data, channel))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return channel

    async def _run_turn(self, user: ChatUser, workflow_id: str, data: TurnInput, channel: Channel) -> None:
        turn: Turn | None = None
        try:
            async with self.locks.turn(workflow_id):
                session = await self.store.get_owned(workflow_id, user.employee.emp_code)
                flow_cls = flow_for(session.mode) if session else None
                if session is None or flow_cls is None:
                    channel.put_nowait(TurnFailed(WorkflowNotFound(workflow_id)))
                    return
                storage = await self.store.load_storage(session.emp_code)
                turn = Turn(self, session, user, flow_cls, storage)
                turn.task = asyncio.current_task()
                turn.attach(channel)
                await turn.execute(data)
        except asyncio.CancelledError:
            # Start over while the turn waited for the browser: the chat is closed, nothing is saved
            log.info("chat turn on %s was cancelled", workflow_id)
            if turn is not None:
                turn.close()
            else:
                channel.put_nowait(END)
        except BaseException as exc:  # noqa: BLE001 - reported to the waiting response
            if turn is not None and turn.emitted:
                log.exception("chat turn on %s failed", workflow_id)
                turn.emit(
                    {"type": "error", "error": "turn_failed", "message": "Something went wrong. Please try again."}
                )
                turn.close()
            else:
                target = turn.channel if turn is not None and turn.channel is not None else channel
                target.put_nowait(TurnFailed(exc))

    async def answer_bridge(
        self, user: ChatUser, workflow_id: str, call_id: str, *, ok: bool, result: Any, error: str | None, form: Any
    ) -> Channel:
        """POST /bridge/{callId}: the browser's answer; returns the channel the rest of the turn arrives on."""
        call: PendingCall = self.bridges.take(call_id, workflow_id, user.employee.emp_code)
        channel: Channel = asyncio.Queue()
        if form is not _UNSET:
            call.turn.session.form = form
        call.turn.attach(channel)
        answer(call, ok=ok, result=result, error=error)
        return channel


async def drain(channel: Channel) -> list[dict[str, Any]]:
    """All events of one response (until the turn ends or waits for the browser)."""
    events: list[dict[str, Any]] = []
    while True:
        item = await channel.get()
        if isinstance(item, _Pause | _End):
            return events
        if isinstance(item, TurnFailed):
            raise item.error
        events.append(item)


UNSET: Final = _UNSET

__all__ = [
    "ChatRuntime",
    "ChatUser",
    "IncomingFile",
    "Turn",
    "TurnInput",
    "TurnFailed",
    "UnknownMode",
    "WorkflowNotFound",
    "PAUSE",
    "END",
    "UNSET",
    "drain",
]
