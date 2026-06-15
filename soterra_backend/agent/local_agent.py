from __future__ import annotations

import json
import os
import re
from typing import Any

from ..analytics import (
    build_dashboard_overview,
    build_insights_page,
    build_report_detail,
    build_report_list,
    build_tracker_page,
)
from ..config import DEFAULT_LOCAL_MODEL_ID
from ..extractors.ollama_model import OllamaModelExtractor
from ..issue_intelligence import enrich_findings
from ..models import RepositorySnapshot
from ..repositories.base import RepositoryBackend
from .schemas import AgentChatResponse, AgentRelatedEntities


LOCAL_AGENT_SYSTEM_PROMPT = """You are Soterra's inspection assistant.
Use only the provided tenant-scoped data.
Do not make up counts, reports, issue IDs, locations, or project names.
If the provided data is insufficient, say exactly what is missing.
Give concise, practical answers for builders, QA teams, and site managers.
Focus on: issue summary, risk, next action, evidence required, and reinspection readiness."""


class LocalOllamaAgentService:
    def __init__(self, repository: RepositoryBackend) -> None:
        self.repository = repository
        self.model = OllamaModelExtractor(
            base_url=os.getenv("SOTERRA_OLLAMA_BASE_URL", "http://localhost:11434"),
            model_id=os.getenv("SOTERRA_AGENT_MODEL_ID", os.getenv("SOTERRA_EXTRACTION_MODEL_ID", DEFAULT_LOCAL_MODEL_ID)),
            api_key=os.getenv("SOTERRA_OLLAMA_API_KEY") or os.getenv("OLLAMA_API_KEY"),
            timeout_seconds=int(os.getenv("SOTERRA_AGENT_TIMEOUT_SECONDS", "90")),
            temperature=float(os.getenv("SOTERRA_AGENT_TEMPERATURE", "0.2")),
        )

    def status(self) -> dict:
        return {
            "enabled": True,
            "configured": True,
            "provider": "ollama",
            "mode": "local_model",
            "model_id": self.model.model_id,
        }

    def chat(
        self,
        *,
        tenant_id: str,
        user_id: str,
        message: str,
        session_id: str | None = None,
        role: str = "member",
        report_id: str | None = None,
        issue_id: str | None = None,
        project_slug: str | None = None,
        page_context: str | None = None,
    ) -> AgentChatResponse:
        _ = role, issue_id
        session = self._get_or_create_session(tenant_id=tenant_id, user_id=user_id, session_id=session_id, message=message)
        history = self.repository.list_agent_chat_messages(tenant_id=tenant_id, user_id=user_id, session_id=session.id, limit=12)
        memory = self.repository.list_agent_memory_entries(tenant_id=tenant_id, user_id=user_id, session_id=session.id, limit=8) if hasattr(self.repository, "list_agent_memory_entries") else []
        self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="user", content=message)

        snapshot = _active_snapshot(self.repository.load_snapshot(tenant_id))
        route, payload = _route_and_payload(
            snapshot=snapshot,
            message=message,
            report_id=report_id,
            project_slug=project_slug,
            page_context=page_context,
        )
        answer = self.model.generate_text(
            system_prompt=LOCAL_AGENT_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(
                message=message,
                route=route,
                payload=payload,
                history=history,
                memory=memory,
                page_context=page_context,
            ),
        )
        if not answer:
            answer = "I could not generate a grounded answer from the provided tenant data."

        self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="assistant", content=answer)
        self._store_memory(tenant_id, user_id, session.id, route, payload)

        related = _related_entities(payload)
        mode = _mode_for_route(route)
        return AgentChatResponse(
            session_id=session.id,
            answer=answer,
            used_tools=[{"name": route, "reason": "Fetched tenant-scoped backend data before calling Ollama."}],
            citations=[{"type": route, "label": "Tenant-scoped Soterra data"}],
            context={"tenant_scoped": True, "history_used": bool(history), "memory_used": bool(memory), "active_records_only": True},
            safety={"tenant_id_used": tenant_id, "external_model_used": False, "provider": "ollama"},
            suggested_follow_ups=["Show open issues", "Summarize reinspection readiness", "List evidence needed"],
            related_entities=related,
            confidence="medium",
            mode=mode,
            structured_response=payload if isinstance(payload, dict) else {},
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

    def _get_or_create_session(self, *, tenant_id: str, user_id: str, session_id: str | None, message: str):
        if session_id:
            session = self.repository.get_agent_chat_session(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if not session:
                raise ValueError("Chat session not found for this account.")
            return session
        title = re.sub(r"\s+", " ", message).strip()[:80] or "New chat"
        return self.repository.create_agent_chat_session(tenant_id=tenant_id, user_id=user_id, title=title)

    def _store_memory(self, tenant_id: str, user_id: str, session_id: str, route: str, payload: dict) -> None:
        if not hasattr(self.repository, "add_agent_memory_entry"):
            return
        self.repository.add_agent_memory_entry(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            memory_type="tool",
            content=f"Local Ollama agent used {route} with tenant-scoped data.",
            payload_json=json.dumps({"route": route, "counts": _payload_counts(payload)}),
        )


def _active_snapshot(snapshot: RepositorySnapshot) -> RepositorySnapshot:
    active_documents = [item for item in snapshot.documents if not item.get("deleted_at")]
    active_ids = {item["id"] for item in active_documents}
    active_findings = [item for item in snapshot.findings if item.get("document_id") in active_ids]
    return snapshot.model_copy(update={"documents": active_documents, "findings": enrich_findings(active_findings, actionable_only=True)})


def _route_and_payload(
    *,
    snapshot: RepositorySnapshot,
    message: str,
    report_id: str | None,
    project_slug: str | None,
    page_context: str | None,
) -> tuple[str, dict]:
    normalized = f"{page_context or ''} {message}".lower()
    if report_id:
        return "report_detail", build_report_detail(snapshot, report_id) or {"found": False, "missing": "report_id"}
    if any(term in normalized for term in ("failure", "failed extraction", "processing", "job", "upload status")):
        return "ingestion_jobs", {"items": snapshot.jobs, "count": len(snapshot.jobs)}
    if any(term in normalized for term in ("dashboard", "overview", "metric", "performance")):
        return "dashboard_summary", build_dashboard_overview(snapshot)
    if any(term in normalized for term in ("insight", "root cause", "repeat", "pattern", "trend")):
        return "insights", build_insights_page(snapshot)
    if any(term in normalized for term in ("issue", "defect", "tracker", "open", "fix", "evidence", "reinspection")):
        return "open_issues", build_tracker_page(snapshot, {"project": project_slug})
    return "reports_summary", build_report_list(snapshot)


def _build_user_prompt(*, message: str, route: str, payload: dict, history: list[Any], memory: list[Any], page_context: str | None) -> str:
    history_text = "\n".join(f"{item.role}: {item.content}" for item in history[-6:])
    memory_text = "\n".join(getattr(item, "content", "") for item in memory[-4:])
    return (
        f"User question:\n{message}\n\n"
        f"Page context:\n{page_context or 'none'}\n\n"
        f"Recent chat history:\n{history_text or 'none'}\n\n"
        f"Stored memory:\n{memory_text or 'none'}\n\n"
        f"Selected data route: {route}\n"
        "Tenant-scoped data JSON:\n"
        f"{json.dumps(payload, default=str)[:24000]}\n\n"
        "Answer from the JSON only. If a needed field is absent, say what is missing."
    )


def _related_entities(payload: dict) -> AgentRelatedEntities:
    text = json.dumps(payload, default=str)
    return AgentRelatedEntities(
        reports=sorted(set(re.findall(r'"(?:id|document_id)"\s*:\s*"(rpt-[^"]+)"', text)))[:10],
        issues=sorted(set(re.findall(r'"(?:id|issue_id)"\s*:\s*"(issue-[^"]+)"', text)))[:10],
        projects=sorted(set(re.findall(r'"project_slug"\s*:\s*"([^"]+)"', text)))[:10],
    )


def _mode_for_route(route: str):
    if route in {"open_issues", "ingestion_jobs"}:
        return "action_plan_mode"
    if route == "insights":
        return "risk_mode"
    return "summary_mode"


def _payload_counts(payload: dict) -> dict:
    return {
        "items": len(payload.get("items", [])) if isinstance(payload.get("items"), list) else None,
        "findings": len(payload.get("findings", [])) if isinstance(payload.get("findings"), list) else None,
        "reports": len(payload.get("reports", [])) if isinstance(payload.get("reports"), list) else None,
    }
