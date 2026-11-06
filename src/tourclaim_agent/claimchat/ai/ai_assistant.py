"""The Claim Assistant's voice and understanding: port of Tour_React src/lib/claimChatbot/aiAssistant.ts.

Every message the employee reads is written by the AI model; nothing is worded in code except the one fixed
notice used when the model can't be reached (SYSTEM_NOTICE).

- The flow does the work (bills, tours, drafts, checks, form changes) and reports each result as an event:
  what happened plus the data behind it.
- narrate() sends the events to the model and streams its reply.
- understand() sends the employee's typed message to the model, which picks one of the actions offered at that
  moment (and lists any claim edits). The flow validates and runs it, and narrate() reports it.

The prompts are loaded from prompts.json, which holds the exact strings the old TypeScript produced.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from tourclaim_agent.claimchat.ai.llm_client import ChatReply, LlmClient, OnText


@cache
def prompts() -> dict[str, Any]:
    """The old assistant's prompts and constants, character for character (generated from the TypeScript)."""
    text = resources.files("tourclaim_agent.claimchat.ai").joinpath("prompts.json").read_text(encoding="utf-8")
    return json.loads(text)


# The only text not written by the model: shown when the model cannot be reached or fails.
SYSTEM_NOTICE = "The assistant is unavailable right now. Please try again."


@dataclass
class AssistantEvent:
    """Something the flow did or found, for the model to tell the employee about. Never shown as it is."""

    note: str  # what happened, in plain facts
    data: Any = None  # the API response or result behind it (None = no data)
    tone: str | None = None  # "error" | "success"
    has_data: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.has_data = self.has_data or self.data is not None


# ─── compact: JSON for the model ────────────────────────────────────────────────


def _js_number(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        return "null"
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def _js_json(value: Any) -> str | None:
    """JSON.stringify with aiAssistant.compact's replacer: '', null and undefined are left out of objects.

    Returns None where JSON.stringify would give undefined (the value itself is left out).
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bytes | bytearray):
        return '"(file)"'
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            encoded = _js_json(item)
            if encoded is not None:
                parts.append(f"{json.dumps(str(key), ensure_ascii=False)}:{encoded}")
        return "{" + ",".join(parts) + "}"
    if isinstance(value, list | tuple):
        # In an array, a left-out value becomes null.
        return "[" + ",".join(_js_json(item) or "null" for item in value) + "]"
    if hasattr(value, "model_dump"):
        return _js_json(value.model_dump(mode="json"))
    return json.dumps(str(value), ensure_ascii=False)


def compact(data: Any, max_length: int = 3500) -> str:
    """aiAssistant.ts › compact: JSON for the model; empty values are left out, long data is cut."""
    text = _js_json(data) or ""
    return f"{text[:max_length]}…(cut)" if len(text) > max_length else text


# ─── reply helpers ──────────────────────────────────────────────────────────────

_ACK = re.compile(
    r"^(ok(ay)?|k|kk|okk+|fine|alright|all right|sure|yes|yeah|yep|yup|no|nope|hmm+|hm|thanks?|thank you|"
    r"thank you so much|thanks a lot|thx|ty|great|good|nice|cool|perfect|got it|understood|noted|hi|hello|hey|"
    r"haan|han|ha|ji|ji haan|theek hai|thik hai|accha|acha|achha|shukriya|dhanyavaad|dhanyawad|nahi|nahin)"
    r"( (ji|sir|madam|aryaan|thanks|thank you))?$"
)


def is_acknowledgement(text: str) -> bool:
    """aiAssistant.ts › isAcknowledgement: "ok", "thanks", "theek hai": not about the claim."""
    t = re.sub(r"\s+", " ", re.sub(r"[^a-z\s]", " ", text.lower())).strip()
    if not t or "?" in text or len(t.split(" ")) > 4:
        return False
    return _ACK.match(t) is not None


def _norm_label(value: str) -> str:
    """value.toLowerCase().replace(/[^\\p{L}\\p{N}]+/gu, ' ').trim()"""
    out: list[str] = []
    in_gap = False
    for ch in value.lower():
        if ch.isalpha() or ch.isnumeric():
            out.append(ch)
            in_gap = False
        elif not in_gap:
            out.append(" ")
            in_gap = True
    return "".join(out).strip()


_POINTER = re.compile(
    r"^(you can |please )?(tap|click|press|choose|pick|select|use)\b.*(button|below|options?|any of these|one of these)"
    r"|^(here are|these are) (your|the) (options|choices)|^(options|choices)\s*:?$",
    re.IGNORECASE,
)


def drop_button_lines(text: str, buttons: list[str]) -> str:
    """aiAssistant.ts › dropButtonLines: a reply never lists the buttons shown under it."""
    labels = {label for label in (_norm_label(b) for b in buttons) if label}
    kept = []
    for line in text.split("\n"):
        bare = re.sub(r"^\s*(?:[•\-*]|\d+[.)])\s*", "", line, count=1)
        bare = re.sub(r"^(?:✅|⚠️|⚠|👉|➡️|➡|\s)+", "", bare, count=1).strip()
        if not bare:
            kept.append(line)
            continue
        if re.fullmatch(r"\[[^\]]+\](\s*\[[^\]]+\])*", bare):
            continue  # "[Not now] [Open claim]"
        if _norm_label(re.sub(r"\*\*$", "", re.sub(r"^\*\*", "", bare))) in labels:
            continue
        if _POINTER.search(bare.replace("**", "")):
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def clean_reply(raw: str) -> str:
    """aiAssistant.ts › cleanReply: the employee only reads the words (no echoed labels, quotes or fences)."""
    text = re.sub(
        r"^[ \t\f\v\r]*(NOTES|QUESTION|OPEN QUESTION|WHAT HAPPENED|EMPLOYEE SAID|ALREADY SHOWN)[ \t\f\v\r]*:.*$",
        "",
        raw,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    text = re.sub(r"^\s*```[a-z]*\s*", "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(r"```\s*\Z", "", text, count=1)
    text = re.sub(r"^\s*(assistant|aryaan)\s*:\s*", "", text, count=1, flags=re.IGNORECASE)
    return text.strip()


# ─── narrate: events -> the reply the employee reads ────────────────────────────


def _notes(events: list[AssistantEvent]) -> str:
    return "\n".join(
        f"{i}. {e.note}" + (f"\n   data: {compact(e.data)}" if e.has_data else "") for i, e in enumerate(events, 1)
    )


def narrator_prompt(domain: str) -> str:
    """aiAssistant.ts › narratorPrompt. The two built prompts are in prompts(): narrate_outside / narrate_within."""
    rules = prompts()["narrate_outside"].removesuffix(f"\n\n{prompts()['OUTSIDE_DOMAIN']}")
    return f"{rules}\n\n{domain}"


async def narrate(
    llm: LlmClient,
    *,
    system_prompt: str,
    events: list[AssistantEvent],
    conversation_id: str | None,
    employee_said: str | None = None,
    open_question: str | None = None,
    already_shown: list[str] | None = None,
    claim: str | None = None,
    page: str | None = None,
    bills: str | None = None,
    brief_prompt: str | None = None,
    on_text: OnText | None = None,
) -> ChatReply:
    """aiAssistant.ts › narrate. Raises LlmError when the gateway fails (the caller shows SYSTEM_NOTICE).

    Not in the old code (owner requirement 2026-11-06, used by the Raise Claim chats only): `bills`, the BILLS section
    of a typed turn (amounts worked out in code), and `brief_prompt`, the prompt for replies without a typed message
    (default: the old `brief` prompt).
    """

    def cleaned(raw: str) -> Any:
        return on_text(clean_reply(raw)) if on_text is not None else None

    if not employee_said:
        # A reply to what the flow did (no typed message): the notes alone, the short prompt, no earlier turns.
        message = "\n\n".join(
            part
            for part in (
                f"NOTES:\n{_notes(events) or '(none)'}",
                f"QUESTION:\n{open_question}" if open_question else "",
                "Write the message now: only what the NOTES say, at most two short sentences (plus a list if a "
                "NOTE has one).",
            )
            if part
        )
        result = await llm.chat_stream(
            message,
            conversation_id=None,
            system_prompt=brief_prompt or prompts()["brief"],
            max_tokens=400,
            temperature=0.2,
            on_text=cleaned,
        )
        return ChatReply(clean_reply(result.reply), conversation_id)

    parts = [f"EMPLOYEE SAID:\n{employee_said}"]
    if events:
        parts.append(f"WHAT HAPPENED:\n{_notes(events)}")
    if open_question:
        parts.append(f"OPEN QUESTION:\n{open_question}")
    if already_shown:
        parts.append("ALREADY SHOWN (do not repeat):\n" + "\n---\n".join(already_shown))
    if claim:
        parts.append(f"CLAIM:\n{claim}")
    if page:
        parts.append(f"PAGE:\n{page}")
    if bills:
        # owner requirement 2026-11-06 (the Raise Claim chats): amounts and totals worked out in code (billing.py)
        parts.append(f"BILLS:\n{bills}")
    # The last thing the model reads: what this reply is for.
    parts.append(
        "Now reply to EMPLOYEE SAID only, briefly (at most three short sentences, or a short list if they asked for "
        "one). Use CLAIM and PAGE only for what they asked; never read out or sum up the claim otherwise. A short "
        '"ok", "yes", "thanks" or greeting gets one short sentence (plus the OPEN QUESTION, if any) and nothing about '
        "the claim."
        + (
            " A question about an amount or a total is answered only with the figures written in BILLS, exactly as "
            "written; never add, subtract or estimate a number yourself, and if the figure asked for is not in BILLS, "
            "say it could not be determined."
            if bills
            else ""
        )
    )
    result = await llm.chat_stream(
        "\n\n".join(parts),
        conversation_id=conversation_id,
        system_prompt=system_prompt,
        max_tokens=700,
        temperature=0.3,
        on_text=cleaned,
    )
    return ChatReply(clean_reply(result.reply), result.conversation_id)


# ─── understand: the employee's message -> one action ──────────────────────────


@dataclass
class ChatAction:
    name: str
    description: str  # what it means, for the model
    args: str | None = None  # what goes in "args", when the action needs any


@dataclass
class Understood:
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    edits: list[Any] = field(default_factory=list)


def understand_message(
    message: str,
    actions: list[ChatAction],
    *,
    open_question: str | None = None,
    context: str | None = None,
    recent_chat: str | None = None,
) -> str:
    """The text understand() sends to the model (aiAssistant.ts › understand)."""
    listed = "\n".join(f"- {a.name}: {a.description}" + (f" args: {a.args}" if a.args else "") for a in actions)
    return "\n\n".join(
        part
        for part in (
            f"ACTIONS:\n{listed}",
            f"OPEN QUESTION:\n{open_question}" if open_question else "",
            f"CONTEXT:\n{context}" if context else "",
            f"RECENT CHAT (oldest first):\n{recent_chat}" if recent_chat else "",
            f"EMPLOYEE'S MESSAGE:\n{message}",
            "Answer with the JSON object only.",
        )
        if part
    )


async def understand(
    llm: LlmClient,
    *,
    system_prompt: str,
    message: str,
    actions: list[ChatAction],
    open_question: str | None = None,
    context: str | None = None,
    recent_chat: str | None = None,
) -> Understood:
    """aiAssistant.ts › understand. A fresh conversation each time: the context is all the model needs."""
    text = understand_message(message, actions, open_question=open_question, context=context, recent_chat=recent_chat)
    result = await llm.chat_stream(
        text, conversation_id=None, system_prompt=system_prompt, max_tokens=600, temperature=0.1
    )
    return parse_understood(result.reply, actions)


def parse_json_object(raw: str) -> Any:
    """aiAssistant.ts › parseJsonObject: the model's JSON, with a code fence or a sentence around it."""
    text = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
    match = re.search(r"[\[{]", text)
    if match is None:
        return None
    start = match.start()
    end = text.rfind("]" if text[start] == "[" else "}")
    if end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


def parse_understood(raw: str, actions: list[ChatAction]) -> Understood:
    """aiAssistant.ts › parseUnderstood. Anything unreadable, or an action not offered, is "talk"."""
    talk = Understood("talk")
    parsed = parse_json_object(raw)
    if not isinstance(parsed, dict | list):
        return talk
    obj = parsed if isinstance(parsed, dict) else {}
    if isinstance(obj.get("edits"), list):
        edits = obj["edits"]
    elif isinstance(parsed, list):
        edits = parsed
    else:
        edits = []
    action = obj.get("action")
    name = action.strip() if isinstance(action, str) else ("edit" if edits else "")
    if not any(a.name == name for a in actions):
        return talk
    args = obj.get("args")
    return Understood(name, args if isinstance(args, dict) else {}, edits)
