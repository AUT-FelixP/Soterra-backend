from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import re
from typing import Any, Callable

from ..agent.schemas import AgentChatResponse, AgentRelatedEntities
from ..issue_intelligence import enrich_findings, group_similar_issues
from ..models import RepositorySnapshot
from ..repositories.base import RepositoryBackend


ToolHandler = Callable[[RepositorySnapshot, str], dict]


@dataclass(frozen=True)
class NativeTool:
    name: str
    description: str
    keywords: tuple[str, ...]
    handler: ToolHandler


class NativeToolRegistry:
    def __init__(self) -> None:
        self.tools = [
            NativeTool("summarize_project", "Summarize uploaded reports and open findings.", ("summary", "summarize", "project", "report"), _summarize_project),
            NativeTool("list_open_issues", "List open issues from active findings.", ("open", "issues", "defects", "fix", "tracker"), _list_open_issues),
            NativeTool("high_priority_issues", "Find high or critical issues.", ("urgent", "priority", "critical", "high", "risk"), _high_priority_issues),
            NativeTool("evidence_needed", "Show evidence required for close-out.", ("evidence", "photo", "close-out", "proof", "sign-off"), _evidence_needed),
            NativeTool("issues_by_trade", "Group open issues by trade.", ("trade", "contractor", "builder", "mechanical", "fire", "plumbing"), _issues_by_trade),
            NativeTool("issues_by_location", "Group open issues by location.", ("location", "where", "area", "level", "unit"), _issues_by_location),
            NativeTool("repeated_patterns", "Identify repeated issue titles and categories.", ("repeat", "repeated", "recurring", "pattern", "root cause"), _repeated_patterns),
            NativeTool("inspection_risk", "Summarize inspection risk from findings and predictions.", ("inspection", "risk", "next", "due", "reinspection"), _inspection_risk),
            NativeTool("report_status", "Show extracted report status.", ("report", "status", "uploaded", "completed", "processing"), _report_status),
            NativeTool("extraction_failures", "Show failed extraction jobs.", ("failed", "failure", "extraction", "manual review", "processing"), _extraction_failures),
            NativeTool("todays_fix_list", "Prioritize today's fix list.", ("today", "fix first", "action", "todo", "priority"), _todays_fix_list),
        ]

    def select(self, query: str) -> NativeTool:
        scored = _score_tools(query, self.tools)
        return scored[0][1] if scored else self.tools[0]


class NativeAgentService:
    def __init__(self, repository: RepositoryBackend) -> None:
        self.repository = repository
        self.registry = NativeToolRegistry()

    def status(self) -> dict:
        return {"enabled": True, "configured": True, "provider": "native", "mode": "deterministic", "model_id": None}

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
        _ = role, report_id, issue_id, page_context
        session = self._get_or_create_session(tenant_id=tenant_id, user_id=user_id, session_id=session_id, message=message)
        history = self.repository.list_agent_chat_messages(tenant_id=tenant_id, user_id=user_id, session_id=session.id, limit=24)
        memory = self.repository.list_agent_memory_entries(tenant_id=tenant_id, user_id=user_id, session_id=session.id, limit=10) if hasattr(self.repository, "list_agent_memory_entries") else []
        self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="user", content=message)

        snapshot = self.repository.load_snapshot(tenant_id)
        query = _contextual_query(message, history, memory, project_slug)
        tool = self.registry.select(query)
        payload = tool.handler(_active_snapshot(snapshot), query)
        answer = payload["answer"]
        self.repository.add_agent_chat_message(tenant_id=tenant_id, user_id=user_id, session_id=session.id, role="assistant", content=answer)

        memory_payload = {
            "intent": tool.name,
            "referenced_report_ids": payload.get("report_ids", []),
            "referenced_issue_ids": payload.get("issue_ids", []),
            "referenced_project_slugs": payload.get("project_slugs", []),
            "confidence": payload.get("confidence", "medium"),
            "mode": payload.get("mode", "summary_mode"),
        }
        self._store_memory(tenant_id, user_id, session.id, tool.name, memory_payload)
        self._store_summary_if_needed(tenant_id, user_id, session.id)

        return AgentChatResponse(
            session_id=session.id,
            answer=answer,
            used_tools=[{"name": tool.name, "reason": tool.description}],
            citations=payload.get("citations", []),
            context={"tenant_scoped": True, "history_used": bool(history), "memory_used": bool(memory), "active_records_only": True},
            safety={"tenant_id_used": tenant_id, "external_model_used": False},
            suggested_follow_ups=payload.get("suggested_follow_ups", _default_followups()),
            related_entities=AgentRelatedEntities(
                reports=payload.get("report_ids", []),
                issues=payload.get("issue_ids", []),
                projects=payload.get("project_slugs", []),
            ),
            confidence=payload.get("confidence", "medium"),
            mode=payload.get("mode", "summary_mode"),
            structured_response=payload.get("structured_response", {}),
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
            "messages": [{"id": item.id, "role": item.role, "content": item.content, "created_at": item.created_at, "tool_name": item.tool_name} for item in messages],
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

    def _store_memory(self, tenant_id: str, user_id: str, session_id: str, tool_name: str, payload: dict) -> None:
        if hasattr(self.repository, "add_agent_memory_entry"):
            self.repository.add_agent_memory_entry(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                memory_type="tool",
                content=f"Selected {tool_name} with {payload.get('confidence')} confidence.",
                payload_json=json.dumps(payload),
            )

    def _store_summary_if_needed(self, tenant_id: str, user_id: str, session_id: str) -> None:
        messages = self.repository.list_agent_chat_messages(tenant_id=tenant_id, user_id=user_id, session_id=session_id, limit=60)
        user_questions = [item.content for item in messages if item.role == "user"]
        if len(user_questions) < 6 or len(user_questions) % 6:
            return
        topics = _top_topics(" ".join(user_questions))
        content = f"Session summary: {len(user_questions)} user questions. Recurring topics: {', '.join(topics) or 'general inspection data'}."
        self.repository.add_agent_memory_entry(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            memory_type="summary",
            content=content,
            payload_json=json.dumps({"question_count": len(user_questions), "topics": topics}),
        )


def _score_tools(query: str, tools: list[NativeTool]) -> list[tuple[float, NativeTool]]:
    lowered = query.lower()
    scores = []
    for tool in tools:
        keyword_score = sum(1 for token in tool.keywords if token in lowered)
        scores.append((float(keyword_score), tool))
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        corpus = [query] + [f"{tool.name} {tool.description} {' '.join(tool.keywords)}" for tool in tools]
        vectors = TfidfVectorizer(stop_words="english").fit_transform(corpus)
        similarities = cosine_similarity(vectors[0:1], vectors[1:]).ravel()
        scores = [(score + float(similarities[index]), tool) for index, (score, tool) in enumerate(scores)]
    except Exception:
        pass
    return sorted(scores, key=lambda item: (-item[0], item[1].name))


def _active_snapshot(snapshot: RepositorySnapshot) -> RepositorySnapshot:
    active_documents = [item for item in snapshot.documents if not item.get("deleted_at")]
    active_ids = {item["id"] for item in active_documents}
    active_findings = [item for item in snapshot.findings if item.get("document_id") in active_ids]
    return snapshot.model_copy(update={"documents": active_documents, "findings": enrich_findings(active_findings, actionable_only=True)})


def _contextual_query(message: str, history: list[Any], memory: list[Any], project_slug: str | None) -> str:
    recent = " ".join(getattr(item, "content", "") for item in history[-4:])
    summaries = " ".join(getattr(item, "content", "") for item in memory[-3:])
    return " ".join(part for part in [project_slug or "", recent, summaries, message] if part)


def _open_findings(snapshot: RepositorySnapshot) -> list[dict]:
    return [item for item in snapshot.findings if item.get("status") != "Closed"]


def _result(answer: str, items: list[dict], *, mode: str = "summary_mode", confidence: str = "high", extra: dict | None = None) -> dict:
    report_ids = sorted({str(item.get("document_id")) for item in items if item.get("document_id")})
    issue_ids = sorted({str(item.get("id")) for item in items if item.get("id")})
    projects = sorted({str(item.get("project_slug")) for item in items if item.get("project_slug")})
    return {
        "answer": answer,
        "confidence": confidence,
        "mode": mode,
        "structured_response": extra or {"items": items[:10], "count": len(items)},
        "citations": [{"type": "active_findings", "label": "Tenant-scoped extracted findings"}] if items else [],
        "suggested_follow_ups": _default_followups(),
        "report_ids": report_ids,
        "issue_ids": issue_ids,
        "project_slugs": projects,
    }


def _summarize_project(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = _filter_by_query_project(_open_findings(snapshot), query)
    if not snapshot.documents:
        return _result("I do not see uploaded reports for this account yet. Upload an inspection report and wait for extraction to finish.", [], confidence="low")
    lines = [f"I found {len(snapshot.documents)} active report(s) and {len(findings)} open issue(s)."]
    by_project = Counter(item.get("project_name") or "Unknown project" for item in findings)
    for project, count in by_project.most_common(5):
        lines.append(f"- {project}: {count} open issue(s).")
    by_category = Counter(item.get("display_category") or item.get("category") or "General" for item in findings)
    if by_category:
        lines.append("\nCategories:")
        for category, count in by_category.most_common(5):
            lines.append(f"- {category}: {count}")
    themes = group_similar_issues(findings)
    if themes:
        lines.append("\nMain issue themes:")
        for theme in themes[:5]:
            lines.append(f"- {theme['label']}: {theme['count']} item(s), highest severity {theme['highestSeverity']}.")
    return _result("\n".join(lines), findings, extra={"items": findings[:10], "count": len(findings), "themes": themes[:8], "categories": dict(by_category)})


def _list_open_issues(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = _filter_by_query_project(_open_findings(snapshot), query)
    if not findings:
        return _result("I do not see open issues in the current tenant data. Check that reports have finished extracting.", [], confidence="medium")
    lines = [f"There are {len(findings)} open issue(s):"]
    for item in findings[:10]:
        location = item.get("location") or item.get("unit_label") or "exact location not stated"
        lines.append(f"- {item.get('severity')}: {item.get('display_title') or item.get('title')} ({location}).")
    return _result("\n".join(lines), findings, mode="full_register_mode")


def _high_priority_issues(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = [item for item in _filter_by_query_project(_open_findings(snapshot), query) if item.get("severity") in {"High", "Critical"}]
    lines = [f"{len(findings)} high-priority open issue(s) need attention."]
    for item in findings[:8]:
        lines.append(f"- {item.get('severity')}: {item.get('display_title') or item.get('title')} - {item.get('required_fix') or 'Rectify and collect evidence.'}")
    return _result("\n".join(lines), findings, mode="action_plan_mode")


def _evidence_needed(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = _filter_by_query_project(_open_findings(snapshot), query)
    needing = [item for item in findings if item.get("evidence_required")]
    if not needing:
        return _result("I do not see evidence requirements on the current open findings. Re-run extraction or review the report manually.", findings, confidence="medium")
    lines = ["Evidence needed for close-out:"]
    for item in needing[:10]:
        evidence = ", ".join(map(str, item.get("evidence_required") or []))
        lines.append(f"- {item.get('display_title') or item.get('title')}: {evidence}.")
    return _result("\n".join(lines), needing, mode="evidence_mode")


def _issues_by_trade(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = _filter_by_query_project(_open_findings(snapshot), query)
    counts = Counter(item.get("trade") or "General" for item in findings)
    answer = "Open issues by trade:\n" + "\n".join(f"- {trade}: {count}" for trade, count in counts.most_common()) if counts else "No open issue trades are available."
    return _result(answer, findings, mode="trade_mode", extra={"byTrade": dict(counts)})


def _issues_by_location(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = _filter_by_query_project(_open_findings(snapshot), query)
    counts = Counter(item.get("location") or item.get("unit_label") or "Exact location not stated" for item in findings)
    answer = "Open issues by location:\n" + "\n".join(f"- {location}: {count}" for location, count in counts.most_common()) if counts else "No open issue locations are available."
    return _result(answer, findings, mode="location_mode", extra={"byLocation": dict(counts)})


def _repeated_patterns(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = _filter_by_query_project(snapshot.findings, query)
    themes = group_similar_issues(findings)
    repeated = [theme for theme in themes if theme["count"] > 1]
    repeated_ids = {issue_id for theme in repeated for issue_id in theme["issueIds"]}
    flat = [item for item in findings if item.get("id") in repeated_ids]
    lines = ["Repeated patterns found:"]
    for theme in repeated[:8]:
        lines.append(f"- {theme['label']}: {theme['count']} occurrence(s), highest severity {theme['highestSeverity']}.")
    if not repeated:
        lines = ["I do not see repeated actionable issue themes yet. More extracted reports may be needed to confirm patterns."]
    return _result("\n".join(lines), flat, mode="risk_mode", extra={"patterns": repeated[:8]})


def _inspection_risk(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = _high_priority_issues(snapshot, query)
    predictions = snapshot.predicted_inspections[:5]
    extra_lines = [f"- {item.get('project_name')}: {item.get('inspection_type')} on {item.get('expected_date')} ({item.get('risk_level')})." for item in predictions]
    findings["answer"] += ("\nUpcoming inspection risk:\n" + "\n".join(extra_lines)) if extra_lines else "\nNo predicted inspections are stored yet."
    findings["mode"] = "risk_mode"
    return findings


def _report_status(snapshot: RepositorySnapshot, query: str) -> dict:
    docs = snapshot.documents
    jobs = snapshot.jobs
    lines = [f"Report status: {len(docs)} active report(s), {len(jobs)} processing job record(s)."]
    for job in jobs[:8]:
        lines.append(f"- {job.get('document_id')}: {job.get('status')} ({job.get('extractor')}).")
    return _result("\n".join(lines), [], extra={"reports": docs[:10], "jobs": jobs[:10]})


def _extraction_failures(snapshot: RepositorySnapshot, query: str) -> dict:
    failed_jobs = [item for item in snapshot.jobs if item.get("status") == "failed"]
    if not failed_jobs:
        return _result("I do not see failed extraction jobs in the current tenant data.", [], confidence="high", extra={"jobs": []})
    lines = ["Extraction failures needing manual review:"]
    for job in failed_jobs[:8]:
        lines.append(f"- {job.get('document_id')}: {job.get('error_message') or 'No error message stored.'}")
    return _result("\n".join(lines), [], confidence="high", extra={"jobs": failed_jobs[:10]})


def _todays_fix_list(snapshot: RepositorySnapshot, query: str) -> dict:
    findings = sorted(_filter_by_query_project(_open_findings(snapshot), query), key=lambda item: (_severity_rank(item), -int(item.get("recurrence_risk") or 0)))
    lines = ["Today's fix list:"]
    for index, item in enumerate(findings[:8], start=1):
        location = item.get("location") or item.get("unit_label") or "exact location not stated"
        lines.append(f"{index}. {item.get('display_title') or item.get('title')} - {item.get('trade') or 'General'} at {location}.")
    if not findings:
        lines.append("No open fixes are available from extracted findings.")
    return _result("\n".join(lines), findings, mode="action_plan_mode")


def _filter_by_query_project(findings: list[dict], query: str) -> list[dict]:
    lowered = query.lower()
    slugs = {item.get("project_slug") for item in findings if item.get("project_slug") and str(item.get("project_slug")).replace("-", " ") in lowered}
    if slugs:
        return [item for item in findings if item.get("project_slug") in slugs]
    return findings


def _severity_rank(item: dict) -> int:
    return {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(str(item.get("severity")), 4)


def _highest(items: list[dict]) -> str:
    return sorted((str(item.get("severity") or "Low") for item in items), key=lambda value: {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(value, 4))[0]


def _top_topics(text: str) -> list[str]:
    terms = re.findall(r"\b[a-z][a-z-]{3,}\b", text.lower())
    ignored = {"what", "which", "there", "with", "from", "issues", "issue", "show", "list"}
    return [term for term, _ in Counter(term for term in terms if term not in ignored).most_common(5)]


def _default_followups() -> list[str]:
    return ["Show open issues by trade", "What evidence is needed?", "Which patterns are repeated?"]
