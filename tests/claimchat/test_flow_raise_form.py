"""The Raise Claim chat inside one claim form (raise_form): ClaimChatbot.tsx with its ClaimFormBridge, through HTTP.

0.4 frontend requirements §6 scenario 7 and the in-form start (Spec A §3.3, §4.9): the form's state comes with every
request (`form`), and the chat writes rows and saves the draft through bridge calls that the test answers as the page
would. The exact notes sent to the fake gateway and the exact chips are checked.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.claimchat.test_flow_raise_page import BRIEF_END, HELLO, Chat, auth, brief, chips, finish, of, replies
from tests.fakes import FLIGHT_OUT, GATEWAY_BASE, HOTEL, PDF, FakeBackend
from tourclaim_agent.claimchat.claim_assistant import initial_claim_state
from tourclaim_agent.main import create_app

CLAIM_FOR = "**Noida → Mumbai** (20 Aug 2026 – 22 Aug 2026)"
MORE = "Do you have more tickets or bills to upload for this claim?"


def empty_form() -> dict[str, Any]:
    s = initial_claim_state()
    return {
        k: s[k]
        for k in ("transportationRows", "accommodationRows", "leaveRows", "includeAccommodation", "includeLeave")
    }


@pytest.fixture
def client(settings, backend: FakeBackend):
    settings.chat_gateway_url = GATEWAY_BASE
    settings = settings.model_copy(update={"enabled_write_endpoints": ["Claim/SaveRaiseClaimDraft"]})
    with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
        yield test_client


def form_chat(client: TestClient, backend: FakeBackend, form: dict[str, Any] | None = None) -> Chat:
    return Chat(client, backend, "raise_form", tourId=503, form=form or empty_form())


def journey_form() -> dict[str, Any]:
    form = empty_form()
    row = form["transportationRows"][0]
    form["transportationRows"] = [
        {
            **row,
            "source": "Noida",
            "destination": "Mumbai",
            "DateOfTravelSource": "2026-08-20",
            "TimeOfTravelSource": "07:15",
            "DateOfTravelDestination": "2026-08-20",
            "TimeOfTravelDestiNation": "09:30",
            "ModeOfTravel": "rail",
            "uploadedFiles": [{"name": "ticket.pdf", "size": 10, "type": "application/pdf", "fileId": 41}],
        }
    ]
    return form


class TestFormStart:
    def test_an_empty_claim_asks_for_the_bills(self, client, backend):
        chat = form_chat(client, backend)
        events = chat.open()
        assert of(events, "status")[0]["busy"] == "Looking at your claim…"
        assert [c["message"] for c in chat.sent] == [
            brief(f"{HELLO} Let's do your claim for {CLAIM_FOR}. Please upload your tickets and bills.")
        ]
        assert chips(events) == ["✍️ I'll type the tour details", "Show my claim", "How is DA worked out?"]
        assert of(events, "status")[-1]["header"] == "Tour #503 · guiding your claim"
        # nothing written to the form: it was only read
        assert chat.bridge_calls == []

    def test_a_claim_with_rows_is_summed_up_and_more_bills_are_asked(self, client, backend):
        chat = form_chat(client, backend, journey_form())
        events = chat.open()
        assert [c["message"] for c in chat.sent] == [
            brief(
                f"{HELLO} Let's finish your claim for {CLAIM_FOR}.",
                f"Your claim has **1 journey**, covering **20 Aug 2026**, with 1 bill attached.\n\n{MORE}",
                question="Do you have more tickets or bills to upload?",
            )
        ]
        assert chips(events) == [
            "Yes, I'll upload more",
            "No, that's everything",
            "Show my claim",
            "How is DA worked out?",
        ]
        events = chat.tap("Yes, I'll upload more")
        # the old chip opened the file picker after saying so
        assert of(events, "open_file_picker") == [{"type": "open_file_picker"}]
        assert chat.sent[-1]["message"] == brief(
            "Okay, attach them whenever you are ready. Everything already in your claim stays as it is."
        )
        chat.open()
        events = chat.say("no more", {"action": "talk"})
        assert chips(events) == ["✍️ I'll type the tour details", "Show my claim", "How is DA worked out?"]

    def test_no_more_files(self, client, backend):
        chat = form_chat(client, backend, journey_form())
        chat.open()
        events = chat.tap("No, that's everything")
        assert chat.sent[-1]["message"] == brief("Okay. Everything I read is in your claim.")
        assert chat.session.state["doneUploading"] is True and chat.session.state["step"] == "start"
        assert chips(events) == ["✍️ I'll type the tour details", "Show my claim", "How is DA worked out?"]

    def test_inside_the_form_readiness_is_never_judged(self, client, backend):
        chat = form_chat(client, backend)
        chat.open()
        chat.say("can I submit now?", {"action": "ask_if_ready"})
        assert (
            "WHAT HAPPENED:\n1. I only read your bills into this claim. You can check it and submit it on the claim "
            "form." in (chat.sent[-1]["message"])
        )
        understand = chat.sent[0]["message"]
        assert (
            "- ask_if_ready:" in understand
            and "- show_progress:" not in understand
            and "- fill_form:" not in understand
        )
        assert (
            "Where the chat is: inside the claim form. Tour: #503 Noida → Mumbai · 20 Aug 2026 – 22 Aug 2026."
            in understand
        )

    def test_the_launcher_before_the_window_opens(self, client, backend):
        chat = form_chat(client, backend)
        chat.attach(("out.pdf", PDF + b"1"), active=False)
        body = client.post("/v1/sessions", headers=auth(), json={"mode": "raise_form", "tourId": 503}).json()
        assert body["status"]["launcher"] == "Processing your bill…"
        # the Raise Claim page's chat does not count the claim's own bills
        body = client.post("/v1/sessions", headers=auth(), json={"mode": "raise_page"}).json()
        assert body["status"]["launcher"] is None


REPORT = (
    "Your bill has been read:\n\n"
    "**✅ New (1)**\n"
    "• **out.pdf**: Flight, Delhi (Noida) → Mumbai, **20 Aug 2026**\n"
    "⚠️ This tour has no air travel, so the travel mode of the flight was left empty.\n\n"
    "✅ Added **1 journey** to this claim. The draft is saved.\n\n" + MORE
)


class TestFormRead:
    def test_a_bill_read_is_written_into_the_form_and_saved(self, client, backend):
        chat = form_chat(client, backend)
        chat.open()
        events = chat.attach(("out.pdf", PDF + b"1"))
        assert {d["reference"] for d in backend.ocr_docs.values()} == {"claim-chat:tour-503"}
        assert chat.sent[-1]["message"] == brief(
            "Your bill is being read. This takes a few minutes, and you can close this window or the browser "
            "meanwhile. I'll add what I find to this claim."
        )
        finish(backend, "out.pdf", FLIGHT_OUT)
        events = chat.poll()
        write, save = chat.bridge_calls
        assert write["method"] == "write" and write["args"]["replace"] is False
        rows = write["args"]["state"]["transportationRows"]
        assert [(r["source"], r["destination"], r["ModeOfTravel"]) for r in rows] == [
            ("Delhi (Noida)", "Mumbai", "select")
        ]
        # the new bill: the server's document, for the page to fetch (GetFile) and attach
        (file,) = rows[0]["uploadedFiles"]
        assert file["name"] == "out.pdf" and file["documentId"] == 7001 and "fileId" not in file
        assert save["method"] == "saveDraft"
        assert [c["message"] for c in chat.sent] == [
            brief(REPORT, question="Do you have more tickets or bills to upload?")
        ]
        assert of(events, "reply")[0]["tone"] == "success"
        assert chips(events) == [
            "Yes, I'll upload more",
            "No, that's everything",
            "Show my claim",
            "How is DA worked out?",
        ]
        # saved into the form's draft: the result is settled, the report is not kept
        assert chat.session.held_document_ids() == []
        storage = client.portal.call(client.app.state.chat.store.load_storage, "E1001")
        assert "aryaan.billReports.v1.1001" not in storage
        # the same bill again: a duplicate
        chat.attach(("again.pdf", PDF + b"1"))
        assert "• **again.pdf**: same file as one already uploaded, skipped" in chat.sent[-1]["message"]

    def test_a_bill_already_in_the_claim_is_not_added_again(self, client, backend):
        chat = form_chat(client, backend)
        chat.open()
        chat.attach(("out.pdf", PDF + b"1"))
        finish(backend, "out.pdf", FLIGHT_OUT)
        chat.poll()
        chat.attach(("copy.pdf", PDF + b"2"))
        backend.finish_ocr(
            "copy.pdf", {**FLIGHT_OUT, "meta": {"per_file_results": [{"filename": "copy.pdf", "success": True}]}}
        )
        before = len(chat.bridge_calls)
        events = chat.poll()
        assert chat.bridge_calls[before:] == []  # nothing new: nothing written, nothing saved
        assert chat.sent[-1]["message"] == brief(
            "Your bill has been read:\n\n**↩ Duplicate (1)**\n"
            "• **copy.pdf**: Flight, Delhi (Noida) → Mumbai, **20 Aug 2026** (already in this claim, not added again)"
            f"\n\nNothing new was added to this claim.\n\n{MORE}",
            question="Do you have more tickets or bills to upload?",
        )
        assert "tone" not in of(events, "reply")[0]

    def test_a_draft_that_could_not_be_saved(self, client, backend):
        chat = form_chat(client, backend)
        chat.open()
        chat.attach(("hotel.pdf", PDF + b"h"))
        finish(backend, "hotel.pdf", HOTEL)
        chat.answer = lambda call: (  # type: ignore[method-assign]
            {"ok": True, "result": False} if call["method"] == "saveDraft" else Chat.answer(chat, call)
        )
        events = chat.poll()
        message = chat.sent[-1]["message"]
        assert "**✅ New (1)**\n• **hotel.pdf**: Hotel stay in Mumbai, **20–22 Aug 2026**" in message
        assert (
            "✅ Added **1 stay** to this claim. ⚠️ The draft could not be saved yet; it is saved again when you leave "
            "the form."
        ) in message
        assert of(events, "reply")[0]["tone"] == "error"
        # not saved: the result stays taken (it is in the form, unsaved)
        assert chat.session.held_document_ids() == [7001]

    def test_kept_report_in_the_claim_comes_first(self, client, backend):
        chat = form_chat(client, backend)
        chat.open()
        chat.attach(("out.pdf", PDF + b"1"))
        finish(backend, "out.pdf", FLIGHT_OUT)
        backend.gateway_fail.append(401)
        chat.poll()
        storage = client.portal.call(client.app.state.chat.store.load_storage, "E1001")
        (kept,) = json.loads(storage["aryaan.billReports.v1.1001"])
        assert kept["scope"] == "tour-503" and kept["text"].endswith(MORE)

        later = form_chat(client, backend, chat.form)
        events = later.open()
        assert later.sent[-1]["message"] == brief(
            f"{HELLO} Let's finish your claim for {CLAIM_FOR}.\n\n{kept['text']}",
            question="Do you have more tickets or bills to upload?",
        )
        assert chips(events)[:2] == ["Yes, I'll upload more", "No, that's everything"]
        # the Raise Claim page does not say a claim's report
        page_chat = Chat(client, backend, "raise_page")
        page_chat.open()
        assert page_chat.sent[-1]["message"] == brief('Say word for word: "Upload your tour tickets/bills/receipts"')

    def test_save_as_draft_in_the_form(self, client, backend):
        chat = form_chat(client, backend, journey_form())
        chat.open()
        chat.say("save it", {"action": "save_draft"})
        assert [c["method"] for c in chat.bridge_calls] == ["saveDraft"]
        assert (
            "WHAT HAPPENED:\n1. Your draft is saved. Nothing has been submitted yet, so you can carry on or come "
            "back later." in (chat.sent[-1]["message"])
        )
        assert replies(chat.events) and BRIEF_END not in chat.sent[-1]["message"]
