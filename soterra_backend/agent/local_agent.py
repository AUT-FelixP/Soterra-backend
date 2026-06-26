from __future__ import annotations

import json
import logging
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
from ..services.issue_query_service import IssueQuery, IssueQueryService
from ..repositories.base import RepositoryBackend
from .schemas import AgentChatResponse, AgentRelatedEntities


logger = logging.getLogger(__name__)


LOCAL_AGENT_SYSTEM_PROMPT = """You are Soterra's inspection assistant.
Use only the provided tenant-scoped data.
Do not make up counts, reports, issue IDs, locations, or project names.
If the provided data is insufficient, say exactly what is missing.
Write for construction workers and site managers who need to assign work today.
Keep language short, plain, practical, and action-focused.
Use plain text only. Do not use Markdown, tables, bullets with symbols, or decorative formatting.
For open issue, risk, evidence, or fix-list answers, use this structure:
Summary:
Open: <count>
Critical: <count>
High: <count>
Medium: <count>
Low: <count>

Issues:
1. <issue title>
ID: <issue id or "Not stated">
Status: <Open, In Progress, or Closed>
Severity: <severity>
Location: <location or "Not stated">
Trade: <trade or "Not stated">
Fix: <specific required fix>
Evidence: <specific evidence required>

Next action:
<one or two short sentences saying who should do what next and whether reinspection is ready>
Show no more than eight issues unless the user asks for more."""


class LocalOllamaAgentService:
    """Remote Ollama API-backed agent. The historical class name is kept for compatibility."""
    def __init__(self, repository: RepositoryBackend) -> None:
        self.repository = repository
        self.model = OllamaModelExtractor(
            base_url=os.getenv("SOTERRA_OLLAMA_BASE_URL", "https://ollama.com"),
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
            "mode": "external_api",
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
        discovery = _issue_discovery_response(self.repository, tenant_id, message, project_slug)
        if discovery is not None:
            answer, payload = discovery
            self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="assistant", content=answer)
            return AgentChatResponse(
                session_id=session.id, answer=answer,
                used_tools=[{"name": "issue_query", "reason": "Applied tenant-scoped issue and location filters."}],
                citations=[{"type": "issues", "label": "Tenant-scoped extracted issues"}],
                context={"tenant_scoped": True, "active_records_only": True},
                safety={"tenant_id_used": tenant_id, "external_model_used": False, "provider": "native_query"},
                suggested_follow_ups=payload.get("follow_up_buttons", []), related_entities=AgentRelatedEntities(),
                confidence="high", mode="location_mode", structured_response=payload,
            )
        route, payload = _route_and_payload(
            snapshot=snapshot,
            message=message,
            report_id=report_id,
            project_slug=project_slug,
            page_context=page_context,
        )
        if route == "changeIssueStatus":
            issue_id = str(payload.get("issue_id") or "")
            status = str(payload.get("status") or "")
            updated = self.repository.update_issue(tenant_id, issue_id, status=status)
            answer = (
                f"Updated {issue_id} to {status}."
                if updated
                else f"I could not find {issue_id} in the current tenant issue register."
            )
            self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="assistant", content=answer)
            return AgentChatResponse(
                session_id=session.id,
                answer=answer,
                used_tools=[{"name": "changeIssueStatus", "reason": "Detected a natural-language issue status change request."}],
                citations=[{"type": "issues", "label": "Tenant-scoped issue register"}],
                context={"tenant_scoped": True, "active_records_only": True},
                safety={"tenant_id_used": tenant_id, "external_model_used": False, "provider": "native_action"},
                suggested_follow_ups=["Show open issues", "Summarize reinspection readiness"],
                related_entities=AgentRelatedEntities(issues=[issue_id] if issue_id else []),
                confidence="high" if updated else "medium",
                mode="action_plan_mode",
                structured_response={**payload, "updated": bool(updated)},
            )
        model_error: Exception | None = None
        try:
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
            answer = _clean_agent_answer(answer)
        except Exception as exc:
            model_error = exc
            logger.warning("local_ollama_agent_failed route=%s model=%s error=%s", route, self.model.model_id, type(exc).__name__)
            answer = _deterministic_answer(route, payload)
        if not answer or _is_weak_open_issue_answer(answer, route):
            answer = _deterministic_answer(route, payload)

        self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="assistant", content=answer)
        self._store_memory(tenant_id, user_id, session.id, route, payload)

        related = _related_entities(payload)
        mode = _mode_for_route(route)
        return AgentChatResponse(
            session_id=session.id,
            answer=answer,
            used_tools=[{"name": route, "reason": "Fetched tenant-scoped backend data before calling Ollama." if model_error is None else "Fetched tenant-scoped backend data and used deterministic fallback because Ollama was unavailable."}],
            citations=[{"type": route, "label": "Tenant-scoped Soterra data"}],
            context={"tenant_scoped": True, "history_used": bool(history), "memory_used": bool(memory), "active_records_only": True},
            safety={"tenant_id_used": tenant_id, "external_model_used": model_error is None, "provider": "ollama" if model_error is None else "ollama_fallback"},
            suggested_follow_ups=["Show open issues", "Summarize reinspection readiness", "List evidence needed"],
            related_entities=related,
            confidence="medium" if model_error is None else "high",
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
            content=f"Remote Ollama agent used {route} with tenant-scoped data.",
            payload_json=json.dumps({"route": route, "counts": _payload_counts(payload)}),
        )


def _active_snapshot(snapshot: RepositorySnapshot) -> RepositorySnapshot:
    active_documents = [item for item in snapshot.documents if not item.get("deleted_at")]
    active_ids = {item["id"] for item in active_documents}
    active_findings = [item for item in snapshot.findings if item.get("document_id") in active_ids]
    return snapshot.model_copy(update={"documents": active_documents, "findings": enrich_findings(active_findings, actionable_only=True)})


def _issue_discovery_response(repository: RepositoryBackend, tenant_id: str, message: str, project_slug: str | None) -> tuple[str, dict] | None:
    normalized = message.lower()
    if not any(term in normalized for term in ("open issue", "open defect", "needs fixing", "list defects", "show issues")):
        return None
    service = IssueQueryService(repository)
    all_result = service.query(tenant_id, IssueQuery(status="Open"))
    facet_values = [entry["value"] for key in ("projects", "addresses", "sites", "levels", "units", "areas", "trades", "severities") for entry in all_result["facets"].get(key, [])]
    has_scope = bool(project_slug) or any(value.lower() in normalized for value in facet_values)
    location_groups: dict[tuple[str, str, str], list[dict]] = {}
    for item in all_result["items"]:
        key = (item.get("project") or "Unknown project", item.get("address") or "Address not stated", item.get("site") or item.get("exact_location") or "Location not stated")
        location_groups.setdefault(key, []).append(item)
    if not has_scope and len(location_groups) > 1:
        options = [{"project": key[0], "address": key[1], "location": key[2], "open_issue_count": len(items), "high_priority_count": sum(i.get("severity") in {"High", "Critical"} for i in items)} for key, items in location_groups.items()]
        return "I found open issues in multiple locations. Which location do you want to inspect?", {"type": "location_clarification", "options": options, "follow_up_buttons": [f"Show open issues at {o['location']}" for o in options[:4]]}
    if not has_scope:
        return None
    filters: dict = {"status": "Open"}
    if project_slug: filters["project"] = project_slug
    for name, key in (("projects", "project"), ("addresses", "address"), ("sites", "site"), ("levels", "level"), ("units", "unit"), ("areas", "area"), ("trades", "trade"), ("severities", "severity")):
        for entry in all_result["facets"].get(name, []):
            if entry["value"].lower() in normalized: filters[key] = entry["value"]
    result = service.query(tenant_id, filters)
    return f"I found {result['total']} matching open issue{'s' if result['total'] != 1 else ''}.", {"type": "issue_cards", **result, "follow_up_buttons": ["Show evidence required", "Show high priority only"]}


def _route_and_payload(
    *,
    snapshot: RepositorySnapshot,
    message: str,
    report_id: str | None,
    project_slug: str | None,
    page_context: str | None,
) -> tuple[str, dict]:
    normalized = f"{page_context or ''} {message}".lower()
    status_change = _change_issue_status_payload(message)
    if status_change:
        return "changeIssueStatus", status_change
    if report_id:
        return "report_detail", build_report_detail(snapshot, report_id) or {"found": False, "missing": "report_id"}
    if any(term in normalized for term in ("failure", "failed extraction", "processing", "job", "upload status")):
        return "ingestion_jobs", {"items": snapshot.jobs, "count": len(snapshot.jobs)}
    if any(term in normalized for term in ("dashboard", "overview", "metric", "performance")):
        return "dashboard_summary", build_dashboard_overview(snapshot)
    if any(term in normalized for term in ("insight", "root cause", "repeat", "pattern", "trend")):
        return "insights", build_insights_page(snapshot)
    if any(term in normalized for term in ("issue", "defect", "tracker", "open", "fix", "evidence", "reinspection")):
        return "open_issues", _build_open_issues_payload(snapshot=snapshot, project_slug=project_slug)
    return "reports_summary", build_report_list(snapshot)


def _change_issue_status_payload(message: str) -> dict | None:
    """Turn short site-manager commands into a deterministic issue update tool call."""
    issue_match = re.search(r"\b(issue-[a-z0-9-]+)\b", message, flags=re.IGNORECASE)
    if not issue_match:
        return None

    normalized = message.lower()
    status: str | None = None
    if re.search(r"\b(close|closed|complete|completed|resolved|resolve)\b", normalized):
        status = "Closed"
    elif re.search(r"\b(reopen|open)\b", normalized):
        status = "Open"
    elif re.search(r"\b(in progress|in-progress|progress|started|start)\b", normalized):
        status = "In Progress"

    if not status:
        return None

    return {
        "type": "changeIssueStatus",
        "tool": "changeIssueStatus",
        "issue_id": issue_match.group(1),
        "status": status,
    }


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
        "Answer from the JSON only. If a needed field is absent, say what is missing. "
        "Avoid generic instructions like 'address each issue according to the summary'; use the specific fixes and evidence fields."
    )


def _build_open_issues_payload(*, snapshot: RepositorySnapshot, project_slug: str | None) -> dict:
    tracker = build_tracker_page(snapshot, {"project": project_slug})
    issues = []
    for item in snapshot.findings:
        if project_slug and item.get("project_slug") != project_slug:
            continue
        if str(item.get("status") or "Open") != "Open":
            continue
        issues.append(
            {
                "id": item.get("id"),
                "report_id": item.get("document_id"),
                "project_name": item.get("project_name"),
                "project_slug": item.get("project_slug"),
                "site_name": item.get("site_name"),
                "title": item.get("display_title") or item.get("title"),
                "description": item.get("plain_english_summary") or item.get("description"),
                "severity": item.get("severity"),
                "trade": item.get("trade") or item.get("display_category") or item.get("category"),
                "category": item.get("display_category") or item.get("category"),
                "location": item.get("location") or item.get("unit_label"),
                "status": item.get("status") or "Open",
                "required_fix": item.get("required_fix"),
                "evidence_required": item.get("evidence_required") or [],
                "source_quote": item.get("source_quote"),
                "confidence": item.get("confidence"),
            }
        )
    severity_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    issues.sort(key=lambda item: (severity_rank.get(str(item.get("severity")), 4), str(item.get("location") or ""), str(item.get("title") or "")))
    return {
        "summary": tracker.get("summary", {}),
        "count": len(issues),
        "critical_count": len([item for item in issues if item.get("severity") == "Critical"]),
        "high_count": len([item for item in issues if item.get("severity") == "High"]),
        "issues": issues[:20],
    }


def _clean_agent_answer(answer: str | None) -> str:
    text = str(answer or "").strip()
    if not text:
        return ""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\n)(Issue \d+:)", r"\n\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_weak_open_issue_answer(answer: str, route: str) -> bool:
    if route != "open_issues":
        return False
    lowered = answer.lower()
    weak_phrases = (
        "address each issue according to the summary",
        "according to the summary",
        "potential fire safety compromise",
        "there are open issues",
    )
    has_specific_action = any(term in lowered for term in ("fix:", "evidence:", "install", "reinstate", "upload", "photo", "sign-off", "register"))
    return any(phrase in lowered for phrase in weak_phrases) and not has_specific_action


def _deterministic_answer(route: str, payload: dict) -> str:
    if route == "open_issues":
        return _format_open_issues_answer(payload)
    return "I could not generate a grounded answer from the provided tenant data."


def _format_open_issues_answer(payload: dict) -> str:
    issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
    if not issues:
        return "Open issues: 0\n\nI do not see open issues in the current tenant-scoped data."
    critical = payload.get("critical_count", 0)
    high = payload.get("high_count", 0)
    lines = [
        f"Open issues: {len(issues)}",
        f"Priority: {critical} critical and {high} high-priority issue(s) need close-out before reinspection.",
        "",
    ]
    for index, issue in enumerate(issues[:8], start=1):
        evidence = issue.get("evidence_required") or []
        if isinstance(evidence, list):
            evidence_text = ", ".join(str(item) for item in evidence if item) or "Close-out photo evidence and trade sign-off."
        else:
            evidence_text = str(evidence)
        lines.extend(
            [
                f"{index}. {issue.get('title') or 'Inspection issue'}",
                f"   Issue ID: {issue.get('id') or 'Not stated'}",
                f"   Status: {issue.get('status') or 'Open'}",
                f"   Severity: {issue.get('severity') or 'Not stated'}",
                f"   Location: {issue.get('location') or 'Not stated'}",
                f"   Responsible trade: {issue.get('trade') or issue.get('category') or 'Not stated'}",
                f"   Source: {issue.get('report_id') or 'Not stated'}",
                f"   Fix: {issue.get('required_fix') or 'Assign an owner, complete the rectification, and update the issue status.'}",
                f"   Evidence: {evidence_text}",
                "",
            ]
        )
    lines.append("Reinspection readiness: Not ready until every open issue is fixed, evidence is uploaded, and the responsible trade has confirmed close-out.")
    return "\n".join(lines).strip()


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
