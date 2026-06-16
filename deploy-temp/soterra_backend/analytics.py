from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
import re
from statistics import mean

from .issue_intelligence import enrich_finding, enrich_findings, group_similar_issues
from .models import RepositorySnapshot
from .utils import safe_int


def _display_findings(findings: list[dict]) -> list[dict]:
    actionable = enrich_findings(findings, actionable_only=True)
    return actionable or enrich_findings(findings, actionable_only=False)


def build_report_list(snapshot: RepositorySnapshot) -> dict:
    findings = _display_findings(snapshot.findings)
    items = [_report_payload(document, findings) for document in snapshot.documents]
    return {"items": items}


def build_report_detail(snapshot: RepositorySnapshot, report_id: str) -> dict | None:
    findings = _display_findings(snapshot.findings)
    for document in snapshot.documents:
        if document["id"] == report_id:
            return {"item": _report_payload(document, findings)}
    return None


def build_issues_list(snapshot: RepositorySnapshot) -> dict:
    findings = _display_findings(snapshot.findings)
    return {
        "items": [
            {
                "id": finding["id"],
                "description": finding["display_title"],
                "summary": finding["plain_english_summary"],
                "project": finding.get("project_name"),
                "site": finding["site_name"],
                "location": finding.get("location") or finding.get("unit_label"),
                "dateIdentified": finding["created_at"][:10],
                "status": finding["status"],
                "reinspections": finding["reinspections"],
                "closedAt": finding["closed_at"][:10] if finding.get("closed_at") else None,
            }
            for finding in findings
        ]
    }


def build_issue_detail(snapshot: RepositorySnapshot, issue_id: str) -> dict | None:
    finding = next((item for item in snapshot.findings if item["id"] == issue_id), None)
    if not finding:
        return None
    finding = enrich_finding(finding)
    return {
        "item": {
            "id": finding["id"],
            "description": finding["display_title"],
            "summary": finding["plain_english_summary"],
            "project": finding.get("project_name"),
            "site": finding["site_name"],
            "location": finding.get("location") or finding.get("unit_label"),
            "dateIdentified": finding["created_at"][:10],
            "status": finding["status"],
            "reinspections": finding["reinspections"],
            "closedAt": finding["closed_at"][:10] if finding.get("closed_at") else None,
        }
    }


def build_dashboard_overview(snapshot: RepositorySnapshot) -> dict:
    reports = snapshot.documents
    findings = _display_findings(snapshot.findings)
    total_reports = len(reports)
    total_findings = len(findings)
    open_findings = [item for item in findings if item["status"] == "Open"]
    ready_findings = [item for item in findings if item["status"] == "Ready"]
    closed_findings = [item for item in findings if item["status"] == "Closed"]
    units_captured = sorted({unit for document in reports for unit in _document_units(document, findings)})

    return {
        "title": "Dashboard",
        "description": "Key inspection results across all uploaded reports.",
        "metrics": [
            {"label": "Inspections", "value": str(total_reports)},
            {"label": "Issues found", "value": str(total_findings)},
            {"label": "Open issues", "value": str(len(open_findings))},
            {"label": "Units captured", "value": str(len(units_captured))},
        ],
        "liveTracker": {
            "openIssues": len(open_findings),
            "overdue": len([item for item in open_findings if _days_open(item["created_at"]) > 7]),
            "readyForInspection": len(ready_findings),
            "href": "/app/tracker",
        },
        "performanceTrend": _performance_trend(reports, findings),
        "upcomingRisks": _overview_risks(snapshot),
        "topFailureDrivers": _top_failure_driver_rows(findings, limit=5),
        "issueStatusSummary": [
            {"label": "Open", "value": str(len(open_findings))},
            {"label": "Ready", "value": str(len(ready_findings))},
            {"label": "Closed", "value": str(len(closed_findings))},
            {"label": "Avg days to close", "value": _average_close_days(closed_findings)},
        ],
    }


def build_company_page(snapshot: RepositorySnapshot) -> dict:
    findings = _display_findings(snapshot.findings)
    project_groups: dict[str, list[dict]] = defaultdict(list)
    for document in snapshot.documents:
        project_groups[document["project_slug"]].append(document)

    projects = []
    for slug, documents in project_groups.items():
        project_findings = [item for item in findings if item["project_slug"] == slug]
        projects.append(
            {
                "slug": slug,
                "name": documents[0]["project_name"],
                "inspections": len(documents),
                "extractedIssues": str(len(project_findings)),
                "openIssues": str(len([item for item in project_findings if item["status"] == "Open"])),
                "issuesPerInspection": f"{len(project_findings) / max(len(documents), 1):.1f}",
            }
        )

    return {
        "title": "Company performance",
        "description": "A company-wide view of results across all projects.",
        "projects": projects,
        "inspectionTypes": _inspection_type_summary(findings, snapshot.documents),
    }


def build_performance_page(snapshot: RepositorySnapshot, inspection_type: str = "All types") -> dict:
    all_findings = _display_findings(snapshot.findings)
    findings = _filter_findings_by_type(all_findings, inspection_type)
    options = ["All types"] + sorted({item["inspection_type"] for item in all_findings})
    recurring_scores = {item["label"]: item for item in _recurring_risk(findings)}
    driver_rows = []
    for index, row in enumerate(_top_failure_driver_rows(findings, limit=8), start=1):
        driver_rows.append(
            {
                "rank": index,
                "issue": row["issue"],
                "failCount": row["failCount"],
                "failureShare": row["failureShare"],
                "inspectionsAffected": f"{row['inspections']} / {max(len(snapshot.documents), 1)}",
            }
        )

    recurring_rows = []
    for row in _top_failure_driver_rows(findings, limit=8):
        recurring = recurring_scores.get(row["issue"], {"value": 0, "tone": "success"})
        recurring_rows.append(
            {
                "issue": row["issue"],
                "recurrenceLikelihood": f"{recurring['value']}%",
                "repeatCount": str(row["failCount"]),
                "inspectionsAffected": str(row["inspections"]),
                "tone": recurring.get("tone"),
            }
        )

    return {
        "title": "Performance analysis",
        "description": "The issues that come up most often across your inspections.",
        "filter": {"selected": inspection_type, "options": options},
        "topFailureDrivers": driver_rows,
        "recurringRisks": recurring_rows,
        "recurrenceSummary": "This section shows the issues that keep appearing across reports, so teams can focus on the biggest repeat problems first.",
    }


def build_insights_page(snapshot: RepositorySnapshot, inspection_type: str = "All inspection types") -> dict:
    all_findings = _display_findings(snapshot.findings)
    findings = _filter_findings_by_type(
        all_findings,
        inspection_type if inspection_type != "All inspection types" else "All types",
    )
    repeated_patterns = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in findings:
        grouped[item["display_title"]].append(item)

    for row in _top_failure_driver_rows(findings, limit=8):
        matching = grouped.get(row["issue"], [])
        highest_severity = _highest_severity(matching)
        repeated_patterns.append(
            {
                "issue": row["issue"],
                "occurrence": row["failureShare"],
                "failureShare": row["failureShare"],
                "failureShareValue": row["failureShareValue"],
                "failCount": row["failCount"],
                "occurrenceCount": row["failCount"],
                "inspectionsAffected": str(row["inspections"]),
                "affectedInspectionCount": row["inspections"],
                "projectCount": row["projectCount"],
                "highestSeverity": highest_severity,
                "severityRank": _severity_rank(highest_severity),
                "issueIds": [item["id"] for item in matching],
                "reportIds": sorted({item["document_id"] for item in matching if item.get("document_id")}),
                "reports": _report_refs(snapshot, matching),
                "categories": [label for label, _ in Counter(item.get("category") or "General" for item in matching).most_common(3)],
                "locations": [label for label, _ in Counter(_exact_location_label(item) for item in matching).most_common(3)],
            }
        )

    root_cause_items = _insight_group_items(
        snapshot,
        findings,
        key_fn=_root_cause_label,
        filter_type="rootCause",
    )
    high_risk_area_items = _insight_group_items(
        snapshot,
        findings,
        key_fn=_exact_location_label,
        filter_type="highRiskArea",
    )

    return {
        "title": "Insights",
        "description": "A simple view of the main problem areas showing up across reports.",
        "filter": {
            "selected": inspection_type,
            "options": ["All inspection types"] + sorted({item["inspection_type"] for item in all_findings}),
        },
        "rootCauses": [item["label"] for item in root_cause_items] or ["No clear cause listed yet"],
        "rootCauseItems": root_cause_items,
        "highRiskAreas": [item["label"] for item in high_risk_area_items],
        "highRiskAreaItems": high_risk_area_items,
        "repeatedPatterns": repeated_patterns,
        "issueThemes": group_similar_issues(findings)[:8],
        "severityLegend": _severity_legend(),
        "tableControls": {
            "searchFields": ["issue", "categories", "locations"],
            "sortOptions": [
                {"value": "frequency", "label": "Frequency", "field": "occurrenceCount", "direction": "desc"},
                {"value": "severity", "label": "Severity", "field": "severityRank", "direction": "desc"},
                {"value": "affectedInspections", "label": "Affected inspections", "field": "affectedInspectionCount", "direction": "desc"},
                {"value": "issue", "label": "Issue", "field": "issue", "direction": "asc"},
            ],
        },
        "projectComparisons": _project_comparisons(findings),
        "lessonsFromPastProjects": _lessons_from_past_projects(findings),
        "export": {
            "fileName": "inspection-insights-report.json",
            "title": "Inspection insights report",
            "sections": ["rootCauseItems", "highRiskAreaItems", "repeatedPatterns", "projectComparisons", "lessonsFromPastProjects"],
            "shareText": "Inspection insights summary for team training.",
        },
    }


def build_project_page(snapshot: RepositorySnapshot, slug: str) -> dict | None:
    documents = [item for item in snapshot.documents if item["project_slug"] == slug]
    if not documents:
        return None
    company_findings = _display_findings(snapshot.findings)
    findings = [item for item in company_findings if item["project_slug"] == slug]
    project_name = documents[0]["project_name"]
    return {
        "title": f"Project overview - {project_name}",
        "description": f"Site: {documents[0]['site_name']}",
        "metrics": [
            {"label": "Inspections", "value": str(len(documents))},
            {"label": "Issues found", "value": str(len(findings))},
            {"label": "Open issues", "value": str(len([item for item in findings if item["status"] == "Open"]))},
            {"label": "Issues / inspection", "value": f"{len(findings) / max(len(documents), 1):.1f}"},
        ],
        "trackerSnapshot": {
            "open": len([item for item in findings if item["status"] == "Open"]),
            "ready": len([item for item in findings if item["status"] == "Ready"]),
            "closedLast7Days": len(
                [item for item in findings if item.get("closed_at") and _days_since(item["closed_at"]) <= 7]
            ),
            "href": "/app/tracker",
        },
        "openItemsByType": [
            {"label": key, "value": str(value)}
            for key, value in Counter(item["trade"] for item in findings if item["status"] != "Closed").items()
        ],
        "topFailureDrivers": [
            {"issue": row["issue"], "count": row["failCount"], "failShare": row["failureShare"]}
            for row in _top_failure_driver_rows(findings, limit=5)
        ],
        "versusCompanyAverage": [
            {
                "label": "Issues found",
                "projectValue": str(len(findings)),
                "companyValue": str(len(company_findings)),
                "deltaLabel": "Higher" if len(findings) >= len(company_findings) else "Lower",
                "tone": "critical" if len(findings) >= len(company_findings) else "success",
            },
            {
                "label": "Issues / inspection",
                "projectValue": f"{len(findings) / max(len(documents), 1):.1f}",
                "companyValue": f"{len(company_findings) / max(len(snapshot.documents), 1):.1f}",
                "deltaLabel": "Higher"
                if len(findings) / max(len(documents), 1) >= len(company_findings) / max(len(snapshot.documents), 1)
                else "Lower",
                "tone": "critical",
            },
        ],
        "recentFailedItems": [
            {
                "id": item["id"],
                "issue": item["display_title"],
                "summary": item["plain_english_summary"],
                "type": item["trade"],
                "date": _pretty_date(item["created_at"]),
                "status": item["status"],
            }
            for item in findings[:5]
        ],
    }


def build_dashboard_risk(
    snapshot: RepositorySnapshot,
    site: str = "All sites",
    window: str = "30d",
    inspection_id: str | None = None,
) -> dict:
    if not snapshot.documents:
        return {
            "title": "Upcoming inspection risk",
            "description": "See which upcoming inspections may need the most attention.",
            "filters": {
                "sites": ["All sites"],
                "selectedSite": site,
                "windows": [
                    {"label": "Next 30 days", "value": "30d"},
                    {"label": "Next 14 days", "value": "14d"},
                    {"label": "Next 60 days", "value": "60d"},
                ],
                "selectedWindow": window,
            },
            "inspections": [],
            "selectedInspectionId": "",
            "likelyFailureTitle": "Items to watch",
            "likelyFailureSubtitle": "No uploaded reports are available for inspection risk.",
            "likelyFailures": [],
        }
    predictions = _normalized_predictions(snapshot)
    if site != "All sites":
        predictions = [item for item in predictions if item["site_name"] == site]
    max_days = 14 if window == "14d" else 60 if window == "60d" else 30
    predictions = [item for item in predictions if item["daysAway"] <= max_days]
    selected = next((item for item in predictions if item["id"] == inspection_id), None) or (
        predictions[0] if predictions else None
    )
    likely = _likely_failures(_display_findings(snapshot.findings), selected["inspection_type"] if selected else "")

    return {
        "title": "Upcoming inspection risk",
        "description": "See which upcoming inspections may need the most attention.",
        "filters": {
            "sites": ["All sites"] + sorted({item["site_name"] for item in predictions}),
            "selectedSite": site,
            "windows": [
                {"label": "Next 30 days", "value": "30d"},
                {"label": "Next 14 days", "value": "14d"},
                {"label": "Next 60 days", "value": "60d"},
            ],
            "selectedWindow": window,
        },
        "inspections": [
            {
                "id": item["id"],
                "type": item["inspection_type"],
                "site": item["site_name"],
                "expectedDate": item["expected_date"],
                "daysAway": item["daysAway"],
                "riskLevel": item["risk_level"],
            }
            for item in predictions
        ],
        "selectedInspectionId": selected["id"] if selected else "",
        "likelyFailureTitle": f"{selected['inspection_type']} - items to watch" if selected else "Items to watch",
        "likelyFailureSubtitle": f"Based on {len(likely)} issue types seen in past reports" if selected else "No upcoming inspections in this time period",
        "likelyFailures": likely,
    }


def build_inspection_risk_page(
    snapshot: RepositorySnapshot,
    site: str | None = None,
    date_range: str | None = None,
    inspection_type: str | None = None,
) -> dict:
    selected_site = site or "All Sites"
    selected_range = date_range or "30d"
    if not snapshot.documents:
        return {
            "title": "Upcoming Inspection Risk",
            "description": "See which upcoming inspections may need the most attention.",
            "filters": {
                "sites": ["All Sites"],
                "dateRanges": [
                    {"label": "Next 30 days", "value": "30d"},
                    {"label": "Next 60 days", "value": "60d"},
                    {"label": "Next 90 days", "value": "90d"},
                ],
                "selectedSite": selected_site,
                "selectedDateRange": selected_range,
                "inspectionTypes": [],
                "selectedInspectionType": "General",
            },
            "upcomingInspections": [],
            "likelyFailureItems": [],
        }
    max_days = 30 if selected_range == "30d" else 60 if selected_range == "60d" else 90
    predictions = _normalized_predictions(snapshot)
    if selected_site != "All Sites":
        predictions = [item for item in predictions if item["site_name"] == selected_site]
    predictions = [item for item in predictions if item["daysAway"] <= max_days]
    inspection_types = sorted({item["inspection_type"] for item in predictions})
    selected_type = inspection_type or (inspection_types[0] if inspection_types else "General")
    likely = _likely_failures(_display_findings(snapshot.findings), selected_type)

    return {
        "title": "Upcoming Inspection Risk",
        "description": "See which upcoming inspections may need the most attention.",
        "filters": {
            "sites": ["All Sites"] + sorted({item["site_name"] for item in _normalized_predictions(snapshot)}),
            "dateRanges": [
                {"label": "Next 30 days", "value": "30d"},
                {"label": "Next 60 days", "value": "60d"},
                {"label": "Next 90 days", "value": "90d"},
            ],
            "selectedSite": selected_site,
            "selectedDateRange": selected_range,
            "inspectionTypes": inspection_types,
            "selectedInspectionType": selected_type,
        },
        "upcomingInspections": [
            {
                "id": item["id"],
                "type": item["inspection_type"],
                "site": item["site_name"],
                "expectedDate": item["expected_date"],
                "daysAway": item["daysAway"],
                "riskLevel": item["risk_level"],
                "actionLabel": "Export Inspection Insights",
            }
            for item in predictions
        ],
        "likelyFailureItems": [
            {
                "issue": item["issue"],
                "historicalFailCount": item["historicalFailCount"],
                "failureShare": int(item["failureShare"].rstrip("%")),
            }
            for item in likely
        ],
    }


def build_tracker_page(snapshot: RepositorySnapshot, filters: dict[str, str | None]) -> dict:
    findings = _display_findings(snapshot.findings)
    selected_site = filters.get("site") or (snapshot.documents[0]["site_name"] if snapshot.documents else "Unknown site")
    selected_status = filters.get("status") or "All"
    selected_type = filters.get("type") or "All"
    selected_date_range = filters.get("dateRange") or "30d"
    search = (filters.get("search") or "").strip().lower()
    issue_id = filters.get("issueId")

    limit_days = 7 if selected_date_range == "7d" else 14 if selected_date_range == "14d" else 30
    issues = [
        item
        for item in findings
        if item["site_name"] == selected_site
        and (selected_status == "All" or item["status"] == selected_status)
        and (selected_type == "All" or item["trade"] == selected_type)
        and _days_open(item["created_at"]) <= limit_days
        and (not search or search in item["title"].lower())
    ]

    selected_issue = next((item for item in issues if item["id"] == issue_id), None)
    site_documents = [item for item in snapshot.documents if item["site_name"] == selected_site]
    site_units: list[str] = []
    for document in site_documents:
        for unit in _document_units(document, findings):
            if unit not in site_units:
                site_units.append(unit)

    inspection_documents = []
    for document in site_documents:
        document_findings = [item for item in findings if item["document_id"] == document["id"]]
        inspection_documents.append(
            {
                "id": document["id"],
                "inspectionType": document["inspection_type"],
                "trade": document["trade"],
                "reportDate": document["report_date"],
                "status": document["status"],
                "issueCount": len(document_findings),
                "unitCount": len(_document_units(document, findings)),
            }
        )

    return {
        "title": "Live Tracker",
        "description": "Track open issues from uploaded reports.",
        "controls": {
            "sites": sorted({item["site_name"] for item in snapshot.documents}),
            "selectedSite": selected_site,
            "search": search,
            "exportFileName": "live-tracker-export.csv",
        },
        "summary": {
            "open": len([item for item in issues if item["status"] == "Open"]),
            "readyForInspection": len([item for item in issues if item["status"] == "Ready"]),
            "closedLast7Days": len(
                [item for item in issues if item.get("closed_at") and _days_since(item["closed_at"]) <= 7]
            ),
        },
        "extractedUnits": site_units,
        "inspectionDocuments": inspection_documents,
        "filters": {
            "statuses": ["All", "Open", "Ready", "Closed"],
            "types": ["All"] + sorted({item["trade"] for item in findings}),
            "dateRanges": [
                {"label": "Last 7 days", "value": "7d"},
                {"label": "Last 14 days", "value": "14d"},
                {"label": "Last 30 days", "value": "30d"},
            ],
            "selectedStatus": selected_status,
            "selectedType": selected_type,
            "selectedDateRange": selected_date_range,
        },
        "issueRegister": {
            "siteSelected": True,
            "columns": ["Issue", "Project", "Site", "Location", "Type", "Date Identified", "Days Open", "Status"],
            "items": [
                {
                    "id": item["id"],
                    "issue": item["display_title"],
                    "summary": item["plain_english_summary"],
                    "project": item.get("project_name"),
                    "site": item.get("site_name"),
                    "location": item.get("location") or item.get("unit_label"),
                    "type": item["trade"],
                    "dateIdentified": item["created_at"][:10],
                    "daysOpen": _days_open(item["created_at"]),
                    "status": item["status"],
                }
                for item in issues
            ],
        },
        "selectedIssue": (
            {
                "id": selected_issue["id"],
                "issue": selected_issue["display_title"],
                "project": selected_issue.get("project_name"),
                "site": selected_issue["site_name"],
                "location": selected_issue.get("location") or selected_issue.get("unit_label"),
                "type": selected_issue["trade"],
                "dateIdentified": selected_issue["created_at"][:10],
                "daysOpen": _days_open(selected_issue["created_at"]),
                "status": selected_issue["status"],
                "inspectionNote": selected_issue["plain_english_summary"],
                "reinspections": selected_issue["reinspections"],
                "subcontractorName": "Trade Contractor",
                "subcontractorEmail": "trade@example.com",
                "consultantName": "Project Consultant",
                "consultantEmail": "consultant@example.com",
                "lastSentTo": selected_issue.get("last_sent_to"),
            }
            if selected_issue
            else None
        ),
    }


def build_dashboard_live_tracker(snapshot: RepositorySnapshot) -> dict:
    findings = _display_findings(snapshot.findings)
    return {
        "openIssues": len([item for item in findings if item["status"] == "Open"]),
        "overdue": len([item for item in findings if item["status"] == "Open" and _days_open(item["created_at"]) > 7]),
        "readyForInspection": len([item for item in findings if item["status"] == "Ready"]),
        "href": "/app/tracker",
        "description": "Keep track of open items, overdue actions, and work ready for the next inspection.",
    }


def build_dashboard_top_failures(snapshot: RepositorySnapshot, inspection_type: str | None = None) -> dict:
    all_findings = _display_findings(snapshot.findings)
    selected_type = inspection_type or (all_findings[0]["inspection_type"] if all_findings else "General")
    findings = _filter_findings_by_type(all_findings, selected_type)
    report_ids = {item["document_id"] for item in findings}
    reviewed_count = len(snapshot.documents) if selected_type in {"All types", "All inspection types"} else len(report_ids)
    drivers = _top_failure_driver_rows(findings, limit=6)
    return {
        "inspectionTypes": sorted({item["inspection_type"] for item in all_findings}),
        "selectedInspectionType": selected_type,
        "summary": [
            {"label": "Inspection Type", "value": selected_type},
            {"label": "Inspections Reviewed", "value": str(reviewed_count)},
            {"label": "Issues Found", "value": str(len(findings))},
            {
                "label": "Repeat Issue Rate",
                "value": f"{round(mean([item['recurrence_risk'] for item in findings]) if findings else 0)}%",
            },
        ],
        "performanceTrend": {
            "label": "Issue Volume Over Time",
            "dataPoints": [
                {"label": point["label"], "value": point["value"]}
                for point in _performance_trend(snapshot.documents, findings)
            ],
            "description": "How many issues were found each month.",
        },
        "drivers": [
            {
                "issue": row["issue"],
                "failCount": row["failCount"],
                "failureShare": int(row["failureShare"].rstrip("%")),
                "inspections": row["inspections"],
            }
            for row in drivers
        ],
        "failureDistribution": [
            {"label": item["label"], "percentage": item["value"]}
            for item in _failure_distribution(findings)
        ],
        "recurringRisk": [
            {"label": item["label"], "percentage": item["value"]}
            for item in _recurring_risk(findings)
        ],
        "inspectionReadiness": {
            "calledEarlyPercentage": round(
                (len([item for item in findings if item["status"] == "Open"]) / max(len(findings), 1)) * 100
            ),
            "description": "Open issues that may still stop a first follow-up inspection from passing.",
        },
        "closeOutPerformance": {
            "averageDaysToClose": int(_average_close_days([item for item in findings if item.get("closed_at")])),
            "needsReinspectionPercentage": _reinspection_rate(findings),
        },
    }


def build_dashboard_upcoming_risk(snapshot: RepositorySnapshot) -> dict:
    if not snapshot.documents:
        return {
            "title": "No upcoming inspections",
            "daysUntilInspection": 0,
            "likelyFailures": [],
            "href": "/app/inspection-risk",
            "description": "Upload a report first to show upcoming inspection risk here.",
        }
    upcoming = _normalized_predictions(snapshot)
    if not upcoming:
        return {
            "title": "No upcoming inspections",
            "daysUntilInspection": 0,
            "likelyFailures": [],
            "href": "/app/inspection-risk",
            "description": "Upload a report first to show upcoming inspection risk here.",
        }

    first = upcoming[0]
    failures = _likely_failures(_display_findings(snapshot.findings), first["inspection_type"])
    return {
        "title": first["inspection_type"],
        "daysUntilInspection": first["daysAway"],
        "likelyFailures": [item["issue"] for item in failures[:3]],
        "href": "/app/inspection-risk",
        "description": "Based on open issues and the types of problems that have come up before.",
    }


def build_dashboard_insights_preview(snapshot: RepositorySnapshot) -> dict:
    insights = build_insights_page(snapshot)
    return {
        "rootCauses": insights["rootCauses"][:3],
        "highRiskAreas": insights["highRiskAreas"][:3],
        "href": "/app/insights",
        "description": "A quick summary of the main problem areas showing up in recent reports.",
    }


def build_legacy_insights_summary(snapshot: RepositorySnapshot) -> dict:
    findings = _display_findings(snapshot.findings)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for finding in findings:
        grouped[finding["display_title"]].append(finding)

    severity_rank = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
    top_issues = []
    for title, items in grouped.items():
        highest = max(items, key=lambda item: severity_rank.get(item["severity"], 0))["severity"]
        top_issues.append(
            {
                "title": title,
                "occurrences": len(items),
                "highestSeverity": highest,
            }
        )

    top_issues.sort(key=lambda item: (-item["occurrences"], item["title"]))
    return {
        "topIssues": top_issues[:5],
        "totalIssues": len(findings),
    }


def _report_payload(document: dict, findings: list[dict]) -> dict:
    related = [item for item in findings if item["document_id"] == document["id"]]
    return {
        "id": document["id"],
        "project": document["project_name"],
        "projectName": document["project_name"],
        "site": document["site_name"],
        "siteName": document["site_name"],
        "address": document.get("address"),
        "createdAt": document["report_date"],
        "uploadedAt": document.get("uploaded_at"),
        "status": document["status"],
        "summary": document.get("summary", ""),
        "inspectionType": document.get("inspection_type", "Unknown"),
        "inspector": document["inspector"],
        "trade": document["trade"],
        "sourceFileName": document.get("source_filename"),
        "units": _document_units(document, findings),
        "issues": [
            {
                "id": item["id"],
                "title": item.get("display_title") or item["title"],
                "description": item.get("plain_english_summary") or item["description"],
                "rawTitle": item["title"],
                "severity": item["severity"],
                "status": item["status"],
                "category": item.get("display_category") or item["category"],
                "trade": item["trade"],
                "project": item.get("project_name"),
                "site": item.get("site_name"),
                "location": item.get("location"),
                "unitLabel": item.get("unit_label"),
                "requiredFix": item.get("required_fix"),
                "evidenceRequired": item.get("evidence_required") or [],
                "confidence": item.get("confidence"),
                "recurrenceRisk": safe_int(item.get("recurrence_risk")),
                "reinspections": safe_int(item.get("reinspections")),
            }
            for item in related
        ],
    }


def _top_failure_driver_rows(findings: list[dict], limit: int) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in findings:
        grouped[item.get("display_title") or item["title"]].append(item)

    total = max(len(findings), 1)
    rows = []
    for title, items in grouped.items():
        fail_count = len(items)
        rows.append(
            {
                "issue": title,
                "failCount": fail_count,
                "failureShare": f"{round((fail_count / total) * 100)}%",
                "failureShareValue": round((fail_count / total) * 100, 1),
                "inspections": len({item["document_id"] for item in items}),
                "projectCount": len({item.get("project_slug") or item.get("project_name") for item in items}),
            }
        )
    rows.sort(key=lambda row: (-row["failCount"], row["issue"]))
    return rows[:limit]


def _root_cause_label(item: dict) -> str:
    root_cause = str(item.get("root_cause") or "").strip()
    category = str(item.get("display_category") or item.get("category") or "").strip()
    if root_cause and root_cause.lower() != "general":
        return root_cause
    return category or "General"


def _insight_group_items(snapshot: RepositorySnapshot, findings: list[dict], *, key_fn, filter_type: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in findings:
        grouped[str(key_fn(item) or "General")].append(item)
    rows = []
    for label, items in grouped.items():
        reports = _report_refs(snapshot, items)
        highest_severity = _highest_severity(items)
        rows.append(
            {
                "label": label,
                "count": len(items),
                "issueCount": len(items),
                "affectedInspectionCount": len({item.get("document_id") for item in items if item.get("document_id")}),
                "projectCount": len({item.get("project_slug") or item.get("project_name") for item in items}),
                "highestSeverity": highest_severity,
                "severityRank": _severity_rank(highest_severity),
                "issueIds": [item["id"] for item in items],
                "reportIds": [report["id"] for report in reports],
                "reports": reports,
                "tableFilter": {"type": filter_type, "value": label},
            }
        )
    rows.sort(key=lambda row: (-row["count"], -row["severityRank"], row["label"]))
    return rows[:5]


def _report_refs(snapshot: RepositorySnapshot, findings: list[dict]) -> list[dict]:
    documents = {document["id"]: document for document in snapshot.documents}
    refs = []
    for document_id in sorted({item.get("document_id") for item in findings if item.get("document_id")}):
        document = documents.get(document_id)
        refs.append(
            {
                "id": document_id,
                "project": (document or {}).get("project_name") or _first_matching_value(findings, document_id, "project_name"),
                "site": (document or {}).get("site_name") or _first_matching_value(findings, document_id, "site_name"),
                "inspectionType": (document or {}).get("inspection_type") or _first_matching_value(findings, document_id, "inspection_type"),
                "reportDate": (document or {}).get("report_date"),
            }
        )
    return refs


def _first_matching_value(findings: list[dict], document_id: str, key: str) -> str | None:
    for item in findings:
        if item.get("document_id") == document_id and item.get(key):
            return str(item[key])
    return None


def _highest_severity(items: list[dict]) -> str:
    highest = "Low"
    for item in items:
        severity = item.get("severity") if item.get("severity") in {"Low", "Medium", "High", "Critical"} else "Low"
        if _severity_rank(severity) > _severity_rank(highest):
            highest = severity
    return highest


def _severity_rank(severity: str) -> int:
    return {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}.get(severity, 1)


def _severity_legend() -> list[dict]:
    return [
        {"level": "Medium", "meaning": "Needs planned attention before inspection.", "recommendedAction": "Assign an owner and prepare close-out evidence."},
        {"level": "High", "meaning": "Likely to affect sign-off or trigger reinspection.", "recommendedAction": "Prioritise the fix and verify before booking inspection."},
        {"level": "Critical", "meaning": "Highest-risk issue with urgent inspection impact.", "recommendedAction": "Escalate immediately, close the issue, and collect evidence before sign-off."},
    ]


def _project_comparisons(findings: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in findings:
        grouped[item.get("project_name") or "Unknown project"].append(item)
    rows = []
    for project, items in grouped.items():
        top_issues = [
            {"issue": issue, "count": count}
            for issue, count in Counter(item.get("title") or "Untitled finding" for item in items).most_common(3)
        ]
        rows.append(
            {
                "project": project,
                "lifecycle": _project_lifecycle(items[0]),
                "issueCount": len(items),
                "openIssueCount": len([item for item in items if item.get("status") != "Closed"]),
                "topIssues": top_issues,
                "dominantRootCause": Counter(item.get("root_cause") or item.get("category") or "General" for item in items).most_common(1)[0][0],
            }
        )
    rows.sort(key=lambda row: (-row["issueCount"], row["project"]))
    return rows


def _lessons_from_past_projects(findings: list[dict]) -> list[dict]:
    historical = [item for item in findings if _project_lifecycle(item) in {"completed", "closed", "archived"}]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in historical:
        grouped[item.get("root_cause") or item.get("category") or item.get("trade") or "General"].append(item)
    lessons = []
    for cause, items in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:6]:
        projects = sorted({item.get("project_name") for item in items if item.get("project_name")})
        issues = [issue for issue, _ in Counter(item.get("title") or "Inspection finding" for item in items).most_common(3)]
        lessons.append(
            {
                "title": f"{cause} showed up in past projects",
                "issueCount": len(items),
                "projectCount": len(projects),
                "seenInProjects": projects[:5],
                "recurringIssues": issues,
                "recommendation": "Add this pattern to team training and pre-inspection QA checklists for new projects.",
            }
        )
    return lessons


def _project_lifecycle(item: dict) -> str:
    value = str(item.get("project_lifecycle") or item.get("project_status") or item.get("lifecycle") or "active").strip().lower()
    return value if value in {"active", "completed", "closed", "archived"} else "active"


def _failure_distribution(findings: list[dict]) -> list[dict]:
    counter = Counter(item["trade"] for item in findings)
    total = max(sum(counter.values()), 1)
    return [
        {"label": label, "value": round((count / total) * 100)}
        for label, count in counter.most_common(5)
    ]


def _recurring_risk(findings: list[dict]) -> list[dict]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for item in findings:
        grouped[item["title"]].append(safe_int(item.get("recurrence_risk")))
    rows = []
    for title, values in grouped.items():
        score = round(mean(values))
        tone = "critical" if score >= 75 else "warning" if score >= 50 else "success"
        rows.append({"label": title, "value": score, "tone": tone})
    rows.sort(key=lambda row: (-row["value"], row["label"]))
    return rows[:5]


def _performance_trend(reports: list[dict], findings: list[dict]) -> list[dict]:
    by_month: dict[str, list[dict]] = defaultdict(list)
    report_lookup = {item["id"]: item for item in reports}
    for finding in findings:
        document = report_lookup.get(finding["document_id"])
        if not document:
            continue
        label = datetime.fromisoformat(document["report_date"]).strftime("%b %y")
        by_month[label].append(finding)
    rows = []
    for label, values in sorted(by_month.items(), key=lambda item: datetime.strptime(item[0], "%b %y")):
        value = len(values)
        rows.append({"label": label, "value": value, "formattedValue": str(value)})
    return rows or [{"label": "No data", "value": 0, "formattedValue": "0"}]


def _document_units_from_findings(document_id: str, findings: list[dict]) -> list[str]:
    units: list[str] = []
    for item in findings:
        if item.get("document_id") != document_id:
            continue
        unit_label = item.get("unit_label")
        if isinstance(unit_label, str) and unit_label and unit_label not in units:
            units.append(unit_label)
    return units


def _document_units(document: dict, findings: list[dict]) -> list[str]:
    units: list[str] = []
    for unit in document.get("units") or []:
        if isinstance(unit, str) and unit and unit not in units:
            units.append(unit)
    for unit in _document_units_from_findings(document["id"], findings):
        if unit not in units:
            units.append(unit)
    return units


def _inspection_type_summary(findings: list[dict], reports: list[dict]) -> list[dict]:
    report_counts = Counter(item["inspection_type"] for item in reports)
    finding_groups: dict[str, list[dict]] = defaultdict(list)
    for item in findings:
        finding_groups[item["inspection_type"]].append(item)
    rows = []
    for inspection_type, report_count in report_counts.items():
        group = finding_groups.get(inspection_type, [])
        rows.append(
            {
                "type": inspection_type,
                "inspections": report_count,
                "extractedIssues": str(len(group)),
                "openIssues": str(len([item for item in group if item["status"] == "Open"])),
                "issuesPerInspection": f"{len(group) / max(report_count, 1):.1f}",
                "tone": "critical" if len(group) >= 10 else "warning" if len(group) >= 5 else "success",
            }
        )
    rows.sort(key=lambda row: -int(row["extractedIssues"]))
    return rows


def _filter_findings_by_type(findings: list[dict], inspection_type: str) -> list[dict]:
    if inspection_type in {"All types", "All inspection types", "", None}:
        return findings
    return [item for item in findings if item["inspection_type"] == inspection_type]


def _overview_risks(snapshot: RepositorySnapshot) -> list[dict]:
    if not snapshot.documents:
        return []
    findings = _display_findings(snapshot.findings)
    risks = []
    for item in _normalized_predictions(snapshot)[:3]:
        failures = _likely_failures(findings, item["inspection_type"])
        risks.append(
            {
                "title": item["inspection_type"],
                "subtitle": "Watch for: " + ", ".join(failure["issue"] for failure in failures[:3]),
                "daysAway": item["daysAway"],
                "level": item["risk_level"],
            }
        )
    return risks


def _likely_failures(findings: list[dict], inspection_type: str) -> list[dict]:
    related = [
        item
        for item in findings
        if inspection_type in item["inspection_type"] or item["trade"] in inspection_type
    ]
    rows = []
    for row in _top_failure_driver_rows(related or findings, limit=5):
        recurrence = next(
            (
                item["value"]
                for item in _recurring_risk(related or findings)
                if item["label"] == row["issue"]
            ),
            50,
        )
        rows.append(
            {
                "issue": row["issue"],
                "historicalFailCount": row["failCount"],
                "failureShare": row["failureShare"],
                "recurrenceLikelihood": f"{recurrence}%",
                "tone": "critical" if int(row["failureShare"].rstrip("%")) >= 25 else "warning",
            }
        )
    return rows


def _normalized_predictions(snapshot: RepositorySnapshot) -> list[dict]:
    if not snapshot.documents:
        return []
    active_project_ids = {item.get("project_id") for item in snapshot.documents}
    today = date.today()
    rows = []
    for index, item in enumerate(snapshot.predicted_inspections):
        if item.get("project_id") not in active_project_ids:
            continue
        expected = datetime.fromisoformat(item["expected_date"]).date()
        if expected < today:
            expected = date.fromordinal(today.toordinal() + 7 * (index + 1))
        rows.append(
            {
                **item,
                "id": item.get("id") or f"pred-{index}",
                "expected_date": expected.isoformat(),
                "daysAway": (expected - today).days,
            }
        )
    rows.sort(key=lambda row: row["expected_date"])
    return rows


def _failure_rate(findings: list[dict]) -> int:
    if not findings:
        return 0
    severe = [item for item in findings if item["severity"] in {"High", "Critical"}]
    return round((len(severe) / len(findings)) * 100)


def _reinspection_rate(findings: list[dict]) -> int:
    if not findings:
        return 0
    return round(
        (
            len([item for item in findings if safe_int(item.get("reinspections")) > 0])
            / len(findings)
        )
        * 100
    )


def _average_close_days(findings: list[dict]) -> str:
    if not findings:
        return "0"
    values = [_days_between(item["created_at"], item["closed_at"]) for item in findings if item.get("closed_at")]
    return str(round(mean(values))) if values else "0"


def _days_between(start_iso: str, end_iso: str | None) -> int:
    if not end_iso:
        return 0
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    return max(0, (end.date() - start.date()).days)


def _days_open(created_at: str) -> int:
    return _days_since(created_at)


def _days_since(iso_value: str) -> int:
    parsed = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    return max(0, (datetime.now(tz=UTC).date() - parsed.date()).days)


def _pretty_date(iso_value: str) -> str:
    return datetime.fromisoformat(iso_value.replace("Z", "+00:00")).strftime("%d %b")


def _exact_location_label(item: dict) -> str:
    for value in (item.get("location"), item.get("unit_label")):
        if not value:
            continue
        label = str(value).strip()
        if not label or re.fullmatch(r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?", label):
            continue
        return label
    return "Exact location not stated"
