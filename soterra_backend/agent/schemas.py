from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentChatRequest(AgentStrictModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    report_id: str | None = None
    issue_id: str | None = None
    project_slug: str | None = None
    page_context: str | None = None


class AgentRelatedEntities(AgentStrictModel):
    reports: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)


class AgentChatResponse(AgentStrictModel):
    session_id: str
    answer: str
    used_tools: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    safety: dict = Field(default_factory=dict)
    suggested_follow_ups: list[str] = Field(default_factory=list)
    related_entities: AgentRelatedEntities = Field(default_factory=AgentRelatedEntities)
    confidence: Literal["low", "medium", "high"]


class AgentChatSessionSummary(AgentStrictModel):
    id: str
    title: str | None = None
    created_at: str
    updated_at: str


class AgentChatMessageSummary(AgentStrictModel):
    id: str
    role: Literal["user", "assistant", "tool"]
    content: str
    created_at: str
    tool_name: str | None = None
