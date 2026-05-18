from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from ..analytics import (
    build_company_page,
    build_dashboard_insights_preview,
    build_dashboard_live_tracker,
    build_dashboard_overview,
    build_dashboard_risk,
    build_dashboard_top_failures,
    build_dashboard_upcoming_risk,
    build_inspection_risk_page,
    build_insights_page,
    build_issue_detail,
    build_performance_page,
    build_project_page,
    build_report_detail,
    build_report_list,
)
from ..repository import RepositoryBackend
from ..utils import safe_int

try:
    from smolagents import Tool
except ModuleNotFoundError:  # pragma: no cover - exercised only before dependencies are installed.
    class Tool:  # type: ignore[no-redef]
        name = ""
        description = ""
        inputs: dict[str, dict[str, str]] = {}
        output_type = "object"

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self.forward(*args, **kwargs)


ToolRecorder = Callable[[str], None]


def build_soterra_tools(repository: RepositoryBackend, tenant_id: str, recorder: ToolRecorder | None = None) -> list[Tool]:
    return [
        ApiRouteCatalogTool(repository, tenant_id, recorder),
        DataSchemaCatalogTool(repository, tenant_id, recorder),
        TenantMembersTool(repository, tenant_id, recorder),
        ProjectCatalogTool(repository, tenant_id, recorder),
        IngestionJobsTool(repository, tenant_id, recorder),
        IssueAnalyticsTool(repository, tenant_id, recorder),
        ReportsSummaryTool(repository, tenant_id, recorder),
        ReportDetailTool(repository, tenant_id, recorder),
        TrackerSummaryTool(repository, tenant_id, recorder),
        IssueDetailTool(repository, tenant_id, recorder),
        DashboardSummaryTool(repository, tenant_id, recorder),
        CompanyMetricsTool(repository, tenant_id, recorder),
        PerformanceMetricsTool(repository, tenant_id, recorder),
        ProjectMetricsTool(repository, tenant_id, recorder),
        DashboardRiskTool(repository, tenant_id, recorder),
        LiveTrackerTool(repository, tenant_id, recorder),
        TopFailuresTool(repository, tenant_id, recorder),
        UpcomingRiskTool(repository, tenant_id, recorder),
        InsightsPreviewTool(repository, tenant_id, recorder),
        InspectionRiskTool(repository, tenant_id, recorder),
        InsightsTool(repository, tenant_id, recorder),
    ]


class SoterraTenantTool(Tool):
    inputs = {"tenant_id": {"type": "string", "description": "Current authenticated tenant id."}}
    output_type = "object"

    def __init__(self, repository: RepositoryBackend, tenant_id: str, recorder: ToolRecorder | None = None) -> None:
        super().__init__()
        self.repository = repository
        self.tenant_id = tenant_id
        self.recorder = recorder

    def _record(self) -> None:
        if self.recorder:
            self.recorder(self.name)

    def _check_tenant(self, tenant_id: str) -> dict | None:
        if tenant_id != self.tenant_id:
            self._record()
            return {"found": False, "error": "Record not found for this tenant."}
        return None

    def _snapshot(self):
        return self.repository.load_snapshot(self.tenant_id)


class ApiRouteCatalogTool(SoterraTenantTool):
    name = "get_api_route_catalog"
    description = "Return the protected Soterra read API routes, what each route returns, and the internal agent tool equivalent. Use if a question is ambiguous and you need to choose the right Soterra data source."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        return {
            "routes": [
                {"route": "GET /reports", "tool": "get_reports_summary", "returns": "Compact report list with project, site, date, status, inspection type, issue count, highest severity."},
                {"route": "GET /reports/{report_id}", "tool": "get_report_detail", "returns": "One report with summary, findings, severity, status, category, trade, location, and follow-up signals."},
                {"route": "GET /issues", "tool": "get_tracker_summary", "returns": "Issue register summary: open, overdue, high-priority counts, status/category breakdown, prioritized issue list."},
                {"route": "GET /issues/{issue_id}", "tool": "get_issue_detail", "returns": "One issue with status, severity, project, site, days open, linked report, notes, trade, location."},
                {"route": "GET /tracker", "tool": "get_tracker_summary", "returns": "Current live tracker issue summary and prioritized issue list."},
                {"route": "GET /tracker/{issue_id}", "tool": "get_issue_detail", "returns": "One tracker item with open/ready/closed status and linked issue details."},
                {"route": "GET /dashboard", "tool": "get_dashboard_summary", "returns": "Overview metrics, live tracker counts, upcoming risks, top failure drivers, issue status summary."},
                {"route": "GET /dashboard/company", "tool": "get_company_metrics", "returns": "Company-level project and inspection-type metrics."},
                {"route": "GET /dashboard/performance", "tool": "get_performance_metrics", "returns": "Failure drivers, recurring risk, issue trend, close-out performance, reinspection performance."},
                {"route": "GET /dashboard/insights", "tool": "get_insights", "returns": "Root causes, high-risk areas, repeated patterns."},
                {"route": "GET /dashboard/project/{slug}", "tool": "get_project_metrics", "returns": "Project-specific metrics, tracker snapshot, open item types, failure drivers, recent failed items."},
                {"route": "GET /dashboard/risk", "tool": "get_dashboard_risk", "returns": "Upcoming inspection risk list and likely failures for selected windows/sites."},
                {"route": "GET /dashboard/live-tracker", "tool": "get_live_tracker", "returns": "Open, overdue, and ready-for-inspection counts for the dashboard live tracker card."},
                {"route": "GET /dashboard/top-failures", "tool": "get_top_failures", "returns": "Repeated failure drivers, failure distribution, recurring risk."},
                {"route": "GET /dashboard/upcoming-risk", "tool": "get_upcoming_risk", "returns": "Next upcoming inspection risk and likely failures."},
                {"route": "GET /dashboard/insights-preview", "tool": "get_insights_preview", "returns": "Short dashboard insight preview with root causes and high-risk areas."},
                {"route": "GET /inspection-risk", "tool": "get_inspection_risk", "returns": "Inspection-risk page filters, upcoming inspections, likely failure items."},
                {"route": "GET /insights", "tool": "get_insights", "returns": "Legacy insight summary and repeated issue signals."},
                {"route": "GET /tenants/members", "tool": "get_tenant_members", "returns": "Tenant member directory with id, name, email, role, created date."},
                {"route": "Issue analytics over /issues, /reports, /inspection-risk", "tool": "get_issue_analytics", "returns": "Top sites/categories, open work to fix this week, closed project issues, passed sites, reinspection root causes."},
                {"route": "GET /auth/session", "tool": "get_tenant_members plus request context", "returns": "Authenticated user's tenant/user identity and role. The agent receives this in task context."},
                {"route": "GET /agent/chat/status", "tool": "agent service status", "returns": "Whether chat is enabled/configured and model/provider metadata."},
                {"route": "GET /health", "tool": "not needed for data answers", "returns": "Public service health status only."},
            ],
            "mutationRoutes": [
                "POST /auth/register",
                "POST /auth/login",
                "POST /auth/forgot-password",
                "POST /auth/reset-password",
                "POST /tenants/members",
                "DELETE /tenants/members/{user_id}",
                "POST /reports",
                "DELETE /reports",
                "DELETE /reports/{report_id}",
                "POST /reports/bulk-delete",
                "PATCH /issues/{issue_id}",
                "PATCH /tracker/{issue_id}",
                "POST /agent/chat",
            ],
            "note": "The chat agent answers questions from read tools only. It does not mutate auth, members, reports, or issues.",
        }


class DataSchemaCatalogTool(SoterraTenantTool):
    name = "get_data_schema_catalog"
    description = "Return the complete backend database schema map, table purpose, safe fields, and the agent tool or route that covers each area. Use before answering data-source, coverage, or ambiguous data-mapping questions."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        snapshot = self._snapshot()
        return {
            "coverage": "All persisted backend tables and analytics views are represented. Sensitive credential/session tables are schema-aware only and never expose hashes or reset tokens.",
            "tables": [
                {
                    "table": "tenants",
                    "purpose": "Tenant/company account boundary. Connects every user, report, issue, project, and prediction to the authenticated company.",
                    "safeFields": ["id", "name", "slug", "email_domain", "created_at"],
                    "coveredBy": ["request context", "get_tenant_members"],
                    "tenantRowCount": 1 if snapshot.projects or snapshot.documents or snapshot.findings else "current authenticated tenant",
                },
                {
                    "table": "users",
                    "purpose": "Tenant member directory and roles for admin/member permissions.",
                    "safeFields": ["id", "tenant_id", "name", "email", "role", "created_at"],
                    "blockedFields": ["password_hash"],
                    "coveredBy": ["GET /tenants/members", "GET /auth/session", "get_tenant_members"],
                },
                {
                    "table": "auth_sessions",
                    "purpose": "Bearer session state for authenticated API access.",
                    "safeFields": ["current user id", "tenant id", "role", "expires_at from auth session responses"],
                    "blockedFields": ["token_hash", "raw access tokens for other users"],
                    "coveredBy": ["GET /auth/session", "request context"],
                },
                {
                    "table": "password_reset_tokens",
                    "purpose": "Password reset lifecycle.",
                    "safeFields": ["whether reset flow exists"],
                    "blockedFields": ["token_hash", "raw reset token", "used_at for other users"],
                    "coveredBy": ["POST /auth/forgot-password", "POST /auth/reset-password"],
                    "agentAccess": "schema-only; not used for analytics answers",
                },
                {
                    "table": "projects",
                    "purpose": "Project/site identity, slug, address, and grouping for reports, findings, predictions, and dashboards.",
                    "safeFields": ["id", "slug", "name", "site_name", "address", "created_at"],
                    "coveredBy": ["GET /dashboard/company", "GET /dashboard/project/{slug}", "get_project_catalog", "get_project_metrics"],
                    "tenantRowCount": len(snapshot.projects),
                },
                {
                    "table": "documents",
                    "purpose": "Uploaded inspection reports and extracted report metadata.",
                    "safeFields": ["id", "project_id", "site_name", "source_filename", "download_url", "inspection_type", "trade", "inspector", "report_date", "status", "summary", "units", "uploaded_at"],
                    "blockedFields": ["storage_path", "file_hash", "file_tag unless needed for duplicate support"],
                    "coveredBy": ["GET /reports", "GET /reports/{report_id}", "get_reports_summary", "get_report_detail"],
                    "tenantRowCount": len(snapshot.documents),
                },
                {
                    "table": "jobs",
                    "purpose": "Report ingestion/extraction status, extractor name, errors, raw text excerpt, and raw extraction payload for processing diagnostics.",
                    "safeFields": ["id", "document_id", "status", "extractor", "error_message", "started_at", "completed_at", "raw_text_excerpt"],
                    "blockedFields": ["full raw_payload_json unless a specific diagnostic needs summarized extraction shape"],
                    "coveredBy": ["get_ingestion_jobs"],
                    "tenantRowCount": len(snapshot.jobs),
                },
                {
                    "table": "findings",
                    "purpose": "Extracted issues/defects from reports, including severity, status, trade, location, recurrence, and close-out state.",
                    "safeFields": ["id", "document_id", "project_id", "title", "description", "category", "trade", "severity", "status", "location", "unit_label", "recurrence_risk", "reinspections", "last_sent_to", "created_at", "closed_at"],
                    "coveredBy": ["GET /issues", "GET /issues/{issue_id}", "GET /tracker", "GET /tracker/{issue_id}", "get_tracker_summary", "get_issue_detail"],
                    "tenantRowCount": len(snapshot.findings),
                },
                {
                    "table": "predicted_inspections",
                    "purpose": "Upcoming predicted inspections and risk levels generated from report extraction.",
                    "safeFields": ["id", "project_id", "inspection_type", "site_name", "expected_date", "risk_level", "source", "created_at"],
                    "coveredBy": ["GET /inspection-risk", "GET /dashboard/risk", "GET /dashboard/upcoming-risk", "get_inspection_risk", "get_dashboard_risk", "get_upcoming_risk"],
                    "tenantRowCount": len(snapshot.predicted_inspections),
                },
            ],
            "analyticsViews": [
                {"view": "analytics_report_summary_v", "purpose": "Report-level finding counts, severe/open findings, highest severity.", "coveredBy": ["report and dashboard tools"]},
                {"view": "analytics_company_metrics_v", "purpose": "Company-level totals, failure rate, findings/report, open findings, reinspection rate.", "coveredBy": ["get_company_metrics", "get_dashboard_summary"]},
                {"view": "analytics_project_metrics_v", "purpose": "Project-level totals and performance metrics.", "coveredBy": ["get_project_catalog", "get_project_metrics"]},
                {"view": "analytics_top_failure_drivers_v", "purpose": "Repeated failure drivers by project, inspection type, trade, title.", "coveredBy": ["get_top_failures", "get_performance_metrics"]},
                {"view": "analytics_upcoming_risk_v", "purpose": "Upcoming predicted inspections enriched with project names/slugs.", "coveredBy": ["get_inspection_risk", "get_dashboard_risk", "get_upcoming_risk"]},
            ],
        }


class TenantMembersTool(SoterraTenantTool):
    name = "get_tenant_members"
    description = "Return the current tenant's safe member directory. Use for questions about team members, roles, admins, account users, or who has access."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            members = self.repository.list_members(tenant_id=tenant_id)
            return {
                "items": [
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "email": item.get("email"),
                        "role": item.get("role"),
                        "createdAt": item.get("created_at"),
                    }
                    for item in members
                ],
                "count": len(members),
                "roleBreakdown": dict(Counter(item.get("role", "unknown") for item in members)),
            }
        except Exception:
            return {"items": [], "count": 0, "error": "Tenant members could not be loaded."}


class ProjectCatalogTool(SoterraTenantTool):
    name = "get_project_catalog"
    description = "Return all tenant projects and site metadata. Use for project/site lookup, project slug discovery, address questions, and choosing get_project_metrics."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            report_counts = Counter(item.get("project_id") for item in snapshot.documents)
            issue_counts = Counter(item.get("project_id") for item in snapshot.findings)
            return {
                "items": [
                    {
                        "id": item.get("id"),
                        "slug": item.get("slug"),
                        "name": item.get("name"),
                        "site": item.get("site_name"),
                        "address": item.get("address"),
                        "createdAt": item.get("created_at"),
                        "reportCount": report_counts.get(item.get("id"), 0),
                        "issueCount": issue_counts.get(item.get("id"), 0),
                    }
                    for item in snapshot.projects
                ],
                "count": len(snapshot.projects),
            }
        except Exception:
            return {"items": [], "count": 0, "error": "Projects could not be loaded."}


class IngestionJobsTool(SoterraTenantTool):
    name = "get_ingestion_jobs"
    description = "Return report ingestion and extraction job status. Use for questions about upload processing, extraction failures, pending reports, extractor source, and raw text excerpts."
    inputs = {
        **SoterraTenantTool.inputs,
        "limit": {"type": "integer", "description": "Maximum number of jobs to return, capped at 50.", "nullable": True},
        "status": {"type": "string", "description": "Optional job status filter such as processing, completed, or failed.", "nullable": True},
    }

    def forward(self, tenant_id: str, limit: int = 20, status: str | None = None) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            documents = {item.get("id"): item for item in snapshot.documents}
            jobs = snapshot.jobs
            if status:
                normalized_status = status.strip().lower()
                jobs = [item for item in jobs if str(item.get("status", "")).lower() == normalized_status]
            capped = min(max(safe_int(limit), 1), 50)
            return {
                "items": [
                    {
                        "id": item.get("id"),
                        "documentId": item.get("document_id"),
                        "reportName": documents.get(item.get("document_id"), {}).get("source_filename"),
                        "project": documents.get(item.get("document_id"), {}).get("project_name"),
                        "status": item.get("status"),
                        "extractor": item.get("extractor"),
                        "errorMessage": item.get("error_message"),
                        "rawTextExcerpt": (item.get("raw_text_excerpt") or "")[:700],
                        "startedAt": item.get("started_at"),
                        "completedAt": item.get("completed_at"),
                    }
                    for item in jobs[:capped]
                ],
                "count": len(jobs),
                "statusBreakdown": dict(Counter(item.get("status", "unknown") for item in snapshot.jobs)),
            }
        except Exception:
            return {"items": [], "count": 0, "error": "Ingestion jobs could not be loaded."}


class IssueAnalyticsTool(SoterraTenantTool):
    name = "get_issue_analytics"
    description = "Return construction analyst summaries across reports, issues, sites, projects, categories, closed work, passed inspections, and reinspection drivers. Use for natural construction questions like what to fix this week, highest-issue sites, closed project issues, passed sites, top issue categories, and repeat causes."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            findings = snapshot.findings
            documents = snapshot.documents
            by_site = Counter(item.get("site_name", "Unknown site") for item in findings)
            by_category = Counter(item.get("category", "General") for item in findings)
            by_trade = Counter(item.get("trade", "General") for item in findings)
            open_items = [item for item in findings if item.get("status") == "Open"]
            closed_items = [item for item in findings if item.get("status") == "Closed"]
            reinspection_items = [item for item in findings if safe_int(item.get("reinspections")) > 0 or safe_int(item.get("recurrence_risk")) >= 60]
            site_documents: dict[str, list[dict]] = {}
            for document in documents:
                site_documents.setdefault(document.get("site_name", "Unknown site"), []).append(document)
            passed_sites = []
            for site, docs in site_documents.items():
                site_findings = [item for item in findings if item.get("site_name") == site]
                open_site_findings = [item for item in site_findings if item.get("status") == "Open"]
                if docs and all(doc.get("status") == "Completed" for doc in docs) and not open_site_findings:
                    passed_sites.append(
                        {
                            "site": site,
                            "reports": len(docs),
                            "closedIssues": len([item for item in site_findings if item.get("status") == "Closed"]),
                        }
                    )

            return {
                "topSitesByIssueCount": [
                    {"site": site, "issueCount": count}
                    for site, count in by_site.most_common(10)
                ],
                "categoryBreakdown": [
                    {"category": category, "issueCount": count}
                    for category, count in by_category.most_common(10)
                ],
                "tradeBreakdown": [
                    {"trade": trade, "issueCount": count}
                    for trade, count in by_trade.most_common(10)
                ],
                "openHighPriorityThisWeek": _compact_findings(
                    sorted(open_items, key=lambda item: (_priority_rank(item), -_days_open(item)))[:10]
                ),
                "closedProjectIssues": _compact_findings(
                    sorted(closed_items, key=lambda item: item.get("closed_at") or item.get("created_at") or "", reverse=True)[:10]
                ),
                "passedSites": passed_sites,
                "reinspectionRootCauses": _compact_findings(
                    sorted(reinspection_items, key=lambda item: (-(safe_int(item.get("reinspections"))), -safe_int(item.get("recurrence_risk"))))[:10]
                ),
                "summary": {
                    "totalIssues": len(findings),
                    "openIssues": len(open_items),
                    "closedIssues": len(closed_items),
                    "passedSiteCount": len(passed_sites),
                },
            }
        except Exception:
            return {"topSitesByIssueCount": [], "categoryBreakdown": [], "openHighPriorityThisWeek": [], "error": "Issue analytics could not be loaded."}


class ReportsSummaryTool(SoterraTenantTool):
    name = "get_reports_summary"
    description = "Return a compact list of reports for the current tenant. Use for report list, recent inspections, status, project, and report risk overview questions."
    inputs = {
        **SoterraTenantTool.inputs,
        "limit": {"type": "integer", "description": "Maximum number of reports to return, capped at 50.", "nullable": True},
    }

    def forward(self, tenant_id: str, limit: int = 20) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            items = build_report_list(self._snapshot()).get("items", [])
            compact = [
                {
                    "id": item.get("id"),
                    "project": item.get("project"),
                    "site": item.get("site"),
                    "date": item.get("createdAt"),
                    "status": item.get("status"),
                    "inspectionType": item.get("inspectionType"),
                    "summary": item.get("summary"),
                    "issueCount": len(item.get("issues") or []),
                    "highestSeverity": _highest_severity(item.get("issues") or []),
                }
                for item in items[: min(max(safe_int(limit), 1), 50)]
            ]
            return {"items": compact, "count": len(items)}
        except Exception:
            return {"items": [], "count": 0, "error": "Reports could not be loaded."}


class ReportDetailTool(SoterraTenantTool):
    name = "get_report_detail"
    description = "Return report metadata, findings, predicted inspections, and a safe excerpt for one report. Use when a report id is present or the user asks about this report."
    inputs = {
        **SoterraTenantTool.inputs,
        "report_id": {"type": "string", "description": "Report id to inspect."},
    }

    def forward(self, tenant_id: str, report_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            payload = build_report_detail(snapshot, report_id)
            if not payload:
                return {"found": False, "report_id": report_id}
            item = payload["item"]
            predictions = [
                {
                    "id": row.get("id"),
                    "inspectionType": row.get("inspection_type"),
                    "expectedDate": row.get("expected_date"),
                    "riskLevel": row.get("risk_level"),
                    "source": row.get("source"),
                }
                for row in snapshot.predicted_inspections
                if row.get("project_slug") == _project_slug_from_report(snapshot.documents, report_id)
            ][:5]
            return {
                "found": True,
                "item": {
                    "id": item.get("id"),
                    "project": item.get("project"),
                    "site": item.get("site"),
                    "date": item.get("createdAt"),
                    "status": item.get("status"),
                    "inspectionType": item.get("inspectionType"),
                    "summary": item.get("summary"),
                    "issues": _compact_issues(item.get("issues") or [], limit=12),
                    "predictedInspections": predictions,
                    "safeTextExcerpt": (item.get("summary") or "")[:700],
                },
            }
        except Exception:
            return {"found": False, "report_id": report_id, "error": "Report could not be loaded."}


class TrackerSummaryTool(SoterraTenantTool):
    name = "get_tracker_summary"
    description = "Return current tracker and issue summary. Use for open, overdue, high-priority, ready, closed, or first-attention issue questions."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            findings = snapshot.findings
            open_items = [item for item in findings if item.get("status") == "Open"]
            overdue = [item for item in open_items if _days_open(item) > 7]
            high = [item for item in findings if item.get("severity") in {"High", "Critical"} and item.get("status") != "Closed"]
            ordered = sorted(findings, key=lambda item: (_priority_rank(item), -_days_open(item), item.get("created_at", "")))
            return {
                "openIssueCount": len(open_items),
                "overdueIssueCount": len(overdue),
                "highPriorityIssueCount": len(high),
                "statusBreakdown": dict(Counter(item.get("status", "Unknown") for item in findings)),
                "categoryBreakdown": dict(Counter(item.get("category", "General") for item in findings)),
                "issues": _compact_findings(ordered[:15]),
            }
        except Exception:
            return {"openIssueCount": 0, "overdueIssueCount": 0, "highPriorityIssueCount": 0, "issues": [], "error": "Tracker could not be loaded."}


class IssueDetailTool(SoterraTenantTool):
    name = "get_issue_detail"
    description = "Return detail for one issue. Use when an issue id is present or the user asks about a specific tracker item."
    inputs = {
        **SoterraTenantTool.inputs,
        "issue_id": {"type": "string", "description": "Issue id to inspect."},
    }

    def forward(self, tenant_id: str, issue_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            payload = build_issue_detail(snapshot, issue_id)
            if not payload:
                return {"found": False, "issue_id": issue_id}
            raw = next((item for item in snapshot.findings if item.get("id") == issue_id), {})
            item = payload["item"]
            return {
                "found": True,
                "item": {
                    "id": item.get("id"),
                    "title": item.get("description"),
                    "status": item.get("status"),
                    "severity": raw.get("severity"),
                    "priority": raw.get("severity"),
                    "project": raw.get("project_name"),
                    "projectSlug": raw.get("project_slug"),
                    "site": item.get("site"),
                    "dueDate": None,
                    "dateIdentified": item.get("dateIdentified"),
                    "daysOpen": _days_open(raw),
                    "linkedReport": raw.get("document_id"),
                    "notes": raw.get("description"),
                    "trade": raw.get("trade"),
                    "location": raw.get("location"),
                    "lastSentTo": raw.get("last_sent_to"),
                },
            }
        except Exception:
            return {"found": False, "issue_id": issue_id, "error": "Issue could not be loaded."}


class DashboardSummaryTool(SoterraTenantTool):
    name = "get_dashboard_summary"
    description = "Return overall dashboard metrics already used by the frontend. Use for broad company status, inspection counts, open issue counts, and dashboard overview questions."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            return _compact_dashboard(build_dashboard_overview(self._snapshot()))
        except Exception:
            return {"error": "Dashboard summary could not be loaded."}


class CompanyMetricsTool(SoterraTenantTool):
    name = "get_company_metrics"
    description = "Return company-level analytics. Use for company-wide performance, comparing projects, inspection types, and broad portfolio trends."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            payload = build_company_page(self._snapshot())
            return {"projects": payload.get("projects", [])[:20], "inspectionTypes": payload.get("inspectionTypes", [])[:20]}
        except Exception:
            return {"projects": [], "inspectionTypes": [], "error": "Company metrics could not be loaded."}


class PerformanceMetricsTool(SoterraTenantTool):
    name = "get_performance_metrics"
    description = "Return /dashboard/performance equivalent data. Use for recurring problems, most common failures, issue trend, close-out performance, reinspection rate, and performance analytics questions."
    inputs = {
        **SoterraTenantTool.inputs,
        "inspection_type": {"type": "string", "description": "Inspection type filter. Use All types for all data.", "nullable": True},
    }

    def forward(self, tenant_id: str, inspection_type: str | None = "All types") -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            payload = build_performance_page(self._snapshot(), inspection_type or "All types")
            return {
                "filter": payload.get("filter", {}),
                "topFailureDrivers": payload.get("topFailureDrivers", [])[:10],
                "recurringRisks": payload.get("recurringRisks", [])[:10],
                "recurrenceSummary": payload.get("recurrenceSummary"),
            }
        except Exception:
            return {"topFailureDrivers": [], "recurringRisks": [], "error": "Performance metrics could not be loaded."}


class ProjectMetricsTool(SoterraTenantTool):
    name = "get_project_metrics"
    description = "Return project-level analytics for a project slug. Use when project_slug is provided or the user asks about one project's risks, failures, or inspection status."
    inputs = {
        **SoterraTenantTool.inputs,
        "project_slug": {"type": "string", "description": "Project slug to inspect."},
    }

    def forward(self, tenant_id: str, project_slug: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            payload = build_project_page(self._snapshot(), project_slug)
            if not payload:
                return {"found": False, "project_slug": project_slug}
            return {"found": True, **payload}
        except Exception:
            return {"found": False, "project_slug": project_slug, "error": "Project metrics could not be loaded."}


class DashboardRiskTool(SoterraTenantTool):
    name = "get_dashboard_risk"
    description = "Return dashboard risk data. Use for highest-risk project, upcoming inspection risk, or which work area needs attention now."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            if not snapshot.documents:
                return {
                    "inspections": [],
                    "likelyFailures": [],
                    "selectedInspectionId": "",
                    "summary": "No uploaded reports are available for inspection risk.",
                }
            payload = build_dashboard_risk(snapshot)
            return {
                "inspections": payload.get("inspections", [])[:12],
                "likelyFailures": payload.get("likelyFailures", [])[:8],
                "selectedInspectionId": payload.get("selectedInspectionId"),
                "summary": payload.get("likelyFailureSubtitle"),
            }
        except Exception:
            return {"inspections": [], "likelyFailures": [], "error": "Risk data could not be loaded."}


class LiveTrackerTool(SoterraTenantTool):
    name = "get_live_tracker"
    description = "Return /dashboard/live-tracker equivalent counts. Use for live tracker dashboard card questions about open issues, overdue issues, and ready-for-inspection work."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            return build_dashboard_live_tracker(self._snapshot())
        except Exception:
            return {"openIssues": 0, "overdue": 0, "readyForInspection": 0, "error": "Live tracker could not be loaded."}


class TopFailuresTool(SoterraTenantTool):
    name = "get_top_failures"
    description = "Return top repeated failure types and categories. Use for repeat problem, root-cause, waterproofing, quality trend, and failure driver questions."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            payload = build_dashboard_top_failures(self._snapshot(), "All types")
            return {
                "summary": payload.get("summary", []),
                "drivers": payload.get("drivers", [])[:10],
                "failureDistribution": payload.get("failureDistribution", [])[:8],
                "recurringRisk": payload.get("recurringRisk", [])[:8],
            }
        except Exception:
            return {"drivers": [], "error": "Top failures could not be loaded."}


class UpcomingRiskTool(SoterraTenantTool):
    name = "get_upcoming_risk"
    description = "Return upcoming predicted inspection risk. Use for upcoming inspections, next risky inspection, high-risk dates, and why upcoming work might fail."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            if not snapshot.documents:
                return {
                    "title": "No upcoming inspections",
                    "daysUntilInspection": 0,
                    "likelyFailures": [],
                    "href": "/app/inspection-risk",
                    "description": "Upload a report first to show upcoming inspection risk here.",
                }
            return build_dashboard_upcoming_risk(snapshot)
        except Exception:
            return {"title": "No upcoming inspections", "likelyFailures": [], "error": "Upcoming risk could not be loaded."}


class InsightsPreviewTool(SoterraTenantTool):
    name = "get_insights_preview"
    description = "Return /dashboard/insights-preview equivalent data. Use for a quick preview of root causes and high-risk areas on the dashboard."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            return build_dashboard_insights_preview(self._snapshot())
        except Exception:
            return {"rootCauses": [], "highRiskAreas": [], "error": "Insights preview could not be loaded."}


class InspectionRiskTool(SoterraTenantTool):
    name = "get_inspection_risk"
    description = "Return /inspection-risk equivalent data. Use for inspection-risk page questions about upcoming inspections, date ranges, sites, inspection types, and likely failure items."
    inputs = {
        **SoterraTenantTool.inputs,
        "site": {"type": "string", "description": "Optional site filter.", "nullable": True},
        "date_range": {"type": "string", "description": "Optional date range such as 30d, 60d, or 90d.", "nullable": True},
        "inspection_type": {"type": "string", "description": "Optional inspection type filter.", "nullable": True},
    }

    def forward(
        self,
        tenant_id: str,
        site: str | None = None,
        date_range: str | None = None,
        inspection_type: str | None = None,
    ) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            if not snapshot.documents:
                return {
                    "filters": {
                        "sites": ["All Sites"],
                        "dateRanges": [
                            {"label": "Next 30 days", "value": "30d"},
                            {"label": "Next 60 days", "value": "60d"},
                            {"label": "Next 90 days", "value": "90d"},
                        ],
                        "selectedSite": site or "All Sites",
                        "selectedDateRange": date_range or "30d",
                        "inspectionTypes": [],
                        "selectedInspectionType": "General",
                    },
                    "upcomingInspections": [],
                    "likelyFailureItems": [],
                }
            payload = build_inspection_risk_page(snapshot, site, date_range, inspection_type)
            return {
                "filters": payload.get("filters", {}),
                "upcomingInspections": payload.get("upcomingInspections", [])[:12],
                "likelyFailureItems": payload.get("likelyFailureItems", [])[:10],
            }
        except Exception:
            return {"upcomingInspections": [], "likelyFailureItems": [], "error": "Inspection risk could not be loaded."}


class InsightsTool(SoterraTenantTool):
    name = "get_insights"
    description = "Return generated or stored insights. Use for high-risk areas, root causes, repeated patterns, and practical improvement themes."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        try:
            snapshot = self._snapshot()
            insights = build_insights_page(snapshot)
            preview = build_dashboard_insights_preview(snapshot)
            return {
                "rootCauses": insights.get("rootCauses", [])[:8],
                "highRiskAreas": insights.get("highRiskAreas", [])[:8],
                "repeatedPatterns": insights.get("repeatedPatterns", [])[:10],
                "preview": preview,
            }
        except Exception:
            return {"rootCauses": [], "highRiskAreas": [], "repeatedPatterns": [], "error": "Insights could not be loaded."}


def _compact_issues(issues: list[dict], *, limit: int) -> list[dict]:
    return [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "description": item.get("description"),
            "severity": item.get("severity"),
            "status": item.get("status"),
            "category": item.get("category"),
            "trade": item.get("trade"),
            "location": item.get("location"),
            "unitLabel": item.get("unitLabel"),
            "recurrenceRisk": item.get("recurrenceRisk"),
            "reinspections": item.get("reinspections"),
        }
        for item in issues[:limit]
    ]


def _compact_findings(findings: list[dict]) -> list[dict]:
    return [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "status": item.get("status"),
            "severity": item.get("severity"),
            "project": item.get("project_name"),
            "projectSlug": item.get("project_slug"),
            "site": item.get("site_name"),
            "trade": item.get("trade"),
            "category": item.get("category"),
            "dateIdentified": str(item.get("created_at", ""))[:10],
            "daysOpen": _days_open(item),
            "linkedReport": item.get("document_id"),
        }
        for item in findings
    ]


def _compact_dashboard(payload: dict) -> dict:
    return {
        "metrics": payload.get("metrics", []),
        "liveTracker": payload.get("liveTracker", {}),
        "upcomingRisks": payload.get("upcomingRisks", [])[:5],
        "topFailureDrivers": payload.get("topFailureDrivers", [])[:8],
        "issueStatusSummary": payload.get("issueStatusSummary", []),
    }


def _highest_severity(issues: list[dict]) -> str | None:
    rank = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
    if not issues:
        return None
    return max((item.get("severity") for item in issues), key=lambda value: rank.get(str(value), 0))


def _priority_rank(item: dict) -> int:
    rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    overdue_penalty = -1 if item.get("status") == "Open" and _days_open(item) > 7 else 0
    return rank.get(str(item.get("severity")), 4) + overdue_penalty


def _days_open(item: dict) -> int:
    from datetime import UTC, datetime

    created_at = item.get("created_at")
    if not created_at:
        return 0
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        return max(0, (datetime.now(tz=UTC).date() - parsed.date()).days)
    except ValueError:
        return 0


def _project_slug_from_report(documents: list[dict], report_id: str) -> str | None:
    document = next((item for item in documents if item.get("id") == report_id), None)
    return document.get("project_slug") if document else None
