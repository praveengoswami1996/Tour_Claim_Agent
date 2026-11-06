"""The Raise Claim chat on the Raise Claim page (raise_page): ClaimChatbot.tsx without a form, through HTTP.

The 0.4 frontend requirements §6 scenarios 1-6, 10 and 11 for `raise_page`, with the fake Tour API, OCR and AI
gateway. What the employee reads is written by the (fake) model from the notes the chat sends it, so the tests check
the exact notes sent to the gateway (the old `say` / `tell` texts, character for character) and the exact chips.

The old component cannot be driven as it is under Node (a React component with hooks, Redux, the DOM, the browser's
OCR queue and localStorage), so parity is checked against the TSX and Spec A, note by note.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FLIGHT_BACK, FLIGHT_OUT, GATEWAY_BASE, PDF, FakeBackend, make_token, tour
from tourclaim_agent.main import create_app

ORIGIN = "https://tour.example"
LINK_503 = f"{ORIGIN}/raise-claim-approver?tourId=503&isDraft=true&statusId=2&totalDays=3"
HELLO = "Hello, Test Employee! 👋\nI'm Aryaan, your Claim Assistant."
BRIEF_END = (
    "Write the message now: only what the NOTES say, at most two short sentences (plus a list if a NOTE has one)."
)


# ─── helpers (also used by test_flow_raise_form.py) ──────────────────────────


def auth(**claims: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token('E1001', **claims)}", "Origin": ORIGIN}


def sse(text: str) -> list[dict[str, Any]]:
    assert text.endswith("event: done\ndata: {}\n\n"), text[-300:]
    events = []
    for block in text.split("\n\n"):
        lines = block.split("\n")
        if lines[0] == "event: agent_event":
            events.append(json.loads(lines[1][len("data: ") :]))
    return events


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


def replies(events: list[dict[str, Any]]) -> list[str]:
    return [e["text"] for e in of(events, "reply")]


def chips(events: list[dict[str, Any]]) -> list[str]:
    return [c["label"] for c in of(events, "chips")[-1]["items"]]


def brief(*notes: str, question: str | None = None) -> str:
    """The message the narrator sends for notes without a typed message (aiAssistant.ts narrate, brief)."""
    listed = "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1))
    parts = [f"NOTES:\n{listed}", *([f"QUESTION:\n{question}"] if question else []), BRIEF_END]
    return "\n\n".join(parts)


class Chat:
    """One chat through HTTP; bridge calls are answered as the page would (`answer`)."""

    def __init__(self, client: TestClient, backend: FakeBackend, mode: str, **params: Any) -> None:
        self.client = client
        self.backend = backend
        self.form: dict[str, Any] | None = params.pop("form", None)
        response = client.post("/v1/workflows", headers=auth(), json={"mode": mode, **params})
        assert response.status_code == 201, response.text
        self.id = response.json()["workflow_id"]
        self.events: list[dict[str, Any]] = []
        self.bridge_calls: list[dict[str, Any]] = []
        self.opened: bool | None = True

    def _body(self, **body: Any) -> dict[str, Any]:
        if self.form is not None:
            body["form"] = self.form
        return body

    def post(self, path: str, active: bool = True, **body: Any) -> list[dict[str, Any]]:
        self.mark = len(self.backend.gateway_calls)
        response = self.client.post(
            f"/v1/workflows/{self.id}/{path}", headers=auth(), json=self._body(active=active, **body)
        )
        assert response.status_code == 200, response.text
        return self._finish(sse(response.text))

    def _finish(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = list(events)
        while out and out[-1]["type"] == "bridge_call":
            call = out[-1]
            self.bridge_calls.append(call)
            answer = self.answer(call)
            response = self.client.post(f"/v1/workflows/{self.id}/bridge/{call['id']}", headers=auth(), json=answer)
            assert response.status_code == 200, response.text
            out += sse(response.text)
        self.events = out
        return out

    def answer(self, call: dict[str, Any]) -> dict[str, Any]:
        """What the page answers: write keeps the rows, saveDraft saves, openForm opens."""
        if call["method"] == "write":
            self.form = call["args"]["state"]
            return {"ok": True, "form": self.form}
        if call["method"] == "saveDraft":
            return {"ok": True, "result": True, **({"form": self.form} if self.form is not None else {})}
        if call["method"] == "openForm":
            return {"ok": True, "result": {"opened": self.opened}}
        if call["method"] == "check":
            return {"ok": True, "result": []}
        return {"ok": True}

    def open(self, active: bool = True) -> list[dict[str, Any]]:
        return self.post("open", active=active)

    def say(self, text: str, understood: Any = None, active: bool = True) -> list[dict[str, Any]]:
        if understood is not None:
            self.backend.understand_replies.append(understood)
        return self.post("messages", active=active, text=text)

    def tap(self, label: str, active: bool = True) -> list[dict[str, Any]]:
        items = of(self.events, "chips")[-1]["items"]
        chip = next(c for c in items if c["label"] == label)
        return self.post("actions", active=active, chip=chip["id"])

    def poll(self, active: bool = True) -> list[dict[str, Any]]:
        return self.post("poll", active=active)

    def attach(self, *files: tuple[str, bytes] | tuple[str, bytes, str], active: bool = True) -> list[dict[str, Any]]:
        self.mark = len(self.backend.gateway_calls)
        parts = [("files", (f[0], f[1], f[2] if len(f) > 2 else "application/pdf")) for f in files]
        data = {"active": "true" if active else "false"}
        if self.form is not None:
            data["form"] = json.dumps(self.form)
        response = self.client.post(f"/v1/workflows/{self.id}/attachments", headers=auth(), data=data, files=parts)
        assert response.status_code == 200, response.text
        return self._finish(sse(response.text))

    @property
    def sent(self) -> list[dict[str, Any]]:
        """The gateway calls of the last request."""
        return self.backend.gateway_calls[self.mark :]

    @property
    def session(self) -> Any:
        return self.client.portal.call(self.client.app.state.chat.store.get, self.id)


@pytest.fixture
def client(settings, backend: FakeBackend):
    settings.chat_gateway_url = GATEWAY_BASE
    settings = settings.model_copy(update={"enabled_write_endpoints": ["Claim/SaveRaiseClaimDraft"]})
    with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
        yield test_client


def page(client: TestClient, backend: FakeBackend) -> Chat:
    return Chat(client, backend, "raise_page")


def finish(backend: FakeBackend, name: str, response: dict[str, Any] | None, *, failed: bool = False) -> None:
    backend.finish_ocr(name, response, failed=failed)


# ─── 1. the greeting ─────────────────────────────────────────────────────────


class TestGreeting:
    def test_no_saved_drafts_says_upload_word_for_word(self, client, backend):
        chat = page(client, backend)
        events = chat.open()
        assert [c["message"] for c in chat.sent] == [
            brief('Say word for word: "Upload your tour tickets/bills/receipts"')
        ]
        assert chips(events) == ["✍️ I'll type the tour details"]
        assert of(events, "composer")[-1] == {
            "type": "composer",
            "placeholder": "Type a detail, a correction or a question…",
            "canType": True,
            "canAttach": True,
        }
        status = of(events, "status")[-1]
        assert status["header"] == "Upload your bills to begin" and status["launcher"] is None
        # the old chip: a note for the model, not a typed message
        events = chat.tap("✍️ I'll type the tour details")
        assert [c["message"] for c in chat.sent] == [
            brief('Tell me about your journey, for example "Left Noida 5 Oct 10:30 am by air, back on 8th by train".')
        ]

    def test_the_greeting_waits_for_the_window(self, client, backend):
        chat = page(client, backend)
        events = chat.poll(active=False)
        assert replies(events) == [] and backend.gateway_calls == []
        assert chat.session.state["greeted"] is False

    def test_no_approved_tours(self, client, backend):
        backend.outside_tours[1001] = [t for t in backend.outside_tours[1001] if t["statusId"] != 2]
        chat = page(client, backend)
        events = chat.open()
        assert [c["message"] for c in chat.sent] == [
            brief(
                f"{HELLO} You have no approved tours waiting for a claim yet. Once one is approved, I can help with it."
            )
        ]
        assert chips(events) == []
        assert of(events, "composer")[-1]["placeholder"] == "No approved tours to claim for yet"
        assert of(events, "composer")[-1]["canAttach"] is False
        assert of(events, "status")[-1]["header"] == "No approved tours to claim for"

    def test_saved_drafts_greeting_with_link_chips(self, client, backend):
        # a draft saved by an earlier chat (its report was seen)
        first = page(client, backend)
        first.open()
        first.attach(("out.pdf", PDF + b"1"))
        finish(backend, "out.pdf", FLIGHT_OUT)
        first.poll()
        assert 503 in backend.drafts

        chat = page(client, backend)
        events = chat.open()
        assert of(events, "status")[0]["busy"] == "Looking at your saved drafts…"
        assert chat.sent[-1]["message"] == brief(
            f"{HELLO} You have **1 saved draft**. You can continue one, or upload new bills."
        )
        assert chips(events) == [LINK_503, "📎 Upload other bills", "Not now"]
        link, upload, _ = of(events, "chips")[-1]["items"]
        assert link["kind"] == "link" and link["href"] == LINK_503 and upload["kind"] == "upload"
        # what the draft holds goes to the model with typed turns (PAGE), never in the greeting
        details = chat.session.state["draftDetails"]["503"]
        assert details["route"] == "Noida → Mumbai" and details["savedInChat"] is True
        events = chat.say("what is in my draft?", {"action": "talk"})
        typed = chat.sent[-1]["message"]
        assert "PAGE:\nTabs on the Raise Claim page" in typed
        assert (
            '\n\nSAVED DRAFTS IN DETAIL (what each saved claim holds):\n[{"tourId":503,"route":"Noida → Mumbai"'
            in typed
        )
        assert "SAVED CLAIMS:\n#503: Noida → Mumbai · 20 Aug 2026 – 22 Aug 2026" in chat.sent[0]["message"]

    def test_not_now_drops_the_saved_claims(self, client, backend):
        first = page(client, backend)
        first.open()
        first.attach(("out.pdf", PDF + b"1"))
        finish(backend, "out.pdf", FLIGHT_OUT)
        first.poll()
        chat = page(client, backend)
        chat.open()
        events = chat.tap("Not now")
        assert [c["message"] for c in chat.sent] == [
            brief("Okay. Upload bills or tell me about a trip whenever you are ready.")
        ]
        assert chips(events) == ["✍️ I'll type the tour details"]

    def test_the_gateway_down_shows_the_notice_and_try_again(self, client, backend):
        chat = page(client, backend)
        backend.gateway_fail.append(401)
        events = chat.open()
        assert of(events, "system_notice") == [
            {"type": "system_notice", "text": "The assistant is unavailable right now. Please try again."}
        ]
        assert of(events, "chips")[-1]["items"] == [
            {"id": "try-again", "label": "Try again", "kind": "action", "primary": True}
        ]
        events = chat.post("retry")
        assert replies(events) == ['Say word for word: "Upload your tour tickets/bills/receipts"']
        assert chips(events) == ["✍️ I'll type the tour details"]


# ─── 3. bills: upload, duplicates, the 20-file limit, the read report ────────


REPORT_3 = (
    "Your bill has been read:\n\n"
    "**✅ New (1)**\n"
    "• **out.pdf**: Flight, Delhi (Noida) → Mumbai, **20 Aug 2026**\n\n"
    "**↩ Duplicate (1)**\n"
    "• **out2.pdf**: same file as one already uploaded, skipped\n\n"
    "**🚫 Not relevant (1)**\n"
    "• **notes.docx**: **not uploaded**: I can only read PDFs and photos or scans (JPG, PNG), not this kind of "
    "file.\n\n"
    "✅ Saved to your draft for **Noida → Mumbai (20 Aug 2026 – 22 Aug 2026)**. Nothing is submitted."
)


class TestBills:
    def test_upload_duplicate_and_rejected_then_the_report_saves_the_draft(self, client, backend):
        chat = page(client, backend)
        chat.open()
        events = chat.attach(
            ("out.pdf", PDF + b"1"), ("out2.pdf", PDF + b"1"), ("notes.docx", b"word", "application/octet-stream")
        )
        # only the new file is a user bubble; the others are reported with the read
        assert of(events, "user_files") == [{"type": "user_files", "names": ["out.pdf"]}]
        assert of(events, "status")[0]["busy"] == "Processing 1 bill…"
        assert [c["message"] for c in chat.sent] == [
            brief(
                "Your bill is being read. This takes a few minutes, and you can close this window or the browser "
                "meanwhile. I'll show what I find and save it to your drafts."
            )
        ]
        assert backend.upload_parts == [["Files", "Reference"]]
        assert {d["reference"] for d in backend.ocr_docs.values()} == {"outside-chat:unassigned"}
        assert of(events, "bills")[-1]["inFlight"] == [{"name": "out.pdf", "documentId": 7001}]
        assert chips(events) == ["✍️ I'll type the tour details", "⏹ Stop processing"]
        status = of(events, "status")[-1]
        assert status["header"] == "Processing 1 bill…" and status["launcher"] == "Processing your bill…"

        # still being read: nothing to go through
        assert replies(chat.poll()) == []
        finish(backend, "out.pdf", FLIGHT_OUT)
        events = chat.poll()
        busy = [e["busy"] for e in of(events, "status")]
        assert busy[:2] == ["Going through 1 read bill…", "Saving to your drafts…"]
        assert [c["message"] for c in chat.sent] == [brief(REPORT_3)]
        assert of(events, "reply")[0]["tone"] == "error"  # a file was turned away
        # saved straight into the tour's draft (no question), the link chip for it
        assert len(backend.saved_requests) == 1 and backend.saved_requests[0]["draft"]["EmpTourPlanId"] == 503
        assert chips(events) == [LINK_503, "📎 Upload other bills", "Not now"]
        assert of(events, "status")[-1].get("draftsChanged") is True
        # the report was on screen: it is not kept; the result is settled
        assert chat.session.held_document_ids() == []
        storage = client.portal.call(client.app.state.chat.store.load_storage, "E1001")
        assert "aryaan.billReports.v1.1001" not in storage
        assert json.loads(storage["aryaan.chatDrafts.v1.1001"]).keys() == {"503"}

        # the same bytes again (another name): a duplicate, nothing to read
        events = chat.attach(("copy.pdf", PDF + b"1"))
        assert of(events, "user_files") == [] and backend.upload_parts == [["Files", "Reference"]]
        assert [c["message"] for c in chat.sent] == [
            brief(
                "Here is how I sorted your upload:\n\n**↩ Duplicate (1)**\n"
                "• **copy.pdf**: same file as one already uploaded, skipped\n\n"
                "Nothing new to read, so nothing was added."
            )
        ]

    def test_only_rejected_files_are_sorted_at_once(self, client, backend):
        chat = page(client, backend)
        chat.open()
        events = chat.attach(("photo.heic", b"x", "image/heic"), ("empty.pdf", b""))
        assert [c["message"] for c in chat.sent] == [
            brief(
                "Here is how I sorted your upload:\n\n**🚫 Not relevant (2)**\n"
                "• **photo.heic**: **not uploaded**: HEIC photos can't be read. Please send it as a JPG or PNG, or a "
                "PDF.\n"
                "• **empty.pdf**: **not uploaded**: the file is empty. Please attach it again.\n\n"
                "Nothing new to read, so nothing was added."
            )
        ]
        assert of(events, "reply")[0]["tone"] == "error" and backend.upload_parts == []

    def test_at_most_twenty_bills_a_read(self, client, backend):
        chat = page(client, backend)
        chat.open()
        files = [(f"b{i:02d}.pdf", PDF + str(i).encode()) for i in range(1, 23)]
        events = chat.attach(*files)
        assert of(events, "user_files")[0]["names"] == [f"b{i:02d}.pdf" for i in range(1, 21)]
        assert [c["message"] for c in chat.sent] == [
            brief(
                "I can read up to 20 bills at a time, so I'm reading the first 20 now. Please attach the other 2 after "
                "this, in groups of up to 20:\n• b21.pdf\n• b22.pdf",
                "Your 20 bills are being read. This takes a few minutes, and you can close this window or the browser "
                "meanwhile. I'll show what I find and save it to your drafts.",
            )
        ]
        assert of(events, "status")[0]["busy"] == "Processing 20 bills…"
        assert len(backend.ocr_docs) == 20

    def test_same_name_different_bytes_is_renamed(self, client, backend):
        chat = page(client, backend)
        chat.open()
        backend.fail_next("Ocr/Upload", 500)  # the send fails: the files stay staged
        events = chat.attach(("bill.pdf", PDF + b"1"))
        assert of(events, "bills")[-1]["staged"][0]["name"] == "bill.pdf"
        assert chips(events)[0] == "Read again"
        assert chat.sent[-1]["message"] == brief("failure Your files are still here, so please try again.")
        events = chat.attach(("bill.pdf", PDF + b"2"))
        # the staged file was tried once already: only the new one triggers the automatic send, and both go
        assert of(events, "user_files") == [{"type": "user_files", "names": ["bill (2).pdf"]}]
        assert [d["fileName"] for d in backend.ocr_docs.values()] == ["bill.pdf", "bill (2).pdf"]

    def test_window_closed_launcher_notice_then_the_report_on_open(self, client, backend):
        chat = page(client, backend)
        chat.open()
        chat.attach(("out.pdf", PDF + b"1"), ("back.pdf", PDF + b"2"))
        finish(backend, "out.pdf", FLIGHT_OUT)
        finish(backend, "back.pdf", FLIGHT_BACK)
        events = chat.poll(active=False)
        assert replies(events) == [] and chat.sent == []
        status = of(events, "status")[-1]
        assert status["launcher"] == "Your 2 bills are read. Open me to go through them."
        body = client.post("/v1/sessions", headers=auth(), json={"mode": "raise_page"}).json()
        assert body["status"]["launcher"] == "Your 2 bills are read. Open me to go through them."
        events = chat.open()
        report = chat.sent[-1]["message"]
        assert report.startswith("NOTES:\n1. Your 2 bills have been read:\n\n**✅ New (2)**\n")
        assert (
            "✅ Saved to your draft for **Noida → Mumbai (20 Aug 2026 – 22 Aug 2026)**. Nothing is submitted." in report
        )
        assert of(events, "status")[-1]["launcher"] is None

    def test_bills_read_before_the_greeting_become_its_header(self, client, backend):
        chat = page(client, backend)
        chat.attach(("out.pdf", PDF + b"1"), active=False)
        assert chat.session.state["greeted"] is False
        finish(backend, "out.pdf", FLIGHT_OUT)
        chat.open()
        assert [c["message"] for c in chat.sent][0].startswith(
            "NOTES:\n1. Your bill is being read. This takes a few minutes"
        )
        assert "2. Hi Test Employee, your bill has been read:\n\n**✅ New (1)**" in chat.sent[-1]["message"]

    def test_still_being_read_greeting(self, client, backend):
        first = page(client, backend)
        first.attach(("out.pdf", PDF + b"1"), ("back.pdf", PDF + b"2"), active=False)
        chat = page(client, backend)
        chat.open()
        assert chat.sent[-1]["message"] == brief(
            "Hi Test Employee, the 2 bills you uploaded are still being read. I'll show what I find and save it to "
            "your "
            "drafts as soon as they're done. You can close this window meanwhile."
        )

    def test_kept_report_comes_first_in_the_next_chat(self, client, backend):
        chat = page(client, backend)
        chat.open()
        chat.attach(("out.pdf", PDF + b"1"))
        finish(backend, "out.pdf", FLIGHT_OUT)
        backend.gateway_fail.append(401)  # the report never reaches the screen
        events = chat.poll()
        assert of(events, "system_notice")
        storage = client.portal.call(client.app.state.chat.store.load_storage, "E1001")
        kept = json.loads(storage["aryaan.billReports.v1.1001"])
        assert [(r["scope"], r["tourIds"], r["tone"]) for r in kept] == [("page", [503], "success")]
        assert kept[0]["text"].startswith("Your bill has been read:")

        # the browser is closed: a new chat the next day
        assert client.delete(f"/v1/workflows/{chat.id}", headers=auth()).status_code == 204
        later = page(client, backend)
        events = later.open()
        assert later.sent[-1]["message"] == brief(f"{HELLO}\n\n{kept[0]['text']}")
        assert of(events, "reply")[0]["tone"] == "success"
        assert chips(events) == [LINK_503, "📎 Upload other bills", "Not now"]
        storage = client.portal.call(client.app.state.chat.store.load_storage, "E1001")
        assert "aryaan.billReports.v1.1001" not in storage

    def test_stop_processing(self, client, backend):
        chat = page(client, backend)
        chat.open()
        chat.attach(("out.pdf", PDF + b"1"))
        events = chat.tap("⏹ Stop processing")
        assert of(events, "status")[0]["busy"] == "Stopping…"
        assert backend.cancelled == [7001]
        assert chat.sent[-1]["message"] == brief(
            "Stopped reading. Nothing was added. Attach the bills again whenever you want me to read them."
        )
        assert of(events, "bills")[-1]["inFlight"] == []

    def test_an_unreadable_bill_can_be_read_again(self, client, backend):
        chat = page(client, backend)
        chat.open()
        chat.attach(("blur.pdf", PDF + b"1"))
        finish(backend, "blur.pdf", None, failed=True)
        events = chat.poll()
        assert "• **blur.pdf**: ⚠️ couldn't be read" in chat.sent[-1]["message"]
        assert chips(events)[-1] == "↻ Read the unreadable bill again"
        events = chat.tap("↻ Read the unreadable bill again")
        assert backend.retried == [7001]
        assert chat.sent[-1]["message"] == brief(
            "That bill is being processed again. I'll go through it when it's done."
        )
        assert of(events, "bills")[-1]["inFlight"] == [{"name": "blur.pdf", "documentId": 7001}]


# ─── 6. typed trip details -> tour -> details -> review -> fill the form ─────


def journeys(*rows: tuple[int, str, str, str, str]) -> dict[str, Any]:
    edits = []
    for row, src, dst, day, mode in rows:
        edits += [
            {"section": "transport", "row": row, "field": "from", "value": src},
            {"section": "transport", "row": row, "field": "to", "value": dst},
            {"section": "transport", "row": row, "field": "departureDate", "value": day},
            {"section": "transport", "row": row, "field": "mode", "value": mode},
        ]
    return {"action": "edit", "edits": edits}


TIMES = {
    "action": "edit",
    "edits": [
        {"section": "transport", "row": 1, "field": "departureTime", "value": "07:15"},
        {"section": "transport", "row": 1, "field": "arrivalDate", "value": "2026-08-20"},
        {"section": "transport", "row": 1, "field": "arrivalTime", "value": "09:30"},
        {"section": "transport", "row": 2, "field": "departureTime", "value": "18:00"},
        {"section": "transport", "row": 2, "field": "arrivalDate", "value": "2026-08-23"},
        {"section": "transport", "row": 2, "field": "arrivalTime", "value": "10:00"},
    ],
}


def what_happened(message: str) -> str:
    """The WHAT HAPPENED part of a typed turn's narrator message."""
    start = message.index("WHAT HAPPENED:\n") + len("WHAT HAPPENED:\n")
    end = re.search(r"\n\n(OPEN QUESTION:|ALREADY SHOWN \(do not repeat\):|CLAIM:|PAGE:|Now reply)", message[start:])
    assert end is not None
    return message[start : start + end.start()]


class TestTypedClaim:
    def test_typed_trip_to_the_form(self, client, backend):
        backend.outside_tours[1001].append(
            tour(504, 1001, kind_dest="Chennai", status_id=2, status="Tour Approved", start="2026-09-01")
        )
        chat = page(client, backend)
        chat.open()
        events = chat.say(
            "left Noida 20 Aug by train, back 22nd by train",
            journeys((0, "Noida", "Mumbai", "2026-08-20", "rail"), (2, "Mumbai", "Noida", "2026-08-22", "rail")),
        )
        understand, narrate = chat.sent
        # what understand() is given: the open question, the context and the recent chat (without this message)
        assert understand["temperature"] == 0.1 and understand["max_tokens"] == 600
        assert understand["system_prompt"].startswith("You read one message from a DFCCIL employee")
        assert (
            "\n\nCONTEXT:\nWhere the chat is: the Raise Claim page. Tour: not chosen yet.\nCURRENT CLAIM:\nToday: "
            in (understand["message"])
        )
        assert understand["message"].endswith(
            ". The tour is not known yet; it is worked out from the bills.\n"
            "IN THE CLAIM NOW: 0 journeys, 0 stays.\nJOURNEYS\n"
            "T1: from=- to=- departure=- - arrival=- - mode=select bills=0 (blank row)\n"
            "STAYS\n(no stays)\nLEAVE\n(no leave)\n\n"
            'RECENT CHAT (oldest first):\nAssistant: Say word for word: "Upload your tour tickets/bills/receipts"\n\n'
            "EMPLOYEE'S MESSAGE:\nleft Noida 20 Aug by train, back 22nd by train\n\nAnswer with the JSON object only."
        )
        assert "OPEN QUESTION" not in understand["message"]  # step start has no question
        assert what_happened(narrate["message"]) == (
            "1. Updated:\n• Journey 1: from Noida\n• Journey 1: to Mumbai\n• Journey 1: departure date 20 Aug 2026\n"
            "• Journey 1: Rail\n• Journey 2: from Mumbai\n• Journey 2: to Noida\n• Journey 2: departure date 22 Aug "
            "2026\n"
            "• Journey 2: Rail\n"
            "2. These bills look like they belong to **Noida → Mumbai** (20 Aug 2026 – 22 Aug 2026), tour #503. Is "
            "that "
            "right?"
        )
        assert "OPEN QUESTION:\nAre the uploaded bills for the suggested tour?" in narrate["message"]
        assert chips(events) == ["Yes, that's right", "No, choose another"]

        # "No, choose another": every tour, the first one primary
        events = chat.tap("No, choose another")
        assert chat.sent[-1]["message"] == brief(
            "Okay, which tour is this claim for?", question="Which tour is this claim for?"
        )
        assert chips(events) == [
            "Noida → Mumbai · 20 Aug 2026 – 22 Aug 2026",
            "Noida → Chennai · 01 Sep 2026 – 03 Sep 2026",
        ]
        assert of(events, "chips")[-1]["items"][0]["primary"] is True

        events = chat.tap("Noida → Mumbai · 20 Aug 2026 – 22 Aug 2026")
        assert of(events, "status")[0]["busy"] == "Getting your tour details…"
        first_question = "What time did you leave for journey 1 (Noida → Mumbai) on 20 Aug 2026?"
        assert chat.sent[-1]["message"] == (
            "NOTES:\n1. Claim for Noida → Mumbai.\n"
            '   data: [{"label":"Tour","value":"#503"},{"label":"Dates","value":"20 Aug 2026 – 22 Aug 2026"}]\n'
            "2. The bills are now in a claim for Noida → Mumbai · 20 Aug 2026 – 22 Aug 2026, but it is not saved yet. "
            "In one short line, say it is not saved as a draft yet.\n"
            f"3. {first_question}\n\n(3 more after this one)\n\n"
            '(If you would rather fill something on the form yourself, just say "skip".)\n\n'
            f"QUESTION:\n{first_question}\n\n{BRIEF_END}"
        )
        assert chips(events) == [
            "Skip this one",
            "I'll fill the rest on the form",
            "Save as draft",
            "Fill the form",
            "What's left?",
            "Show my claim",
            "How is DA worked out?",
            "↩ Undo last change",
            "↩ Different tour",
        ]
        assert of(events, "composer")[-1]["placeholder"] == "Type your answer…"
        assert of(events, "status")[-1]["header"] == "Tour #503 · you review and submit"

        # the other details in one message, then the stay and leave questions one at a time
        events = chat.say("7:15 to 9:30, back 6 pm arriving 10 am next day", TIMES)
        assert what_happened(chat.sent[-1]["message"]).endswith(
            '2. Did you stay anywhere overnight? You can upload the hotel bill or just tell me, like "guest house in '
            'Ahmedabad, 5th to 8th". Say "no" if you didn\'t.'
        )
        assert chips(events)[:3] == ["📎 Yes, upload hotel bill", "✍️ Yes, I'll type it", "No stay"]
        events = chat.tap(
            "No stay",
        )
        assert of(events, "user_message") == [{"type": "user_message", "text": "no"}]
        # the chip's words go through the model like typed ones (no understand reply scripted: "talk")
        assert chips(events)[:3] == ["📎 Yes, upload hotel bill", "✍️ Yes, I'll type it", "No stay"]
        events = chat.say("no", {"action": "no_stay"})
        assert what_happened(chat.sent[-1]["message"]) == (
            '1. Were you on leave on any day of the tour? For example, "leave on 7th". Say "no" if not.'
        )
        assert chips(events)[:2] == ["Yes, I was on leave", "No leave"]
        events = chat.say("no", {"action": "no_leave"})
        # the review: one question at a time, with its chips
        assert '"kind":"uncoveredDays","mustFix":false' in chat.sent[-1]["message"]
        assert chips(events)[:2] == ["📎 Upload it", "I don't have it / not needed"]
        asked = 0
        while chips(events)[:2] == ["📎 Upload it", "I don't have it / not needed"] and asked < 20:
            events = chat.tap("I don't have it / not needed")
            asked += 1
        # the day not covered, the nights in Mumbai, four station rides, local travel, two tickets
        assert asked == 9
        readout = chat.sent[-1]["message"]
        assert readout.endswith(
            "2. Your claim:\n\nJourneys\n1. Noida → Mumbai · 20 Aug 2026 07:15 to 20 Aug 2026 09:30 · Rail\n"
            "2. Mumbai → Noida · 22 Aug 2026 18:00 to 23 Aug 2026 10:00 · Rail\n\nStays\nNo stays added.\n\n"
            "Leave\nNo leave.\n\nIf anything looks off, tell me and I'll fix it. When you're happy, say \"fill the "
            'form".'
            f"\n\n{BRIEF_END}"
        )
        # the settled note before the read-out has no tone, so the reply has none (useNarrator toneOf)
        assert "tone" not in of(events, "reply")[0]
        assert chips(events) == [
            "Save as draft",
            "Fill the form",
            "What's left?",
            "Show my claim",
            "How is DA worked out?",
            "↩ Undo last change",
            "↩ Different tour",
        ]
        assert of(events, "chips")[-1]["items"][1]["primary"] is True  # "Fill the form" in confirm

        # fill the form: the page opens it with the claim (bridge openForm)
        events = chat.tap("Fill the form")
        (call,) = chat.bridge_calls
        assert call["method"] == "openForm" and call["args"]["tourId"] == 503
        assert [(r["source"], r["destination"]) for r in call["args"]["state"]["transportationRows"]] == [
            ("Noida", "Mumbai"),
            ("Mumbai", "Noida"),
        ]
        assert chat.sent[-1]["message"] == (
            "NOTES:\n1. The claim form was filled with the claim and opened. Ask the employee to review each section, "
            "attach anything missing (amounts and bill files go on the form), then submit.\n\n"
            f"{BRIEF_END}"
        )

    def test_save_as_draft_and_open_it(self, client, backend):
        chat = page(client, backend)
        chat.open()
        chat.say("left Noida 20 Aug by train", journeys((0, "Noida", "Mumbai", "2026-08-20", "rail")))
        events = chat.tap("Yes, that's right")
        events = chat.tap("Save as draft")
        assert of(events, "status")[0]["busy"] == "Saving your draft…"
        assert backend.saved_requests[-1]["draft"]["EmpTourPlanId"] == 503
        assert chat.sent[-1]["message"] == brief(
            "Draft saved for Noida → Mumbai · 20 Aug 2026 – 22 Aug 2026. It stays in the Raise Claim tab until you "
            "submit it.",
            "Would you like to open the claim now?",
            question="Would you like to open the claim now?",
        )
        assert chips(events)[:2] == ["Yes, open it", "No, keep working here"]
        events = chat.tap("Yes, open it")
        assert chat.bridge_calls[-1]["method"] == "openForm" and chat.bridge_calls[-1]["args"] == {"tourId": 503}
        assert of(events, "status")[0]["busy"] == "Opening the claim…"

    def test_start_over_and_undo(self, client, backend):
        chat = page(client, backend)
        chat.open()
        chat.say("left Noida 20 Aug by train", journeys((0, "Noida", "Mumbai", "2026-08-20", "rail")))
        chat.tap("Yes, that's right")
        events = chat.say("start over", {"action": "start_over"})
        assert what_happened(chat.sent[-1]["message"]) == (
            "1. The employee asked to start over. NOTHING HAS BEEN CLEARED YET. If they confirm, every journey, stay "
            'and leave entry in this claim will be cleared (they can say "undo" afterwards). Ask whether they want to '
            "clear the claim and start over."
        )
        assert chips(events)[:2] == ["Yes, start over", "No, keep my claim"]
        assert [c.get("primary", False) for c in of(events, "chips")[-1]["items"][:2]] == [False, True]
        chat.backend.understand_replies.append({"action": "confirm_start_over"})
        events = chat.tap("Yes, start over")
        assert of(events, "user_message") == [{"type": "user_message", "text": "yes"}]
        typed = chat.sent[-1]["message"]
        assert what_happened(typed) == (
            '1. Cleared. Attach your tickets and bills, or tell me about the trip. Say "undo" if you change your mind.'
        )
        # the old quirk: the start-over question stays open (resetAskedRef is not cleared)
        assert "OPEN QUESTION:\nShall I clear the claim and start over?" in typed
        assert "↩ Undo last change" in chips(events) and "Yes, start over" not in chips(events)
        events = chat.tap("↩ Undo last change")
        assert chat.sent[-1]["message"].startswith("NOTES:\n1. Last change undone.\n2. What time did you leave")

    def test_a_message_too_long_is_not_read(self, client, backend):
        chat = page(client, backend)
        chat.open()
        chat.say("x" * 1001)
        (narrate,) = chat.sent
        assert narrate["message"].startswith(
            "EMPLOYEE SAID:\n"
            + "x" * 300
            + "…\n\nWHAT HAPPENED:\n1. The employee's message is 1001 characters long, more than the 1000 that can "
            "be read, so it was not read. Ask them to send it in shorter pieces: one journey or stay at a time."
        )
        assert chat.session.messages[-2] == {
            "id": chat.session.messages[-2]["id"],
            "role": "user",
            "text": "x" * 200 + "…",
        }

    def test_understand_down_shows_the_notice_and_try_again_reads_it_again(self, client, backend):
        chat = page(client, backend)
        chat.open()
        backend.gateway_fail.append(401)
        events = chat.say("hello")
        assert of(events, "system_notice") and chips(events) == ["Try again"]
        events = chat.post("retry")
        assert replies(events) == ["You said: hello"]
        assert what_happened(chat.sent[-1]["message"]).startswith(
            "1. Nothing in the claim was changed by this message."
        )
