"""Base model for backend payloads.

The .NET API serialises with camelCase, but a few payloads use PascalCase. Keys are
normalised to lower-camel before validation so both forms are accepted. Dates arrive as
ISO strings with or without time; unparseable values become None instead of failing.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator


def _lower_first(key: str) -> str:
    return key[:1].lower() + key[1:] if key else key


def _normalise_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {_lower_first(k) if isinstance(k, str) else k: v for k, v in value.items()}
    return value


# Formats seen from the Tour API and the OCR provider. Some backend fields come from .NET
# DateTime.ToString(), whose format depends on the server culture: slash dates are read as
# month/day/year (en-US) and dash dates as day-month-year (en-IN). To be verified on UAT.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
)


def parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _lenient_date(value: Any) -> date | None:
    return parse_date(value)


def _lenient_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


LenientDate = Annotated[date | None, BeforeValidator(_lenient_date)]
LenientDateTime = Annotated[datetime | None, BeforeValidator(_lenient_datetime)]


class BackendModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _keys(cls, data: Any) -> Any:
        return _normalise_keys(data)
