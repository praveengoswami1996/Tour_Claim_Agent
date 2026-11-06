"""Validation of the employee's OIDC access token (FRS IDN-01, DEC-2).

Modes:
- jwks: verify the signature against the SSO server's published keys, plus issuer, expiry,
  optional audience and the required scope.
- introspection: ask the SSO server whether the token is active (mirrors the .NET filter).
- unverified_dev: decode without verifying. Refused by Settings outside development/test.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import jwt

from tourclaim_agent.config import AuthMode, Settings


class InvalidToken(Exception):
    """The token is missing, malformed, expired or not accepted."""


@dataclass(frozen=True)
class TokenClaims:
    emp_code: str
    expires_at: float | None
    read_only: bool
    scopes: frozenset[str] = field(default_factory=frozenset)
    # the SSO profile's display name (`unique_name`): the old Claim Assistant greeted the employee with it
    name: str | None = None

    @property
    def seconds_left(self) -> float | None:
        return None if self.expires_at is None else self.expires_at - time.time()


class TokenValidator(Protocol):
    async def validate(self, token: str) -> TokenClaims: ...


def _scopes(claims: dict[str, Any]) -> frozenset[str]:
    raw = claims.get("scope") or claims.get("scp") or []
    if isinstance(raw, str):
        raw = raw.split()
    return frozenset(str(s) for s in raw)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def claims_from_payload(payload: dict[str, Any], settings: Settings) -> TokenClaims:
    emp_code = payload.get(settings.userid_claim)
    if emp_code in (None, ""):
        raise InvalidToken(f"token has no '{settings.userid_claim}' claim")
    scopes = _scopes(payload)
    if settings.oidc_required_scope and scopes and settings.oidc_required_scope not in scopes:
        raise InvalidToken("token does not carry the required scope")
    exp = payload.get("exp")
    return TokenClaims(
        emp_code=str(emp_code).strip(),
        expires_at=float(exp) if exp is not None else None,
        read_only=_truthy(payload.get("IsReadOnly", False)),
        scopes=scopes,
        name=str(payload["unique_name"]) if isinstance(payload.get("unique_name"), str) else None,
    )


class JwksTokenValidator:
    """Verifies RS/ES-signed JWTs against the issuer's JWKS, cached for `cache_seconds`."""

    _ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]

    def __init__(self, settings: Settings, http: httpx.AsyncClient, cache_seconds: int = 3600) -> None:
        self._settings = settings
        self._http = http
        self._cache_seconds = cache_seconds
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = 0.0

    async def _jwks_url(self) -> str:
        if self._settings.oidc_jwks_url:
            return self._settings.oidc_jwks_url
        discovery = f"{self._settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
        response = await self._http.get(discovery)
        response.raise_for_status()
        return response.json()["jwks_uri"]

    async def _refresh(self) -> None:
        response = await self._http.get(await self._jwks_url())
        response.raise_for_status()
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in response.json().get("keys", []):
            if jwk.get("use", "sig") != "sig":
                continue
            try:
                keys[jwk.get("kid", "")] = jwt.PyJWK(jwk)
            except jwt.PyJWTError:
                continue
        self._keys = keys
        self._fetched_at = time.time()

    async def _key_for(self, kid: str) -> jwt.PyJWK:
        stale = time.time() - self._fetched_at > self._cache_seconds
        if stale or kid not in self._keys:
            try:
                await self._refresh()
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise InvalidToken("could not load signing keys") from exc
        if kid not in self._keys:
            raise InvalidToken("token signed with an unknown key")
        return self._keys[kid]

    async def validate(self, token: str) -> TokenClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidToken("malformed token") from exc
        key = await self._key_for(header.get("kid", ""))
        options = {"require": ["exp", "iss"], "verify_aud": bool(self._settings.oidc_audience)}
        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=self._ALGORITHMS,
                issuer=self._settings.oidc_issuer,
                audience=self._settings.oidc_audience or None,
                options=options,
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc
        return claims_from_payload(payload, self._settings)


class IntrospectionTokenValidator:
    """Checks the issuer locally, then asks the SSO introspection endpoint if the token is active."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

    async def validate(self, token: str) -> TokenClaims:
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise InvalidToken("malformed token") from exc
        if unverified.get("iss", "").rstrip("/") != self._settings.oidc_issuer.rstrip("/"):
            raise InvalidToken("token issuer is not accepted")
        try:
            response = await self._http.post(self._settings.oidc_introspection_url, data={"token": token})
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise InvalidToken("token could not be introspected") from exc
        if not result.get("active"):
            raise InvalidToken("token is not active")
        return claims_from_payload({**unverified, **result}, self._settings)


class UnverifiedDevTokenValidator:
    """Development only: decodes without a signature check but still enforces expiry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def validate(self, token: str) -> TokenClaims:
        try:
            payload = jwt.decode(token, options={"verify_signature": False, "verify_exp": True})
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc
        return claims_from_payload(payload, self._settings)


def build_token_validator(settings: Settings, http: httpx.AsyncClient) -> TokenValidator:
    if settings.auth_mode is AuthMode.JWKS:
        return JwksTokenValidator(settings, http)
    if settings.auth_mode is AuthMode.INTROSPECTION:
        return IntrospectionTokenValidator(settings, http)
    return UnverifiedDevTokenValidator(settings)
