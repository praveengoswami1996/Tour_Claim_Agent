"""Errors raised by backend clients."""

from __future__ import annotations


class BackendError(Exception):
    """A backend call failed. `status_code` is the envelope status when there is one."""

    def __init__(self, message: str, *, status_code: int | None = None, endpoint: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class BackendUnauthorized(BackendError):
    """The backend rejected the user's token (expired, revoked or invalid)."""


class BackendForbidden(BackendError):
    """The backend refused the operation for this user (for example a read-only session)."""


class BackendNotFound(BackendError):
    """The requested record does not exist."""


class BackendUnavailable(BackendError):
    """The backend could not be reached or returned a server error."""


class EndpointNotAllowed(Exception):
    """The agent tried to call an endpoint that is not on its allow-list. This is a programming error."""
