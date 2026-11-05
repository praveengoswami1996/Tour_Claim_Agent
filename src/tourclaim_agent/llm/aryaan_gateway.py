"""Intent extraction through the DFCCIL ARYAAN AI gateway (the local LLM behind /chat-api).

The gateway is not OpenAI-compatible. It offers POST {base}/extract-json, which takes an
instruction and a JSON Schema and returns one JSON object matching that schema. Only the
employee's message and the labels of any offered options are sent. On any failure the
rule-based extractor is used instead.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from pydantic import ValidationError

from tourclaim_agent.llm.intent import Goal, Intent, IntentContext
from tourclaim_agent.llm.openai_compatible import _SYSTEM_PROMPT
from tourclaim_agent.llm.rules import RuleBasedIntentExtractor

log = logging.getLogger(__name__)

# Short meanings help the small local model tell similar goals apart.
_GOAL_HINTS = """Goal meanings (the message may be in English, Hindi or Hinglish):
- tour_status: where a tour or claim is, its progress or status ("kahan tak pahuncha", "kya hua")
- policy_question: a rule or entitlement question, not about one specific tour's progress
- list_tours / claimable_tours / pending_actions: list tours / tours ready to claim / what needs my action
- raise_claim, claim_review, save_draft, cancel_claim: start, check, save or cancel the claim being prepared"""

_NULLABLE_INT = {"type": ["integer", "null"]}
_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string", "enum": [g.value for g in Goal]},
        "tour": {
            "type": "object",
            "properties": {
                "tour_id": _NULLABLE_INT,
                "place": {"type": ["string", "null"]},
                "month": _NULLABLE_INT,
                "year": _NULLABLE_INT,
                "latest": {"type": "boolean"},
            },
            "required": ["tour_id", "place", "month", "year", "latest"],
        },
        "selection": _NULLABLE_INT,
    },
    "required": ["goal", "tour", "selection"],
}


class AryaanGatewayIntentExtractor:
    def __init__(self, http: httpx.AsyncClient, *, base_url: str, api_key: str, timeout: float) -> None:
        self._http = http
        self._url = f"{base_url.rstrip('/')}/extract-json"
        self._api_key = api_key
        self._timeout = timeout
        self._fallback = RuleBasedIntentExtractor()

    async def extract(self, message: str, context: IntentContext) -> Intent:
        options = "\n".join(f"{i}. {label}" for i, label in enumerate(context.pending_options, 1)) or "(none)"
        system = _SYSTEM_PROMPT % {
            "goals": json.dumps([g.value for g in Goal]),
            "today": context.today_iso,
            "options": options,
        }
        body = {
            "instruction": f"{system}\n\n{_GOAL_HINTS}\n\nEmployee message:\n<<<\n{message[:2000]}\n>>>",
            "json_schema": _SCHEMA,
            "max_tokens": 256,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            response = await self._http.post(self._url, json=body, headers=headers, timeout=self._timeout)
            response.raise_for_status()
            intent = Intent.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            log.warning("LLM intent extraction failed (%s); using rule-based fallback", type(exc).__name__)
            return await self._fallback.extract(message, context)
        # Small models sometimes invent a tour number; keep it only if the employee typed it.
        if intent.tour.tour_id is not None and str(intent.tour.tour_id) not in re.findall(r"\d+", message):
            intent.tour.tour_id = None
        if intent.selection is not None and not 1 <= intent.selection <= len(context.pending_options):
            intent.selection = None
            if intent.goal is Goal.SELECT_OPTION:
                intent.goal = Goal.UNKNOWN
        return intent
