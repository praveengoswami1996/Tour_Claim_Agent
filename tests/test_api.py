"""End-to-end tests through the HTTP interface, with the backend faked."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeBackend, make_token
from tourclaim_agent.main import create_app


@pytest.fixture
def client(settings, backend: FakeBackend):
    with TestClient(create_app(settings, http_transport=backend.transport())) as test_client:
        yield test_client


def auth(emp_code: str = "E1001") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(emp_code)}"}


def start(client: TestClient, emp_code: str = "E1001") -> str:
    response = client.post("/v1/workflows", headers=auth(emp_code))
    assert response.status_code == 201
    return response.json()["workflow_id"]


def say(client: TestClient, workflow_id: str, text: str, emp_code: str = "E1001") -> list[dict]:
    response = client.post(
        f"/v1/workflows/{workflow_id}/messages?stream=false", headers=auth(emp_code), json={"text": text}
    )
    assert response.status_code == 200, response.text
    return response.json()["events"]


def texts(events: list[dict]) -> str:
    return " ".join(e["text"] for e in events if "text" in e)


def test_health(client):
    assert client.get("/v1/health").json()["status"] == "ok"


class TestAuthentication:
    def test_missing_token(self, client):
        assert client.post("/v1/sessions").status_code == 401

    def test_garbage_token(self, client):
        response = client.post("/v1/sessions", headers={"Authorization": "Bearer not-a-jwt"})
        assert response.status_code == 401 and response.json()["error"] == "invalid_token"

    def test_session_returns_identity_and_pending_count(self, client):
        body = client.post("/v1/sessions", headers=auth()).json()
        assert body["employee"]["name"] == "Test Employee"
        assert body["pending"]["action_needed_count"] == 1

    def test_backend_rejects_token(self, client, backend):
        backend.fail_with = 401
        response = client.post("/v1/sessions", headers=auth())
        assert response.status_code == 401 and response.json()["error"] == "session_expired"

    def test_profile_for_other_employee_is_refused(self, client, backend):
        backend.org_override = backend.employees["E2002"]
        response = client.post("/v1/sessions", headers=auth("E1001"))
        assert response.status_code == 403 and response.json()["error"] == "identity_mismatch"


class TestJourneys:
    def test_list_tours(self, client):
        events = say(client, start(client), "show my tours")
        tour_list = next(e for e in events if e["type"] == "tour_list")
        assert [t["tour_id"] for t in tour_list["tours"]] == [501, 502, 503]
        assert "1 needs your action" in texts(events)

    def test_pending_actions(self, client):
        events = say(client, start(client), "what is pending for me?")
        lists = {e["title"]: [t["tour_id"] for t in e["tours"]] for e in events if e["type"] == "tour_list"}
        assert lists == {"Needs your action": [502], "Ready to claim": [503]}

    def test_status_by_id(self, client, backend):
        events = say(client, start(client), "status of tour 501")
        status = next(e for e in events if e["type"] == "tour_status")
        assert status["tour"]["tour_id"] == 501
        assert "finance" in texts(events)
        assert len(status["steps"]) == 2

    def test_ambiguous_place_asks_then_answers(self, client):
        workflow_id = start(client)
        events = say(client, workflow_id, "where is my Mumbai claim?")
        choices = next(e for e in events if e["type"] == "choices")
        assert [o["tour_id"] for o in choices["options"]] == [501, 503]
        events = say(client, workflow_id, "2")
        assert next(e for e in events if e["type"] == "tour_status")["tour"]["tour_id"] == 503

    def test_follow_up_uses_focused_tour(self, client):
        workflow_id = start(client)
        say(client, workflow_id, "status of tour 502")
        events = say(client, workflow_id, "what is the status now?")
        assert next(e for e in events if e["type"] == "tour_status")["tour"]["tour_id"] == 502

    def test_write_goals_are_not_executed(self, client, backend):
        events = say(client, start(client), "reply to finance's clarification on tour 502")
        assert "not switched on yet" in texts(events)
        assert not [c for c in backend.calls if c.startswith(("POST", "PUT", "DELETE"))]

    def test_sse_stream(self, client):
        workflow_id = start(client)
        response = client.post(f"/v1/workflows/{workflow_id}/messages", headers=auth(), json={"text": "help"})
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = [f for f in response.text.split("\n\n") if f]
        assert frames[-1].startswith("event: done")
        first = json.loads(frames[0].split("data: ", 1)[1])
        assert first["type"] == "text"


class TestOwnership:
    def test_other_employees_tour_is_never_fetched(self, client, backend):
        events = say(client, start(client), "status of tour 777")  # belongs to E2002
        assert "couldn't find tour 777" in texts(events)
        assert not any("GetTourTrajectory/777" in c for c in backend.calls)

    def test_other_employee_cannot_use_my_workflow(self, client):
        workflow_id = start(client, "E1001")
        response = client.post(
            f"/v1/workflows/{workflow_id}/messages?stream=false", headers=auth("E2002"), json={"text": "show my tours"}
        )
        assert response.status_code == 404

    def test_abandoned_workflow_is_closed(self, client):
        workflow_id = start(client)
        assert client.delete(f"/v1/workflows/{workflow_id}", headers=auth()).status_code == 204
        assert workflow_id not in [w["workflow_id"] for w in client.get("/v1/workflows", headers=auth()).json()]
        response = client.post(
            f"/v1/workflows/{workflow_id}/messages?stream=false", headers=auth(), json={"text": "hi"}
        )
        assert response.status_code == 404


def test_conversation_resumes_after_restart(settings, backend):
    """State is checkpointed: a pending choice survives an application restart (STA-01/02)."""
    with TestClient(create_app(settings, http_transport=backend.transport())) as first:
        workflow_id = start(first)
        say(first, workflow_id, "where is my Mumbai claim?")
    with TestClient(create_app(settings, http_transport=backend.transport())) as second:
        events = say(second, workflow_id, "1")
        assert next(e for e in events if e["type"] == "tour_status")["tour"]["tour_id"] == 501


def test_backend_outage_gives_friendly_notice(client, backend):
    workflow_id = start(client)
    backend.fail_with = 503
    response = client.post(
        f"/v1/workflows/{workflow_id}/messages?stream=false", headers=auth(), json={"text": "show my tours"}
    )
    # Identity is cached from the previous call, so the failure surfaces inside the workflow.
    assert response.status_code == 200
    assert response.json()["events"][0]["type"] == "notice"
