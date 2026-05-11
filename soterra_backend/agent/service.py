from __future__ import annotations

import json
import os
import re
from typing import Any

from .prompts import SOTERRA_AGENT_SYSTEM_PROMPT
from .schemas import AgentChatResponse, AgentRelatedEntities
from .tools import build_soterra_tools
from ..repository import RepositoryBackend


class AgentDisabledError(RuntimeError):
    pass


class AgentConfigurationError(RuntimeError):
    pass


class SoterraAgentService:
    def __init__(self, repository: RepositoryBackend) -> None:
        self.repository = repository

    def status(self) -> dict:
        enabled = os.getenv("SOTERRA_AGENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        provider = os.getenv("SOTERRA_AGENT_MODEL_PROVIDER", "huggingface").strip().lower()
        model_id = self._default_model_id(provider)
        configured = False
        if provider == "openai":
            configured = bool(os.getenv("OPENAI_API_KEY"))
        elif provider == "huggingface":
            configured = bool(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN"))
        elif provider == "litellm":
            configured = True
        return {
            "enabled": enabled,
            "configured": configured,
            "provider": provider,
            "model_id": model_id,
        }

    def chat(
        self,
        *,
        message: str,
        tenant_id: str,
        user_id: str,
        role: str,
        report_id: str | None = None,
        issue_id: str | None = None,
        project_slug: str | None = None,
        page_context: str | None = None,
    ) -> AgentChatResponse:
        self._ensure_enabled()
        used_tools: list[str] = []

        def record_tool(name: str) -> None:
            if name not in used_tools:
                used_tools.append(name)

        tools = build_soterra_tools(self.repository, tenant_id, record_tool)
        try:
            agent = self._build_agent(tools)
            task = self._build_task(
                message=message,
                tenant_id=tenant_id,
                user_id=user_id,
                role=role,
                report_id=report_id,
                issue_id=issue_id,
                project_slug=project_slug,
                page_context=page_context,
            )
            raw_answer = agent.run(task)
            answer = self._coerce_answer(raw_answer)
        except (AgentDisabledError, AgentConfigurationError):
            raise
        except Exception:
            answer = (
                "I could not complete the agent analysis right now. Please try again shortly, or open the relevant "
                "report, tracker item, or dashboard page and ask a more specific question."
            )

        related = self._related_entities(answer, report_id=report_id, issue_id=issue_id, project_slug=project_slug)
        confidence = self._confidence(used_tools, answer, report_id=report_id, issue_id=issue_id, project_slug=project_slug)
        return AgentChatResponse(answer=answer, used_tools=used_tools, related_entities=related, confidence=confidence)

    def _ensure_enabled(self) -> None:
        enabled = os.getenv("SOTERRA_AGENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            raise AgentDisabledError("Soterra agent chat is disabled.")

    def _build_agent(self, tools: list[Any]) -> Any:
        try:
            from smolagents import ToolCallingAgent
            from smolagents.monitoring import LogLevel
        except ModuleNotFoundError as exc:
            raise AgentConfigurationError("smolagents is not installed for agent chat.") from exc

        model = self._build_model()
        try:
            max_steps = int(os.getenv("SOTERRA_AGENT_MAX_STEPS", "6"))
        except ValueError:
            max_steps = 6
        return ToolCallingAgent(
            tools=tools,
            model=model,
            instructions=SOTERRA_AGENT_SYSTEM_PROMPT,
            max_steps=max(1, min(max_steps, 12)),
            verbosity_level=LogLevel.ERROR,
        )

    def _build_model(self) -> Any:
        provider = os.getenv("SOTERRA_AGENT_MODEL_PROVIDER", "huggingface").strip().lower()
        model_id = self._default_model_id(provider)
        try:
            temperature = float(os.getenv("SOTERRA_AGENT_TEMPERATURE", "0.2"))
        except ValueError:
            temperature = 0.2

        if provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise AgentConfigurationError("OPENAI_API_KEY is required for Soterra agent chat.")
            from smolagents import OpenAIModel

            return OpenAIModel(model_id=model_id, api_key=api_key, temperature=temperature)

        if provider == "huggingface":
            token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
            if not token:
                raise AgentConfigurationError("HF_TOKEN is required for Hugging Face Soterra agent chat.")
            from smolagents import InferenceClientModel

            hf_provider = os.getenv("SOTERRA_AGENT_HF_PROVIDER") or None
            try:
                max_tokens = int(os.getenv("SOTERRA_AGENT_MAX_TOKENS", "1200"))
            except ValueError:
                max_tokens = 1200
            return InferenceClientModel(
                model_id=model_id,
                provider=hf_provider,
                token=token,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        if provider == "litellm":
            from smolagents import LiteLLMModel

            return LiteLLMModel(model_id=model_id, temperature=temperature)

        raise AgentConfigurationError("Unsupported Soterra agent model provider.")

    def _default_model_id(self, provider: str) -> str:
        configured = os.getenv("SOTERRA_AGENT_MODEL_ID")
        if configured:
            return configured
        if provider == "openai":
            return os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        if provider == "huggingface":
            return "Qwen/Qwen2.5-72B-Instruct"
        return "Qwen/Qwen2.5-72B-Instruct"

    def _build_task(
        self,
        *,
        message: str,
        tenant_id: str,
        user_id: str,
        role: str,
        report_id: str | None,
        issue_id: str | None,
        project_slug: str | None,
        page_context: str | None,
    ) -> str:
        context = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role": role,
            "page_context": page_context,
            "report_id": report_id,
            "issue_id": issue_id,
            "project_slug": project_slug,
        }
        return (
            "Answer the user's Soterra question using only the provided internal tools. "
            "Pass the tenant_id shown in context to every tool call. Do not use external tools, SQL, code execution, "
            "network access, filesystem access, or user-supplied tool names. "
            "If report_id, issue_id, project_slug, or page_context points to a relevant tool, use that first.\n\n"
            f"Context:\n{json.dumps(context, indent=2)}\n\n"
            f"User question:\n{message}\n\n"
            "Return only the final plain-English answer."
        )

    def _coerce_answer(self, raw_answer: Any) -> str:
        if raw_answer is None:
            return "I could not find enough Soterra data to answer that clearly."
        text = str(raw_answer).strip()
        return text or "I could not find enough Soterra data to answer that clearly."

    def _related_entities(
        self,
        answer: str,
        *,
        report_id: str | None,
        issue_id: str | None,
        project_slug: str | None,
    ) -> AgentRelatedEntities:
        reports = [report_id] if report_id else sorted(set(re.findall(r"rpt-[A-Za-z0-9-]+", answer)))
        issues = [issue_id] if issue_id else sorted(set(re.findall(r"issue-[A-Za-z0-9-]+", answer)))
        projects = [project_slug] if project_slug else []
        return AgentRelatedEntities(reports=reports, issues=issues, projects=projects)

    def _confidence(
        self,
        used_tools: list[str],
        answer: str,
        *,
        report_id: str | None,
        issue_id: str | None,
        project_slug: str | None,
    ) -> str:
        lowered = answer.lower()
        if (report_id and "get_report_detail" in used_tools) or (issue_id and "get_issue_detail" in used_tools) or (
            project_slug and "get_project_metrics" in used_tools
        ):
            return "high"
        if any(phrase in lowered for phrase in ["could not find", "not enough", "missing data", "missing soterra data", "unclear", "no upcoming inspections"]):
            return "low"
        if used_tools:
            return "medium"
        return "low"
