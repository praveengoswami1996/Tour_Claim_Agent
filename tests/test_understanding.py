from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from tourclaim_agent.agent.resolver import resolve_tours
from tourclaim_agent.domain.models import TourSummary
from tourclaim_agent.llm.aryaan_gateway import AryaanGatewayIntentExtractor
from tourclaim_agent.llm.intent import Goal, IntentContext, TourReference
from tourclaim_agent.llm.openai_compatible import OpenAICompatibleIntentExtractor
from tourclaim_agent.llm.rules import RuleBasedIntentExtractor

NO_OPTIONS = IntentContext()


@pytest.mark.parametrize(
    ("message", "goal"),
    [
        ("Show my tours", Goal.LIST_TOURS),
        ("what is pending for me?", Goal.PENDING_ACTIONS),
        ("Which tours can I claim", Goal.CLAIMABLE_TOURS),
        ("Where is my Mumbai claim?", Goal.TOUR_STATUS),
        ("status of tour 1234", Goal.TOUR_STATUS),
        ("I want to raise a claim for my Pune trip", Goal.RAISE_CLAIM),
        ("reply to finance's clarification", Goal.ANSWER_CLARIFICATION),
        ("Approve the claim of my colleague", Goal.OUT_OF_SCOPE),
        ("am I eligible for a hotel in Pune?", Goal.POLICY_QUESTION),
        ("hello", Goal.GREETING),
        ("asdf qwerty", Goal.UNKNOWN),
    ],
)
async def test_goals(message, goal):
    assert (await RuleBasedIntentExtractor().extract(message, NO_OPTIONS)).goal is goal


async def test_tour_reference_fields():
    intent = await RuleBasedIntentExtractor().extract("status of my trip to Mumbai in October 2026", NO_OPTIONS)
    assert intent.tour.place == "mumbai"
    assert intent.tour.month == 10 and intent.tour.year == 2026
    intent = await RuleBasedIntentExtractor().extract("where is tour no. 552", NO_OPTIONS)
    assert intent.tour.tour_id == 552


@pytest.mark.parametrize(("message", "selection"), [("2", 2), ("option 1", 1), ("the last one", 3), ("7", None)])
async def test_selection_only_within_offered_options(message, selection):
    context = IntentContext(pending_options=["a", "b", "c"])
    intent = await RuleBasedIntentExtractor().extract(message, context)
    assert intent.selection == selection


def _tour(tour_id: int, dest: str, start: date) -> TourSummary:
    return TourSummary(tour_id=tour_id, destination=dest, source="Noida", start_date=start)


class TestResolver:
    tours = [
        _tour(3, "Mumbai", date(2026, 10, 5)),
        _tour(2, "Kolkata", date(2026, 9, 12)),
        _tour(1, "Mumbai", date(2026, 8, 20)),
    ]

    def test_by_id_only_matches_own_tours(self):
        assert [t.tour_id for t in resolve_tours(self.tours, TourReference(tour_id=2))] == [2]
        assert resolve_tours(self.tours, TourReference(tour_id=999)) == []

    def test_by_place_can_be_ambiguous(self):
        assert [t.tour_id for t in resolve_tours(self.tours, TourReference(place="mumbai"))] == [3, 1]

    def test_place_and_month_narrow_down(self):
        result = resolve_tours(self.tours, TourReference(place="Mumbai", month=8))
        assert [t.tour_id for t in result] == [1]

    def test_latest(self):
        assert [t.tour_id for t in resolve_tours(self.tours, TourReference(place="mumbai", latest=True))] == [3]


class TestOpenAICompatible:
    async def _extract(self, handler, message="status of tour 12", options=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            extractor = OpenAICompatibleIntentExtractor(
                http, base_url="https://llm.test/v1", model="m", api_key="k", timeout=5
            )
            return await extractor.extract(message, IntentContext(pending_options=options or []))

    async def test_parses_json_reply(self):
        def handler(request):
            body = '{"goal": "tour_status", "tour": {"tour_id": 12, "latest": false}, "selection": null}'
            return httpx.Response(200, json={"choices": [{"message": {"content": body}}]})

        intent = await self._extract(handler)
        assert intent.goal is Goal.TOUR_STATUS and intent.tour.tour_id == 12

    async def test_falls_back_to_rules_on_bad_reply(self):
        def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

        intent = await self._extract(handler, message="show my tours")
        assert intent.goal is Goal.LIST_TOURS

    async def test_rejects_out_of_range_selection(self):
        def handler(request):
            body = '{"goal": "select_option", "tour": {}, "selection": 9}'
            return httpx.Response(200, json={"choices": [{"message": {"content": body}}]})

        intent = await self._extract(handler, message="nine", options=["a", "b"])
        assert intent.selection is None and intent.goal is Goal.UNKNOWN


class TestAryaanGateway:
    async def _extract(self, handler, message="status of tour 12", options=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            extractor = AryaanGatewayIntentExtractor(http, base_url="https://gw.test/chat-api", api_key="k", timeout=5)
            return await extractor.extract(message, IntentContext(pending_options=options or []))

    async def test_parses_reply_and_sends_schema(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"goal": "tour_status", "tour": {"tour_id": 12}, "selection": None})

        intent = await self._extract(handler)
        assert intent.goal is Goal.TOUR_STATUS and intent.tour.tour_id == 12
        assert seen["url"] == "https://gw.test/chat-api/extract-json"
        assert seen["auth"] == "Bearer k"
        assert "status of tour 12" in seen["body"]["instruction"]
        assert "tour_status" in seen["body"]["json_schema"]["properties"]["goal"]["enum"]

    async def test_drops_invented_tour_number(self):
        def handler(request):
            return httpx.Response(200, json={"goal": "tour_status", "tour": {"tour_id": 1, "place": "mumbai"}})

        intent = await self._extract(handler, message="where is my Mumbai claim?")
        assert intent.tour.tour_id is None and intent.tour.place == "mumbai"

    async def test_falls_back_to_rules_on_error(self):
        def handler(request):
            return httpx.Response(503)

        intent = await self._extract(handler, message="show my tours")
        assert intent.goal is Goal.LIST_TOURS
