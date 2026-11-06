"""The chat runtime end to end through HTTP (0.4 contract), with a tiny demo flow and the fake backend.

The demo flow is not a port of anything: it exercises each part of the runtime once (an effect, the narrator with
streaming, a chip with a bridge call, an OCR upload -> poll -> take cycle, held replies, Start over, expiry).
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes import GATEWAY_BASE, PDF, FakeBackend, make_token
from tourclaim_agent.claimchat.ocr_references import RAISE_CLAIM_CHAT_REF, is_raise_claim_chat_ref
from tourclaim_agent.claimchat.ocr_results import combine_ocr_results
from tourclaim_agent.claimchat.runtime import (
    BridgeError,
    ChatFlow,
    Chip,
    FilePill,
    OcrApiError,
    cont,
    continuation,
    effect,
    is_in_flight,
    register_flow,
    unregister_flow,
)
from tourclaim_agent.main import create_app

MODE = "demo_echo"


class EchoFlow(ChatFlow):
    """A demo chat: greets, echoes, asks the form, sends bills to the OCR and goes through them when read."""

    mode = MODE

    @classmethod
    def initial_state(cls, params: dict[str, Any]) -> dict[str, Any]:
        return {"busy": None, "greeted": False, "shown": 0, "taking": False, "read": [], "tourId": params.get("tourId")}

    def system_prompt(self) -> str:
        return "DEMO NARRATOR"

    async def on_open(self) -> None:
        await self.rt.ocr.refresh()

    async def on_message(self, text: str) -> None:
        self.rt.narrator.resume()
        self.rt.user_message(text, show=False)
        self.rt.narrator.heard(text)

    async def on_files(self, files) -> None:
        self.rt.user_files([f.name for f in files])
        self.rt.set_busy(f"Processing {len(files)} bill{'s' if len(files) > 1 else ''}…")
        try:
            await self.rt.ocr.upload_for_ocr([(f.name, f.data, f.type) for f in files], RAISE_CLAIM_CHAT_REF)
            self.rt.narrator.say("Your bills are being read.")
        except OcrApiError as error:
            self.rt.narrator.say(error.message, "error")
        finally:
            self.rt.set_busy(None)

    def my_docs(self) -> list[dict[str, Any]]:
        return [d for d in self.rt.ocr.documents if is_raise_claim_chat_ref(d["reference"])]

    @effect
    async def effect_greet(self) -> bool:
        if not self.rt.active or self.state["greeted"]:
            return False
        self.state["greeted"] = True
        name = self.rt.user_name
        self.rt.narrator.say(f"Hello{f', {name}' if name else ''}!", on_shown=cont("greeting_shown", n=1))
        return True

    @effect
    async def effect_read(self) -> bool:
        if not self.rt.active or self.state["busy"] or self.rt.narrator.writing or self.state["taking"]:
            return False
        done = [b for b in self.rt.ocr.batches(self.my_docs()) if b["done"]]
        if not done:
            return False
        docs = [d for b in done for d in b["documents"]]
        self.state["taking"] = True
        self.rt.set_busy(f"Going through {len(docs)} read bills…")
        try:
            marked = await self.rt.ocr.take_ocr_results([d["documentId"] for d in docs])
            taken = await self.rt.ocr.read_taken_documents(
                [d for d in docs if d["documentId"] in marked], with_files=True
            )
            combined = combine_ocr_results([t["result"] for t in taken])
            names = [t["result"]["fileName"] for t in taken]
            self.state["read"] = names
            self.state["local"] = all(b["local"] for b in done)
            self.rt.narrator.tell(f"Read {len(names)} bills: {', '.join(names)}", data=combined)
            # the first is applied; the others stay taken (as bills in an unsaved claim)
            await self.rt.ocr.settle_ocr_results(marked[:1])
        finally:
            self.state["taking"] = False
            self.rt.set_busy(None)
        return True

    @continuation
    def greeting_shown(self, n: int) -> None:
        self.state["shown"] += n

    @continuation
    async def ask_form(self) -> None:
        self.rt.set_busy("Checking the form…")
        try:
            info = await self.rt.bridge.call("info")
            self.rt.narrator.say(f"The form says self approve is {info['selfApprove']}.")
        except BridgeError:
            self.rt.narrator.say("The form did not answer.", "error")
        finally:
            self.rt.set_busy(None)

    def chips(self) -> list[Chip]:
        return [
            Chip("Ask the form", cont("ask_form"), primary=True),
            Chip("📎 Upload other bills", kind="upload"),
        ]

    def composer(self) -> dict[str, Any]:
        return {"placeholder": "Type a detail, a correction or a question…", "canType": True, "canAttach": True}

    def status(self) -> dict[str, Any]:
        reading = [d for d in self.my_docs() if is_in_flight(d)]
        ready = len(self.my_docs()) - len(reading)
        launcher = (
            f"Your {'bill is' if ready == 1 else f'{ready} bills are'} read. Open me to go through "
            f"{'it' if ready == 1 else 'them'}."
            if ready
            else (f"Processing {'your bill' if len(reading) == 1 else f'{len(reading)} bills'}…" if reading else None)
        )
        return {"header": "Upload your bills to begin", "busy": self.state["busy"], "launcher": launcher}

    def bills(self) -> dict[str, list[FilePill]]:
        return {
            "staged": [],
            "inFlight": [FilePill(d["fileName"], d["documentId"]) for d in self.my_docs() if is_in_flight(d)],
        }


@pytest.fixture
def client(settings, backend: FakeBackend):
    settings.chat_gateway_url = GATEWAY_BASE
    register_flow(EchoFlow)
    try:
        with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
            yield test_client
    finally:
        unregister_flow(MODE)


def auth(**claims: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token('E1001', **claims)}"}


def sse(text: str) -> list[dict[str, Any]]:
    assert text.endswith("event: done\ndata: {}\n\n"), text[-200:]
    events = []
    for block in text.split("\n\n"):
        lines = block.split("\n")
        if lines[0] == "event: agent_event":
            assert lines[1].startswith("data: ")
            events.append(json.loads(lines[1][len("data: ") :]))
    return events


def new_chat(client: TestClient, **claims: Any) -> str:
    response = client.post("/v1/workflows", headers=auth(**claims), json={"mode": MODE, "tourId": 503})
    assert response.status_code == 201, response.text
    return response.json()["workflow_id"]


def call(client: TestClient, workflow_id: str, path: str, body: dict[str, Any] | None = None, **claims: Any):
    response = client.post(f"/v1/workflows/{workflow_id}/{path}", headers=auth(**claims), json=body or {})
    assert response.status_code == 200, response.text
    return sse(response.text)


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


def replies(events: list[dict[str, Any]]) -> list[str]:
    return [e["text"] for e in of(events, "reply")]


def session_of(client: TestClient, workflow_id: str):
    return client.portal.call(client.app.state.chat.store.get, workflow_id)


class TestConversations:
    def test_without_a_mode_the_0_3_conversation_is_created(self, client):
        response = client.post("/v1/workflows", headers=auth())
        assert response.status_code == 201 and response.json()["title"] == "New conversation"

    def test_unknown_mode(self, client):
        response = client.post("/v1/workflows", headers=auth(), json={"mode": "nope"})
        assert response.status_code == 400 and response.json()["error"] == "unknown_mode"

    def test_another_employee_cannot_use_the_chat(self, client):
        workflow_id = new_chat(client)
        response = client.post(
            f"/v1/workflows/{workflow_id}/open",
            headers={"Authorization": f"Bearer {make_token('E2002')}"},
            json={"active": True},
        )
        assert response.status_code == 404 and response.json()["error"] == "workflow_not_found"

    def test_the_session_keeps_the_props(self, client):
        workflow_id = new_chat(client)
        session = session_of(client, workflow_id)
        assert session.mode == MODE and session.params == {"tourId": 503} and session.state["tourId"] == 503


class TestNarration:
    def test_open_greets_with_a_streamed_reply_then_the_ui_state(self, client, backend):
        events = call(client, new_chat(client), "open", {"active": True}, unique_name="Anil")
        kinds = [e["type"] for e in events]
        assert kinds[0] == "status"  # the typing indicator while the reply is written
        start, deltas, reply = of(events, "reply_start")[0], of(events, "reply_delta"), of(events, "reply")[0]
        assert start["id"] == reply["id"] and reply["text"] == "Hello, Anil!"
        assert deltas and all(reply["text"].startswith(d["text"]) for d in deltas)
        assert kinds[-4:] == ["chips", "composer", "status", "bills"]
        chips = of(events, "chips")[0]["items"]
        assert [c["label"] for c in chips] == ["Ask the form", "📎 Upload other bills"]
        assert chips[0]["primary"] is True and chips[1]["kind"] == "upload"
        assert events[-2]["held"] is False and events[-2]["busy"] is None
        # the brief prompt, no conversation, as the old narrator without a typed message
        sent = backend.gateway_calls[0]
        assert sent["conversation_id"] is None and sent["max_tokens"] == 400 and sent["temperature"] == 0.2
        assert sent["message"].startswith("NOTES:\n1. Hello, Anil!")

    def test_on_shown_runs_once_the_reply_is_shown(self, client):
        workflow_id = new_chat(client)
        call(client, workflow_id, "open", {"active": True})
        assert session_of(client, workflow_id).state["shown"] == 1

    def test_replies_are_held_while_the_window_is_closed(self, client, backend):
        workflow_id = new_chat(client)
        call(client, workflow_id, "open", {"active": True})
        events = call(client, workflow_id, "messages", {"text": "hello there", "active": False})
        assert replies(events) == [] and events[-2]["held"] is True
        events = call(client, workflow_id, "open", {"active": True})
        assert replies(events) == ["You said: hello there"] and events[-2]["held"] is False

    def test_typed_messages_carry_the_conversation_on(self, client, backend):
        workflow_id = new_chat(client)
        call(client, workflow_id, "open", {"active": True})
        call(client, workflow_id, "messages", {"text": "what is a DA?", "active": True})
        call(client, workflow_id, "messages", {"text": "thanks", "active": True})
        first, second = backend.gateway_calls[1], backend.gateway_calls[2]
        assert first["system_prompt"] == "DEMO NARRATOR" and first["conversation_id"] is None
        assert first["message"].startswith("EMPLOYEE SAID:\nwhat is a DA?")
        # the greeting was shown since the last typed turn: the model is told not to repeat it
        assert "ALREADY SHOWN (do not repeat):\nHello, Test Employee!" in first["message"]
        assert second["conversation_id"] == "conv-2" and "ALREADY SHOWN" not in second["message"]

    def test_the_gateway_down_shows_the_notice_and_try_again_writes_it_again(self, client, backend):
        workflow_id = new_chat(client)
        backend.gateway_fail.append(401)
        events = call(client, workflow_id, "open", {"active": True})
        assert of(events, "system_notice") == [
            {"type": "system_notice", "text": "The assistant is unavailable right now. Please try again."}
        ]
        chips = of(events, "chips")[0]["items"]
        assert chips == [{"id": "try-again", "label": "Try again", "kind": "action", "primary": True}]
        events = call(client, workflow_id, "retry", {"active": True})
        assert replies(events) == ["Hello, Test Employee!"]
        assert [c["label"] for c in of(events, "chips")[0]["items"]][0] == "Ask the form"

    def test_stream_false_returns_the_events_as_json(self, client):
        workflow_id = new_chat(client)
        response = client.post(f"/v1/workflows/{workflow_id}/open?stream=false", headers=auth(), json={"active": True})
        assert response.status_code == 200
        assert replies(response.json()["events"]) == ["Hello, Test Employee!"]

    def test_a_chip_not_on_screen_does_nothing(self, client, backend):
        workflow_id = new_chat(client)
        call(client, workflow_id, "open", {"active": True})
        events = call(client, workflow_id, "actions", {"chip": "c-unknown", "active": True})
        assert replies(events) == [] and of(events, "bridge_call") == []


class TestBridge:
    def ask_form(self, client, workflow_id: str) -> dict[str, Any]:
        call(client, workflow_id, "open", {"active": True})
        events = call(client, workflow_id, "actions", {"chip": self.chip_id(client, workflow_id), "active": True})
        assert events[-1]["type"] == "bridge_call", events
        assert of(events, "status")[0]["busy"] == "Checking the form…"
        return events[-1]

    def chip_id(self, client, workflow_id: str) -> str:
        return next(k for k, v in session_of(client, workflow_id).data["chipActions"].items() if v[0] == "ask_form")

    def test_round_trip(self, client):
        workflow_id = new_chat(client)
        bridge = self.ask_form(client, workflow_id)
        assert bridge["method"] == "info" and "args" not in bridge
        response = client.post(
            f"/v1/workflows/{workflow_id}/bridge/{bridge['id']}",
            headers=auth(),
            json={"ok": True, "result": {"selfApprove": True, "approverChosen": False}, "form": {"tours": []}},
        )
        events = sse(response.text)
        assert replies(events) == ["The form says self approve is True."]
        assert [e["type"] for e in events][-4:] == ["chips", "composer", "status", "bills"]
        assert session_of(client, workflow_id).form == {"tours": []}
        # answered once only
        again = client.post(f"/v1/workflows/{workflow_id}/bridge/{bridge['id']}", headers=auth(), json={"ok": True})
        assert again.status_code == 404 and again.json()["error"] == "bridge_call_not_found"

    def test_a_failed_call_is_an_error_in_the_flow(self, client):
        workflow_id = new_chat(client)
        bridge = self.ask_form(client, workflow_id)
        response = client.post(
            f"/v1/workflows/{workflow_id}/bridge/{bridge['id']}", headers=auth(), json={"ok": False, "error": "boom"}
        )
        events = sse(response.text)
        assert replies(events) == ["The form did not answer."] and of(events, "reply")[0]["tone"] == "error"

    def test_no_answer_in_time_fails_the_call_and_the_rest_comes_with_the_next_request(self, client):
        client.app.state.chat.bridge_timeout = 0.2
        workflow_id = new_chat(client)
        self.ask_form(client, workflow_id)
        time.sleep(0.6)
        events = call(client, workflow_id, "poll", {"active": True})
        assert replies(events)[0] == "The form did not answer."


class TestBills:
    def upload(self, client, workflow_id: str, *files: tuple[str, bytes], **claims: Any) -> list[dict[str, Any]]:
        response = client.post(
            f"/v1/workflows/{workflow_id}/attachments",
            headers=auth(**claims),
            data={"active": "true"},
            files=[("files", (name, content, "application/pdf")) for name, content in files],
        )
        assert response.status_code == 200, response.text
        return sse(response.text)

    def test_upload_poll_take(self, client, backend):
        workflow_id = new_chat(client)
        call(client, workflow_id, "open", {"active": True})
        events = self.upload(client, workflow_id, ("out.pdf", PDF + b"1"), ("back.pdf", PDF + b"2"))
        assert events[0] == {"type": "user_files", "names": ["out.pdf", "back.pdf"]}
        assert of(events, "status")[0]["busy"] == "Processing 2 bills…"
        assert replies(events) == ["Your bills are being read."]
        assert [p["name"] for p in of(events, "bills")[-1]["inFlight"]] == ["out.pdf", "back.pdf"]
        # the old upload: parts Files, then Reference, tagged for the Raise Claim page chat
        assert backend.upload_parts[-1] == ["Files", "Reference"]
        assert {d["reference"] for d in backend.ocr_docs.values()} == {"outside-chat:unassigned"}

        events = call(client, workflow_id, "poll", {"active": True})
        assert replies(events) == [] and of(events, "bills")[-1]["inFlight"]
        # the old list call: no ReferencePrefix, no response, 200 a page
        assert backend.list_params[-1] == {"IncludeResponse": "false", "PageSize": "200"}

        backend.finish_ocr("out.pdf", {"tourDetails": {"from": "Noida", "to": "Mumbai", "startDate": "2026-08-20"}})
        backend.finish_ocr("back.pdf", None, failed=True)
        events = call(client, workflow_id, "poll", {"active": True})
        assert replies(events) == ["Read 2 bills: out.pdf, back.pdf"]
        assert of(events, "status")[0]["busy"] == "Going through 2 read bills…"
        assert of(events, "bills")[-1]["inFlight"] == []
        assert "POST /api/Ocr/MarkAsRead" in backend.calls
        assert sum(c.startswith("GET /api/Ocr/GetDocument/") for c in backend.calls) == 1  # Completed only
        assert not any(c.startswith("GET /api/Ocr/GetFile/") for c in backend.calls)  # the bytes are here
        session = session_of(client, workflow_id)
        assert session.state["local"] is True and session.held_document_ids() == [7002]
        # the model was given the read result
        assert '"from":"Noida"' in backend.gateway_calls[-1]["message"]

    def test_the_launcher_reports_bills_before_the_window_opens(self, client, backend):
        workflow_id = new_chat(client)
        self.upload(client, workflow_id, ("out.pdf", PDF + b"1"))
        body = client.post("/v1/sessions", headers=auth(), json={"mode": MODE}).json()
        assert body["status"]["launcher"] == "Processing your bill…"
        assert [p["name"] for p in body["bills"]["inFlight"]] == ["out.pdf"]
        backend.finish_ocr("out.pdf", None)
        body = client.post("/v1/sessions", headers=auth(), json={"mode": MODE}).json()
        assert body["status"]["launcher"] == "Your bill is read. Open me to go through it."
        # without a mode it is still the 0.3 session
        assert "employee" in client.post("/v1/sessions", headers=auth()).json()

    def test_a_read_only_login_cannot_send_bills(self, client, backend):
        workflow_id = new_chat(client, IsReadOnly="True")
        call(client, workflow_id, "open", {"active": True}, IsReadOnly="True")
        events = self.upload(client, workflow_id, ("out.pdf", PDF), IsReadOnly="True")
        assert replies(events) == ["This login is read-only, so bills cannot be sent."]
        assert backend.upload_parts == []

    def read_two(self, client, backend) -> str:
        workflow_id = new_chat(client)
        call(client, workflow_id, "open", {"active": True})
        self.upload(client, workflow_id, ("out.pdf", PDF + b"1"), ("back.pdf", PDF + b"2"))
        backend.finish_ocr("out.pdf", None)
        backend.finish_ocr("back.pdf", None)
        call(client, workflow_id, "poll", {"active": True})
        return workflow_id

    def test_start_over_gives_back_what_the_chat_holds(self, client, backend):
        workflow_id = self.read_two(client, backend)
        response = client.delete(f"/v1/workflows/{workflow_id}", headers=auth())
        assert response.status_code == 204
        assert backend.released == [7002]
        response = client.post(f"/v1/workflows/{workflow_id}/open", headers=auth(), json={"active": True})
        assert response.status_code == 404 and response.json()["error"] == "workflow_not_found"

    def test_an_idle_chat_gives_back_what_it_holds_and_carries_on(self, client, backend):
        workflow_id = self.read_two(client, backend)
        assert session_of(client, workflow_id).held_document_ids() == [7002]
        client.app.state.chat.idle_seconds = -1  # (the clock may not have moved on since the last save)
        new_chat(client)  # any request of the employee gives back what their idle chats hold
        client.app.state.chat.idle_seconds = 15 * 60
        assert backend.released == [7002]
        session = session_of(client, workflow_id)
        assert session.status == "open" and session.held_document_ids() == []
        # the chat continues: the given-back result is on the list again and is taken again the normal way
        events = call(client, workflow_id, "poll", {"active": True})
        assert replies(events) == ["Read 1 bills: back.pdf"]
        assert backend.calls.count("POST /api/Ocr/MarkAsRead") == 2
        assert session_of(client, workflow_id).state["read"] == ["back.pdf"]
        events = call(client, workflow_id, "messages", {"text": "still there?", "active": True})
        assert replies(events) == ["You said: still there?"]

    def test_a_chat_holding_nothing_is_left_alone_when_idle(self, client, backend):
        workflow_id = new_chat(client)
        call(client, workflow_id, "open", {"active": True})
        client.app.state.chat.idle_seconds = -1
        new_chat(client)
        client.app.state.chat.idle_seconds = 15 * 60
        assert backend.released == [] and session_of(client, workflow_id).status == "open"
        assert replies(call(client, workflow_id, "messages", {"text": "hi again", "active": True})) == [
            "You said: hi again"
        ]
