"""Resolve who the caller is (FRS IDN-03).

The employee code comes from the validated token; the numeric empId, grade and unit come
from the Organisation service. Nothing here is ever taken from chat text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from tourclaim_agent.auth.tokens import TokenClaims
from tourclaim_agent.clients.org_api import OrgApiClient
from tourclaim_agent.domain.models import EmployeeProfile


class IdentityMismatch(Exception):
    """The Organisation service returned a profile for a different employee than the token."""


@dataclass(frozen=True)
class Employee:
    """The signed-in employee. Safe to keep in workflow state (contains no token)."""

    emp_id: int
    emp_code: str
    name: str | None
    designation: str | None
    unit: str | None
    grade: str | None
    read_only: bool

    @classmethod
    def from_profile(cls, profile: EmployeeProfile, claims: TokenClaims) -> Employee:
        return cls(
            emp_id=profile.emp_id,
            emp_code=profile.emp_code or claims.emp_code,
            name=profile.emp_name,
            designation=profile.designation,
            unit=profile.unit,
            grade=profile.grade,
            read_only=claims.read_only,
        )


class IdentityResolver:
    """Caches profiles per employee code for a short time to avoid a lookup on every message."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, EmployeeProfile]] = {}

    async def resolve(self, claims: TokenClaims, org: OrgApiClient) -> Employee:
        cached = self._cache.get(claims.emp_code)
        if cached and time.monotonic() - cached[0] < self._ttl:
            profile = cached[1]
        else:
            profile = await org.get_employee(claims.emp_code)
            if (profile.emp_code or "").strip().lower() != claims.emp_code.strip().lower():
                raise IdentityMismatch("The employee record does not match the signed-in user.")
            self._cache[claims.emp_code] = (time.monotonic(), profile)
        return Employee.from_profile(profile, claims)
