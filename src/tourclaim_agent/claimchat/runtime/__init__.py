"""Runs the ported chat components (Raise Claim, Within Jurisdiction) on the server.

Runtime design: Project-Context/agent/old_assistant_parity/02_runtime_design.md. A flow (claimchat/flows/*) is the
old React component's logic on `ChatFlow`; the runtime gives it a session (its state), turns (one per request),
effects, the narrator (useNarrator), the form bridge, the bill queue (lib/ocr) and the page's callbacks.
"""

from tourclaim_agent.claimchat.runtime.bridge import BridgeError, BridgeTimeout
from tourclaim_agent.claimchat.runtime.flow import (
    ChatFlow,
    Chip,
    FilePill,
    NarratorView,
    continuation,
    effect,
    flow_for,
    register_flow,
    unregister_flow,
)
from tourclaim_agent.claimchat.runtime.ocr import OcrApiError, batches_of, is_in_flight, queue_ahead
from tourclaim_agent.claimchat.runtime.session import ChatSession, Continuation, cont
from tourclaim_agent.claimchat.runtime.turn import ChatRuntime, IncomingFile, Turn

__all__ = [
    "BridgeError",
    "BridgeTimeout",
    "ChatFlow",
    "ChatRuntime",
    "ChatSession",
    "Chip",
    "Continuation",
    "FilePill",
    "IncomingFile",
    "NarratorView",
    "OcrApiError",
    "Turn",
    "batches_of",
    "cont",
    "continuation",
    "effect",
    "flow_for",
    "is_in_flight",
    "queue_ahead",
    "register_flow",
    "unregister_flow",
]
