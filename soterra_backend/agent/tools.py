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
            ],
            "mutationRoutes": [
                "POST /reports",
                "DELETE /reports",
                "DELETE /reports/{report_id}",
                "PATCH /issues/{issue_id}",
                "PATCH /tracker/{issue_id}",
            ],
            "note": "The chat agent answers questions from read tools only. It does not mutate reports or issues.",
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
