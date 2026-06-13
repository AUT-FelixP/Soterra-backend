from __future__ import annotations

import json
import logging
import os
import re
from enum import Enum
from typing import Any

from .prompts import SOTERRA_AGENT_SYSTEM_PROMPT
from .schemas import AgentChatResponse, AgentRelatedEntities
from .tools import build_soterra_tools
from ..config import DEFAULT_MODEL_ID, DEFAULT_MODEL_PROVIDER, DEFAULT_REMOTE_MODEL_ID
from ..repository import RepositoryBackend
from ..services.work_package_service import build_chat_cards, build_todays_fix_list, build_work_packages
from ..utils import safe_int

logger = logging.getLogger(__name__)


class AgentDisabledError(RuntimeError):
    pass


class AgentConfigurationError(RuntimeError):
    pass


class AgentIntent(str, Enum):
    REPORT_SUMMARY = "REPORT_SUMMARY"
    REPORT_DETAIL = "REPORT_DETAIL"
    LIST_OPEN_ISSUES = "LIST_OPEN_ISSUES"
    URGENT_ISSUES = "URGENT_ISSUES"
    ISSUE_LOCATION_LIST = "ISSUE_LOCATION_LIST"
    ISSUE_STATUS_UPDATE_HELP = "ISSUE_STATUS_UPDATE_HELP"
    TRACKER_VIEW = "TRACKER_VIEW"
    DASHBOARD_OVERVIEW = "DASHBOARD_OVERVIEW"
    PROJECT_METRICS = "PROJECT_METRICS"
    COMPANY_METRICS = "COMPANY_METRICS"
    RISK_SUMMARY = "RISK_SUMMARY"
    UPCOMING_INSPECTIONS = "UPCOMING_INSPECTIONS"
    INGESTION_STATUS = "INGESTION_STATUS"
    SCHEMA_OR_CAPABILITY = "SCHEMA_OR_CAPABILITY"
    GENERAL_AGENT_QUERY = "GENERAL_AGENT_QUERY"


INTENT_TOOL_MAP = {
    AgentIntent.REPORT_SUMMARY: ["summarize_reports"],
    AgentIntent.REPORT_DETAIL: ["get_report_detail", "summarize_reports"],
    AgentIntent.LIST_OPEN_ISSUES: ["list_open_issues"],
    AgentIntent.URGENT_ISSUES: ["list_open_issues", "get_tracker_state"],
    AgentIntent.ISSUE_LOCATION_LIST: ["list_open_issues"],
    AgentIntent.ISSUE_STATUS_UPDATE_HELP: ["get_tracker_state"],
    AgentIntent.TRACKER_VIEW: ["get_tracker_state"],
    AgentIntent.DASHBOARD_OVERVIEW: ["get_dashboard_metrics"],
    AgentIntent.PROJECT_METRICS: ["get_project_metrics"],
    AgentIntent.COMPANY_METRICS: ["get_company_metrics"],
    AgentIntent.RISK_SUMMARY: ["get_risk_summary"],
    AgentIntent.UPCOMING_INSPECTIONS: ["get_upcoming_risk"],
    AgentIntent.INGESTION_STATUS: ["get_ingestion_jobs"],
    AgentIntent.SCHEMA_OR_CAPABILITY: ["get_backend_catalog"],
}


class SoterraAgentService:
    def __init__(self, repository: RepositoryBackend) -> None:
        self.repository = repository

    def status(self) -> dict:
        enabled = os.getenv("SOTERRA_AGENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        provider = os.getenv("SOTERRA_AGENT_PROVIDER", _default_agent_provider()).strip().lower()
        model_id = self._default_model_id(provider)
        configured = False
        if provider in {"huggingface", "hf_inference", "huggingface_inference"}:
            configured = bool(os.getenv("HF_TOKEN"))
        elif provider in {"local_transformers", "transformers_local", "local-hf", "local_hf"}:
            configured = bool(model_id)
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
        session_id: str | None = None,
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

        session = self._get_or_create_session(tenant_id=tenant_id, user_id=user_id, session_id=session_id, message=message)
        history = self.repository.list_agent_chat_messages(tenant_id=tenant_id, user_id=user_id, session_id=session.id, limit=24)
        self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="user", content=message)

        tools = build_soterra_tools(self.repository, tenant_id, record_tool, role=role)
        intent = classify_intent(message, page_context=page_context, history=history, report_id=report_id, issue_id=issue_id, project_slug=project_slug)
        fallback_error: Exception | None = None
        try:
            if intent != AgentIntent.GENERAL_AGENT_QUERY:
                answer = self._fallback_answer(
                    message=message,
                    tenant_id=tenant_id,
                    report_id=report_id,
                    issue_id=issue_id,
                    project_slug=project_slug,
                    page_context=page_context,
                    used_tools=used_tools,
                    history=history,
                    intent=intent,
                    role=role,
                )
            else:
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
                    history=history,
                    intent=intent,
                )
                raw_answer = agent.run(task)
                answer = self._coerce_answer(raw_answer)
                if is_vague_answer(answer, intent) or self._answer_is_too_vague(answer, message):
                    answer = self._fallback_answer(
                        message=message,
                        tenant_id=tenant_id,
                        report_id=report_id,
                        issue_id=issue_id,
                        project_slug=project_slug,
                        page_context=page_context,
                        used_tools=used_tools,
                        history=history,
                        intent=intent,
                        role=role,
                    )
        except AgentDisabledError:
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
                history=history,
                intent=intent,
                role=role,
            )

        related = self._related_entities(answer, report_id=report_id, issue_id=issue_id, project_slug=project_slug)
        mode = classify_mode(message)
        structured_response = build_chat_cards(self.repository.load_snapshot(tenant_id).findings, mode=mode)
        confidence = self._confidence(used_tools, answer, report_id=report_id, issue_id=issue_id, project_slug=project_slug)
        if fallback_error and confidence == "high":
            confidence = "medium"
        self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="assistant", content=answer)
        return AgentChatResponse(
            session_id=session.id,
            answer=answer,
            used_tools=_used_tool_entries(used_tools),
            citations=self._citations(answer, used_tools),
            context={
                "tenant_scoped": True,
                "history_used": bool(history),
                "active_records_only": True,
            },
            safety={
                "tenant_id_used": tenant_id,
                "stale_records_excluded": True,
            },
            suggested_follow_ups=self._suggested_follow_ups(answer),
            related_entities=related,
            confidence=confidence,
            mode=mode,
            structured_response=structured_response,
        )

    def list_sessions(self, *, tenant_id: str, user_id: str) -> list[dict]:
        return [
            {"id": item.id, "title": item.title, "created_at": item.created_at, "updated_at": item.updated_at}
            for item in self.repository.list_agent_chat_sessions(tenant_id=tenant_id, user_id=user_id)
        ]

    def get_session(self, *, tenant_id: str, user_id: str, session_id: str) -> dict | None:
        session = self.repository.get_agent_chat_session(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
        if not session:
            return None
        messages = self.repository.list_agent_chat_messages(tenant_id=tenant_id, user_id=user_id, session_id=session_id, limit=100)
        return {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": [
                {"id": item.id, "role": item.role, "content": item.content, "created_at": item.created_at, "tool_name": item.tool_name}
                for item in messages
            ],
        }

    def delete_session(self, *, tenant_id: str, user_id: str, session_id: str) -> bool:
        return self.repository.soft_delete_agent_chat_session(tenant_id=tenant_id, user_id=user_id, session_id=session_id)

    def _ensure_enabled(self) -> None:
        enabled = os.getenv("SOTERRA_AGENT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            raise AgentDisabledError("Soterra agent chat is disabled.")

    def _get_or_create_session(self, *, tenant_id: str, user_id: str, session_id: str | None, message: str):
        if session_id:
            session = self.repository.get_agent_chat_session(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if not session:
                raise ValueError("Chat session not found for this account.")
            return session
        title = re.sub(r"\s+", " ", message).strip()[:80] or "New chat"
        return self.repository.create_agent_chat_session(tenant_id=tenant_id, user_id=user_id, title=title)

    def _answer_is_too_vague(self, answer: str, message: str) -> bool:
        lowered = answer.lower()
        vague_patterns = [
            "there are some issues",
            "the report has problems",
            "more work is needed",
            "please check the dashboard",
        ]
        if any(pattern in lowered for pattern in vague_patterns):
            return True
        if _looks_like_report_summary_question(message.lower()):
            required_any = ["project", "inspection", "report"]
            return len(answer.split()) < 35 or not any(term in lowered for term in required_any)
        return False

    def _citations(self, answer: str, used_tools: list[str]) -> list[dict]:
        citations: list[dict] = []
        if "get_reports_summary" in used_tools or "get_report_detail" in used_tools:
            citations.append({"type": "active_reports", "label": "Active report summaries"})
        if "get_tracker_summary" in used_tools or "get_issue_analytics" in used_tools:
            citations.append({"type": "active_findings", "label": "Active issue tracker"})
        if any(tool in used_tools for tool in ["get_dashboard_summary", "get_project_metrics", "get_dashboard_risk", "get_inspection_risk"]):
            citations.append({"type": "dashboard_metrics", "label": "Current dashboard metrics"})
        return citations

    def _suggested_follow_ups(self, answer: str) -> list[str]:
        lowered = answer.lower()
        if "fire" in lowered:
            return ["Show open issues by trade", "Create tracker priorities", "Summarize fire stopping risks"]
        if "dashboard" in lowered:
            return ["Show open issues by trade", "Explain project risk", "List aging issues"]
        return ["Show open issues by trade", "Create tracker priorities", "Summarize project risks"]

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
        provider = os.getenv("SOTERRA_AGENT_PROVIDER", _default_agent_provider()).strip().lower()
        model_id = self._default_model_id(provider)
        try:
            temperature = float(os.getenv("SOTERRA_AGENT_TEMPERATURE", "0.2"))
        except ValueError:
            temperature = 0.2

        if provider in {"huggingface", "hf_inference", "huggingface_inference"}:
            api_key = os.getenv("HF_TOKEN")
            if not api_key:
                raise AgentConfigurationError("HF_TOKEN is required for Hugging Face Soterra agent chat.")
            from .huggingface_inference import HuggingFaceInferenceChatModel

            try:
                max_tokens = int(os.getenv("SOTERRA_AGENT_MAX_TOKENS", "1200"))
            except ValueError:
                max_tokens = 1200
            return HuggingFaceInferenceChatModel(
                model_id=model_id,
                api_key=api_key,
                timeout_seconds=int(os.getenv("SOTERRA_AGENT_TIMEOUT_SECONDS", "90")),
                max_tokens=max_tokens,
                temperature=temperature,
            )

        if provider in {"local_transformers", "transformers_local", "local-hf", "local_hf"}:
            from .direct_transformers import DirectTransformersChatModel
            try:
                max_tokens = int(os.getenv("SOTERRA_AGENT_MAX_TOKENS", os.getenv("SOTERRA_LOCAL_MODEL_MAX_NEW_TOKENS", "1200")))
            except ValueError:
                max_tokens = 1200
            return DirectTransformersChatModel(
                model_id=model_id,
                device_map=os.getenv("SOTERRA_LOCAL_MODEL_DEVICE_MAP", "auto"),
                torch_dtype=os.getenv("SOTERRA_LOCAL_MODEL_TORCH_DTYPE") or None,
                trust_remote_code=os.getenv("SOTERRA_LOCAL_MODEL_TRUST_REMOTE_CODE", "true").strip().lower()
                in {"1", "true", "yes", "on"},
                max_new_tokens=max_tokens,
                temperature=temperature,
            )

        raise AgentConfigurationError("Unsupported Soterra agent model provider.")

    def _default_model_id(self, provider: str) -> str:
        configured = os.getenv("SOTERRA_AGENT_MODEL_ID")
        if configured:
            return configured
        if provider in {"huggingface", "hf_inference", "huggingface_inference"}:
            return DEFAULT_REMOTE_MODEL_ID if os.getenv("VERCEL") else DEFAULT_MODEL_ID
        if provider in {
            "local_transformers",
            "transformers_local",
            "local-hf",
            "local_hf",
        }:
            return DEFAULT_MODEL_ID
        return DEFAULT_MODEL_ID

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
        history: list[Any] | None = None,
        intent: AgentIntent | None = None,
        role: str = "tenant_admin",
    ) -> str:
        tools = {
            tool.name: tool
            for tool in build_soterra_tools(
                self.repository,
                tenant_id,
                lambda name: used_tools.append(name) if name not in used_tools else None,
                role=role,
            )
        }
        normalized = f"{page_context or ''} {message}".lower()
        intent = intent or classify_intent(message, page_context=page_context, history=history, report_id=report_id, issue_id=issue_id, project_slug=project_slug)

        if _asks_to_bypass_scope(normalized):
            tools["get_backend_catalog"].forward(tenant_id)
            return "I can only access active records available to your current account. I cannot bypass tenant or deleted-record protections."
        if _is_off_domain_question(normalized):
            return _answer_off_domain()
        inferred_project = project_slug or _infer_project_slug_from_history(history)
        if classify_mode(message) == "evidence_mode":
            payload = tools["get_work_packages"].forward(tenant_id)
            return build_evidence_answer(payload)
        if "today" in normalized and "fix" in normalized:
            payload = tools["get_todays_fix_list"].forward(tenant_id)
            return build_todays_fix_list_answer(payload)
        if intent in {AgentIntent.LIST_OPEN_ISSUES, AgentIntent.URGENT_ISSUES, AgentIntent.ISSUE_LOCATION_LIST}:
            payload = tools["list_open_issues"].forward(tenant_id, inferred_project)
            if intent == AgentIntent.URGENT_ISSUES:
                tools["get_tracker_state"].forward(tenant_id)
            return build_issue_table_answer(payload, include_full_register=classify_mode(message) == "full_register_mode")
        if intent == AgentIntent.ISSUE_STATUS_UPDATE_HELP and _asks_issue_due_question(normalized, history):
            payload = tools["list_open_issues"].forward(tenant_id, inferred_project)
            return build_issue_due_answer(payload)
        if intent in {AgentIntent.TRACKER_VIEW, AgentIntent.ISSUE_STATUS_UPDATE_HELP}:
            payload = tools["get_tracker_state"].forward(tenant_id)
            return build_tracker_answer(payload)
        if intent == AgentIntent.DASHBOARD_OVERVIEW:
            payload = tools["get_dashboard_metrics"].forward(tenant_id)
            return build_dashboard_answer(payload)
        if intent == AgentIntent.RISK_SUMMARY:
            payload = tools["get_risk_summary"].forward(tenant_id)
            return build_risk_answer(payload)
        if intent == AgentIntent.UPCOMING_INSPECTIONS:
            payload = tools["get_upcoming_risk"].forward(tenant_id)
            return _answer_from_upcoming_risk(payload)
        if intent == AgentIntent.INGESTION_STATUS:
            payload = tools["get_ingestion_jobs"].forward(tenant_id)
            return build_ingestion_status_answer(payload)
        if intent == AgentIntent.SCHEMA_OR_CAPABILITY:
            payload = tools["get_backend_catalog"].forward(tenant_id)
            return _answer_from_schema_catalog(payload)
        if intent == AgentIntent.REPORT_SUMMARY:
            payload = tools["summarize_reports"].forward(tenant_id)
            return build_report_summary_answer(payload, normalized, inferred_project)
        if report_id:
            payload = tools["get_report_detail"].forward(tenant_id, report_id)
            return _answer_from_report_detail(payload)
        if issue_id:
            payload = tools["get_issue_detail"].forward(tenant_id, issue_id)
            return _answer_from_issue_detail(payload)
        if inferred_project and any(term in normalized for term in ["metric", "project", "risk", "previous", "contractor", "fix first", "top 3", "dashboard", "tracker"]):
            reports_payload = tools["get_reports_summary"].forward(tenant_id)
            if any(term in normalized for term in ["contractor", "fix first", "top 3", "priority", "prioritise", "prioritize"]):
                return _answer_priorities_from_reports(reports_payload, inferred_project)
            if "dashboard" in normalized:
                dashboard_payload = tools["get_dashboard_summary"].forward(tenant_id)
                return _answer_dashboard_linkage(reports_payload, dashboard_payload, inferred_project)
            if "tracker" in normalized:
                tracker_payload = tools["get_tracker_summary"].forward(tenant_id)
                return _answer_tracker_linkage(tracker_payload, inferred_project)
            if project_slug:
                payload = tools["get_project_metrics"].forward(tenant_id, project_slug)
                return _answer_from_project_metrics(payload)
        if any(term in normalized for term in ["why", "failed", "fail"]) and "report" in normalized:
            payload = tools["get_reports_summary"].forward(tenant_id)
            return _answer_failed_report(payload, normalized, inferred_project)
        if any(term in normalized for term in ["coordination", "services", "mechanical", "plumbing", "electrical", "data"]):
            payload = tools["get_reports_summary"].forward(tenant_id)
            return _answer_services_coordination(payload, inferred_project)
        if any(term in normalized for term in ["passive fire", "fire stopping", "fire close", "fire issues"]):
            payload = tools["get_reports_summary"].forward(tenant_id)
            return _answer_fire_stopping(payload, inferred_project)
        if any(term in normalized for term in ["dashboard", "company performance", "close-out rate", "close out rate"]):
            reports_payload = tools["get_reports_summary"].forward(tenant_id)
            dashboard_payload = tools["get_dashboard_summary"].forward(tenant_id)
            return _answer_dashboard_linkage(reports_payload, dashboard_payload, inferred_project)
        if _looks_like_report_summary_question(normalized):
            payload = tools["get_reports_summary"].forward(tenant_id)
            return _answer_project_reports(payload, normalized, inferred_project)
        if any(term in normalized for term in ["tracker", "what needs fixing", "open defects"]):
            payload = tools["get_tracker_summary"].forward(tenant_id)
            return _answer_tracker_linkage(payload, inferred_project)
        if project_slug:
            payload = tools["get_project_metrics"].forward(tenant_id, project_slug)
            return _answer_from_project_metrics(payload)
        if any(term in normalized for term in ["schema", "catalog", "database", "table", "field", "coverage", "data source", "route", "endpoint"]):
            payload = tools["get_backend_catalog"].forward(tenant_id)
            return _answer_from_schema_catalog(payload)
        if any(term in normalized for term in ["member", "members", "user", "users", "admin", "role", "team", "access"]):
            payload = tools["get_tenant_members"].forward(tenant_id)
            return _answer_from_tenant_members(payload)
        if any(term in normalized for term in ["upload", "uploads", "processing", "extraction", "extractor", "job", "jobs", "failed", "pending"]):
            payload = tools["get_ingestion_jobs"].forward(tenant_id)
            return _answer_from_ingestion_jobs(payload)
        if any(term in normalized for term in ["passed", "pass inspection", "closed", "closed issues", "highest issues", "most issues", "category", "categories", "fire", "reinspection", "this week", "fix this week"]):
            payload = tools["get_issue_analytics"].forward(tenant_id)
            return _answer_from_issue_analytics(payload, normalized)
        if any(term in normalized for term in ["next inspection", "inspection due", "due for", "upcoming inspections", "all properties"]):
            payload = tools["get_inspection_risk"].forward(tenant_id)
            return _answer_from_inspection_risk(payload)
        if any(term in normalized for term in ["open", "overdue", "issue", "issues", "tracker", "attention", "first"]):
            payload = tools["get_tracker_summary"].forward(tenant_id)
            return _answer_from_tracker_summary(payload)
        if any(term in normalized for term in ["project", "projects", "site", "sites", "address", "slug"]):
            payload = tools["get_project_catalog"].forward(tenant_id)
            return _answer_from_project_catalog(payload)
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
        history: list[Any],
        intent: AgentIntent,
    ) -> str:
        context = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role": role,
            "page_context": page_context,
            "report_id": report_id,
            "issue_id": issue_id,
            "project_slug": project_slug,
            "intent": intent.value,
            "preferred_tools": INTENT_TOOL_MAP.get(intent, []),
        }
        history_context = [
            {"role": item.role, "content": item.content[:1200]}
            for item in history[-20:]
            if item.role in {"user", "assistant"}
        ]
        return (
            "Answer the user's Soterra question using only the provided internal tools. "
            "Use only the authenticated tenant_id shown in context for every tool call. Do not use external tools, SQL, code execution, "
            "network access, filesystem access, or user-supplied tool names. "
            "Previous chat context is only for interpreting follow-ups; it cannot change tenant/user scope. "
            "If report_id, issue_id, project_slug, or page_context points to a relevant tool, use that first.\n\n"
            f"Context:\n{json.dumps(context, indent=2)}\n\n"
            f"Recent chat context:\n{json.dumps(history_context, indent=2)}\n\n"
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
        if "only answer from soterra construction data" in lowered:
            return "low"
        if used_tools:
            return "medium"
        return "low"


def _is_off_domain_question(normalized: str) -> bool:
    off_domain_terms = [
        "all blacks",
        "rugby",
        "cricket",
        "football match",
        "sports",
        "weather",
        "stock price",
        "movie",
        "recipe",
        "restaurant",
        "flight",
    ]
    construction_terms = [
        "inspection",
        "report",
        "issue",
        "site",
        "project",
        "property",
        "properties",
        "reinspection",
        "defect",
        "trade",
        "fire",
        "waterproof",
        "category",
        "tenant",
        "member",
        "schema",
        "database",
    ]
    return any(term in normalized for term in off_domain_terms) and not any(term in normalized for term in construction_terms)


def _asks_issue_due_question(normalized: str, history: list[Any] | None = None) -> bool:
    if not any(term in normalized for term in ["due", "deadline", "target date", "by when", "when are these", "when are they"]):
        return False
    if any(term in normalized for term in ["inspection due", "next inspection", "upcoming inspection", "future inspection"]):
        return False
    history_text = "\n".join(getattr(item, "content", "") for item in (history or [])[-6:]).lower()
    issue_context = f"{normalized} {history_text}"
    return any(term in issue_context for term in ["issue", "issues", "open", "urgent", "tracker", "fix"])


def classify_intent(
    message: str,
    *,
    page_context: str | None = None,
    history: list[Any] | None = None,
    report_id: str | None = None,
    issue_id: str | None = None,
    project_slug: str | None = None,
) -> AgentIntent:
    normalized = f"{page_context or ''} {message}".lower()
    history_text = "\n".join(getattr(item, "content", "") for item in (history or [])[-6:]).lower()
    if _asks_to_bypass_scope(normalized):
        return AgentIntent.SCHEMA_OR_CAPABILITY
    if issue_id:
        return AgentIntent.ISSUE_STATUS_UPDATE_HELP
    if report_id:
        return AgentIntent.REPORT_DETAIL
    if any(term in normalized for term in ["schema", "catalog", "capability", "can you access", "what data", "backend"]):
        return AgentIntent.SCHEMA_OR_CAPABILITY
    if any(term in normalized for term in ["processing", "extract", "extraction", "ingestion", "upload status", "still processing", "not appearing", "jobs"]):
        return AgentIntent.INGESTION_STATUS
    if any(term in normalized for term in ["upcoming inspection", "next inspection", "future inspection", "inspection due"]):
        return AgentIntent.UPCOMING_INSPECTIONS
    if _asks_issue_due_question(normalized, history):
        return AgentIntent.ISSUE_STATUS_UPDATE_HELP
    if any(term in normalized for term in ["highest risk", "risk", "risky", "reinspection risk"]):
        return AgentIntent.RISK_SUMMARY
    if any(term in normalized for term in ["dashboard", "company performance", "close-out rate", "close out rate", "metrics", "overview"]):
        return AgentIntent.DASHBOARD_OVERVIEW
    if any(term in normalized for term in ["tracker", "last sent", "reinspection count", "closure", "closed at"]):
        return AgentIntent.TRACKER_VIEW
    if any(term in normalized for term in ["where are", "location", "locations", "where is", "list them"]) and any(term in f"{normalized} {history_text}" for term in ["issue", "issues", "urgent", "open", "fix"]):
        return AgentIntent.ISSUE_LOCATION_LIST
    if any(term in normalized for term in ["urgent", "high priority", "fix first", "what should i fix first", "needs fixing", "first"]):
        return AgentIntent.URGENT_ISSUES
    if any(term in normalized for term in ["open issue", "open issues", "defects", "issue list", "list of open", "what to fix"]):
        return AgentIntent.LIST_OPEN_ISSUES
    if any(term in normalized for term in ["why did", "failed", "fail"]) and any(term in normalized for term in ["report", "inspection", "council"]):
        return AgentIntent.REPORT_DETAIL
    if _looks_like_report_summary_question(normalized):
        return AgentIntent.REPORT_SUMMARY
    if project_slug and any(term in normalized for term in ["project", "metrics", "performance"]):
        return AgentIntent.PROJECT_METRICS
    return AgentIntent.GENERAL_AGENT_QUERY


def classify_mode(message: str) -> str:
    normalized = message.lower()
    if any(term in normalized for term in ["show all", "full list", "full register", "every issue"]):
        return "full_register_mode"
    if any(term in normalized for term in ["evidence", "photos", "sign-off", "sign off", "documents needed"]):
        return "evidence_mode"
    if any(term in normalized for term in ["by trade", "responsible trade", "which trade"]):
        return "trade_mode"
    if any(term in normalized for term in ["by location", "by level", "by unit", "which area"]):
        return "location_mode"
    if any(term in normalized for term in ["risk", "block inspection", "block handover"]):
        return "risk_mode"
    if any(term in normalized for term in ["fix first", "today", "action plan", "priority"]):
        return "action_plan_mode"
    return "summary_mode"


def is_vague_answer(answer: str, intent: AgentIntent) -> bool:
    lowered = answer.lower()
    vague_phrases = [
        "some issues",
        "several issues",
        "main issues",
        "there are issues",
        "refer to the tracker",
        "close out failed items",
        "assign services coordination items",
        "review the reports",
    ]
    if intent in {AgentIntent.LIST_OPEN_ISSUES, AgentIntent.URGENT_ISSUES, AgentIntent.ISSUE_LOCATION_LIST}:
        required_terms = ["location", "priority", "recommended action"]
        if not all(term in lowered for term in required_terms):
            return True
        if "|" not in answer and "- " not in answer:
            return True
    return any(phrase in lowered for phrase in vague_phrases)


def _answer_off_domain() -> str:
    return (
        "I can only answer from Soterra construction data: inspections, reports, issues, projects, sites, members, and backend data. "
        "Ask me a construction, inspection, report, or issue question and I can help quickly."
    )


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


def _answer_from_schema_catalog(payload: dict) -> str:
    if "reports" in payload and "issues" in payload:
        return (
            "I can answer from Soterra backend domains including reports, issues, tracker, dashboard, risk, jobs, and members. "
            "For safety, I only use active records available to your current account and never expose passwords, token hashes, raw storage paths, deleted records, or cross-tenant data."
        )
    if payload.get("available_data_domains"):
        domains = ", ".join(payload.get("available_data_domains", [])[:8])
        return (
            f"I can answer from these active tenant-scoped Soterra data areas: {domains}. "
            "I cannot access password hashes, token hashes, raw storage paths, deleted records, or cross-tenant data."
        )
    tables = payload.get("tables") or []
    views = payload.get("analyticsViews") or []
    if not tables:
        return "I could not load the backend data map clearly enough to answer that."
    covered = ", ".join(str(item.get("table")) for item in tables[:9])
    return (
        f"The agent has a data map for these backend tables: {covered}. "
        f"It also understands {len(views)} analytics views. Sensitive auth data is schema-aware only, so I can explain what it does without exposing hashes, tokens, or private storage paths."
    )


def _answer_from_tenant_members(payload: dict) -> str:
    members = payload.get("items") or []
    if not members:
        return "I could not find tenant members in the current Soterra data."
    admins = [item for item in members if item.get("role") == "admin"]
    return f"The tenant has {len(members)} member account(s), including {len(admins)} admin(s). Admins: {', '.join(item.get('name') or item.get('email') for item in admins) or 'none listed'}."


def _answer_from_project_catalog(payload: dict) -> str:
    projects = payload.get("items") or []
    if not projects:
        return "I could not find any projects in the current Soterra data."
    lines = [f"I found {len(projects)} project(s):"]
    for project in projects[:5]:
        lines.append(
            f"- {project.get('name')} at {project.get('site')}: {project.get('reportCount', 0)} reports and {project.get('issueCount', 0)} issues."
        )
    return "\n".join(lines)


def _answer_from_ingestion_jobs(payload: dict) -> str:
    jobs = payload.get("items") or []
    if not jobs:
        return "I could not find any report ingestion or extraction jobs in the current Soterra data."
    breakdown = payload.get("statusBreakdown") or {}
    lines = [f"I found {payload.get('count', len(jobs))} ingestion job(s). Status breakdown: {breakdown}."]
    for job in jobs[:5]:
        lines.append(
            f"- {job.get('reportName') or job.get('documentId')}: {job.get('status', 'unknown')} via {job.get('extractor', 'unknown extractor')}."
        )
    return "\n".join(lines)


def _answer_from_issue_analytics(payload: dict, normalized: str) -> str:
    if payload.get("error"):
        return "I could not load enough issue analytics to answer that clearly."
    if "passed" in normalized or "pass inspection" in normalized:
        sites = payload.get("passedSites") or []
        if not sites:
            return "I do not see any sites that clearly passed inspection with all reports completed and no open issues."
        lines = ["Sites that look passed based on completed reports and no open issues:"]
        for site in sites[:6]:
            lines.append(f"- {site.get('site')}: {site.get('reports', 0)} completed reports, {site.get('closedIssues', 0)} closed issues.")
        return "\n".join(lines)
    if "closed" in normalized:
        closed = payload.get("closedProjectIssues") or []
        if not closed:
            return "I do not see any closed project issues in the current Soterra data."
        lines = ["Recently closed project issues:"]
        for issue in closed[:6]:
            lines.append(f"- {issue.get('title')}: {issue.get('project')} / {issue.get('site')}, {issue.get('trade')} trade.")
        return "\n".join(lines)
    if "category" in normalized or "categories" in normalized or "fire" in normalized:
        categories = payload.get("categoryBreakdown") or []
        if not categories:
            return "I could not find issue categories in the current Soterra data."
        top = categories[0]
        lines = [f"The biggest issue category is {top.get('category')} with {top.get('issueCount')} issue(s)."]
        for category in categories[1:6]:
            lines.append(f"- {category.get('category')}: {category.get('issueCount')} issue(s).")
        return "\n".join(lines)
    if "highest issues" in normalized or "most issues" in normalized or ("site" in normalized and "issue" in normalized):
        sites = payload.get("topSitesByIssueCount") or []
        causes = payload.get("categoryBreakdown") or []
        if not sites:
            return "I could not find site-level issue counts in the current Soterra data."
        top_site = sites[0]
        cause_text = ", ".join(f"{item.get('category')} ({item.get('issueCount')})" for item in causes[:3]) or "no clear category causes listed"
        return f"{top_site.get('site')} has the highest issue count with {top_site.get('issueCount')} issue(s). Likely cause areas across the data are: {cause_text}. Suggested next action: review the top category items first and confirm ownership by trade."
    if "reinspection" in normalized or "repeat" in normalized or "root cause" in normalized or "repitative" in normalized or "repetitive" in normalized:
        causes = payload.get("reinspectionRootCauses") or []
        if not causes:
            return "I could not find repeat or reinspection-linked issue causes in the current Soterra data."
        lines = ["Repeat issues most likely to drive reinspection:"]
        for issue in causes[:6]:
            lines.append(
                f"- {issue.get('title')}: {issue.get('severity', 'Unknown')} severity, {issue.get('trade')} trade, {issue.get('project')}."
            )
        lines.append("Suggested next action: close these before booking reinspection and capture evidence for each trade.")
        return "\n".join(lines)

    open_items = payload.get("openHighPriorityThisWeek") or []
    if not open_items:
        return "I do not see open high-priority issues that need fixing this week in the current Soterra data."
    lines = ["Open issues to fix this week, ordered by priority:"]
    for issue in open_items[:6]:
        lines.append(f"- {issue.get('title')}: {issue.get('severity', 'Unknown')} severity, {issue.get('project')} / {issue.get('site')}, {issue.get('trade')} trade.")
    return "\n".join(lines)


def _answer_from_inspection_risk(payload: dict) -> str:
    inspections = payload.get("upcomingInspections") or []
    failures = payload.get("likelyFailureItems") or []
    if not inspections:
        return "I do not see upcoming inspections in the current Soterra data."
    lines = ["Upcoming inspections across properties:"]
    for inspection in inspections[:8]:
        lines.append(
            f"- {inspection.get('site')}: {inspection.get('type')} due {inspection.get('expectedDate')} ({inspection.get('riskLevel')} risk)."
        )
    if failures:
        lines.append("High-priority issues to watch before those inspections:")
        for failure in failures[:5]:
            lines.append(f"- {failure.get('issue')}: seen {failure.get('historicalFailCount')} time(s), {failure.get('failureShare')}% failure share.")
    return "\n".join(lines)


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


def build_issue_table_answer(payload: dict, *, include_full_register: bool = False) -> str:
    issues = payload.get("issues") or []
    project = payload.get("project_name") or "this account"
    address = payload.get("project_address") or "address not specified"
    total_open = payload.get("total_open", len(issues))
    high = payload.get("high_priority_open", 0)
    overdue = payload.get("overdue_open", 0)
    if not issues:
        return f"No open issues are available for {project} in the active records for your current account."
    packages = build_work_packages(issues, limit=3)
    lines = [
        f"Yes - I found {total_open} open issues for {project} at {address}. {high} are high priority and {overdue} are overdue.",
        "",
        "Main work packages:",
    ]
    for index, package in enumerate(packages, start=1):
        lines.extend(
            [
                f"{index}. {package['group_title']}",
                f"   Trade: {package['trade']}",
                f"   Includes: {package['summary']}",
                f"   Fix: {package['recommended_action']}",
            ]
        )
    lines.extend(["", "Issue examples (up to 5):", "| Priority | Issue | Location | Trade | Source | Recommended action |", "|---|---|---|---|---|---|"])
    issue_limit = len(issues) if include_full_register else 5
    for issue in issues[:issue_limit]:
        lines.append(
            "| {priority} | {title} | {location} | {trade} | {source} | {action} |".format(
                priority=_cell(issue.get("priority") or issue.get("severity")),
                title=_cell(issue.get("title")),
                location=_cell(issue.get("location")),
                trade=_cell(issue.get("trade") or issue.get("category")),
                source=_cell(issue.get("source") or f"{issue.get('source_report')}, {issue.get('source_date')}"),
                action=_cell(issue.get("recommended_action")),
            )
        )
    remaining = safe_int(payload.get("remaining_count"))
    if not include_full_register and (remaining or len(issues) > 5):
        lines.append(f"\nShowing {min(len(issues), 5)} issue examples; ask for the full register to see all {total_open}.")
    lines.extend(_fix_order_from_issues(issues))
    return "\n".join(lines)


def build_todays_fix_list_answer(payload: dict) -> str:
    items = payload.get("items") or []
    if not items:
        return "There are no open issues for today's fix list."
    lines = ["Today's Fix List:"]
    for item in items[:5]:
        lines.extend(
            [
                f"{item.get('priority')}. {item.get('trade')} - {item.get('location')}",
                f"   Task: {item.get('task')}",
                f"   Evidence: {', '.join(item.get('evidence_required') or [])}",
            ]
        )
    return "\n".join(lines)


def build_evidence_answer(payload: dict) -> str:
    packages = payload.get("items") or []
    if not packages:
        return "I do not see open issues that need close-out evidence."
    lines = ["Evidence needed for close-out:"]
    for package in packages[:5]:
        lines.append(f"- {package.get('group_title')}: {', '.join(package.get('evidence_required') or [])}.")
    lines.append("Use labelled after photos so the reviewer can match each item to its location.")
    return "\n".join(lines)


def build_tracker_answer(payload: dict) -> str:
    issues = [item for item in payload.get("issues", []) if item.get("status") == "Open"]
    themes = _issue_theme_summary(issues)
    lines = [
        f"The tracker has {payload.get('total_issues', 0)} issues: {payload.get('open', 0)} open and {payload.get('closed', 0)} closed.",
        f"By trade: {payload.get('by_trade', {})}.",
    ]
    if themes:
        lines.append(f"Main extracted themes: {themes}.")
    lines.append("")
    return "\n".join(lines) + build_issue_table_answer(
        {
            "project_name": "tracked projects",
            "project_address": "active account",
            "total_open": payload.get("open", 0),
            "high_priority_open": len([item for item in issues if item.get("priority") in {"High", "Critical"}]),
            "overdue_open": 0,
            "issues": issues,
        }
    )


def build_issue_due_answer(payload: dict) -> str:
    issues = payload.get("issues") or []
    project = payload.get("project_name") or "this account"
    address = payload.get("project_address") or "address not specified"
    total_open = payload.get("total_open", len(issues))
    high = payload.get("high_priority_open", 0)
    overdue = payload.get("overdue_open", 0)
    if not issues:
        return f"No open issues are available for {project} in the active records for your current account."

    dated = [issue for issue in issues if issue.get("due_date") or issue.get("dueDate")]
    if dated:
        opening = f"I found due dates for {len(dated)} of {total_open} open issues for {project} at {address}. {high} are high priority and {overdue} are overdue."
    else:
        opening = (
            f"I do not see due dates stored on these active tracker issues yet. "
            f"There are {total_open} open issues for {project} at {address}; {high} are high priority and {overdue} are overdue."
        )

    lines = [
        opening,
        "",
        "| Priority | Issue | Location | Due date | Source | Recommended action |",
        "|---|---|---|---|---|---|",
    ]
    for issue in issues[:10]:
        due_date = issue.get("due_date") or issue.get("dueDate") or "Not set"
        lines.append(
            "| {priority} | {title} | {location} | {due_date} | {source} | {action} |".format(
                priority=_cell(issue.get("priority") or issue.get("severity")),
                title=_cell(issue.get("title")),
                location=_cell(issue.get("location")),
                due_date=_cell(due_date),
                source=_cell(issue.get("source") or f"{issue.get('source_report')}, {issue.get('source_date')}"),
                action=_cell(issue.get("recommended_action")),
            )
        )
    remaining = safe_int(payload.get("remaining_count"))
    if remaining:
        lines.append(f"\nShowing the first {min(len(issues), 10)} issues; {remaining} more remain in the tracker.")
    lines.extend(_fix_order_from_issues(issues, heading="Suggested due order:"))
    return "\n".join(lines)


def build_report_summary_answer(payload: dict, normalized: str = "", project_slug: str | None = None) -> str:
    reports = payload.get("reports") or payload.get("items") or []
    if project_slug:
        reports = [item for item in reports if item.get("project_slug") == project_slug]
    if "kauri" in normalized:
        reports = [item for item in reports if "kauri" in str(item.get("project_name", "")).lower()]
    if not reports:
        return "No matching active reports are available for your current account."
    project = reports[0].get("project_name") or "this project"
    lines = [f"I found {len(reports)} active reports for {project}."]
    for index, report in enumerate(reports[:8], start=1):
        failed = report.get("failed_items") or []
        failed_text = ", ".join(str(item.get("item") or item.get("title") or item) for item in failed[:5]) or report.get("summary") or "No extracted failed items listed"
        lines.append(
            f"{index}. {report.get('report_title') or report.get('inspection_type')} - {report.get('overall_outcome', 'Unknown')}\n"
            f"   Trade/area: {report.get('trade') or report.get('category') or report.get('inspection_type') or 'Not specified'}.\n"
            f"   Main findings: {failed_text}.\n"
            f"   Report context: {report.get('summary') or 'No extracted summary available'}.\n"
            f"   Open issues: {report.get('open_findings_count', 0)}.\n"
            "   Recommended action: Assign the open findings to the responsible trades and collect close-out evidence."
        )
    lines.append("\nOverall priority: close high-priority open findings before reinspection or site meeting.")
    return "\n".join(lines)


def build_dashboard_answer(payload: dict) -> str:
    drivers = payload.get("top_failure_drivers") or []
    close = payload.get("close_out_performance") or {}
    return (
        "The dashboard should show:\n\n"
        f"- Open issue count: {payload.get('open_issue_count', 0)}\n"
        f"- High-priority issue count: {payload.get('high_priority_open', 0)}\n"
        f"- Overdue issue count: {payload.get('overdue_count', 0)}\n"
        f"- Top failure drivers: {', '.join(map(str, drivers)) or 'None identified'}\n"
        f"- Project count: {payload.get('project_count', 0)}\n"
        f"- Report count: {payload.get('report_count', 0)}\n"
        f"- Failed inspection count: {payload.get('failed_inspection_count', 'see failed report outcomes')}\n"
        f"- Project risk: driven by {payload.get('high_priority_open', 0)} high-priority open issues and repeated failure drivers\n"
        f"- Close-out performance: {close.get('closed_count', 0)} closed / {close.get('open_count', 0)} open\n\n"
        "Recommended dashboard action: use open/high-priority counts and top failure drivers to assign close-out work by trade."
    )


def build_risk_answer(payload: dict) -> str:
    projects = payload.get("highest_risk_projects") or []
    drivers = payload.get("risk_drivers") or []
    actions = payload.get("recommended_actions") or []
    highest = projects[0].get("project_name") if projects else "the active project set"
    lines = [f"The highest risk area is {highest} because:"]
    for index, driver in enumerate(drivers[:5], start=1):
        lines.append(f"{index}. {driver}")
    lines.append("\nRecommended risk reduction:")
    for action in actions[:5]:
        lines.append(f"- {action}")
    return "\n".join(lines)


def build_ingestion_status_answer(payload: dict) -> str:
    jobs = payload.get("jobs") or payload.get("items") or []
    if not jobs:
        return "I could not find active report processing jobs for your current account."
    lines = ["Current report processing status:"]
    for job in jobs[:10]:
        lines.append(
            f"- {job.get('document_title') or job.get('reportName') or job.get('documentId')}: {job.get('status')} "
            f"started {job.get('started_at') or job.get('startedAt')}, completed {job.get('completed_at') or job.get('completedAt') or 'not completed yet'}."
        )
    return "\n".join(lines)


def _cell(value: Any) -> str:
    return str(value or "Not specified").replace("|", "/").strip()


def _default_agent_provider() -> str:
    return "huggingface" if os.getenv("VERCEL") else DEFAULT_MODEL_PROVIDER


def _used_tool_entries(used_tools: list[str]) -> list[dict]:
    reasons = {
        "get_schema_catalog": "Mapped the question to safe Soterra data domains",
        "get_backend_catalog": "Mapped the question to safe backend data domains",
        "summarize_reports": "User asked for project report summary",
        "list_open_issues": "User asked for open issues, urgent issues, locations, or work to fix",
        "get_tracker_state": "User asked about tracker state or issue ownership",
        "get_dashboard_metrics": "User asked about dashboard metrics",
        "get_risk_summary": "User asked about risk",
        "get_reports_summary": "User asked for project report summary",
        "get_report_detail": "User asked about a specific report",
        "get_tracker_summary": "User asked about open issues or tracker state",
        "get_dashboard_summary": "User asked about dashboard metrics",
        "get_project_metrics": "User asked about project metrics",
        "get_dashboard_risk": "User asked about risk",
        "get_inspection_risk": "User asked about upcoming inspection risk",
        "get_ingestion_jobs": "User asked about upload or extraction status",
        "get_issue_analytics": "User asked for issue patterns or close-out analytics",
    }
    return [{"name": name, "reason": reasons.get(name, "Used tenant-scoped Soterra backend data")} for name in used_tools]


def _asks_to_bypass_scope(normalized: str) -> bool:
    return any(term in normalized for term in ["ignore tenant", "all reports in the database", "every tenant", "cross tenant", "deleted kauri", "deleted files"])


def _looks_like_report_summary_question(normalized: str) -> bool:
    return any(term in normalized for term in ["summarize", "summary", "reports", "inspection reports", "uploaded pdfs", "inspection summary"]) and (
        "report" in normalized or "inspection" in normalized or "pdf" in normalized
    )


def _infer_project_slug_from_history(history: list[Any] | None) -> str | None:
    if not history:
        return None
    text = "\n".join(getattr(item, "content", "") for item in history[-10:]).lower()
    match = re.search(r"\b([a-z0-9]+(?:-[a-z0-9]+)+)\b", text)
    return match.group(1) if match else None


def _filter_reports(payload: dict, normalized: str, project_slug: str | None = None) -> list[dict]:
    items = payload.get("items") or []
    if project_slug:
        slug_words = project_slug.replace("-", " ")
        return [item for item in items if slug_words in str(item.get("project_name") or item.get("project") or "").lower() or project_slug == item.get("projectSlug")]
    if "kauri" in normalized:
        return [item for item in items if "kauri" in str(item.get("project_name") or item.get("project") or "").lower()]
    return items


def _answer_project_reports(payload: dict, normalized: str, project_slug: str | None = None) -> str:
    reports = _filter_reports(payload, normalized, project_slug)
    project_name = _project_name_from_reports(reports, normalized, project_slug)
    if not reports:
        if "kauri" in normalized or project_slug:
            return f"No {project_name or 'matching'} reports are available in the active records for your current account."
        return "I could not find active inspection reports for your current account. Upload or finish extracting a report first."
    lines = [f"For {project_name}, I found {len(reports)} active inspection report(s)."]
    for index, report in enumerate(reports[:6], start=1):
        outcome = report.get("overall_outcome") or report.get("reportStatus") or "Unknown"
        findings = report.get("failed_items") or [item.get("title") for item in report.get("top_findings", []) if item.get("title")]
        finding_text = ", ".join(map(str, findings[:6])) or report.get("summary") or "No extracted findings listed"
        lines.append(f"{index}. {report.get('inspection_type') or report.get('inspectionType') or 'Inspection report'} - {outcome}. Main issues: {finding_text}.")
    lines.append("Recommended next action: assign the listed open items to the responsible trade and attach the close-out evidence requested in the extracted findings.")
    return "\n".join(lines)


def _answer_failed_report(payload: dict, normalized: str, project_slug: str | None = None) -> str:
    reports = _filter_reports(payload, normalized, project_slug)
    failed = [item for item in reports if str(item.get("overall_outcome") or item.get("reportStatus") or "").lower() in {"fail", "failed"}]
    if not reports:
        return "That report is no longer available in the active records for your current account, so I cannot summarize stale deleted content."
    if not failed:
        failed = [item for item in reports if item.get("failed_items")]
    if not failed:
        return "I could not find an active failed report matching that question in your current account."
    report = failed[0]
    items = report.get("failed_items") or [issue.get("title") for issue in report.get("top_findings", []) if issue.get("title")]
    return (
        f"The {report.get('inspection_type') or report.get('inspectionType')} report for {report.get('project_name') or report.get('project')} failed. "
        f"Reasons include {', '.join(map(str, items[:8]))}. "
        "Recommended next action: close these failed items out with evidence before reinspection."
    )


def _answer_priorities_from_reports(payload: dict, project_slug: str | None) -> str:
    reports = _filter_reports(payload, "", project_slug)
    if not reports:
        return "I do not have active reports in this chat context for your current account. Ask me to summarize an active project first."
    issues = []
    for report in reports:
        issues.extend(report.get("failed_items") or [])
        issues.extend(item.get("title") for item in report.get("top_findings", []) if item.get("title"))
    if not issues:
        return "I found active reports, but no extracted open findings are available to rank yet."
    lines = ["Top contractor priorities from the active extracted findings:"]
    for index, issue in enumerate(issues[:3], start=1):
        title = issue.get("title") or issue.get("item") if isinstance(issue, dict) else issue
        trade = issue.get("trade") if isinstance(issue, dict) else None
        location = issue.get("location") if isinstance(issue, dict) else None
        detail = " - ".join(str(value) for value in [trade, location] if value)
        lines.append(f"{index}. {title}{f' ({detail})' if detail else ''}.")
    lines.append("Recommended next action: assign these items to owners and collect the evidence listed on each issue.")
    return "\n".join(lines)


def _answer_services_coordination(payload: dict, project_slug: str | None) -> str:
    reports = _filter_reports_by_terms(payload, ["services", "mechanical", "plumbing", "electrical", "data"], project_slug)
    if not reports:
        return "I could not find active extracted services, mechanical, plumbing, electrical, or data findings for your current account."
    return _answer_project_reports({"items": reports}, "services", project_slug)


def _answer_fire_stopping(payload: dict, project_slug: str | None) -> str:
    reports = _filter_reports_by_terms(payload, ["fire", "passive fire", "fire stopping"], project_slug)
    if not reports:
        return "I could not find active extracted fire-stopping findings for your current account."
    return _answer_project_reports({"items": reports}, "fire", project_slug)


def _answer_dashboard_linkage(reports_payload: dict, dashboard_payload: dict, project_slug: str | None) -> str:
    reports = _filter_reports(reports_payload, "", project_slug)
    if not reports:
        return "No matching active reports are available for your current account, so I cannot map them to dashboard items."
    return _answer_from_dashboard_summary(dashboard_payload)


def _answer_tracker_linkage(payload: dict, project_slug: str | None) -> str:
    issues = payload.get("issues") or []
    if project_slug:
        issues = [item for item in issues if item.get("projectSlug") == project_slug or project_slug.replace("-", " ") in str(item.get("project", "")).lower()]
    if not issues:
        return "I could not find actionable open tracker issues for that project in your current account."
    return build_issue_table_answer(
        {
            "project_name": project_slug.replace("-", " ").title() if project_slug else "active projects",
            "project_address": "active account",
            "total_open": len(issues),
            "high_priority_open": len([item for item in issues if item.get("priority") in {"High", "Critical"}]),
            "overdue_open": len([item for item in issues if item.get("overdue")]),
            "issues": issues,
        }
    )


def _project_name_from_reports(reports: list[dict], normalized: str, project_slug: str | None) -> str:
    if reports:
        return str(reports[0].get("project_name") or reports[0].get("project") or "this project")
    if project_slug:
        return project_slug.replace("-", " ").title()
    return "this project"


def _filter_reports_by_terms(payload: dict, terms: list[str], project_slug: str | None = None) -> list[dict]:
    reports = _filter_reports(payload, "", project_slug)
    lowered_terms = [term.lower() for term in terms]
    matched = []
    for report in reports:
        failed_items = report.get("failed_items") or []
        top_findings = report.get("top_findings") or []
        haystack = " ".join(
            str(value or "")
            for value in [
                report.get("inspection_type"),
                report.get("inspectionType"),
                report.get("trade"),
                report.get("summary"),
                report.get("report_title"),
                report.get("reportTitle"),
                " ".join(_finding_text(item) for item in failed_items),
                " ".join(_finding_text(item) for item in top_findings),
            ]
        ).lower()
        if any(term in haystack for term in lowered_terms):
            matched.append(report)
    return matched


def _fix_order_from_issues(issues: list[dict], *, heading: str = "Suggested fix order:") -> list[str]:
    if not issues:
        return []
    prioritized = sorted(
        issues,
        key=lambda item: (
            _priority_rank(str(item.get("priority") or item.get("severity") or "")),
            str(item.get("trade") or item.get("category") or ""),
            str(item.get("location") or ""),
        ),
    )
    lines = ["", heading]
    for index, issue in enumerate(prioritized[:3], start=1):
        title = _cell(issue.get("title"))
        trade = _cell(issue.get("trade") or issue.get("category"))
        location = _cell(issue.get("location"))
        action = _cell(issue.get("recommended_action") or issue.get("required_fix") or "Confirm close-out evidence and update the issue status.")
        lines.append(f"{index}. {title} - {trade}, {location}. {action}")
    return lines


def _priority_rank(value: str) -> int:
    return {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(value, 4)


def _finding_text(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("title") or item.get("item") or item.get("description") or "")
    return str(item or "")


def _issue_theme_summary(issues: list[dict]) -> str:
    haystack = " ".join(
        " ".join(
            str(value or "")
            for value in [
                issue.get("title"),
                issue.get("description"),
                issue.get("trade"),
                issue.get("category"),
                issue.get("recommended_action"),
            ]
        )
        for issue in issues
    ).lower()
    themes = []
    if "mechanical" in haystack and ("duct" in haystack or "ducting" in haystack):
        themes.append("mechanical ducting")
    if "passive fire" in haystack or "fire stopping" in haystack or "fire-rated" in haystack:
        themes.append("passive fire")
    if "cavity" in haystack or "wrap" in haystack:
        themes.append("cavity wrap")
    if "flashing" in haystack:
        themes.append("flashings")
    return ", ".join(themes[:5])
