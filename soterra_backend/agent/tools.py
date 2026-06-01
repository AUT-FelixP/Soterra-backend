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
from ..models import RepositorySnapshot
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


def build_soterra_tools(
    repository: RepositoryBackend,
    tenant_id: str,
    recorder: ToolRecorder | None = None,
    *,
    role: str = "tenant_admin",
) -> list[Tool]:
    return [
        tool(repository, tenant_id, recorder, role=role)
        for tool in (
            ApiRouteCatalogTool, SchemaCatalogTool, BackendCatalogTool, DataSchemaCatalogTool,
            TenantMembersTool, ProjectCatalogTool, IngestionJobsTool, IssueAnalyticsTool,
            SummarizeReportsTool, ListOpenIssuesTool, TrackerStateTool, DashboardMetricsTool,
            RiskSummaryTool, ReportsSummaryTool, ReportDetailTool, TrackerSummaryTool,
            IssueDetailTool, DashboardSummaryTool, CompanyMetricsTool, PerformanceMetricsTool,
            ProjectMetricsTool, DashboardRiskTool, LiveTrackerTool, TopFailuresTool,
            UpcomingRiskTool, InsightsPreviewTool, InspectionRiskTool, InsightsTool,
        )
    ]


class SoterraTenantTool(Tool):
    inputs = {"tenant_id": {"type": "string", "description": "Current authenticated tenant id."}}
    output_type = "object"

    def __init__(
        self,
        repository: RepositoryBackend,
        tenant_id: str,
        recorder: ToolRecorder | None = None,
        *,
        role: str = "tenant_admin",
    ) -> None:
        super().__init__()
        self.repository = repository
        self.tenant_id = tenant_id
        self.recorder = recorder
        self.role = role

    def _record(self) -> None:
        if self.recorder:
            self.recorder(self.name)

    def _check_tenant(self, tenant_id: str) -> dict | None:
        if tenant_id != self.tenant_id:
            self._record()
            return {"found": False, "error": "Record not found for this tenant."}
        return None

    def _snapshot(self):
        return _active_snapshot(self.repository.load_snapshot(self.tenant_id))


class SchemaCatalogTool(SoterraTenantTool):
    name = "get_schema_catalog"
    description = "Return the safe Soterra agent data catalogue, available data domains, safe tools, and forbidden data. Use to understand what backend data the agent can query."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        return {
            "available_data_domains": [
                "reports",
                "documents",
                "projects",
                "findings",
                "jobs",
                "tracker",
                "dashboard",
                "company_metrics",
                "project_metrics",
                "inspection_risk",
                "top_failure_drivers",
                "upcoming_risk",
            ],
            "safe_tools": [
                "summarize_reports",
                "get_report_detail",
                "summarize_issues",
                "get_tracker_state",
                "get_dashboard_metrics",
                "get_project_metrics",
                "get_risk_summary",
                "get_ingestion_jobs",
            ],
            "forbidden_data": [
                "password_hash",
                "token_hash",
                "reset_token_hash",
                "raw_storage_path",
                "cross_tenant_data",
            ],
            "tenant_scope": "current authenticated tenant and user only",
            "active_records_only": True,
        }


class BackendCatalogTool(SoterraTenantTool):
    name = "get_backend_catalog"
    description = "Return the static Soterra backend data-domain catalog. Use to choose between reports, issues, tracker, dashboard, risk, jobs, and members tools."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        return {
            "reports": {
                "routes": ["GET /reports", "GET /reports/{report_id}", "DELETE /reports/{report_id}"],
                "tables": ["documents", "projects", "findings", "jobs"],
                "use_for": ["uploaded reports", "inspection summaries", "report detail", "failed checklist items", "source document questions"],
            },
            "issues": {
                "routes": ["GET /issues", "GET /issues/{issue_id}", "PATCH /issues/{issue_id}"],
                "tables": ["findings", "documents", "projects"],
                "use_for": ["open issues", "urgent issues", "defects", "what needs fixing", "issue location", "issue status", "close-out actions"],
            },
            "tracker": {
                "routes": ["GET /tracker", "GET /tracker/{issue_id}", "PATCH /tracker/{issue_id}"],
                "tables": ["findings"],
                "use_for": ["field work list", "issue owner/trade", "reinspection count", "last sent to", "open/closed tracker state"],
            },
            "dashboard": {
                "routes": ["GET /dashboard", "GET /dashboard/company", "GET /dashboard/performance", "GET /dashboard/insights", "GET /dashboard/project/{slug}", "GET /dashboard/top-failures", "GET /dashboard/live-tracker"],
                "source": "analytics.py + RepositorySnapshot",
                "use_for": ["company overview", "project performance", "top failure drivers", "close-out rate", "aging issues", "summary metrics"],
            },
            "risk": {
                "routes": ["GET /dashboard/risk", "GET /dashboard/upcoming-risk", "GET /inspection-risk"],
                "tables": ["predicted_inspections", "findings", "documents", "projects"],
                "use_for": ["upcoming inspection risk", "highest risk project", "reinspection risk", "future inspections"],
            },
            "jobs": {"tables": ["jobs", "documents"], "use_for": ["extraction status", "failed ingestion", "processing state", "why a report is not appearing"]},
            "members": {
                "routes": ["GET /tenants/members", "POST /tenants/members", "DELETE /tenants/members/{user_id}"],
                "use_for": ["team members", "tenant users", "admin/member roles"],
                "restrictions": ["Do not expose secrets", "Do not expose password hashes", "Do not expose token hashes"],
            },
        }


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
                {"route": "GET /agent/chat/sessions", "tool": "chat history repository", "returns": "Current user's active agent chat sessions for this tenant."},
                {"route": "GET /agent/chat/sessions/{session_id}", "tool": "chat history repository", "returns": "Current user's tenant-scoped chat session and messages."},
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
                "DELETE /agent/chat/sessions/{session_id}",
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
                    "safeFields": ["id", "project_id", "site_name", "source_filename", "inspection_type", "trade", "inspector", "report_date", "status", "summary", "units", "uploaded_at"],
                    "blockedFields": ["storage_path", "download_url", "file_hash", "file_tag unless needed for duplicate support"],
                    "coveredBy": ["GET /reports", "GET /reports/{report_id}", "get_reports_summary", "get_report_detail"],
                    "tenantRowCount": len(snapshot.documents),
                },
                {
                    "table": "jobs",
                    "purpose": "Report ingestion/extraction status, extractor name, and safe processing diagnostics.",
                    "safeFields": ["id", "document_id", "status", "extractor", "error_message", "started_at", "completed_at"],
                    "blockedFields": ["raw_text_excerpt", "raw_payload_json"],
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
        if self.role not in {"admin", "tenant_admin"}:
            return {"items": [], "count": 0, "error": "Tenant administrator access required."}
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
    description = "Return report ingestion and extraction job status. Use for questions about upload processing, extraction failures, pending reports, and extractor source."
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
            jobs_payload = [
                    {
                        "job_id": item.get("id"),
                        "id": item.get("id"),
                        "documentId": item.get("document_id"),
                        "document_id": item.get("document_id"),
                        "document_title": documents.get(item.get("document_id"), {}).get("source_filename"),
                        "reportName": documents.get(item.get("document_id"), {}).get("source_filename"),
                        "project": documents.get(item.get("document_id"), {}).get("project_name"),
                        "status": item.get("status"),
                        "extractor": item.get("extractor"),
                        "error": item.get("error_message"),
                        "errorMessage": item.get("error_message"),
                        "started_at": item.get("started_at"),
                        "completed_at": item.get("completed_at"),
                        "startedAt": item.get("started_at"),
                        "completedAt": item.get("completed_at"),
                    }
                    for item in jobs[:capped]
                ]
            return {
                "jobs": jobs_payload,
                "items": jobs_payload,
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


class SummarizeReportsTool(SoterraTenantTool):
    name = "summarize_reports"
    description = "Return active report summaries with project, outcome, failed items, and open/high-priority finding counts."
    inputs = {
        **SoterraTenantTool.inputs,
        "limit": {"type": "integer", "description": "Maximum number of reports to return, capped at 50.", "nullable": True},
    }

    def forward(self, tenant_id: str, limit: int = 20) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        snapshot = self._snapshot()
        capped = min(max(safe_int(limit), 1), 50)
        findings_by_doc: dict[str, list[dict]] = {}
        for finding in snapshot.findings:
            findings_by_doc.setdefault(str(finding.get("document_id")), []).append(finding)
        reports = []
        for document in snapshot.documents[:capped]:
            findings = findings_by_doc.get(str(document.get("id")), [])
            open_findings = [item for item in findings if item.get("status") == "Open"]
            high = [item for item in open_findings if item.get("severity") in {"High", "Critical"}]
            reports.append(
                {
                    "report_id": document.get("id"),
                    "document_id": document.get("id"),
                    "project_id": document.get("project_id"),
                    "project_name": document.get("project_name"),
                    "project_slug": document.get("project_slug"),
                    "project_address": document.get("address"),
                    "report_title": _report_title(document),
                    "inspection_type": document.get("inspection_type"),
                    "report_date": document.get("report_date"),
                    "overall_outcome": document.get("status") or "Unknown",
                    "summary": document.get("summary"),
                    "failed_items": [_issue_payload(item, document) for item in open_findings[:12]],
                    "open_findings_count": len(open_findings),
                    "high_priority_count": len(high),
                    "source_status": {"active_document": True, "deleted_at": None},
                }
            )
        return {"total_reports": len(snapshot.documents), "reports": reports, "items": reports, "count": len(snapshot.documents)}


class ListOpenIssuesTool(SoterraTenantTool):
    name = "list_open_issues"
    description = "Return active open issues with location, priority, trade, source report/date, and recommended action. Use for urgent issues, what to fix, issue locations, and tracker work lists."
    inputs = {
        **SoterraTenantTool.inputs,
        "project_slug": {"type": "string", "description": "Optional project slug filter.", "nullable": True},
        "limit": {"type": "integer", "description": "Maximum number of issues to return, capped at 50.", "nullable": True},
    }

    def forward(self, tenant_id: str, project_slug: str | None = None, limit: int = 50) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        snapshot = self._snapshot()
        docs = {item.get("id"): item for item in snapshot.documents}
        open_findings = [item for item in snapshot.findings if item.get("status") == "Open"]
        if project_slug:
            open_findings = [item for item in open_findings if item.get("project_slug") == project_slug]
        capped = min(max(safe_int(limit), 1), 50)
        issues = [_issue_payload(item, docs.get(item.get("document_id"), {})) for item in sorted(open_findings, key=_priority_rank)[:capped]]
        project = _dominant_project(open_findings, snapshot.projects)
        high = [item for item in open_findings if item.get("severity") in {"High", "Critical"}]
        overdue = [item for item in open_findings if _days_open(item) > 7]
        return {
            "project_name": project.get("name") or (issues[0].get("project_name") if issues else None),
            "project_slug": project.get("slug") or (issues[0].get("project_slug") if issues else None),
            "project_address": project.get("address") or _first_value(docs.values(), "address"),
            "total_open": len(open_findings),
            "high_priority_open": len(high),
            "overdue_open": len(overdue),
            "issues": issues,
            "remaining_count": max(0, len(open_findings) - len(issues)),
        }


class TrackerStateTool(SoterraTenantTool):
    name = "get_tracker_state"
    description = "Return tracker state with issue ownership, status, reinspections, last sent to, closure state, location, and trade."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        snapshot = self._snapshot()
        docs = {item.get("id"): item for item in snapshot.documents}
        issues = [_issue_payload(item, docs.get(item.get("document_id"), {})) | {
            "reinspections": safe_int(item.get("reinspections")),
            "last_sent_to": item.get("last_sent_to"),
            "closed_at": item.get("closed_at"),
        } for item in sorted(snapshot.findings, key=_priority_rank)]
        return {
            "total_issues": len(snapshot.findings),
            "open": len([item for item in snapshot.findings if item.get("status") == "Open"]),
            "closed": len([item for item in snapshot.findings if item.get("status") == "Closed"]),
            "by_status": dict(Counter(item.get("status", "Unknown") for item in snapshot.findings)),
            "by_trade": dict(Counter(item.get("trade", "General") for item in snapshot.findings)),
            "issues": issues[:50],
        }


class DashboardMetricsTool(SoterraTenantTool):
    name = "get_dashboard_metrics"
    description = "Return dashboard overview metrics, open/high-priority issue counts, top failure drivers, and close-out performance."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        snapshot = self._snapshot()
        open_findings = [item for item in snapshot.findings if item.get("status") == "Open"]
        high = [item for item in open_findings if item.get("severity") in {"High", "Critical"}]
        overdue = [item for item in open_findings if _days_open(item) > 7]
        closed = [item for item in snapshot.findings if item.get("status") == "Closed"]
        drivers = [item.get("issue") for item in build_dashboard_top_failures(snapshot, "All types").get("drivers", []) if item.get("issue")]
        if not drivers:
            drivers = [item for item, _count in Counter(finding.get("category", "General") for finding in snapshot.findings).most_common(5)]
        return {
            "project_count": len(snapshot.projects),
            "report_count": len(snapshot.documents),
            "open_issue_count": len(open_findings),
            "high_priority_open": len(high),
            "overdue_count": len(overdue),
            "top_failure_drivers": drivers[:5],
            "close_out_performance": {
                "closed_count": len(closed),
                "open_count": len(open_findings),
                "average_age_days": None,
            },
        }


class RiskSummaryTool(SoterraTenantTool):
    name = "get_risk_summary"
    description = "Return highest-risk projects, risk drivers, and recommended risk-reduction actions."

    def forward(self, tenant_id: str) -> dict:
        if mismatch := self._check_tenant(tenant_id):
            return mismatch
        self._record()
        snapshot = self._snapshot()
        by_project = Counter(item.get("project_name", "Unknown project") for item in snapshot.findings if item.get("status") == "Open")
        highest = [{"project_name": name, "open_issue_count": count} for name, count in by_project.most_common(5)]
        categories = [name for name, _ in Counter(item.get("category", "General") for item in snapshot.findings if item.get("status") == "Open").most_common(5)]
        drivers = []
        if highest:
            drivers.append("High number of open high-priority issues")
        drivers.extend(categories)
        return {
            "highest_risk_projects": highest,
            "risk_drivers": drivers,
            "recommended_actions": [
                "Close high-priority weather-tightness and life-safety items first.",
                "Collect close-out evidence before reinspection.",
                "Assign recurring services coordination issues by trade.",
            ],
        }


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
                    "report_id": item.get("id"),
                    "document_id": item.get("id"),
                    "project_name": item.get("project"),
                    "report_title": item.get("title") or item.get("sourceFilename") or item.get("inspectionType"),
                    "inspection_type": item.get("inspectionType"),
                    "report_date": item.get("createdAt"),
                    "status": "active",
                    "overall_outcome": item.get("overallOutcome") or item.get("status") or "Unknown",
                    "summary": item.get("summary"),
                    "failed_items": _failed_items(item.get("issues") or []),
                    "open_findings_count": len([issue for issue in item.get("issues") or [] if issue.get("status") == "Open"]),
                    "closed_findings_count": len([issue for issue in item.get("issues") or [] if issue.get("status") == "Closed"]),
                    "top_findings": _compact_issues(item.get("issues") or [], limit=8),
                    "source": {"active_document": True, "deleted_at": None},
                    "id": item.get("id"),
                    "project": item.get("project"),
                    "site": item.get("site"),
                    "date": item.get("createdAt"),
                    "reportStatus": item.get("status"),
                    "inspectionType": item.get("inspectionType"),
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
                    "report_id": item.get("id"),
                    "document_id": item.get("id"),
                    "project_name": item.get("project"),
                    "report_title": item.get("title") or item.get("inspectionType"),
                    "inspection_type": item.get("inspectionType"),
                    "report_date": item.get("createdAt"),
                    "status": "active",
                    "overall_outcome": item.get("overallOutcome") or item.get("status") or "Unknown",
                    "summary": item.get("summary"),
                    "failed_items": _failed_items(item.get("issues") or []),
                    "open_findings_count": len([issue for issue in item.get("issues") or [] if issue.get("status") == "Open"]),
                    "closed_findings_count": len([issue for issue in item.get("issues") or [] if issue.get("status") == "Closed"]),
                    "top_findings": _compact_issues(item.get("issues") or [], limit=12),
                    "source": {"active_document": True, "deleted_at": None},
                    "id": item.get("id"),
                    "project": item.get("project"),
                    "site": item.get("site"),
                    "date": item.get("createdAt"),
                    "reportStatus": item.get("status"),
                    "inspectionType": item.get("inspectionType"),
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


def _issue_payload(item: dict, document: dict) -> dict:
    title = str(item.get("title") or item.get("description") or "Open issue")
    source_title = _report_title(document)
    report_date = str(document.get("report_date") or item.get("created_at") or "")[:10]
    due_date = _issue_due_date(item)
    return {
        "issue_id": item.get("id"),
        "id": item.get("id"),
        "title": title,
        "status": item.get("status") or "Open",
        "priority": item.get("severity") or "Medium",
        "severity": item.get("severity") or "Medium",
        "trade": item.get("trade") or item.get("category") or "General",
        "category": item.get("category") or "General",
        "location": _normalise_location(item, document),
        "project_name": item.get("project_name") or document.get("project_name"),
        "project_slug": item.get("project_slug") or document.get("project_slug"),
        "source_report": source_title,
        "source_date": report_date,
        "source": f"{source_title}, {_format_date(report_date)}" if report_date else source_title,
        "recommended_action": _recommended_action(title, item),
        "due_date": due_date,
        "dueDate": due_date,
        "linkedReport": item.get("document_id"),
    }


def _issue_due_date(item: dict) -> str | None:
    for key in ("due_date", "dueDate", "target_date", "targetDate", "deadline", "due_at", "dueAt"):
        value = item.get(key)
        if value:
            return str(value)[:10]
    return None


def _normalise_location(item: dict, document: dict) -> str:
    unit = item.get("unit_label")
    location = item.get("location")
    if unit and location:
        return f"{unit} / {location}"
    if location:
        return str(location)
    if unit:
        return str(unit)
    text = f"{item.get('title', '')} {item.get('description', '')}".lower()
    inspection = str(document.get("inspection_type") or item.get("inspection_type") or "").lower()
    if "flash" in text or "cavity" in text:
        return "Level 1 apartments / junction and cavity wall areas"
    if "deck" in text or "balcony" in text or "membrane" in text:
        return "Level 1 deck/balcony areas"
    if "damper" in text:
        return "Level 1 corridor / fire damper locations"
    if "plasterboard" in text:
        return "Level 1 corridor / plasterboard linings"
    if "lift" in text:
        return "Level 5 lift shaft / lift door frame area"
    if "riser" in text or "collar" in text:
        return "Fire-rated risers"
    if "duct" in text or "cabling" in text or "mechanical" in inspection:
        return "Level 3 mechanical/services area"
    if "drainage" in text or "hydraulic" in text or "plumbing" in inspection:
        return "Level 3 hydraulics/drainage areas"
    if document.get("units"):
        return f"{', '.join(map(str, document.get('units')[:3]))} / exact location not specified"
    if "cavity" in inspection:
        return "Level 1 apartments"
    if "fire" in inspection:
        return "Passive fire stopping areas"
    if "services" in inspection:
        return "Project services areas"
    return "Project-wide / exact location not specified"


def _recommended_action(title: str, item: dict) -> str:
    text = title.lower()
    if "flash" in text:
        return "Rectify flashing installation against approved details and provide close-out evidence."
    if "cavity batten" in text:
        return "Correct cavity batten installation to match the approved plans."
    if "membrane" in text or "upstand" in text:
        return "Correct membrane upstand to the required minimum and provide photo evidence."
    if "damper" in text or "breakaway" in text:
        return "Replace with compliant fire damper fixings and provide close-out photos."
    if "plasterboard" in text:
        return "Install missing plasterboard fixings and provide close-out evidence."
    if "penetration" in text or "fire stopping" in text or "collar" in text:
        return "Complete passive fire stopping treatment and capture compliance evidence."
    if "duct" in text or "cabling" in text or "clearance" in text:
        return "Re-coordinate services to remove clashes, maintain clearance, and record QA evidence."
    if "lagging" in text:
        return "Install required acoustic lagging and provide photo evidence."
    return "Assign the responsible trade, rectify the item, and upload close-out evidence."


def _report_title(document: dict) -> str:
    return str(document.get("report_title") or document.get("source_filename") or document.get("inspection_type") or "Inspection report").replace(".pdf", "")


def _dominant_project(findings: list[dict], projects: list[dict]) -> dict:
    if not findings:
        return projects[0] if projects else {}
    project_id, _count = Counter(item.get("project_id") for item in findings).most_common(1)[0]
    return next((item for item in projects if item.get("id") == project_id), {})


def _first_value(items, key: str):
    for item in items:
        if item.get(key):
            return item.get(key)
    return None


def _format_date(value: str) -> str:
    from datetime import datetime

    try:
        return datetime.fromisoformat(value[:10]).strftime("%d %b %Y")
    except ValueError:
        return value


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


def _failed_items(issues: list[dict]) -> list[str]:
    failed = []
    for item in issues:
        status = str(item.get("status") or "").lower()
        severity = str(item.get("severity") or "").lower()
        title = item.get("title") or item.get("description")
        if title and (status in {"open", "ready"} or severity in {"high", "critical", "medium"}):
            failed.append(str(title))
    return failed[:12]


def _active_snapshot(snapshot: RepositorySnapshot) -> RepositorySnapshot:
    projects = [item for item in snapshot.projects if not item.get("deleted_at")]
    active_project_ids = {item.get("id") for item in projects}
    documents = [
        item
        for item in snapshot.documents
        if not item.get("deleted_at") and item.get("project_id") in active_project_ids
    ]
    active_document_ids = {item.get("id") for item in documents}
    findings = [
        item
        for item in snapshot.findings
        if item.get("document_id") in active_document_ids
        and item.get("project_id") in active_project_ids
        and not item.get("document_deleted_at")
        and not item.get("deleted_at")
    ]
    jobs = [
        item
        for item in snapshot.jobs
        if item.get("document_id") in active_document_ids and not item.get("deleted_at")
    ]
    predictions = [
        item
        for item in snapshot.predicted_inspections
        if item.get("project_id") in active_project_ids and not item.get("deleted_at") and documents
    ]
    return RepositorySnapshot(
        projects=projects,
        documents=documents,
        jobs=jobs,
        findings=findings,
        predicted_inspections=predictions,
    )
