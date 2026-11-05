"""Gateway client, port of Tour_React llmClient.ts (chatStream)."""

from __future__ import annotations

import json

import httpx
import pytest

from tourclaim_agent.claimchat.ai.llm_client import LlmClient, LlmError


def client(handler, sleeps: list[float] | None = None) -> tuple[LlmClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return LlmClient(http, base_url="https://gw.test/chat-api/", api_key="k", sleep=sleep), http


async def test_streams_the_reply_and_returns_the_conversation_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"Hello there", headers={"X-Conversation-Id": "c-1"})

    llm, http = client(handler)
    parts: list[str] = []
    async with http:
        result = await llm.chat_stream(
            "hi", system_prompt="be brief", max_tokens=400, temperature=0.2, on_text=parts.append
        )
    assert (result.reply, result.conversation_id) == ("Hello there", "c-1")
    assert parts[-1] == "Hello there"
    assert seen["url"] == "https://gw.test/chat-api/chat/stream" and seen["auth"] == "Bearer k"
    assert seen["body"] == {
        "message": "hi",
        "conversation_id": None,
        "system_prompt": "be brief",
        "max_tokens": 400,
        "temperature": 0.2,
    }


async def test_a_lost_conversation_is_started_again():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["conversation_id"])
        if body["conversation_id"] == "old":
            return httpx.Response(404)
        return httpx.Response(200, content=b"ok", headers={"X-Conversation-Id": "new"})

    llm, http = client(handler)
    async with http:
        result = await llm.chat_stream("hi", conversation_id="old")
    assert calls == ["old", None] and result.conversation_id == "new"


async def test_server_error_is_retried_once_after_three_seconds():
    statuses = [503, 200]
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(statuses.pop(0), content=b"fine")

    llm, http = client(handler, sleeps)
    async with http:
        assert (await llm.chat_stream("hi")).reply == "fine"
    assert sleeps == [3.0]


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "My typing helper isn't available to this account right now."),
        (429, "I'm getting a lot of requests right now. Please try again in a moment."),
        (400, "My typing helper returned an error (400)."),
    ],
)
async def test_errors_carry_the_old_messages(status, message):
    llm, http = client(lambda request: httpx.Response(status))
    async with http:
        with pytest.raises(LlmError) as raised:
            await llm.chat_stream("hi")
    assert raised.value.message == message and raised.value.status == status


async def test_unreachable_gateway():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    llm, http = client(handler)
    async with http:
        with pytest.raises(LlmError) as raised:
            await llm.chat_stream("hi")
    assert raised.value.message == "My typing helper is not reachable right now. Please check your connection."
