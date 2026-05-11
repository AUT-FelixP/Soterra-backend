from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentChatRequest(AgentStrictModel):
    message: str = Field(min_length=1, max_length=4000)
    report_id: str | None = None
    issue_id: str | None = None
    project_slug: str | None = None
    page_context: str | None = None


class AgentRelatedEntities(AgentStrictModel):
    reports: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)


class AgentChatResponse(AgentStrictModel):
    answer: str
    used_tools: list[str] = Field(default_factory=list)
    related_entities: AgentRelatedEntities = Field(default_factory=AgentRelatedEntities)
    confidence: Literal["low", "medium", "high"]
