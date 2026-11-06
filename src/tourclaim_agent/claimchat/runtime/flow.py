"""What a chat flow (the port of one old chat component) gives the runtime, and the registry of flows by mode.

Runtime design §2-§5. A flow is the old React component's logic, line for line, with React replaced:

- **state**: `self.state` (a JSON dict kept in the session) holds every `useState` / `useRef` value;
  `initial_state(params)` gives the values of a fresh mount;
- **entry points** (the old event handlers): `on_open`, `on_message(text)`, `on_files(files)`, `on_poll`,
  `on_retry`, and chips (each chip carries a named continuation that is run when it is tapped);
- **effects** (the old `useEffect`s that do work): methods marked `@effect`, in the order they are declared, each
  returning True when it did something; the runtime runs them after the entry point until none fires;
- **continuations**: methods marked `@continuation` (the callbacks the old state held: `askYN.yes`, a reply's
  `onShown`, "Try again"); referred to as `[name, {args}]` (session.cont) so they survive serialisation;
- **derived UI** (the old render): `chips()`, `chips_hidden()`, `composer()`, `status()`, `bills()`, pure
  functions of the state; `narrator_view()` (what the narrator reads when it writes) and `idle()` (nothing else is
  running, so a reply can be written).

The flow reaches the world through `self.rt` (a Turn): `rt.narrator`, `rt.bridge`, `rt.ocr`, `rt.page`,
`rt.storage`, `rt.files`, `rt.set_busy()`, `rt.user_files()`, `rt.run()`.

Phase 3b-3d write `claimchat/flows/raise_claim.py` (raise_page, raise_form) and `claimchat/flows/within.py`
(within_form) on this base and register them with `register_flow`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict

from tourclaim_agent.claimchat.runtime.session import Continuation

if TYPE_CHECKING:
    from tourclaim_agent.claimchat.runtime.turn import IncomingFile, Turn


class NarratorView(TypedDict, total=False):
    """useNarrator.ts NarratorView: what the employee sees right now, read when the reply is written."""

    # the question the employee still has to answer
    openQuestion: str | None
    # the buttons on screen (never sent to the model; a reply that lists them has those lines taken out)
    buttons: list[str]
    # the claim as it is now, in a few lines
    claim: str | None
    # the page around the chat (its lists and counts)
    page: str | None


@dataclass
class Chip:
    """One chip of the old quick actions. `run` is what tapping it does (a named continuation).

    kind: 'action' (sends POST /actions), 'link' (opens `href`, sends nothing), 'upload' (file picker), 'camera'.
    """

    label: str
    run: Continuation | None = None
    kind: str = "action"
    href: str | None = None
    primary: bool = False
    disabled: bool = False
    id: str | None = None

    def chip_id(self) -> str:
        """A stable id for this chip: the same chip gets the same id on every turn."""
        if self.id:
            return self.id
        key = json.dumps([self.label, self.kind, self.href, self.run], ensure_ascii=False, sort_keys=True)
        return "c" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]  # noqa: S324 - an id, not security

    def event(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.chip_id(), "label": self.label, "kind": self.kind}
        if self.href is not None:
            out["href"] = self.href
        if self.primary:
            out["primary"] = True
        if self.disabled:
            out["disabled"] = True
        return out


@dataclass
class FilePill:
    """A file shown above the composer: staged (with ✕ to remove it: `remove`) or being read (`documentId`)."""

    name: str
    document_id: int | None = None
    remove: Continuation | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def effect(fn: Callable[..., Any] | None = None, *, max_runs: int = 1) -> Any:
    """Marks a flow method as an effect (an old `useEffect` that does work).

    The method is `async def effect_x(self) -> bool` with the old guard conditions; it returns True when it did
    something. Each effect runs at most `max_runs` times per turn (raise it for effects that could loop in React).
    """

    def mark(f: Callable[..., Any]) -> Callable[..., Any]:
        f._chat_effect = max_runs  # type: ignore[attr-defined]
        return f

    return mark(fn) if fn is not None else mark


def continuation(fn: Callable[..., Any] | str | None = None) -> Any:
    """Marks a flow method as a continuation, run by name: `@continuation` or `@continuation("name")`."""

    def mark(f: Callable[..., Any], name: str | None = None) -> Callable[..., Any]:
        f._chat_continuation = name or f.__name__  # type: ignore[attr-defined]
        return f

    if callable(fn):
        return mark(fn)
    return lambda f: mark(f, fn)


class ChatFlow:
    """Base of a ported chat component. Subclasses set `mode` and implement the old logic."""

    mode: ClassVar[str] = ""

    def __init__(self, rt: Turn) -> None:
        self.rt = rt

    # ── state ──
    @classmethod
    def initial_state(cls, params: dict[str, Any]) -> dict[str, Any]:
        """The state of a freshly mounted component. `params` = {tourId?, draftId?} from POST /workflows."""
        return {"busy": None}

    @property
    def state(self) -> dict[str, Any]:
        return self.rt.session.state

    # ── entry points ──
    async def on_open(self) -> None:
        """POST /open: the window was opened (the greeting effects then run, as `active` is now true)."""

    async def on_message(self, text: str) -> None:
        """POST /messages: the old send(raw)."""

    async def on_files(self, files: list[IncomingFile]) -> None:
        """POST /attachments: the old addBills(files) / addFiles(files)."""

    async def on_poll(self) -> None:
        """POST /poll: the old shared poller ticked (the bill list is read again)."""
        await self.rt.ocr.refresh()

    async def on_retry(self) -> None:
        """POST /retry: the "Try again" chip (narrator.retry)."""
        await self.rt.narrator.retry()

    async def on_launcher(self) -> None:
        """POST /sessions with a mode: the launcher's state before the window was ever opened."""
        await self.rt.ocr.refresh()

    async def on_close(self) -> None:
        """The component unmounts (Start over, DELETE): what it took is given back (the old unmount cleanup)."""
        await self.rt.ocr.give_back_all()

    # ── derived UI (the old render) ──
    def idle(self) -> bool:
        """Nothing else is running, so the narrator can write (the old `idle: !busy`)."""
        return not self.state.get("busy")

    def system_prompt(self) -> str:
        """The narrator's system prompt for typed turns (narratorPrompt(domain))."""
        return ""

    def narrator_view(self) -> NarratorView:
        return {"buttons": [c.label for c in self.chips()]}

    def chips_hidden(self) -> bool:
        return False

    def chips(self) -> list[Chip]:
        return []

    def composer(self) -> dict[str, Any]:
        return {"placeholder": "", "canType": True, "canAttach": True}

    def status(self) -> dict[str, Any]:
        """{header, busy, launcher}; the runtime adds `held` (and `draftsChanged` after a draft was saved)."""
        return {"header": "", "busy": self.state.get("busy"), "launcher": None}

    def bills(self) -> dict[str, list[FilePill]]:
        """{staged: [...], inFlight: [...]} for the pills above the composer."""
        return {"staged": [], "inFlight": []}

    # ── introspection (effects and continuations, in declaration order) ──
    _effects_cache: ClassVar[dict[type, list[tuple[str, int]]]] = {}
    _continuations_cache: ClassVar[dict[type, dict[str, str]]] = {}

    @classmethod
    def effects(cls) -> list[tuple[str, int]]:
        """[(method name, max runs per turn)] in the order the effects are declared (base classes first)."""
        if cls not in ChatFlow._effects_cache:
            seen: dict[str, int] = {}
            for klass in reversed(cls.__mro__):
                for name, value in vars(klass).items():
                    if callable(value) and hasattr(value, "_chat_effect"):
                        seen[name] = value._chat_effect
                    elif name in seen and not hasattr(value, "_chat_effect"):
                        seen.pop(name)
            ChatFlow._effects_cache[cls] = list(seen.items())
        return ChatFlow._effects_cache[cls]

    @classmethod
    def continuations(cls) -> dict[str, str]:
        """{continuation name: method name}."""
        if cls not in ChatFlow._continuations_cache:
            table: dict[str, str] = {}
            for klass in reversed(cls.__mro__):
                for name, value in vars(klass).items():
                    if callable(value) and hasattr(value, "_chat_continuation"):
                        table[value._chat_continuation] = name
            ChatFlow._continuations_cache[cls] = table
        return ChatFlow._continuations_cache[cls]


_FLOWS: dict[str, type[ChatFlow]] = {}


def register_flow(flow: type[ChatFlow], mode: str | None = None) -> type[ChatFlow]:
    """Makes a flow available for a chat mode (raise_page, raise_form, within_form, ...). Usable as a decorator."""
    key = mode or flow.mode
    if not key:
        raise ValueError("a flow needs a mode")
    _FLOWS[key] = flow
    return flow


def unregister_flow(mode: str) -> None:
    _FLOWS.pop(mode, None)


def flow_for(mode: str) -> type[ChatFlow] | None:
    return _FLOWS.get(mode)


def registered_modes() -> list[str]:
    return sorted(_FLOWS)
