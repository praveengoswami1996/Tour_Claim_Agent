"""Conversation changes for bills (BIL-70..73)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.fakes import PDF, FakeBackend
from tests.test_api import say, start, texts
from tests.test_claim_journey import event, upload
from tourclaim_agent.llm.intent import Goal, IntentContext
from tourclaim_agent.llm.rules import RuleBasedIntentExtractor
from tourclaim_agent.main import create_app

SAVED_DRAFT = {
    "draftId": 42,
    "empTourPlanId": 503,
    "draftData": {
        "TransportaionDetailDtos": json.dumps(
            [{"DateOfTravelSource": "2026-08-20", "Source": "Noida", "Destination": "Mumbai", "BilledAmount": 6500}]
        ),
        "transportationFileReferences": [{"fileId": 7, "originalFileName": "out.pdf", "rowIndex": 0}],
    },
}


@pytest.fixture
def client(settings, backend: FakeBackend):
    with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "message",
    ["are my bills read?", "what happened to my bills", "status of my tickets", "did you read my bills"],
)
async def test_bill_status_intent(message):
    assert (await RuleBasedIntentExtractor().extract(message, IntentContext())).goal is Goal.BILL_STATUS


@pytest.mark.parametrize("message", ["review my claim", "where is my Mumbai claim?", "I uploaded my tickets"])
async def test_other_messages_are_not_bill_status(message):
    assert (await RuleBasedIntentExtractor().extract(message, IntentContext())).goal is not Goal.BILL_STATUS


def test_start_claim_loads_the_saved_draft(client, backend):
    backend.drafts[503] = SAVED_DRAFT
    events = say(client, start(client), "start a claim for my Mumbai tour")
    summary = event(events, "claim_summary")
    assert summary["draft_id"] == 42 and [j["origin"] for j in summary["journeys"]] == ["saved"]
    assert "with 1 journey and 0 stays" in texts(events)


def test_help_says_upload_any_time(client):
    text = texts(say(client, start(client), "help"))
    assert "Upload your tickets and hotel bills and I'll find the tour they belong to." in text
    assert "Start a claim" not in text


def test_bill_status_answer(client, backend):
    workflow_id = start(client)
    assert "No bills are being read right now" in texts(say(client, workflow_id, "are my bills read?"))
    upload(client, workflow_id, ("out.pdf", PDF))
    events = say(client, workflow_id, "are my bills read?")
    assert "I'm still reading 1 bill: out.pdf" in texts(events)
    assert event(events, "bill_status")["pending"] == 1


def test_assign_bills_is_never_reached_without_the_question(client, backend):
    events = say(client, start(client), "assign_bills")
    assert not any(e["type"] == "bill_report" for e in events)
