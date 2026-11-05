"""Port of Tour_React tests/claimChatbot/aiAssistant.test.ts, isAcknowledgement.test.ts, dropButtonLines.test.ts,
plus checks that narrate()/understand() send the old messages and settings."""

from __future__ import annotations

import json

import httpx
import pytest

from tourclaim_agent.claimchat.ai.ai_assistant import (
    AssistantEvent,
    ChatAction,
    clean_reply,
    compact,
    drop_button_lines,
    is_acknowledgement,
    narrate,
    narrator_prompt,
    parse_understood,
    prompts,
    understand,
)
from tourclaim_agent.claimchat.ai.llm_client import LlmClient

ACTIONS = [
    ChatAction("talk", "anything else"),
    ChatAction("yes", "agrees"),
    ChatAction("edit", "gives details"),
]


# aiAssistant.test.ts


def test_understand_reads_the_action_even_inside_a_code_fence_or_a_sentence():
    assert parse_understood('{"action":"yes","args":{},"edits":[]}', ACTIONS).action == "yes"
    assert parse_understood('```json\n{"action":"yes"}\n```', ACTIONS).action == "yes"
    raw = 'Sure: {"action":"edit","edits":[{"section":"transport","row":1,"field":"mode","value":"rail"}]}'
    assert len(parse_understood(raw, ACTIONS).edits) == 1


@pytest.mark.parametrize("raw", ['{"action":"submit_now"}', '{"action":', "Hello! How can I help?", ""])
def test_understand_unknown_action_broken_json_or_text_is_talk(raw):
    assert parse_understood(raw, ACTIONS).action == "talk"


def test_understand_edits_with_no_action_name_count_as_edit():
    raw = '{"edits":[{"section":"transport","row":1,"field":"mode","value":"rail"}]}'
    assert parse_understood(raw, ACTIONS).action == "edit"


def test_compact_leaves_out_empty_values_and_cuts_long_data():
    assert compact({"a": 1, "b": "", "c": None, "d": [1]}) == '{"a":1,"d":[1]}'
    assert compact({"text": "x" * 5000}, 100).endswith("…(cut)")


def test_compact_numbers_and_arrays_as_javascript_writes_them():
    assert compact({"n": 1200.0, "f": 1.5, "list": [None, "", 2]}) == '{"n":1200,"f":1.5,"list":[null,null,2]}'


def test_clean_reply_removes_fences_and_a_speaker_label():
    assert clean_reply("```\nHello there.\n```") == "Hello there."
    assert clean_reply("Aryaan: Hello there.") == "Hello there."


# isAcknowledgement.test.ts


@pytest.mark.parametrize(
    "text", ["ok", "Okay!", "thanks", "Thank you so much", "yes", "hmm", "theek hai", "ok ji", "Hello Aryaan"]
)
def test_is_acknowledgement_positive(text):
    assert is_acknowledgement(text)


@pytest.mark.parametrize(
    "text",
    [
        "what's in my claim?",
        "ok what is missing",
        "yes I have the return ticket",
        "how many bills are attached",
        "thanks, now submit it please",
        "ok?",
    ],
)
def test_is_acknowledgement_negative(text):
    assert not is_acknowledgement(text)


# dropButtonLines.test.ts

BUTTONS = ["Open claim: Delhi → Prayagraj", "Open claim: Delhi → Jaipur", "📎 Upload other bills", "Not now"]


def test_drop_button_lines_a_reply_that_lists_the_buttons_keeps_only_what_it_says():
    reply = "\n".join(
        [
            "You have 4 saved drafts.",
            "",
            "✅ You can pick any of these to continue or upload bills:",
            "",
            "[Open claim: Delhi → Prayagraj]",
            "[Open claim: Delhi → Jaipur]",
            "[📎 Upload other bills]",
            "[Not now]",
        ]
    )
    assert drop_button_lines(reply, BUTTONS) == "You have 4 saved drafts."


def test_drop_button_lines_bulleted_labels_and_tap_below_lines_go():
    reply = (
        "Saved to your draft for **Delhi → Jaipur**.\n• Open claim: Delhi → Jaipur\n• Not now\n"
        "Tap the button below to open it."
    )
    assert drop_button_lines(reply, BUTTONS) == "Saved to your draft for **Delhi → Jaipur**."


def test_drop_button_lines_ordinary_lines_that_mention_a_tour_are_kept():
    reply = "• Delhi → Jaipur: 2 journeys\n• Delhi → Prayagraj: 1 stay\nWhich tour is this claim for?"
    assert drop_button_lines(reply, BUTTONS) == reply


def test_drop_button_lines_the_reported_upload_more_reply_keeps_no_button_lines():
    reply = "Okay, attach them whenever you're ready.\n[Yes, I'll upload more]\n[No, that's everything]"
    assert (
        drop_button_lines(reply, ["Yes, I'll upload more", "No, that's everything"])
        == "Okay, attach them whenever you're ready."
    )


def test_clean_reply_takes_out_prompt_labels_echoed_by_the_model():
    raw = "These bills look right. Is that right?\n\nQUESTION: Are the uploaded bills for the suggested tour?"
    assert clean_reply(raw) == "These bills look right. Is that right?"


def test_clean_reply_leaves_an_ordinary_reply():
    assert clean_reply("Okay, attach them whenever you are ready.") == "Okay, attach them whenever you are ready."


# What is sent to the gateway (aiAssistant.ts › narrate / understand)


def recording_llm(reply: str) -> tuple[LlmClient, list[dict], httpx.AsyncClient]:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=reply.encode(), headers={"X-Conversation-Id": "conv-9"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LlmClient(http, base_url="https://gw.test/chat-api"), sent, http


async def test_narrate_without_a_typed_message_uses_the_brief_prompt_and_no_conversation():
    llm, sent, http = recording_llm("```\nSaved your bills.\n```")
    async with http:
        result = await narrate(
            llm,
            system_prompt="ignored here",
            events=[AssistantEvent("Saved 2 bills", data={"tour": "Noida → Mumbai", "x": ""})],
            conversation_id="keep",
            open_question="Anything else?",
        )
    assert result.reply == "Saved your bills." and result.conversation_id == "keep"
    body = sent[0]
    assert body["system_prompt"] == prompts()["brief"]
    assert (body["max_tokens"], body["temperature"], body["conversation_id"]) == (400, 0.2, None)
    assert body["message"] == (
        'NOTES:\n1. Saved 2 bills\n   data: {"tour":"Noida → Mumbai"}\n\nQUESTION:\nAnything else?\n\n'
        "Write the message now: only what the NOTES say, at most two short sentences (plus a list if a NOTE has one)."
    )


async def test_narrate_a_typed_message_continues_the_conversation():
    llm, sent, http = recording_llm("Sure.")
    async with http:
        result = await narrate(
            llm,
            system_prompt=prompts()["narrate_outside"],
            events=[AssistantEvent("Nothing changed")],
            conversation_id="c-1",
            employee_said="what is missing?",
            claim="{}",
        )
    assert result.conversation_id == "conv-9"
    body = sent[0]
    assert (body["max_tokens"], body["temperature"], body["conversation_id"]) == (700, 0.3, "c-1")
    assert body["message"].startswith(
        "EMPLOYEE SAID:\nwhat is missing?\n\nWHAT HAPPENED:\n1. Nothing changed\n\nCLAIM:\n{}"
    )


async def test_understand_sends_the_actions_and_a_fresh_conversation():
    llm, sent, http = recording_llm('{"action":"yes"}')
    async with http:
        understood = await understand(
            llm,
            system_prompt=prompts()["understand_outside"],
            message="haan",
            actions=[ChatAction("yes", "agrees"), ChatAction("pick", "a tour", '{"tourId": <number>}')],
            open_question="Is that right?",
        )
    assert understood.action == "yes"
    body = sent[0]
    assert (body["max_tokens"], body["temperature"], body["conversation_id"]) == (600, 0.1, None)
    assert body["message"] == (
        'ACTIONS:\n- yes: agrees\n- pick: a tour args: {"tourId": <number>}\n\nOPEN QUESTION:\nIs that right?\n\n'
        "EMPLOYEE'S MESSAGE:\nhaan\n\nAnswer with the JSON object only."
    )


def test_prompts_are_the_old_strings():
    p = prompts()
    assert p["SYSTEM_NOTICE"] == "The assistant is unavailable right now. Please try again."
    assert narrator_prompt(p["WITHIN_DOMAIN"]) == p["narrate_within"]
    assert p["understand_outside"].startswith("You read one message from a DFCCIL employee")
