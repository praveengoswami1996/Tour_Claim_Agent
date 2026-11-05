"""Client for the ARYAAN AI gateway (Gemma), as the old in-app assistant used it.

Port of Tour_React src/lib/claimChatbot/llmClient.ts. The agent calls the gateway itself
(`<base>/chat/stream`, base ending in `/chat-api`) with the gateway key as a Bearer token;
the browser no longer does. Text only: bills are read by the Tour API's OCR queue.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

# The retry after a server error waits this long (llmClient.ts: setTimeout 3000).
RETRY_DELAY_SECONDS = 3.0


class LlmError(Exception):
    """The gateway could not answer. `message` is what the old client would have shown."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def llm_error_message(status: int) -> str:
    """llmClient.ts › llmErrorMessage: what the employee should read for each way the chat service can answer."""
    if status in (401, 403):
        return "My typing helper isn't available to this account right now."
    if status == 429:
        return "I'm getting a lot of requests right now. Please try again in a moment."
    if status >= 500:
        return "My typing helper isn't responding right now. Please try again in a moment."
    return f"My typing helper returned an error ({status})."


@dataclass
class ChatReply:
    reply: str
    conversation_id: str | None


OnText = Callable[[str], Awaitable[None] | None]


class LlmClient:
    """chatStream (POST /chat/stream): the reply arrives as plain text while it is generated."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        api_key: str = "",
        timeout: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/chat/stream"
        self._api_key = api_key
        self._timeout = timeout
        self._sleep = sleep

    async def chat_stream(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        on_text: OnText | None = None,
    ) -> ChatReply:
        """llmClient.ts › chatStream. `on_text` gets the whole reply so far each time more of it arrives.

        Pass the returned conversation id on the next call to continue the same conversation (the gateway
        keeps the history); `system_prompt` is only used when a conversation starts.
        """

        async def send(conv: str | None) -> ChatReply:
            body = {
                "message": message,
                "conversation_id": conv,
                "system_prompt": system_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            return await self._post(body, conv, on_text)

        try:
            return await send(conversation_id)
        except LlmError as error:
            # The conversation is gone on the gateway (expired / restarted): start a fresh one.
            if conversation_id and error.status in (404, 422):
                return await send(None)
            # The model server restarts after a request it can't handle; one retry a moment later gets past that.
            if (error.status or 0) >= 500:
                await self._sleep(RETRY_DELAY_SECONDS)
                return await send(conversation_id)
            raise

    async def _post(self, body: dict, conversation_id: str | None, on_text: OnText | None) -> ChatReply:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            async with self._http.stream(
                "POST", self._url, json=body, headers=headers, timeout=self._timeout
            ) as response:
                if response.status_code >= 400:
                    raise LlmError(llm_error_message(response.status_code), response.status_code)
                conv = response.headers.get("X-Conversation-Id") or conversation_id
                reply = ""
                try:
                    async for chunk in response.aiter_text():
                        reply += chunk
                        if on_text is not None:
                            result = on_text(reply)
                            if asyncio.iscoroutine(result):
                                await result
                except httpx.HTTPError as exc:
                    raise LlmError("The assistant stopped replying. Please try again.") from exc
                return ChatReply(reply, conv)
        except LlmError:
            raise
        except httpx.HTTPError as exc:
            raise LlmError("My typing helper is not reachable right now. Please check your connection.") from exc
