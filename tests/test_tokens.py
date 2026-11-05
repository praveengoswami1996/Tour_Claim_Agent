from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.fakes import make_token
from tourclaim_agent.auth.tokens import InvalidToken, JwksTokenValidator, UnverifiedDevTokenValidator
from tourclaim_agent.config import AuthMode, Environment, Settings

ISSUER = "https://sso.test"


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "environment": Environment.TEST, "auth_mode": AuthMode.JWKS, "oidc_issuer": ISSUER}
    return Settings(**{**base, **overrides})


class TestSettingsGuards:
    def test_unverified_mode_refused_in_production(self):
        with pytest.raises(ValueError, match="unverified_dev"):
            Settings(_env_file=None, environment=Environment.PRODUCTION, auth_mode=AuthMode.UNVERIFIED_DEV)

    def test_jwks_needs_issuer(self):
        with pytest.raises(ValueError, match="oidc_issuer"):
            Settings(_env_file=None, environment=Environment.UAT, auth_mode=AuthMode.JWKS)


class TestUnverifiedDev:
    async def test_reads_employee_code_and_read_only(self):
        validator = UnverifiedDevTokenValidator(_settings(auth_mode=AuthMode.UNVERIFIED_DEV))
        claims = await validator.validate(make_token("E1001", IsReadOnly="True", scope="openid api1"))
        assert claims.emp_code == "E1001"
        assert claims.read_only is True

    async def test_expired_token_rejected(self):
        validator = UnverifiedDevTokenValidator(_settings(auth_mode=AuthMode.UNVERIFIED_DEV))
        with pytest.raises(InvalidToken):
            await validator.validate(make_token(expires_in=-120))

    async def test_missing_userid_claim_rejected(self):
        validator = UnverifiedDevTokenValidator(_settings(auth_mode=AuthMode.UNVERIFIED_DEV))
        token = jwt.encode({"exp": int(time.time()) + 60}, "x" * 32, algorithm="HS256")
        with pytest.raises(InvalidToken, match="UserId"):
            await validator.validate(token)

    async def test_wrong_scope_rejected(self):
        validator = UnverifiedDevTokenValidator(_settings(auth_mode=AuthMode.UNVERIFIED_DEV))
        with pytest.raises(InvalidToken, match="scope"):
            await validator.validate(make_token(scope="openid profile"))


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks_transport(public_key, kid="k1") -> httpx.MockTransport:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/.well-known/jwks"})
        return httpx.Response(200, json={"keys": [jwk]})

    return httpx.MockTransport(handler)


def _signed(key, *, kid="k1", **claims) -> str:
    payload = {"UserId": "E1001", "iss": ISSUER, "exp": int(time.time()) + 300, "scope": ["api1"], **claims}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


class TestJwks:
    async def test_valid_signature_accepted(self, rsa_key):
        async with httpx.AsyncClient(transport=_jwks_transport(rsa_key.public_key())) as http:
            claims = await JwksTokenValidator(_settings(), http).validate(_signed(rsa_key))
        assert claims.emp_code == "E1001"

    async def test_signature_from_other_key_rejected(self, rsa_key):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        async with httpx.AsyncClient(transport=_jwks_transport(rsa_key.public_key())) as http:
            with pytest.raises(InvalidToken):
                await JwksTokenValidator(_settings(), http).validate(_signed(other))

    async def test_wrong_issuer_rejected(self, rsa_key):
        async with httpx.AsyncClient(transport=_jwks_transport(rsa_key.public_key())) as http:
            with pytest.raises(InvalidToken):
                await JwksTokenValidator(_settings(), http).validate(_signed(rsa_key, iss="https://evil.test"))

    async def test_unknown_key_id_rejected(self, rsa_key):
        async with httpx.AsyncClient(transport=_jwks_transport(rsa_key.public_key())) as http:
            with pytest.raises(InvalidToken, match="unknown key"):
                await JwksTokenValidator(_settings(), http).validate(_signed(rsa_key, kid="other"))
