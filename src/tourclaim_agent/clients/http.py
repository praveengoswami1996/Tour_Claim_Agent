"""Shared HTTP plumbing for backend calls made on the user's behalf.

Every call: is checked against the allow-list, carries the user's own bearer token and the
DeviceType and correlation headers, and is parsed through the response envelope.
Calls are never retried automatically: a write whose outcome is unclear must be checked
by reading the backend state again (FRS EXE-12).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from tourclaim_agent.clients.allowlist import Service, check_allowed
from tourclaim_agent.clients.envelope import Envelope
from tourclaim_agent.clients.errors import (
    BackendError,
    BackendForbidden,
    BackendNotFound,
    BackendUnauthorized,
    BackendUnavailable,
)
from tourclaim_agent.logging_setup import correlation_id

log = logging.getLogger(__name__)

# (field name, (file name, content, content type))
FilePart = tuple[str, tuple[str, bytes, str]]


class UserScopedClient:
    """Calls one backend service with one user's token. Create one per request."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        service: Service,
        base_url: str,
        token: str,
        device_type: str,
        enabled_writes: frozenset[str] = frozenset(),
    ) -> None:
        self._http = http
        self._service = service
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._device_type = device_type
        self._enabled_writes = enabled_writes

    async def get_envelope(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        not_found_ok: bool = False,
    ) -> Envelope | None:
        """GET a path and return its envelope. Returns None for 'not found' when not_found_ok."""
        response = await self._send("GET", path, params=params)
        return self._result(self._parse(response, path), path, not_found_ok=not_found_ok)

    async def post_envelope(
        self,
        path: str,
        *,
        json: Any = None,
        data: dict[str, Any] | None = None,
        files: list[FilePart] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Envelope:
        response = await self._send("POST", path, params=params, json=json, data=data, files=files)
        result = self._result(self._parse(response, path), path, not_found_ok=False)
        assert result is not None
        return result

    async def get_bytes(self, path: str) -> tuple[bytes, str]:
        """Download a file. Returns (content, content type)."""
        response = await self._send("GET", path)
        if response.status_code >= 400:
            self._result(self._parse(response, path), path, not_found_ok=False)
        return response.content, response.headers.get("content-type", "application/octet-stream")

    async def send_raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: dict[str, Any] | None = None,
        files: list[Any] | None = None,
    ) -> httpx.Response:
        """One allow-listed call, with the user's headers, and the raw answer whatever its status.

        For ports of old browser code that branch on the HTTP status and body themselves (the old axios callers).
        A call that gets no answer raises BackendUnavailable.
        """
        return await self._send(method, path, params=params, json=json, data=data, files=files, map_status=False)

    def _result(self, envelope: Envelope, path: str, *, not_found_ok: bool) -> Envelope | None:
        if envelope.succeeded:
            return envelope
        if envelope.is_not_found:
            if not_found_ok:
                return None
            raise BackendNotFound(envelope.message or "Not found", status_code=404, endpoint=path)
        self._raise_for_envelope(envelope, path)
        raise AssertionError("unreachable")

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: dict[str, Any] | None = None,
        files: list[FilePart] | None = None,
        map_status: bool = True,
    ) -> httpx.Response:
        check_allowed(self._service, method, path, self._enabled_writes)
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "DeviceType": self._device_type,
            "X-Correlation-ID": correlation_id.get(),
            "Accept": "application/json",
        }
        started = time.perf_counter()
        try:
            response = await self._http.request(
                method, url, params=params, headers=headers, json=json, data=data, files=files
            )
        except httpx.HTTPError as exc:
            log.warning("backend call failed: %s %s (%s)", method, path, type(exc).__name__)
            raise BackendUnavailable("The Tour system could not be reached.", endpoint=path) from exc
        log.info(
            "backend %s %s -> %s in %.0f ms",
            method,
            path.split("?", 1)[0],
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        if not map_status:
            return response
        if response.status_code == 401:
            raise BackendUnauthorized("Your session has expired. Please sign in again.", status_code=401)
        if response.status_code == 403:
            raise BackendForbidden("You are not allowed to do this.", status_code=403, endpoint=path)
        if response.status_code >= 500:
            raise BackendUnavailable(
                "The Tour system returned an error.", status_code=response.status_code, endpoint=path
            )
        return response

    @staticmethod
    def _parse(response: httpx.Response, path: str) -> Envelope:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendError("Unexpected response from the Tour system.", endpoint=path) from exc
        if not isinstance(payload, dict):
            raise BackendError("Unexpected response from the Tour system.", endpoint=path)
        # Model-binding errors (ValidationProblemDetails) and some 404s carry no envelope status.
        if response.status_code >= 400 and "statusCode" not in payload:
            payload = {**payload, "statusCode": response.status_code, "error": True}
        return Envelope.model_validate(payload)

    @staticmethod
    def _raise_for_envelope(envelope: Envelope, path: str) -> None:
        status = envelope.status_code
        message = envelope.message or envelope.error_detail or "The Tour system rejected the request."
        if status == 401:
            raise BackendUnauthorized(message, status_code=401, endpoint=path)
        if status == 403:
            raise BackendForbidden(message, status_code=403, endpoint=path)
        if status is not None and status >= 500:
            raise BackendUnavailable(message, status_code=status, endpoint=path)
        raise BackendError(message, status_code=status, endpoint=path)
