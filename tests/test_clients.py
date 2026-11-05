from __future__ import annotations

import httpx
import pytest

from tests.fakes import TOUR_BASE, FakeBackend
from tourclaim_agent.clients.allowlist import Service, check_allowed
from tourclaim_agent.clients.envelope import Envelope
from tourclaim_agent.clients.errors import BackendUnauthorized, BackendUnavailable, EndpointNotAllowed
from tourclaim_agent.clients.http import UserScopedClient
from tourclaim_agent.clients.tour_api import TourApiClient
from tourclaim_agent.domain.models import TourKind


class TestEnvelope:
    @pytest.mark.parametrize(
        ("payload", "ok"),
        [
            ({"statusCode": 200, "data": 1}, True),
            ({"statusCode": 201}, True),
            ({"statusCode": 0, "data": {}}, True),  # GetTourTrajectory never sets statusCode
            ({"data": {}}, True),
            ({"statusCode": 0, "error": True}, False),
            ({"statusCode": 400, "error": False}, False),  # failure reported inside an HTTP 200
            ({"statusCode": 404}, False),
        ],
    )
    def test_success_is_read_from_the_body(self, payload, ok):
        assert Envelope.model_validate(payload).succeeded is ok


class TestAllowList:
    def test_reads_are_allowed(self):
        assert check_allowed(Service.TOUR, "GET", "TourListing/GetTourRequest/1001?touretype=1").risk == "R0"
        assert check_allowed(Service.ORG, "GET", "Organization/GetEmployeDetailsWithEmpCode/E1").risk == "R0"

    def test_write_endpoints_are_listed_but_disabled(self):
        with pytest.raises(EndpointNotAllowed, match="not enabled"):
            check_allowed(Service.TOUR, "POST", "Claim/SubmitRaiseClaimDraft/12")

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("PUT", "TourStatus/Update/TourRequestStatus"),
            ("PUT", "Claim/ApproveRaiseClaimAmount/1/2/3/4"),
            ("POST", "TourPlan/EditTourRequest/5"),
            ("GET", "Dashboard/Approver/1"),
            ("DELETE", "TourListing/GetTourRequest/1"),
        ],
    )
    def test_everything_else_is_denied(self, method, path):
        with pytest.raises(EndpointNotAllowed):
            check_allowed(Service.TOUR, method, path)

    def test_service_must_match(self):
        with pytest.raises(EndpointNotAllowed):
            check_allowed(Service.ORG, "GET", "TourListing/GetTourRequest/1")


def _client(backend: FakeBackend, http: httpx.AsyncClient) -> TourApiClient:
    return TourApiClient(
        UserScopedClient(http, service=Service.TOUR, base_url=TOUR_BASE, token="tok", device_type="web")
    )


class TestTourApiClient:
    async def test_lists_tours_and_sends_user_headers(self, backend: FakeBackend):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return backend.handle(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            tours = await _client(backend, http).list_tours(1001, TourKind.OUTSIDE_JURISDICTION)
        assert [t.tour_id for t in tours] == [501, 502, 503]
        assert tours[0].recipient_name == "Approver One"
        assert tours[0].kind is TourKind.OUTSIDE_JURISDICTION
        assert seen[0].headers["Authorization"] == "Bearer tok"
        assert seen[0].headers["DeviceType"] == "web"
        assert seen[0].url.params["touretype"] == "1"

    async def test_no_tours_returns_empty_list(self, backend: FakeBackend):
        async with httpx.AsyncClient(transport=backend.transport()) as http:
            assert await _client(backend, http).list_tours(1001, TourKind.WITHIN_JURISDICTION) == []

    async def test_trajectory_without_status_code_is_accepted(self, backend: FakeBackend):
        async with httpx.AsyncClient(transport=backend.transport()) as http:
            trajectory = await _client(backend, http).get_trajectory(501)
        assert trajectory is not None and trajectory.latest.status_id == 2

    async def test_401_becomes_session_expired(self, backend: FakeBackend):
        backend.fail_with = 401
        async with httpx.AsyncClient(transport=backend.transport()) as http:
            with pytest.raises(BackendUnauthorized):
                await _client(backend, http).list_claimable_tours(1001)

    async def test_server_error_is_unavailable(self, backend: FakeBackend):
        backend.fail_with = 500
        async with httpx.AsyncClient(transport=backend.transport()) as http:
            with pytest.raises(BackendUnavailable):
                await _client(backend, http).pending_task_count(1001)
