from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .prompts import SOTERRA_AGENT_SYSTEM_PROMPT
from .schemas import AgentChatResponse, AgentRelatedEntities
from .tools import build_soterra_tools
from ..repository import RepositoryBackend

logger = logging.getLogger(__name__)


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
        fallback_error: Exception | None = None
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
        except Exception as exc:
            fallback_error = exc
            logger.warning("soterra_agent_model_failed provider=%s model=%s error=%s", self.status().get("provider"), self.status().get("model_id"), type(exc).__name__)
            answer = self._fallback_answer(
                message=message,
                tenant_id=tenant_id,
                report_id=report_id,
                issue_id=issue_id,
                project_slug=project_slug,
                page_context=page_context,
                used_tools=used_tools,
            )

        related = self._related_entities(answer, report_id=report_id, issue_id=issue_id, project_slug=project_slug)
        confidence = self._confidence(used_tools, answer, report_id=report_id, issue_id=issue_id, project_slug=project_slug)
        if fallback_error and confidence == "high":
            confidence = "medium"
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
            try:
                timeout = int(os.getenv("SOTERRA_AGENT_HF_TIMEOUT_SECONDS", "8"))
            except ValueError:
                timeout = 8
            return InferenceClientModel(
                model_id=model_id,
                provider=hf_provider,
                token=token,
                timeout=max(3, min(timeout, 60)),
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

    def _fallback_answer(
        self,
        *,
        message: str,
        tenant_id: str,
        report_id: str | None,
        issue_id: str | None,
        project_slug: str | None,
        page_context: str | None,
        used_tools: list[str],
    ) -> str:
        tools = {tool.name: tool for tool in build_soterra_tools(self.repository, tenant_id, lambda name: used_tools.append(name) if name not in used_tools else None)}
        normalized = f"{page_context or ''} {message}".lower()

        if report_id:
            payload = tools["get_report_detail"].forward(tenant_id, report_id)
            return _answer_from_report_detail(payload)
        if issue_id:
            payload = tools["get_issue_detail"].forward(tenant_id, issue_id)
            return _answer_from_issue_detail(payload)
        if project_slug:
            payload = tools["get_project_metrics"].forward(tenant_id, project_slug)
            return _answer_from_project_metrics(payload)
        if any(term in normalized for term in ["open", "overdue", "issue", "issues", "tracker", "attention", "first"]):
            payload = tools["get_tracker_summary"].forward(tenant_id)
            return _answer_from_tracker_summary(payload)
        if any(term in normalized for term in ["repeat", "recurring", "failure", "failures", "waterproofing", "root cause"]):
            payload = tools["get_top_failures"].forward(tenant_id)
            return _answer_from_top_failures(payload)
        if any(term in normalized for term in ["upcoming", "inspection risk", "risky inspection"]):
            payload = tools["get_upcoming_risk"].forward(tenant_id)
            return _answer_from_upcoming_risk(payload)
        if any(term in normalized for term in ["risk", "risky", "highest risk", "most risky"]):
            payload = tools["get_dashboard_risk"].forward(tenant_id)
            return _answer_from_dashboard_risk(payload)

        payload = tools["get_dashboard_summary"].forward(tenant_id)
        return _answer_from_dashboard_summary(payload)

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


def _answer_from_report_detail(payload: dict) -> str:
    if not payload.get("found"):
        return "I could not find that report in the current Soterra data."
    item = payload.get("item") or {}
    issues = item.get("issues") or []
    if not issues:
        return f"{item.get('project', 'This report')} has no extracted issues available yet. Suggested next action: confirm the report extraction has completed."
    top = sorted(issues, key=lambda issue: {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(str(issue.get("severity")), 4))[:4]
    lines = [f"The main risks in {item.get('project', 'this report')} are:"]
    for issue in top:
        lines.append(f"- {issue.get('title')}: {issue.get('severity', 'Unknown')} severity, {issue.get('status', 'unknown')} status.")
    lines.append("Suggested next action: close out the critical and high-severity items first, then capture evidence for reinspection.")
    return "\n".join(lines)


def _answer_from_issue_detail(payload: dict) -> str:
    if not payload.get("found"):
        return "I could not find that issue in the current Soterra data."
    item = payload.get("item") or {}
    return (
        f"{item.get('title', 'This issue')} is {item.get('status', 'unknown')} with {item.get('severity', 'unknown')} priority "
        f"on {item.get('project', 'the project')}. Suggested next action: assign the relevant trade or site lead to close it out and record evidence."
    )


def _answer_from_project_metrics(payload: dict) -> str:
    if not payload.get("found"):
        return "I could not find that project in the current Soterra data."
    metrics = payload.get("metrics") or []
    drivers = payload.get("topFailureDrivers") or []
    metric_text = ", ".join(f"{item.get('label')}: {item.get('value')}" for item in metrics[:4])
    driver_text = ", ".join(str(item.get("issue")) for item in drivers[:3] if item.get("issue")) or "no repeated failure drivers"
    return f"{payload.get('title', 'This project')} shows {metric_text}. Main failure areas: {driver_text}. Suggested next action: review open items and repeated failures before the next inspection."


def _answer_from_tracker_summary(payload: dict) -> str:
    issues = payload.get("issues") or []
    if not issues:
        return "There are currently no open, overdue, or high-priority issues that need attention from the tracker data."
    lines = [
        f"The tracker shows {payload.get('openIssueCount', 0)} open issues, {payload.get('overdueIssueCount', 0)} overdue issues, and {payload.get('highPriorityIssueCount', 0)} high-priority issues.",
        "Fix first:",
    ]
    for issue in issues[:5]:
        lines.append(f"- {issue.get('title')}: {issue.get('severity', 'Unknown')} severity, {issue.get('status', 'unknown')} status, {issue.get('project', 'unknown project')}.")
    return "\n".join(lines)


def _answer_from_top_failures(payload: dict) -> str:
    drivers = payload.get("drivers") or []
    if not drivers:
        return "I could not find repeated failure patterns in the current Soterra data."
    lines = ["The most repeated failure drivers are:"]
    for driver in drivers[:5]:
        lines.append(f"- {driver.get('issue')}: {driver.get('failCount', driver.get('failureShare', 'unknown'))} occurrence signal.")
    lines.append("Suggested next action: brief the responsible trades on the top repeat items before the next inspection.")
    return "\n".join(lines)


def _answer_from_upcoming_risk(payload: dict) -> str:
    failures = payload.get("likelyFailures") or []
    if payload.get("title") == "No upcoming inspections":
        return "There are no upcoming inspection risks available from current uploaded reports."
    failure_text = ", ".join(map(str, failures[:3])) if failures else "no likely failures identified yet"
    return f"The next risky inspection is {payload.get('title')} in {payload.get('daysUntilInspection', 'unknown')} days. Watch for: {failure_text}. Suggested next action: confirm readiness and evidence before booking reinspection."


def _answer_from_dashboard_risk(payload: dict) -> str:
    inspections = payload.get("inspections") or []
    if not inspections:
        return "I cannot identify a risky project right now because there are no current uploaded reports with active inspection-risk data."
    selected = inspections[0]
    failures = payload.get("likelyFailures") or []
    failure_text = ", ".join(str(item.get("issue")) for item in failures[:3] if isinstance(item, dict) and item.get("issue")) or "no likely failures identified yet"
    return f"The riskiest current inspection is {selected.get('type')} at {selected.get('site')}, due in {selected.get('daysAway')} days with {selected.get('riskLevel')} risk. Watch for: {failure_text}. Suggested next action: review readiness and close-out evidence before inspection."


def _answer_from_dashboard_summary(payload: dict) -> str:
    metrics = payload.get("metrics") or []
    if not metrics:
        return "I could not find enough dashboard data to answer that clearly."
    metric_text = ", ".join(f"{item.get('label')}: {item.get('value')}" for item in metrics[:4])
    return f"Current dashboard summary: {metric_text}. Suggested next action: review open issues and upcoming risks before reinspection."
