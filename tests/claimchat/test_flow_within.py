"""The Within Jurisdiction chat (claimchat/flows/within.py, port of WithinClaimChatbot.tsx) through HTTP.

The fake backend plays the Tour API (OCR) and the AI gateway; `FakeForm` plays the form in the browser (it answers
every bridge call and sends its tours with every request, as the frontend does). The fake model writes back the
NOTES it was given, and the tests check the notes the flow sent it, character for character.
"""

from __future__ import annotations

import copy
import json
import random
import re
from contextvars import ContextVar
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes import GATEWAY_BASE, PDF, FakeBackend, make_token
from tourclaim_agent.claimchat import js_compat
from tourclaim_agent.claimchat.ai.ai_assistant import compact, prompts
from tourclaim_agent.claimchat.ai.assistant_actions import TALK_NOTE, WITHIN_START_OVER_ASK
from tourclaim_agent.claimchat.within_assistant import WITHIN_DA_RULES, totals_text, tour_summary_rows
from tourclaim_agent.claimchat.within_form import DECLARATION_TEXTS
from tourclaim_agent.claimchat.within_tour_plan import map_tour_plan_to_tours
from tourclaim_agent.main import create_app

NOW_MS = 1_793_934_000_000  # 2026-11-06 08:30 IST: the October plan is inside the tour plan date window
MODE = "within_form"
UNDERSTAND = prompts()["understand_within"]
REPLACE = 'Shall I go ahead? (You can say "undo" afterwards.)'
DATA_CHIPS = ["What's left?", "Show my tours", "Check & submit", "Save draft", "How is DA worked out?"]


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    # the turns run on the app's own thread: the clock is replaced for every context, not only this one
    clock = js_compat.Runtime(now_ms=lambda: NOW_MS, random=random.random)
    monkeypatch.setattr(js_compat, "_runtime", ContextVar("claimchat_js_runtime_test", default=clock))


@pytest.fixture
def client(settings, backend: FakeBackend):
    settings.chat_gateway_url = GATEWAY_BASE
    with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
        yield test_client


# ── the October plan (as tests/claimchat/test_within_assistant.py) ──


def leg(from_: str, to: str, d: str, dep: str, arr: str, mode: str, vehicle: str = "") -> dict[str, Any]:
    return {
        "from": from_,
        "to": to,
        "departureDate": d,
        "departureTime": dep,
        "arrivalDate": d,
        "arrivalTime": arr,
        "modeOfTravel": mode,
        "personalVehicleType": vehicle,
        "distance": 0,
        "includeTravelCosts": True,
    }


def plan(name: str = "Oct_Tour_Plan.pdf") -> dict[str, Any]:
    """1 Oct (1 journey), 2 Oct (2), 3 Oct (3, personal vehicle), 4 Oct (2, personal vehicle)."""
    return {
        "meta": {"per_file_results": [{"filename": name, "success": True, "bill_type": "tour_plan"}]},
        "tourDetails": {
            "purpose": "Inspect; Review; Audit; Meeting",
            "startDate": "2026-10-01",
            "startTime": "09:00",
            "from": "Delhi",
            "endDate": "2026-10-01",
            "endTime": "11:00",
            "to": "Ghaziabad",
            "modeOfTravel": "Company Provided Vehicle",
            "personalVehicleType": "",
            "distance": 0,
            "includeTravelCosts": True,
            "tourLegs": [
                leg("Delhi", "Faridabad", "2026-10-02", "08:30", "10:00", "Rail"),
                leg("Faridabad", "Palwal", "2026-10-02", "14:00", "15:15", "Public Transport"),
                leg("Delhi", "Noida", "2026-10-03", "07:45", "09:15", "Personal Vehicle", "Four Wheeler"),
                leg("Noida", "Greater Noida", "2026-10-03", "13:00", "14:00", "Personal Vehicle", "Four Wheeler"),
                leg("Greater Noida", "Dadri", "2026-10-03", "16:30", "17:15", "Personal Vehicle", "Four Wheeler"),
                leg("Delhi", "Gurugram", "2026-10-04", "09:30", "11:00", "Personal Vehicle", "Two Wheeler"),
                leg("Gurugram", "Manesar", "2026-10-04", "14:30", "15:30", "Personal Vehicle", "Two Wheeler"),
            ],
        },
    }


def plan_tours() -> list[dict[str, Any]]:
    """The plan's 4 tours, as the form holds them after the chat wrote them."""
    return map_tour_plan_to_tours(plan())["tours"]


def complete_tours(*, ended: bool = False) -> list[dict[str, Any]]:
    """The plan's tours with every personal-vehicle distance given (nothing left to resolve)."""
    tours = plan_tours()
    for t in tours:
        d = t["tourDetails"]
        if d["modeOfTravel"] == "Personal Vehicle":
            d["distance"] = 10
        for lg in d["tourLegs"]:
            if lg["modeOfTravel"] == "Personal Vehicle":
                lg["distance"] = 10
        d["tourEnd"] = ended
    return tours


BLANK = {
    "id": "tour-1",
    "tourDetails": {
        "startTime": "",
        "endTime": "",
        "modeOfTravel": "",
        "from": "",
        "to": "",
        "purpose": "",
        "includeTravelCosts": False,
        "tourLegs": [],
        "tourEnd": False,
        "subTotal": 0,
    },
    "accommodationDetails": {"entries": [], "totalAmount": 0},
    "declarationAccepted": False,
    "isExpanded": True,
}


# ── the browser: the form and the chat ──


class FakeForm:
    """The Within Jurisdiction form in the browser: answers the bridge calls as the old WithinFormBridge did."""

    def __init__(self, tours: list[dict[str, Any]] | None = None) -> None:
        self.tours = copy.deepcopy(tours) if tours is not None else [copy.deepcopy(BLANK)]
        self.check: list[str] = []
        self.info = {"selfApprove": False, "approverChosen": True}
        self.calls: list[tuple[str, Any]] = []
        self.fail: dict[str, str] = {}

    def snapshot(self) -> dict[str, Any]:
        return {"tours": copy.deepcopy(self.tours)}

    def answer(self, method: str, args: Any) -> dict[str, Any]:
        self.calls.append((method, args))
        if method in self.fail:
            return {"ok": False, "error": self.fail[method], "form": self.snapshot()}
        result: Any = None
        if method == "write":
            self.tours = copy.deepcopy(args["tours"])
        elif method == "check":
            result = list(self.check)
        elif method == "info":
            result = dict(self.info)
        elif method == "submit":
            result = {"submitted": True}
        return {"ok": True, "result": result, "form": self.snapshot()}

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


def headers(**claims: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token('E1001', **claims)}"}


def sse(text: str) -> list[dict[str, Any]]:
    assert text.endswith("event: done\ndata: {}\n\n"), text[-200:]
    events = []
    for block in text.split("\n\n"):
        lines = block.split("\n")
        if lines[0] == "event: agent_event":
            events.append(json.loads(lines[1][len("data: ") :]))
    return events


def of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


class Chat:
    """One within_form conversation, driven as the frontend drives it (every bridge call is answered)."""

    def __init__(self, client: TestClient, backend: FakeBackend, form: FakeForm, **claims: Any) -> None:
        self.client, self.backend, self.form, self.claims = client, backend, form, claims
        response = client.post("/v1/workflows", headers=headers(**claims), json={"mode": MODE})
        assert response.status_code == 201, response.text
        self.id = response.json()["workflow_id"]
        self.events: list[dict[str, Any]] = []
        self.gateway: list[dict[str, Any]] = []

    def _drive(self, response) -> list[dict[str, Any]]:
        assert response.status_code == 200, response.text
        events = sse(response.text)
        all_events = list(events)
        while events and events[-1]["type"] == "bridge_call":
            call = events[-1]
            body = self.form.answer(call["method"], call.get("args"))
            response = self.client.post(
                f"/v1/workflows/{self.id}/bridge/{call['id']}", headers=headers(**self.claims), json=body
            )
            assert response.status_code == 200, response.text
            events = sse(response.text)
            all_events += events
        return all_events

    def post(self, path: str, body: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        before = len(self.backend.gateway_calls)
        body = {"active": True, "form": self.form.snapshot(), **(body or {})}
        response = self.client.post(f"/v1/workflows/{self.id}/{path}", headers=headers(**self.claims), json=body)
        self.events = self._drive(response)
        self.gateway = self.backend.gateway_calls[before:]
        return self.events

    def open(self) -> list[dict[str, Any]]:
        return self.post("open")

    def poll(self) -> list[dict[str, Any]]:
        return self.post("poll")

    def say(self, text: str, understood: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if understood is not None:
            self.backend.gateway_replies.append(json.dumps(understood))
        return self.post("messages", {"text": text})

    def tap(self, label: str, understood: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if understood is not None:
            self.backend.gateway_replies.append(json.dumps(understood))
        chip = next(c for c in of(self.events, "chips")[-1]["items"] if c["label"] == label)
        return self.post("actions", {"chip": chip["id"]})

    def upload(self, *files: tuple[str, bytes]) -> list[dict[str, Any]]:
        before = len(self.backend.gateway_calls)
        response = self.client.post(
            f"/v1/workflows/{self.id}/attachments",
            headers=headers(**self.claims),
            data={"active": "true", "form": json.dumps(self.form.snapshot())},
            files=[("files", (name, content, "application/pdf")) for name, content in files],
        )
        self.events = self._drive(response)
        self.gateway = self.backend.gateway_calls[before:]
        return self.events

    # what the turn showed
    def chips(self) -> list[str]:
        return [c["label"] for c in of(self.events, "chips")[-1]["items"]]

    def primary(self) -> list[str]:
        return [c["label"] for c in of(self.events, "chips")[-1]["items"] if c.get("primary")]

    def status(self) -> dict[str, Any]:
        return of(self.events, "status")[-1]

    def composer(self) -> dict[str, Any]:
        return of(self.events, "composer")[-1]

    def bills(self) -> dict[str, Any]:
        return of(self.events, "bills")[-1]

    def understand_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.gateway if c["system_prompt"] == UNDERSTAND]

    def narrator_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.gateway if c["system_prompt"] != UNDERSTAND]

    def notes(self) -> str:
        """The notes of this turn's reply (NOTES for a reply on its own, WHAT HAPPENED for a typed message)."""
        [call] = self.narrator_calls()
        message = call["message"]
        found = re.search(r"\ANOTES:\n(.*?)\n\n(?=QUESTION:\n|Write the message now)", message, re.S) or re.search(
            r"WHAT HAPPENED:\n(.*?)\n\n(?=OPEN QUESTION:\n|ALREADY SHOWN|CLAIM:\n|PAGE:\n|Now reply)", message, re.S
        )
        return found.group(1) if found else ""

    def question(self) -> str | None:
        [call] = self.narrator_calls()
        found = re.search(r"(?:\n\nQUESTION|OPEN QUESTION):\n(.*?)\n\n", call["message"], re.S)
        return found.group(1) if found else None

    def session(self):
        return self.client.portal.call(self.client.app.state.chat.store.get, self.id)


def notes(*items: str | tuple[str, Any]) -> str:
    """The notes block the narrator sends the model (aiAssistant.ts notes())."""
    lines = []
    for i, item in enumerate(items, 1):
        if isinstance(item, tuple):
            lines.append(f"{i}. {item[0]}\n   data: {compact(item[1])}")
        else:
            lines.append(f"{i}. {item}")
    return "\n".join(lines)


def km_question(label: str, route: str) -> str:
    return f"How many km was {label} ({route})?"


Q_T3 = km_question("Tour 3 (03 Oct)", "Delhi → Noida")
Q_T3L1 = km_question("Tour 3, leg 1 (03 Oct)", "Noida → Greater Noida")
Q_T3L2 = km_question("Tour 3, leg 2 (03 Oct)", "Greater Noida → Dadri")
SENT = (
    "Oct_Tour_Plan.pdf was sent for reading. In two short lines, tell the employee: reading a tour plan takes a few "
    "minutes on the server, and they don't have to wait: they can close this chat or even the browser. You'll create "
    "the tours as soon as it is read, here or the next time they open the assistant on this form."
)


def read_plan(chat: Chat, backend: FakeBackend, response: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Upload the October plan on an empty form, let the OCR finish it, and poll."""
    chat.upload(("Oct_Tour_Plan.pdf", PDF))
    backend.finish_ocr("Oct_Tour_Plan.pdf", response or plan())
    return chat.poll()


# ── greeting, launcher ──


class TestGreeting:
    def test_an_empty_form(self, client, backend):
        chat = Chat(client, backend, FakeForm(), unique_name="Anil")
        chat.open()
        [call] = chat.narrator_calls()
        # the brief prompt (no typed message)
        assert call["system_prompt"] == prompts()["brief"] and call["conversation_id"] is None
        assert chat.notes() == notes(
            "Hello, Anil! 👋\nI'm Aryaan, your Claim Assistant. Please upload your monthly tour plan (a PDF or a "
            "photo)."
        )
        assert chat.chips() == ["📎 Upload tour plan"] and chat.primary() == ["📎 Upload tour plan"]
        assert of(chat.events, "chips")[-1]["items"][0]["kind"] == "upload"
        assert chat.status() == {
            "type": "status",
            "header": "Upload your tour plan to begin",
            "busy": None,
            "launcher": None,
            "greeting": "Hi, I'm ARYAAN! Upload your monthly tour plan and I'll create every tour for you.",
            "held": False,
        }
        assert chat.composer() == {
            "type": "composer",
            "placeholder": "Type a detail, a correction or a question…",
            "canType": True,
            "canAttach": True,
            "highlightUpload": True,
        }
        # once only
        chat.open()
        assert chat.gateway == [] and chat.form.calls == []

    def test_a_form_with_tours(self, client, backend):
        chat = Chat(client, backend, FakeForm(plan_tours()))
        chat.open()
        assert chat.notes() == notes(
            "Hello, Test Employee! 👋\nI'm Aryaan, your Claim Assistant. Your form has **4 tours**. Tell me what to "
            "change, or upload a new tour plan."
        )
        # no questions on open; a distance is missing, so ending is not the primary chip
        assert chat.chips() == ["End all tours", *DATA_CHIPS]
        assert chat.primary() == []

    def test_the_greeting_waits_for_the_window(self, client, backend):
        chat = Chat(client, backend, FakeForm())
        chat.post("poll", {"active": False})
        assert chat.gateway == [] and chat.status()["held"] is True
        chat.open()
        assert chat.notes().startswith("1. Hello, Test Employee!")


# ── the tour plan ──


class TestTourPlan:
    def test_upload_read_write_and_the_gaps_one_at_a_time(self, client, backend):
        form = FakeForm()
        chat = Chat(client, backend, form)
        chat.open()
        chat.upload(("Oct_Tour_Plan.pdf", PDF))
        assert of(chat.events, "user_files") == []  # the old chat drew no 📎 bubble for a plan
        assert of(chat.events, "status")[0]["busy"] == "Processing your tour plan…"
        assert {d["reference"] for d in backend.ocr_docs.values()} == {"within-plan"}
        assert chat.notes() == notes(SENT)
        assert chat.chips() == ["⏹ Stop processing", "📎 Upload tour plan"]
        assert [p["name"] for p in chat.bills()["inFlight"]] == ["Oct_Tour_Plan.pdf"] and chat.bills()["staged"] == []
        assert chat.status()["header"] == "Processing your tour plan…"
        assert chat.status()["launcher"] == "Processing your tour plan…"

        chat.poll()
        assert chat.gateway == [] and form.calls == []
        backend.finish_ocr("Oct_Tour_Plan.pdf", plan())
        chat.poll()
        assert of(chat.events, "status")[0]["busy"] == "Going through your tour plan…"
        # the tours went into the form through the bridge (one write; a gap is open, so no info call)
        assert form.methods() == ["write"]
        assert form.calls[0][1] == {"tours": plan_tours()}
        assert chat.notes() == notes(
            ("I read your tour plan and created 4 tours, one for each date:", tour_summary_rows(plan_tours())),
            Q_T3,
        )
        assert chat.question() == Q_T3
        assert chat.chips() == ["Skip this one", *DATA_CHIPS, "↩ Undo last change"]
        assert chat.composer()["placeholder"] == "Type your answer…"
        assert chat.composer()["highlightUpload"] is False
        assert chat.session().held_document_ids() == []  # settled: it is in the form
        assert chat.status()["header"] == "Upload your tour plan to begin" and chat.status()["launcher"] is None

        # an answer, read by the model
        chat.say("12 km", {"action": "answer_question", "args": {"value": "12"}})
        [understand] = chat.understand_calls()
        assert f"OPEN QUESTION:\n{Q_T3}" in understand["message"]
        assert (
            "CONTEXT:\nThe open question is about Tour 3: distance.\nCURRENT TOURS:\nTour 1 (01 Oct 2026)"
            in (understand["message"])
        )
        assert "- answer_question: the message IS the answer" in understand["message"]
        assert form.tours[2]["tourDetails"]["distance"] == 12
        assert chat.notes() == notes(
            (f'Saved the employee\'s answer to "{Q_T3}" on the form.', {"answer": "12"}),
            Q_T3L1,
        )
        assert chat.narrator_calls()[0]["message"].startswith("EMPLOYEE SAID:\n12 km\n\n")

        # an answer that cannot be used: the same question again
        chat.say("a lot", {"action": "answer_question", "args": {"value": "a lot"}})
        assert chat.notes() == notes(
            (
                f'The employee\'s answer to "{Q_T3L1}" could not be used: Please give the distance in km, like "24". '
                "Ask the question again.",
                {"answer": "a lot"},
            )
        )
        assert form.methods() == ["write", "write"]

        # skipped from the chip (no model)
        chat.tap("Skip this one")
        assert chat.understand_calls() == []
        assert chat.notes() == notes("Okay, I'll leave that one. You can fill it on the form.", Q_T3L2)

        # skipped by the model
        chat.say("not sure", {"action": "skip_question"})
        assert chat.notes() == notes(
            "Okay, I'll leave that one. You can fill it on the form.",
            km_question("Tour 4 (04 Oct)", "Delhi → Gurugram"),
        )

    def test_the_last_answer_reports_what_is_left(self, client, backend):
        tours = complete_tours()
        tours[3]["tourDetails"]["tourLegs"][0]["distance"] = ""
        form = FakeForm(tours)
        chat = Chat(client, backend, form)
        chat.open()
        chat.tap("End all tours")
        question = km_question("Tour 4, leg 1 (04 Oct)", "Gurugram → Manesar")
        assert chat.notes().endswith(f"2. {question}")
        chat.say("4 km", {"action": "answer_question", "args": {"value": "4"}})
        assert form.methods() == ["write", "info"]
        assert chat.notes() == notes(
            (f'Saved the employee\'s answer to "{question}" on the form.', {"answer": "4"}),
            "Still to do:\n• 4 tours not ended yet (1, 2, 3, 4). DA and accommodation open up once a tour is ended. "
            'Every open tour can be ended now: say "end all tours".\n'
            "• Add hotel stays and travel bills on the form if you have any.",
        )
        assert chat.chips() == ["End all tours", *DATA_CHIPS, "↩ Undo last change"]
        assert chat.primary() == ["End all tours"]
        assert chat.composer()["placeholder"] == "Type a detail, a correction or a question…"

    def test_mode_chips_send_the_mode_through_the_model(self, client, backend):
        raw = plan()
        raw["tourDetails"]["tourLegs"][1]["modeOfTravel"] = ""
        form = FakeForm()
        chat = Chat(client, backend, form)
        chat.open()
        read_plan(chat, backend, raw)
        question = "How did you travel for Tour 2, leg 1 (02 Oct) (Faridabad → Palwal)?"
        assert chat.notes() == notes(
            (
                "I read your tour plan and created 4 tours, one for each date:",
                tour_summary_rows(map_tour_plan_to_tours(raw)["tours"]),
            ),
            "Some journeys have no mode of travel. Please select it before ending the tour.",
            question,
        )
        assert of(chat.events, "reply")[-1]["tone"] == "error"
        assert chat.chips() == [
            "🚆 Rail",
            "🚌 Public transport",
            "🚌 Public transport (beyond entitled)",
            "🏢 Company vehicle",
            "🚗 Personal vehicle",
            "Skip this one",
            *DATA_CHIPS,
            "↩ Undo last change",
        ]
        assert chat.primary() == ["🚆 Rail"]
        chat.tap("🚆 Rail", {"action": "answer_question", "args": {"value": "Rail"}})
        assert of(chat.events, "user_message") == [{"type": "user_message", "text": "Rail"}]
        assert chat.understand_calls()[0]["message"].endswith(
            "EMPLOYEE'S MESSAGE:\nRail\n\nAnswer with the JSON object only."
        )
        assert form.tours[1]["tourDetails"]["tourLegs"][0]["modeOfTravel"] == "Rail"

    def test_replace_the_tours_on_the_form(self, client, backend):
        form = FakeForm(plan_tours())
        chat = Chat(client, backend, form)
        chat.open()
        chat.upload(("Nov.pdf", PDF))
        question = f"Your form already has 4 tours. Reading Nov.pdf will replace them. {REPLACE}"
        assert chat.notes() == notes(question) and chat.question() == question
        assert chat.chips() == ["Yes, replace them", "No, keep them"]
        assert chat.primary() == ["Yes, replace them"]
        assert [p["name"] for p in chat.bills()["staged"]] == ["Nov.pdf"]
        assert backend.upload_parts == []

        chat.tap("Yes, replace them")
        assert backend.upload_parts == [["Files", "Reference"]]
        assert chat.notes() == notes(SENT.replace("Oct_Tour_Plan.pdf", "Nov.pdf"))
        assert chat.bills()["staged"] == []
        # read: the go-ahead was given, so the tours are replaced without asking again
        backend.finish_ocr("Nov.pdf", plan("Nov.pdf"))
        chat.poll()
        assert form.methods() == ["write"]
        assert chat.notes().startswith("1. I read your tour plan and created 4 tours")

    def test_keep_the_tours(self, client, backend):
        chat = Chat(client, backend, FakeForm(plan_tours()))
        chat.open()
        chat.upload(("Nov.pdf", PDF))
        chat.say("no", {"action": "no"})
        assert 'no: declines ("No, keep them")' in chat.understand_calls()[0]["message"]
        assert chat.notes() == notes("Okay, I left your tours as they are.")
        assert backend.upload_parts == [] and chat.bills()["staged"] == []
        assert chat.chips() == ["End all tours", *DATA_CHIPS]

    def test_a_file_that_cannot_be_read_and_more_than_one_file(self, client, backend):
        chat = Chat(client, backend, FakeForm())
        chat.open()
        chat.upload(("notes.docx", b"x"), ("a.pdf", PDF), ("b.pdf", PDF))
        assert chat.notes() == notes(
            "notes.docx: I can only read PDFs and photos or scans (JPG, PNG), not this kind of file.",
            "A tour plan is one file, so I'll read a.pdf. If your plan has several pages, please send it as a single "
            "PDF.",
            SENT.replace("Oct_Tour_Plan.pdf", "a.pdf"),
        )
        assert [d["fileName"] for d in backend.ocr_docs.values()] == ["a.pdf"]

    def test_a_failed_send_keeps_the_file_for_read_again(self, client, backend):
        chat = Chat(client, backend, FakeForm())
        chat.open()
        backend.fail_next("Ocr/Upload", 500)
        chat.upload(("Oct_Tour_Plan.pdf", PDF))
        assert chat.notes() == notes("failure Your file is still here, so please try again.")
        assert chat.chips() == ["Read again"] and [p["name"] for p in chat.bills()["staged"]] == ["Oct_Tour_Plan.pdf"]
        chat.tap("Read again")
        assert chat.notes() == notes(SENT) and chat.bills()["staged"] == []

    def test_a_plan_read_while_away_on_an_empty_form(self, client, backend):
        first = Chat(client, backend, FakeForm())
        first.open()
        first.upload(("Oct_Tour_Plan.pdf", PDF))
        backend.finish_ocr("Oct_Tour_Plan.pdf", plan())
        assert client.post("/v1/sessions", headers=headers(), json={"mode": MODE}).json()["status"]["launcher"] == (
            "Your tour plan is read. Open me to create the tours."
        )
        # the next time the assistant is opened on this form (a new chat)
        chat = Chat(client, backend, FakeForm())
        chat.open()
        assert chat.notes() == notes(
            "Hello, Test Employee! 👋\nI'm Aryaan, your Claim Assistant. Please upload your monthly tour plan (a PDF "
            "or a photo).",
            "The tour plan the employee sent earlier has been read on the server while they were away. Say so in one "
            "line before what follows.",
            ("I read your tour plan and created 4 tours, one for each date:", tour_summary_rows(plan_tours())),
            Q_T3,
        )
        assert chat.form.tours == plan_tours()

    def test_a_plan_read_while_away_asks_before_replacing_the_tours(self, client, backend):
        first = Chat(client, backend, FakeForm())
        first.open()
        first.upload(("Oct_Tour_Plan.pdf", PDF))
        backend.finish_ocr("Oct_Tour_Plan.pdf", plan())
        form = FakeForm(complete_tours()[:1])
        chat = Chat(client, backend, form)
        chat.open()
        question = (
            "The tour plan Oct_Tour_Plan.pdf, sent earlier, has been read. Your form has 1 tour now, and its tours "
            f"would replace them. {REPLACE}"
        )
        assert chat.notes().endswith(f"2. {question}")
        assert chat.chips() == ["Yes, replace them", "No, keep them"]
        assert chat.session().held_document_ids() == [7001] and form.calls == []
        chat.tap("Yes, replace them")
        assert form.methods() == ["write"] and len(form.tours) == 4
        assert chat.session().held_document_ids() == []
        assert backend.ocr_docs[7001]["isRead"] is True

    def test_a_plan_read_while_away_kept_out(self, client, backend):
        first = Chat(client, backend, FakeForm())
        first.open()
        first.upload(("Oct_Tour_Plan.pdf", PDF))
        backend.finish_ocr("Oct_Tour_Plan.pdf", plan())
        chat = Chat(client, backend, FakeForm(complete_tours()[:1]))
        chat.open()
        chat.tap("No, keep them")
        assert chat.notes() == notes("Okay, I left your tours as they are.")
        assert backend.cancelled == [7001] and chat.session().held_document_ids() == []

    def test_a_plan_that_could_not_be_read_is_read_again(self, client, backend):
        chat = Chat(client, backend, FakeForm())
        chat.open()
        chat.upload(("Oct_Tour_Plan.pdf", PDF))
        backend.finish_ocr("Oct_Tour_Plan.pdf", None, failed=True)
        chat.poll()
        assert chat.notes() == notes(
            "I couldn't read the tour plan Oct_Tour_Plan.pdf. Please try again, or attach a clearer copy."
        )
        assert chat.chips() == ["↻ Read it again", "📎 Upload tour plan"]
        chat.tap("↻ Read it again")
        assert backend.retried == [7001]
        assert chat.notes() == notes("Your tour plan is being processed again. I'll create the tours when it's done.")
        assert chat.chips() == ["⏹ Stop processing", "📎 Upload tour plan"]

    def test_a_plan_that_is_not_a_tour_plan(self, client, backend):
        raw = plan()
        raw["meta"]["per_file_results"][0]["bill_type"] = "ticket"
        chat = Chat(client, backend, FakeForm())
        chat.open()
        read_plan(chat, backend, raw)
        assert chat.notes() == notes("This file does not look like a tour plan. Please upload your monthly tour plan.")
        assert chat.form.calls == [] and chat.session().held_document_ids() == []

    def test_stop_processing(self, client, backend):
        chat = Chat(client, backend, FakeForm())
        chat.open()
        chat.upload(("Oct_Tour_Plan.pdf", PDF))
        chat.tap("⏹ Stop processing")
        assert backend.cancelled == [7001]
        assert chat.notes() == notes(
            "Stopped reading. Nothing was added. Attach the tour plan again whenever you want me to read it."
        )
        assert chat.chips() == ["📎 Upload tour plan"] and chat.bills()["inFlight"] == []


# ── ending tours, undo ──


class TestEndTours:
    def test_discrepancies_are_listed_and_the_first_gap_is_asked(self, client, backend):
        chat = Chat(client, backend, FakeForm(plan_tours()))
        chat.open()
        chat.tap("End all tours")
        assert chat.notes() == notes(
            "I can't end the tours yet. Please resolve these first:\n"
            "• Tour 3 (Delhi → Noida): distance (km) missing\n"
            "• Tour 3, leg 1 (Noida → Greater Noida): distance (km) missing\n"
            "• Tour 3, leg 2 (Greater Noida → Dadri): distance (km) missing\n"
            "• Tour 4 (Delhi → Gurugram): distance (km) missing\n"
            "• Tour 4, leg 1 (Gurugram → Manesar): distance (km) missing\n"
            "\nOnce every tour is complete I can end them all at once.",
            Q_T3,
        )
        assert of(chat.events, "reply")[-1]["tone"] == "error"
        assert chat.chips() == ["Skip this one", *DATA_CHIPS]

    def test_end_all_tours_after_yes_and_undo(self, client, backend):
        form = FakeForm(complete_tours())
        chat = Chat(client, backend, form)
        chat.open()
        assert chat.primary() == ["End all tours"]
        chat.tap("End all tours")
        question = (
            "No discrepancies found. Shall I end all 4 tours (1, 2, 3, 4) now? DA is worked out once a tour is ended, "
            "and an ended tour can't be changed from the chat (untick End Tour on the form to reopen it)."
        )
        assert chat.notes() == notes(question)
        assert chat.chips() == ["Yes, end all tours", "Not yet"]
        chat.say("yes end them", {"action": "yes"})
        assert 'yes: agrees ("Yes, end all tours")' in chat.understand_calls()[0]["message"]
        ended = [t["tourDetails"]["tourEnd"] for t in form.tours]
        assert ended == [True] * 4 and all(t["isExpanded"] for t in form.tours)
        assert chat.notes() == notes(
            "Ended all 4 tours. DA is now calculated, and I've opened the tours so you can check it. To add hotel "
            "stays or travel bills, use Add Accommodation and Add Travel Costs on each tour."
        )
        assert of(chat.events, "reply")[-1]["tone"] == "success"
        assert chat.chips() == DATA_CHIPS + ["↩ Undo last change"]
        assert chat.primary() == []  # step is still 'start'

        chat.tap("↩ Undo last change")
        assert form.tours == complete_tours()
        assert chat.notes() == notes("Undone. The tours are back as they were.")
        assert chat.chips() == ["End all tours", *DATA_CHIPS]

    def test_not_yet(self, client, backend):
        form = FakeForm(complete_tours())
        chat = Chat(client, backend, form)
        chat.open()
        chat.tap("End all tours")
        chat.tap("Not yet")
        assert chat.notes() == notes("Okay, I left the tours open.") and form.calls == []

    def test_typing_something_else_drops_the_question(self, client, backend):
        chat = Chat(client, backend, FakeForm(complete_tours()))
        chat.open()
        chat.tap("End all tours")
        chat.say("what is DA?", {"action": "explain_da"})
        assert chat.notes() == notes(f"The employee asked how DA is worked out. The rules:\n{WITHIN_DA_RULES}")
        assert chat.chips() == ["End all tours", *DATA_CHIPS]

    def test_every_tour_already_ended(self, client, backend):
        chat = Chat(client, backend, FakeForm(complete_tours(ended=True)))
        chat.open()
        chat.say("end the tours", {"action": "end_tours"})
        assert chat.notes() == notes("Every tour is already ended.")


# ── progress, tours, draft, submit ──


class TestSubmit:
    def test_check_problems_then_the_declarations_and_yes(self, client, backend):
        tours = complete_tours(ended=True)
        tours[0]["daAllowanceDetails"]["entries"] = [
            {"id": 1, "date": "2026-10-01", "amount": 350, "daApplicable": True}
        ]
        form = FakeForm(tours)
        form.check = [f"Tour {n}: Tour purpose is required" for n in range(1, 11)]
        chat = Chat(client, backend, form)
        chat.open()
        assert chat.primary() == []
        chat.tap("Check & submit")
        assert form.methods() == ["check"]
        assert chat.notes() == notes(
            "I can't submit yet:\n"
            + "\n".join(f"• Tour {n}: Tour purpose is required" for n in range(1, 9))
            + "\n…and 2 more"
        )
        assert of(chat.events, "reply")[-1]["tone"] == "error"

        form.check = []
        form.info = {"selfApprove": True, "approverChosen": False}
        chat.say("submit my claim", {"action": "check_and_submit"})
        assert form.methods() == ["check", "check", "info"]
        declarations = "\n".join(f"{i}. {t}" for i, t in enumerate(DECLARATION_TEXTS, 1))
        assert chat.notes() == notes(
            f"{totals_text(tours)}\n\nPlease read these declarations:\n{declarations}\n\nDo you agree to all of them "
            "and want me to self-approve and submit the claim now?"
        )
        assert totals_text(tours) == "Transport ₹0.00 · Accommodation ₹0.00 · DA ₹350.00 · Total ₹350.00"
        assert "7. The due amount can be deducted from my salary" in chat.notes()
        assert chat.chips() == ["Yes, I agree — submit", "Not yet"]
        assert chat.question() == "Do you agree to all the declarations and want the claim submitted now?"

        chat.say("yes I agree", {"action": "agree_and_submit"})
        assert "agree_and_submit: a plain yes" in chat.understand_calls()[0]["message"]
        assert form.methods()[-1] == "submit"
        assert chat.notes() == notes(
            "I started the submission. If something is missing, a list will appear on the form."
        )
        assert of(chat.events, "status")[1]["busy"] == "Submitting…"
        assert chat.chips() == DATA_CHIPS

    def test_the_submit_chip_and_a_failed_submit(self, client, backend):
        form = FakeForm(complete_tours(ended=True))
        chat = Chat(client, backend, form)
        chat.open()
        chat.tap("Check & submit")
        assert "want me to submit the claim now?" in chat.notes()
        form.fail["submit"] = "boom"
        chat.tap("Yes, I agree — submit")
        assert chat.understand_calls() == []
        assert chat.notes() == notes("The claim couldn't be submitted. Please check the form and try again.")
        assert chat.chips() == DATA_CHIPS

    def test_not_yet_goes_through_the_model(self, client, backend):
        chat = Chat(client, backend, FakeForm(complete_tours(ended=True)))
        chat.open()
        chat.tap("Check & submit")
        chat.tap("Not yet", {"action": "dont_submit_yet"})
        assert of(chat.events, "user_message") == [{"type": "user_message", "text": "no"}]
        assert chat.notes() == notes(
            "The employee does not want to submit yet. Nothing has been submitted. Ask what to change."
        )
        assert chat.primary() == ["Check & submit"]  # step 'ready'

    def test_nothing_to_submit(self, client, backend):
        chat = Chat(client, backend, FakeForm())
        chat.open()
        chat.say("submit", {"action": "check_and_submit"})
        assert chat.notes() == notes("There is nothing to submit yet. Attach your monthly tour plan first.")
        assert chat.form.calls == []

    def test_progress_tours_draft_and_da(self, client, backend):
        tours = plan_tours()
        form = FakeForm(tours)
        form.info = {"selfApprove": False, "approverChosen": False}
        chat = Chat(client, backend, form)
        chat.open()
        chat.tap("What's left?")
        assert form.methods() == ["info"]
        assert chat.notes() == notes(
            "Still to do:\n• 5 details still missing: Tour 3, Tour 4.\n• 4 tours not ended yet (1, 2, 3, 4). DA and "
            "accommodation open up once a tour is ended. Resolve the missing details first, then I can end them all."
            "\n• No approving authority selected: choose one at the bottom of the form.\n• Add hotel stays and travel "
            "bills on the form if you have any."
        )
        chat.tap("Show my tours")
        assert chat.notes() == notes(("Your claim has 4 tours:", tour_summary_rows(tours)), totals_text(tours))
        chat.tap("Save draft")
        assert form.methods()[-1] == "saveDraft"
        assert of(chat.events, "status")[0]["busy"] == "Saving your draft…"
        assert chat.notes() == notes(
            "Draft save finished. If no error appeared, it is in the Draft tab and you can resume it any time."
        )
        form.fail["saveDraft"] = "boom"
        chat.tap("Save draft")
        assert chat.notes() == notes("I couldn't save the draft. Please try the Save Draft button.")
        chat.tap("How is DA worked out?")
        assert chat.notes() == notes(WITHIN_DA_RULES)


# ── the model: talk, gateway down, start over ──


class TestModel:
    def test_talk_and_the_context(self, client, backend):
        chat = Chat(client, backend, FakeForm(plan_tours()))
        chat.open()
        chat.say("who are you?", {"action": "talk"})
        [understand] = chat.understand_calls()
        assert understand["max_tokens"] == 600 and understand["conversation_id"] is None
        message = understand["message"]
        assert message.startswith("ACTIONS:\n- talk: a question, greeting")
        assert "OPEN QUESTION" not in message and "undo" not in message.split("CONTEXT:")[0]
        # the greeting was the last bubble before the message (the message itself is not in the recent chat)
        assert "RECENT CHAT (oldest first):\nAssistant: Hello, Test Employee! 👋 I'm Aryaan" in message
        assert "Employee: who are you?" not in message
        assert chat.notes() == notes(TALK_NOTE)
        [narrate] = chat.narrator_calls()
        assert narrate["system_prompt"] == prompts()["narrate_within"]
        assert "CLAIM:\nTour 1 (01 Oct 2026) purpose: Inspect" in narrate["message"]

    def test_a_message_that_is_too_long(self, client, backend):
        chat = Chat(client, backend, FakeForm())
        chat.open()
        chat.say("x" * 1001)
        assert chat.understand_calls() == []
        assert chat.notes() == notes(
            "The employee's message is 1001 characters long, more than the 1000 that can be read, so it was not "
            "read. Ask them to send it in shorter pieces."
        )
        assert chat.narrator_calls()[0]["message"].startswith(f"EMPLOYEE SAID:\n{'x' * 300}…")

    def test_the_gateway_down_then_try_again(self, client, backend):
        form = FakeForm(complete_tours())
        chat = Chat(client, backend, form)
        chat.open()
        backend.gateway_fail.append(401)
        chat.say("end all tours")
        assert of(chat.events, "system_notice") == [
            {"type": "system_notice", "text": "The assistant is unavailable right now. Please try again."}
        ]
        assert of(chat.events, "chips")[-1]["items"] == [
            {"id": "try-again", "label": "Try again", "kind": "action", "primary": True}
        ]
        assert form.calls == []
        backend.gateway_replies.append(json.dumps({"action": "end_tours"}))
        chat.post("retry")
        assert chat.understand_calls()[0]["message"].endswith(
            "EMPLOYEE'S MESSAGE:\nend all tours\n\nAnswer with the JSON object only."
        )
        assert chat.notes().startswith("1. No discrepancies found. Shall I end all 4 tours")
        assert chat.chips() == ["Yes, end all tours", "Not yet"]

    def test_start_over(self, client, backend):
        form = FakeForm(plan_tours())
        chat = Chat(client, backend, form)
        chat.open()
        chat.say("start over", {"action": "start_over"})
        assert chat.notes() == notes(WITHIN_START_OVER_ASK)
        assert chat.question() == "Shall I clear every tour on the form and start over?"
        assert chat.chips() == ["Yes, start over", "No, keep my tours"] and chat.primary() == ["No, keep my tours"]
        chat.tap("Yes, start over", {"action": "confirm_start_over"})
        assert of(chat.events, "user_message") == [{"type": "user_message", "text": "yes"}]
        message = chat.understand_calls()[0]["message"]
        assert "- confirm_start_over: yes, clear every tour and start over" in message
        assert "- start_over:" not in message
        assert form.methods() == ["write"]
        assert form.tours == [{**BLANK, "id": f"tour-{NOW_MS}"}]
        assert chat.notes() == notes(
            'Cleared. Attach your tour plan whenever you are ready. Say "undo" if you change your mind.'
        )
        assert chat.chips() == ["📎 Upload tour plan", "↩ Undo last change"]
        chat.say("undo", {"action": "undo"})
        assert form.tours == plan_tours()

    def test_keep_the_tours_and_nothing_to_clear(self, client, backend):
        chat = Chat(client, backend, FakeForm(plan_tours()))
        chat.open()
        chat.say("start over", {"action": "start_over"})
        chat.tap("No, keep my tours", {"action": "keep_tours"})
        assert chat.notes() == notes("The employee chose to keep the tours. Nothing was cleared.")
        assert chat.chips() == ["End all tours", *DATA_CHIPS]
        empty = Chat(client, backend, FakeForm())
        empty.open()
        empty.say("start over", {"action": "start_over"})
        assert empty.notes() == notes(
            "The employee asked to start over, but the form has no tours yet, so there is nothing to clear."
        )

    def test_edits_applied_and_refused(self, client, backend):
        form = FakeForm(plan_tours())
        chat = Chat(client, backend, form)
        chat.open()
        chat.say(
            "tour 2 leg 1 arrives at 15:45, tour 9 by rail",
            {
                "action": "edit",
                "edits": [
                    {"tour": 2, "leg": 1, "field": "arrivalTime", "value": "15:45"},
                    {"tour": 9, "leg": 0, "field": "mode", "value": "Rail"},
                ],
            },
        )
        assert form.tours[1]["tourDetails"]["tourLegs"][0]["arrivalTime"] == "15:45"
        assert chat.notes() == notes(
            "Changed on the form:\n• Tour 2, leg 1: arrival 15:45\nCould not change:\n• Tour 9 doesn't exist.",
            Q_T3,
        )
        chat.say("hmm", {"action": "edit", "edits": [{"nonsense": True}]})
        assert chat.notes() == notes(
            "The employee seemed to give tour details, but none could be read. Ask them to give the detail again, more "
            "plainly."
        )
