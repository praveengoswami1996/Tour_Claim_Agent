"""Calls to the open form in the browser: runtime design §6.

The old chat awaited the form's functions directly (`form.write`, `form.saveDraft()`, `form.check()`,
`form.submit()`, `form.info()`, `onFillForm`). Here a turn runs as an asyncio task; when the flow reaches
`await rt.bridge.call(method, args)`:

1. the runtime sends `bridge_call {id, method, args}` and ends that HTTP response;
2. the task waits on a future (the turn lock stays held: the conversation's other calls wait);
3. `POST /workflows/{id}/bridge/{callId}` resolves the future (and replaces the form snapshot); its response streams
   the rest of the turn, until the next bridge call or the end.

`{ok: false, error}` raises BridgeError in the flow, as the form's function would have thrown. No answer within the
timeout (60 s) raises BridgeTimeout (a BridgeError) the same way. `read()` needs no call: it returns the snapshot
sent with the request.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from tourclaim_agent.claimchat.runtime.turn import Turn

BRIDGE_TIMEOUT_SECONDS: Final = 60.0
BRIDGE_METHODS: Final = ("read", "write", "check", "info", "saveDraft", "submit", "openForm")


class BridgeError(Exception):
    """The form's function failed (or never answered)."""


class BridgeTimeout(BridgeError):
    """No answer from the browser in time."""


class BridgeCallNotFound(Exception):
    """No bridge call with this id is waiting (answered already, timed out, or not this conversation's)."""


@dataclass
class PendingCall:
    call_id: str
    workflow_id: str
    emp_code: str
    method: str
    turn: Turn
    future: asyncio.Future[Any] = field(repr=False)


class BridgeCalls:
    """The bridge calls waiting for the browser's answer, across all conversations (one process)."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingCall] = {}

    def add(self, call: PendingCall) -> None:
        self._pending[call.call_id] = call

    def take(self, call_id: str, workflow_id: str, emp_code: str) -> PendingCall:
        """The waiting call, removed from the list. Raises BridgeCallNotFound."""
        call = self._pending.get(call_id)
        if call is None or call.workflow_id != workflow_id or call.emp_code != emp_code or call.future.done():
            raise BridgeCallNotFound(call_id)
        del self._pending[call_id]
        return call

    def discard(self, call_id: str) -> None:
        self._pending.pop(call_id, None)

    def for_workflow(self, workflow_id: str) -> list[PendingCall]:
        return [c for c in self._pending.values() if c.workflow_id == workflow_id]


class Bridge:
    """The form bridge of one turn (the old ClaimFormBridge / WithinFormBridge / page callbacks)."""

    def __init__(self, turn: Turn, calls: BridgeCalls, timeout: float = BRIDGE_TIMEOUT_SECONDS) -> None:
        self._turn = turn
        self._calls = calls
        self._timeout = timeout

    def read(self) -> Any:
        """form.read(): the form's state as sent with the request (or the last bridge answer)."""
        return self._turn.session.form

    async def call(self, method: str, args: Any = None) -> Any:
        """Asks the browser to run one of the form's functions; returns its `result`. Raises BridgeError."""
        if method not in BRIDGE_METHODS:
            raise ValueError(f"unknown bridge method {method!r}")
        turn = self._turn
        loop = asyncio.get_running_loop()
        call = PendingCall(
            call_id=uuid.uuid4().hex,
            workflow_id=turn.session.workflow_id,
            emp_code=turn.session.emp_code,
            method=method,
            turn=turn,
            future=loop.create_future(),
        )
        self._calls.add(call)
        event: dict[str, Any] = {"type": "bridge_call", "id": call.call_id, "method": method}
        if args is not None:
            event["args"] = args
        turn.emit(event)
        turn.pause()
        try:
            return await asyncio.wait_for(call.future, self._timeout)
        except TimeoutError as exc:
            raise BridgeTimeout(f"The form did not answer the {method} call.") from exc
        finally:
            self._calls.discard(call.call_id)


def answer(call: PendingCall, *, ok: bool, result: Any = None, error: str | None = None) -> None:
    """Resolves a waiting call with the browser's answer."""
    if ok:
        call.future.set_result(result)
    else:
        call.future.set_exception(BridgeError(error or f"The form could not run {call.method}."))
