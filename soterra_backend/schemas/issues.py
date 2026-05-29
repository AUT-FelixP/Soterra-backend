from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import StrictRequestModel


class IssueUpdateRequest(StrictRequestModel):
    status: Literal["Open", "Ready", "Closed"] | None = None
    reinspections: int | None = Field(default=None, ge=0, le=1000)
    lastSentTo: str | None = Field(default=None, max_length=254)
