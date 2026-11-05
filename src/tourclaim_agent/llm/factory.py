from __future__ import annotations

import httpx

from tourclaim_agent.config import LlmProvider, Settings
from tourclaim_agent.llm.aryaan_gateway import AryaanGatewayIntentExtractor
from tourclaim_agent.llm.intent import IntentExtractor
from tourclaim_agent.llm.openai_compatible import OpenAICompatibleIntentExtractor
from tourclaim_agent.llm.rules import RuleBasedIntentExtractor


def build_intent_extractor(settings: Settings, http: httpx.AsyncClient) -> IntentExtractor:
    if settings.llm_provider is LlmProvider.OPENAI_COMPATIBLE:
        return OpenAICompatibleIntentExtractor(
            http,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
        )
    if settings.llm_provider is LlmProvider.ARYAAN_GATEWAY:
        return AryaanGatewayIntentExtractor(
            http,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
        )
    return RuleBasedIntentExtractor()
