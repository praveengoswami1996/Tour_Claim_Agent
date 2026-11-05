"""The .NET response envelope `{statusCode, message, data, error, errorDetail, ...}`.

The Tour API often returns HTTP 200 with a failure status inside the body, and some
endpoints never set `statusCode` at all (it stays 0). Callers must branch on the body.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    status_code: int | None = Field(default=None, alias="statusCode")
    message: str | None = None
    data: Any = None
    error: bool | None = None
    error_detail: str | None = Field(default=None, alias="errorDetail")
    total_records: int | None = Field(default=None, alias="totalRecords")

    @property
    def succeeded(self) -> bool:
        if self.status_code in (200, 201):
            return True
        # Some endpoints (e.g. GetTourTrajectory) never set statusCode.
        return self.status_code in (None, 0) and not self.error

    @property
    def is_not_found(self) -> bool:
        return self.status_code == 404
