"""Application settings, read from environment variables with the TCA_ prefix."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    UAT = "uat"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    JWKS = "jwks"
    INTROSPECTION = "introspection"
    # Decodes the token without checking its signature. Development only.
    UNVERIFIED_DEV = "unverified_dev"


class LlmProvider(StrEnum):
    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"
    # DFCCIL ARYAAN AI gateway (base URL ends with /chat-api); uses its /extract-json endpoint
    ARYAAN_GATEWAY = "aryaan_gateway"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TCA_", env_file=".env", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT

    # Backends
    tour_api_base_url: str = "https://uattourapi.dfccil.com/api"
    org_api_base_url: str = "https://uatorganization.dfccil.com/api"
    device_type: str = "web"
    backend_timeout_seconds: float = 30.0
    # Disabled allow-list write endpoints switched on by an operator after UAT verification,
    # e.g. ["Claim/SaveRaiseClaimDraft"]. Empty by default: the agent cannot write claims.
    enabled_write_endpoints: list[str] = Field(default_factory=list)

    # Bill uploads (match the OCR service limits)
    max_upload_files: int = 20
    max_upload_bytes: int = 20 * 1024 * 1024
    # Read bills are saved into the tour's draft automatically when draft saving is enabled (BIL-49).
    auto_save_bills: bool = True
    # Bill outcomes never told to the employee are dropped after this many days (BIL-63).
    bill_report_keep_days: int = 14
    # A bill still being read after this many minutes is reported once as slow (BIL-16).
    bill_slow_minutes: int = 15

    # Authentication
    auth_mode: AuthMode = AuthMode.JWKS
    oidc_issuer: str = ""
    oidc_jwks_url: str = ""
    oidc_audience: str = ""
    oidc_required_scope: str = "api1"
    oidc_introspection_url: str = ""
    userid_claim: str = "UserId"

    # State
    state_db_path: str = "data/agent_state.sqlite"
    confirmation_ttl_seconds: int = 15 * 60
    identity_cache_seconds: int = 5 * 60

    # LLM
    llm_provider: LlmProvider = LlmProvider.FAKE
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0

    # HTTP
    cors_origins: list[str] = Field(default_factory=lambda: ["https://localhost:3001"])

    @model_validator(mode="after")
    def _check_safe_combinations(self) -> Settings:
        if self.auth_mode is AuthMode.UNVERIFIED_DEV and self.environment not in (
            Environment.DEVELOPMENT,
            Environment.TEST,
        ):
            raise ValueError("auth_mode=unverified_dev is only allowed in development or test")
        if self.auth_mode is AuthMode.JWKS and not self.oidc_issuer:
            raise ValueError("oidc_issuer is required when auth_mode=jwks")
        if self.auth_mode is AuthMode.INTROSPECTION and not (self.oidc_issuer and self.oidc_introspection_url):
            raise ValueError("oidc_issuer and oidc_introspection_url are required when auth_mode=introspection")
        if self.llm_provider is LlmProvider.OPENAI_COMPATIBLE and not (self.llm_base_url and self.llm_model):
            raise ValueError("llm_base_url and llm_model are required when llm_provider=openai_compatible")
        if self.llm_provider is LlmProvider.ARYAAN_GATEWAY and not self.llm_base_url:
            raise ValueError("llm_base_url is required when llm_provider=aryaan_gateway")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
