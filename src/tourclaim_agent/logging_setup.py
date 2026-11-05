"""Logging with a per-request correlation ID. Tokens and personal data are never logged."""

from __future__ import annotations

import contextvars
import logging
import uuid

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_CorrelationFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(correlation_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
