"""Read-only client for the Organisation service (employee profile)."""

from __future__ import annotations

from urllib.parse import quote

from tourclaim_agent.clients.errors import BackendNotFound
from tourclaim_agent.clients.http import UserScopedClient
from tourclaim_agent.domain.models import EmployeeProfile


class OrgApiClient:
    def __init__(self, client: UserScopedClient) -> None:
        self._c = client

    async def get_employee(self, emp_code: str) -> EmployeeProfile:
        envelope = await self._c.get_envelope(f"Organization/GetEmployeDetailsWithEmpCode/{quote(emp_code, safe='')}")
        if envelope is None or not envelope.data:
            raise BackendNotFound("Your employee record was not found.", status_code=404)
        return EmployeeProfile.model_validate(envelope.data)
