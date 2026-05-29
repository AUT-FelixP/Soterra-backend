from __future__ import annotations

from pydantic import Field

from .base import StrictRequestModel


class BulkDeleteReportsRequest(StrictRequestModel):
    ids: list[str] = Field(min_length=1, max_length=100)
