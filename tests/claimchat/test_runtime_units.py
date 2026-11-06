"""The chat runtime's parts: narrator (useNarrator.ts), the OCR API port (ocrApi.ts), the page callbacks
(RaiseClaim.tsx) and the session store."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiosqlite
import httpx
import pytest

from tests.fakes import GATEWAY_BASE, PDF, TOUR_BASE, FakeBackend, tour
from tourclaim_agent.auth.identity import Employee
from tourclaim_agent.claimchat.ai.llm_client import LlmClient
from tourclaim_agent.claimchat.claim_assistant import initial_claim_state, new_transport_row
from tourclaim_agent.claimchat.js_compat import UNDEFINED
from tourclaim_agent.claimchat.runtime import ChatFlow, ChatSession, Chip, OcrApiError, cont, continuation, effect
from tourclaim_agent.claimchat.runtime.bridge import answer
from tourclaim_agent.claimchat.runtime.ocr import OcrApi, batches_of, queue_ahead
from tourclaim_agent.claimchat.runtime.page import describe_raise_claim_page
from tourclaim_agent.claimchat.runtime.session import EmployeeStorage, SessionStore, to_jsonable
from tourclaim_agent.claimchat.runtime.turn import END, ChatRuntime, ChatUser, Turn, TurnInput
from tourclaim_agent.clients.allowlist import Service
from tourclaim_agent.clients.http import UserScopedClient
from tourclaim_agent.clients.ocr_api import OcrApiClient


class PlainFlow(ChatFlow):
    mode = "unit"
    view: dict[str, Any] = {}

    @classmethod
    def initial_state(cls, params: dict[str, Any]) -> dict[str, Any]:
        return {"busy": None, "dropped": 0}

    def system_prompt(self) -> str:
        return "SYSTEM"

    def narrator_view(self):
        return {"buttons": [c.label for c in self.chips()], **self.view}

    def chips(self) -> list[Chip]:
        return [Chip("Yes, that's right", cont("noop")), Chip("Not now", cont("noop"))]

    @continuation
    def drop_report(self, n: int) -> None:
        self.state["dropped"] += n

    @continuation
    def noop(self) -> None:
        pass


@pytest.fixture
async def make_turn(tmp_path, backend: FakeBackend):
    conn = await aiosqlite.connect(str(tmp_path / "s.sqlite"))
    store = SessionStore(conn)
    await store.setup()
    http = httpx.AsyncClient(transport=backend.transport())
    runtime = ChatRuntime(
        store=store,
        http=http,
        llm=LlmClient(http, base_url=GATEWAY_BASE),
        tour_api_base_url=TOUR_BASE,
        device_type="web",
        enabled_writes=frozenset({"Claim/SaveRaiseClaimDraft"}),
    )

    def make(*, active: bool = True, read_only: bool = False, flow=PlainFlow) -> Turn:
        session = ChatSession.new("E1001", "unit", {}, flow.initial_state({}))
        session.active = active
        employee = Employee(1001, "E1001", "Test Employee", None, None, None, read_only)
        turn = Turn(runtime, session, ChatUser("token", employee), flow, EmployeeStorage())
        turn.attach(asyncio.Queue())
        return turn

    make.runtime = runtime  # type: ignore[attr-defined]
    yield make
    await http.aclose()
    await conn.close()


def events(turn: Turn) -> list[dict[str, Any]]:
    out = []
    channel = turn.channel
    while channel is not None and not channel.empty():
        out.append(channel.get_nowait())
    return out


def replies(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in evs if e["type"] == "reply"]


class TestNarrator:
    async def test_everything_told_is_one_reply(self, make_turn, backend):
        turn = make_turn()
        turn.narrator.tell("Bill read.", data={"rows": 2, "empty": ""})
        turn.narrator.say("Saved to the draft.", "success")
        turn.narrator.say("One bill failed.", "error", on_shown=cont("drop_report", n=1))
        assert await turn.narrator.maybe_write()
        (call,) = backend.gateway_calls
        assert call["message"].startswith(
            'NOTES:\n1. Bill read.\n   data: {"rows":2}\n2. Saved to the draft.\n3. One bill failed.'
        )
        (reply,) = replies(events(turn))
        assert reply["tone"] == "error"
        assert turn.state["dropped"] == 1

    async def test_all_success_is_a_success_reply(self, make_turn):
        turn = make_turn()
        turn.narrator.say("Saved.", "success")
        await turn.narrator.maybe_write()
        assert replies(events(turn))[0]["tone"] == "success"

    async def test_a_reply_never_lists_the_buttons(self, make_turn, backend):
        turn = make_turn()
        backend.gateway_replies.append("Is this your Mumbai tour?\nYes, that's right\n**Not now**\nTap a button below.")
        turn.narrator.say("Ask about the tour.")
        await turn.narrator.maybe_write()
        assert replies(events(turn))[0]["text"] == "Is this your Mumbai tour?"

    async def test_nothing_is_written_while_busy_or_closed(self, make_turn, backend):
        turn = make_turn(active=False)
        turn.narrator.say("Hello")
        assert not await turn.narrator.maybe_write()
        turn.session.active = True
        turn.state["busy"] = "Thinking…"
        assert not await turn.narrator.maybe_write()
        turn.state["busy"] = None
        assert await turn.narrator.maybe_write() and len(backend.gateway_calls) == 1

    async def test_claim_and_page_go_only_with_a_question(self, make_turn, backend):
        turn = make_turn()
        PlainFlow.view = {"claim": "CLAIM TEXT", "page": "PAGE TEXT", "openQuestion": "Which tour?"}
        try:
            turn.narrator.heard("ok thanks")
            await turn.narrator.maybe_write()
            turn.narrator.heard("how much DA do I get?")
            await turn.narrator.maybe_write()
        finally:
            PlainFlow.view = {}
        ack, question = backend.gateway_calls
        assert "CLAIM:" not in ack["message"] and "PAGE:" not in ack["message"]
        assert "OPEN QUESTION:\nWhich tour?" in ack["message"]
        assert "CLAIM:\nCLAIM TEXT" in question["message"] and "PAGE:\nPAGE TEXT" in question["message"]
        assert question["conversation_id"] == "conv-1" and ack["max_tokens"] == 700

    async def test_a_failed_reply_goes_out_with_the_next_one(self, make_turn, backend):
        turn = make_turn()
        backend.gateway_fail.append(401)
        turn.narrator.say("First note.")
        await turn.narrator.maybe_write()
        evs = events(turn)
        assert [e["type"] for e in evs if e["type"] != "status"] == ["system_notice"]
        assert turn.narrator.unavailable and turn.session.messages[-1]["system"] is True
        # waiting for "Try again": nothing is written
        turn.narrator.say("Second note.")
        assert not await turn.narrator.maybe_write()
        # the employee carried on: both go out together
        turn.narrator.resume()
        await turn.narrator.maybe_write()
        assert backend.gateway_calls[-1]["message"].startswith("NOTES:\n1. First note.\n2. Second note.")

    async def test_try_again_writes_it_again(self, make_turn, backend):
        turn = make_turn()
        backend.gateway_fail.append(429)
        turn.narrator.heard("hello")
        await turn.narrator.maybe_write()
        await turn.narrator.retry()
        assert await turn.narrator.maybe_write()
        assert replies(events(turn))[0]["text"] == "You said: hello"

    async def test_an_empty_reply_is_a_failure(self, make_turn, backend):
        turn = make_turn()
        backend.gateway_replies.append("  ")
        turn.narrator.say("Note.")
        await turn.narrator.maybe_write()
        assert turn.narrator.unavailable and turn.session.narrator["failed"]["events"][0]["note"] == "Note."
        assert [e["type"] for e in events(turn) if e["type"].startswith("reply")] == []

    async def test_app_replies_are_remembered_for_the_next_typed_turn(self, make_turn, backend):
        turn = make_turn()
        for note in ("one", "two", "three", "four", "five"):
            turn.narrator.say(note)
            await turn.narrator.maybe_write()
        assert turn.session.narrator["shown"] == ["two", "three", "four", "five"]
        turn.narrator.heard("what now?")
        await turn.narrator.maybe_write()
        assert (
            "ALREADY SHOWN (do not repeat):\ntwo\n---\nthree\n---\nfour\n---\nfive"
            in backend.gateway_calls[-1]["message"]
        )
        assert turn.session.narrator["shown"] == []


def ocr_api(handler, *, read_only: bool = False) -> tuple[OcrApi, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = UserScopedClient(http, service=Service.TOUR, base_url=TOUR_BASE, token="t", device_type="web")
    return OcrApi(OcrApiClient(client), read_only=read_only), http


class TestOcrApi:
    @pytest.mark.parametrize(
        ("status", "body", "message"),
        [
            (401, {"message": "x"}, "Your session has expired. Please sign in again."),
            (403, {}, "This login is read-only, so bills cannot be sent."),
            (500, {"message": "Queue is full."}, "Queue is full."),
            (400, "not json", "The bills could not be sent."),
        ],
    )
    async def test_upload_errors_have_the_old_messages(self, status, body, message):
        def handler(request):
            return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)

        api, http = ocr_api(handler)
        async with http:
            with pytest.raises(OcrApiError) as error:
                await api.upload_ocr_files([("a.pdf", PDF, "application/pdf")], "outside-chat:unassigned")
        assert error.value.message == message

    async def test_a_refused_upload_lists_the_files(self):
        def handler(request):
            detail = [{"fileName": "a.docx", "reason": "Unsupported file type."}, "junk"]
            return httpx.Response(400, json={"message": "", "errorDetail": detail})

        api, http = ocr_api(handler)
        async with http:
            with pytest.raises(OcrApiError) as error:
                await api.upload_ocr_files([("a.docx", b"x", "")], "outside-chat:unassigned")
        assert error.value.invalid == [{"fileName": "a.docx", "reason": "Unsupported file type."}]
        assert error.value.message == "The bills could not be sent."

    async def test_a_refusal_inside_a_200_uses_its_message(self):
        api, http = ocr_api(lambda r: httpx.Response(200, json={"statusCode": 409, "message": "Already taken."}))
        async with http:
            with pytest.raises(OcrApiError) as error:
                await api.mark_ocr_read([1])
        assert (error.value.message, error.value.status, error.value.invalid) == ("Already taken.", 409, [])

    async def test_nothing_reachable(self):
        def handler(request):
            raise httpx.ConnectError("down")

        api, http = ocr_api(handler)
        async with http:
            with pytest.raises(OcrApiError) as error:
                await api.get_my_ocr_documents()
        assert (
            error.value.message == "The server is not reachable right now. Please check your connection and try again."
        )

    async def test_a_read_only_login_sends_no_write(self):
        seen = []
        api, http = ocr_api(lambda r: seen.append(r) or httpx.Response(200, json={"statusCode": 200}), read_only=True)
        async with http:
            with pytest.raises(OcrApiError) as error:
                await api.release_ocr([1])
            await api.get_my_ocr_documents()
        assert error.value.read_only and len(seen) == 1 and seen[0].method == "GET"

    async def test_retry_tries_a_409_once_more(self):
        statuses = [409, 200]

        def handler(request):
            return httpx.Response(statuses.pop(0), json={"statusCode": 200, "data": {}})

        api, http = ocr_api(handler)
        async with http:
            await api.retry_ocr(5)
        assert statuses == []

    async def test_the_list_is_read_as_the_old_poller_did(self):
        seen: list[httpx.Request] = []

        def handler(request):
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "statusCode": 200,
                    "data": {
                        "documents": [{"documentId": "7", "fileName": "a.pdf", "status": "Pending", "index": None}]
                    },
                    "totalRecords": 1,
                },
            )

        api, http = ocr_api(handler)
        async with http:
            result = await api.get_my_ocr_documents()
        assert str(seen[0].url) == f"{TOUR_BASE}/Ocr/GetMyDocuments?IncludeResponse=false&PageSize=200"
        assert result["summary"] == {"pending": 0, "completed": 0, "failed": 0, "total": 0}
        doc = result["documents"][0]
        assert (doc["documentId"], doc["index"], doc["batchId"], doc["reference"], doc["isRead"]) == (
            7,
            0,
            "",
            None,
            False,
        )

    def test_batches_and_queue(self):
        docs = [
            {"documentId": 9, "batchId": "b2", "index": 0, "status": "Completed", "reference": "r2"},
            {"documentId": 4, "batchId": "b1", "index": 1, "status": "Pending", "queuePosition": 3},
            {"documentId": 3, "batchId": "b1", "index": 0, "status": "Completed", "reference": "r1"},
            {"documentId": 12, "batchId": "", "index": 0, "status": "Failed"},
        ]
        batches = batches_of(docs, local=[3, 4])
        assert [(b["batchId"], [d["documentId"] for d in b["documents"]], b["done"], b["local"]) for b in batches] == [
            ("b1", [3, 4], False, True),
            ("b2", [9], True, False),
            ("doc-12", [12], True, False),
        ]
        assert batches[0]["reference"] == "r1"
        assert queue_ahead(docs) == 3 and queue_ahead(docs[:1]) is None


class TestPage:
    async def test_tours_are_the_raise_claim_tab(self, make_turn, backend):
        backend.outside_tours[1001] += [
            tour(510, 1001, kind_dest="Pune", status_id=9, status="Advance", start="2026-07-01", isAdvanceTaken=True),
            tour(511, 1001, kind_dest="Goa", status_id=9, status="Advance", start="2026-07-02", isAdvanceTaken=False),
        ]
        turn = make_turn()
        assert turn.page.tours_loading
        await turn.page.load()
        assert [t["tourId"] for t in turn.page.tours] == [503, 510]
        assert not turn.page.tours_loading and turn.page.drafts == [] and not turn.page.drafts_loading
        assert "Raise Claim (approved tours waiting for a claim): 2" in turn.page.page_info

    def test_page_description(self):
        rows = [
            {
                "tourId": 1,
                "source": "Noida",
                "destination": "",
                "tourStartDate": "2026-10-05T00:00:00",
                "totalDays": 1,
                "status": "Tour Approved",
                "statusId": 2,
            },
            {"tourId": 2, "statusId": 12},
        ]
        assert describe_raise_claim_page(rows, [{"empTourPlanId": 1, "draftName": "Trip"}]) == (
            "Tabs on the Raise Claim page, with the tours in each (2 tours in all):\n"
            "Raise Claim (approved tours waiting for a claim): 1\n"
            '  • Tour #1: Noida → ?, starts 2026-10-05, 1 day, status "Tour Approved"\n'
            "Clarification (finance asked a question): 0\n"
            "Pending With Finance (claim submitted): 0\n"
            "Availed (claim completed): 1\n"
            "  • Tour #2: ? → ?, starts ?\n"
            "Not Availed: 0\n"
            "Saved drafts (claims started but not submitted): 1\n"
            "  • Tour #1: Trip"
        )

    async def test_load_tour_is_cached_and_uses_the_tour_plan(self, make_turn, backend):
        backend.tour_plans[503] = {"tentativeTourPlans": [{"date": "2026-08-20"}, {"date": "2026-08-23"}]}
        turn = make_turn()
        first = await turn.page.load_tour({"tourId": 503})
        assert first["tour"]["endDate"] == "2026-08-23" and first["tour"]["destination"] == "Mumbai"
        calls = len(backend.calls)
        assert await turn.page.load_tour({"tourId": 503}) == first and len(backend.calls) == calls
        # an inner 404 in an HTTP 200 was no error for axios: a tour with nothing known
        assert (await turn.page.load_tour({"tourId": 999}))["tour"] == {"tourId": 999}
        backend.fail_next("Claim/RaiseClaimRequest/998", 500)
        assert await turn.page.load_tour({"tourId": 998}) is None
        assert "998" not in turn.session.page["tourCache"]

    async def test_save_draft_from_the_assistant(self, make_turn, backend):
        turn = make_turn()
        await turn.page.load()
        docs = await turn.ocr.upload_for_ocr([("ticket.pdf", PDF, "application/pdf")], "outside-chat:unassigned")
        row = {
            **new_transport_row(),
            "source": "Noida",
            "destination": "Mumbai",
            "DateOfTravelSource": "2026-08-20",
            "ModeOfTravel": "air",
            "uploadedFiles": [
                {"name": "ticket.pdf", "size": len(PDF), "type": "application/pdf", "documentId": docs[0]["documentId"]}
            ],
        }
        state = {**initial_claim_state(), "transportationRows": [row]}
        assert await turn.page.save_draft({"tourId": 503}, {"tourId": 503, "state": state}) == "saved"
        (saved,) = backend.saved_requests
        assert saved["files"] == [("transportationFiles", "ticket.pdf")]
        assert saved["draft"]["EmpTourPlanId"] == 503 and "DraftId" not in saved["draft"]
        assert turn.page.draft_tour_ids == [503] and turn.drafts_changed
        assert not any(c.startswith("GET /api/Ocr/GetFile") for c in backend.calls)  # the bytes were here
        # a draft that is there is only replaced when the employee agreed
        assert await turn.page.save_draft({"tourId": 503}, {"tourId": 503, "state": state}) == "exists"
        assert await turn.page.save_draft({"tourId": 503}, {"tourId": 503, "state": state}, overwrite=True) == "saved"
        assert backend.saved_requests[-1]["draft"]["DraftId"] == 9001
        assert turn.page.drafts_info == [{"tourId": 503, "lastSaved": "now"}]
        assert (await turn.page.load_draft(503))["draftId"] == 9001 and await turn.page.load_draft(501) is None

    async def test_a_save_that_is_not_enabled_fails(self, make_turn, backend):
        turn = make_turn()
        make_turn.runtime._enabled_writes = frozenset()
        turn = make_turn()
        assert await turn.page.save_draft({"tourId": 503}, {"tourId": 503, "state": initial_claim_state()}) == "failed"
        assert backend.saved_requests == []

    async def test_fill_form_asks_the_browser(self, make_turn):
        turn = make_turn()
        task = asyncio.create_task(turn.page.fill_form({"tourId": 503}, {"tourId": 503, "state": {"x": 1}}))
        for _ in range(100):  # until the bridge call has ended the response
            if turn.channel is None:
                break
            await asyncio.sleep(0)
        (call,) = make_turn.runtime.bridges.for_workflow(turn.session.workflow_id)
        answer(
            make_turn.runtime.bridges.take(call.call_id, turn.session.workflow_id, "E1001"),
            ok=True,
            result={"opened": True},
        )
        assert await task is True
        assert call.method == "openForm"


class TestSession:
    def test_json_keeps_what_json_stringify_keeps(self):
        assert to_jsonable({"a": UNDEFINED, "b": [UNDEFINED, 1, float("nan")], "c": {1, 2}}) == {
            "b": [None, 1, None],
            "c": [1, 2],
        }
        with pytest.raises(TypeError):
            to_jsonable({"file": b"bytes"})

    async def test_store_round_trip(self, tmp_path):
        async with aiosqlite.connect(str(tmp_path / "x.sqlite")) as conn:
            store = SessionStore(conn)
            await store.setup()
            session = ChatSession.new("E1", "unit", {"tourId": 5}, {"step": "start"})
            await store.create(session)
            session.state["step"] = "details"
            await store.save(session)
            loaded = await store.get_owned(session.workflow_id, "E1")
            assert loaded.state == {"step": "details"} and loaded.params == {"tourId": 5}
            assert await store.get_owned(session.workflow_id, "E2") is None
            await store.put_file(session.workflow_id, "k", "a.pdf", "application/pdf", PDF)
            assert (await store.get_file(session.workflow_id, "k")).data == PDF
            storage = await store.load_storage("E1")
            storage["aryaan.billReports.v1.1"] = json.dumps([1])
            await store.save_storage("E1", storage)
            assert dict(await store.load_storage("E1")) == {"aryaan.billReports.v1.1": "[1]"}


class BrokenFlow(PlainFlow):
    calls = 0

    @effect
    async def effect_broken(self) -> bool:
        BrokenFlow.calls += 1
        self.state["busy"] = "Working…"
        raise RuntimeError("bug")


async def test_an_effect_that_fails_is_reported_once_and_the_turn_ends(make_turn):
    turn = make_turn(flow=BrokenFlow)
    await make_turn.runtime.store.create(turn.session)
    channel = turn.channel
    await turn.execute(TurnInput("open", active=True))
    evs = [channel.get_nowait() for _ in range(channel.qsize())]
    assert evs.pop() is END
    assert BrokenFlow.calls == 1 and turn.state["busy"] is None
    assert [e["type"] for e in evs] == ["error", "chips", "composer", "status", "bills"]
    assert (await make_turn.runtime.store.get(turn.session.workflow_id)).state["busy"] is None
