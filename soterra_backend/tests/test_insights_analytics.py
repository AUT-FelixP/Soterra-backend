from __future__ import annotations

import unittest

from soterra_backend.analytics import build_insights_page
from soterra_backend.models import RepositorySnapshot


def _finding(
    *,
    id: str,
    title: str,
    document_id: str,
    project_name: str,
    inspection_type: str = "Final",
    category: str = "Evidence gap",
    location: str = "Level 2",
    severity: str = "High",
    project_lifecycle: str = "active",
    root_cause: str | None = None,
) -> dict:
    return {
        "id": id,
        "tenant_id": "ten-a",
        "document_id": document_id,
        "project_slug": project_name.lower().replace(" ", "-"),
        "project_name": project_name,
        "project_lifecycle": project_lifecycle,
        "site_name": project_name,
        "title": title,
        "description": f"{title} needs resolution.",
        "severity": severity,
        "trade": "General",
        "category": category,
        "root_cause": root_cause or category,
        "location": location,
        "inspection_type": inspection_type,
        "status": "Open",
        "created_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None,
        "reinspections": 0,
        "recurrence_risk": 50,
    }


def _snapshot() -> RepositorySnapshot:
    findings = [
        _finding(id="iss-1", title="Missing close-out photo", document_id="rpt-1", project_name="Kauri Apartments"),
        _finding(id="iss-2", title="Missing close-out photo", document_id="rpt-2", project_name="Kauri Apartments"),
        _finding(
            id="iss-3",
            title="Fire collar incomplete",
            document_id="rpt-3",
            project_name="Rimu Townhouses",
            inspection_type="Passive Fire",
            category="Passive Fire",
            location="Basement",
            severity="Critical",
            project_lifecycle="closed",
        ),
        _finding(
            id="iss-4",
            title="Cavity wrap loose",
            document_id="rpt-4",
            project_name="Rimu Townhouses",
            category="Envelope",
            location="Level 1",
            severity="Medium",
            project_lifecycle="closed",
        ),
    ]
    documents = [
        {
            "id": "rpt-1",
            "project_name": "Kauri Apartments",
            "site_name": "Kauri Apartments",
            "inspection_type": "Final",
            "report_date": "2026-01-01",
        },
        {
            "id": "rpt-2",
            "project_name": "Kauri Apartments",
            "site_name": "Kauri Apartments",
            "inspection_type": "Final",
            "report_date": "2026-01-02",
        },
        {
            "id": "rpt-3",
            "project_name": "Rimu Townhouses",
            "site_name": "Rimu Townhouses",
            "inspection_type": "Passive Fire",
            "report_date": "2026-01-03",
        },
        {
            "id": "rpt-4",
            "project_name": "Rimu Townhouses",
            "site_name": "Rimu Townhouses",
            "inspection_type": "Final",
            "report_date": "2026-01-04",
        },
    ]
    return RepositorySnapshot(
        projects=[],
        documents=documents,
        jobs=[],
        findings=findings,
        predicted_inspections=[],
    )


class InsightsAnalyticsTest(unittest.TestCase):
    def test_inspection_type_filter_limits_insights_payload(self) -> None:
        payload = build_insights_page(_snapshot(), "Passive Fire")

        self.assertEqual(payload["filter"]["selected"], "Passive Fire")
        self.assertEqual(payload["repeatedPatterns"][0]["issue"], "Fire collar incomplete")
        self.assertEqual(payload["repeatedPatterns"][0]["failureShareValue"], 100.0)
        self.assertEqual(payload["repeatedPatterns"][0]["affectedInspectionCount"], 1)
        self.assertEqual(payload["rootCauseItems"][0]["label"], "Passive Fire")
        self.assertEqual(payload["rootCauseItems"][0]["count"], 1)

    def test_repeated_patterns_use_real_percentages_and_report_counts(self) -> None:
        payload = build_insights_page(_snapshot())
        pattern = next(item for item in payload["repeatedPatterns"] if item["issue"] == "Missing close-out photo")

        self.assertEqual(pattern["failureShare"], "50%")
        self.assertEqual(pattern["failureShareValue"], 50.0)
        self.assertEqual(pattern["occurrenceCount"], 2)
        self.assertEqual(pattern["affectedInspectionCount"], 2)
        self.assertEqual(pattern["reportIds"], ["rpt-1", "rpt-2"])

    def test_group_items_include_counts_click_filters_and_reports(self) -> None:
        payload = build_insights_page(_snapshot())
        root_cause = payload["rootCauseItems"][0]
        high_risk_area = next(item for item in payload["highRiskAreaItems"] if item["label"] == "Level 2")

        self.assertEqual(root_cause["count"], 2)
        self.assertEqual(root_cause["tableFilter"], {"type": "rootCause", "value": "Evidence gap"})
        self.assertEqual(high_risk_area["count"], 2)
        self.assertEqual(high_risk_area["tableFilter"], {"type": "highRiskArea", "value": "Level 2"})
        self.assertEqual(len(high_risk_area["reports"]), 2)

    def test_generic_root_cause_uses_enriched_category(self) -> None:
        snapshot = RepositorySnapshot(
            projects=[],
            documents=[
                {
                    "id": "rpt-1",
                    "project_name": "Kauri Apartments",
                    "site_name": "Kauri Apartments",
                    "inspection_type": "Fire",
                    "report_date": "2026-01-01",
                }
            ],
            jobs=[],
            findings=[
                _finding(
                    id="iss-1",
                    title="Fixings are missing on the bottom part of plasterboard linings",
                    document_id="rpt-1",
                    project_name="Kauri Apartments",
                    inspection_type="Fire",
                    category="General",
                    root_cause="General",
                )
            ],
            predicted_inspections=[],
        )

        payload = build_insights_page(snapshot)

        self.assertEqual(payload["rootCauseItems"][0]["label"], "Passive Fire - Linings")

    def test_training_and_table_metadata_are_available(self) -> None:
        payload = build_insights_page(_snapshot())

        self.assertTrue(payload["severityLegend"])
        self.assertIn("sortOptions", payload["tableControls"])
        self.assertTrue(payload["projectComparisons"])
        self.assertTrue(payload["lessonsFromPastProjects"])
        self.assertEqual(payload["export"]["fileName"], "inspection-insights-report.json")

    def test_dashboard_filters_and_visual_payload_are_available(self) -> None:
        payload = build_insights_page(
            _snapshot(), project="Rimu Townhouses", severity="Critical"
        )

        self.assertEqual(payload["selectedFilters"]["project"], "Rimu Townhouses")
        self.assertEqual(payload["kpis"][1]["value"], 1)
        self.assertEqual(payload["visuals"]["severityDonut"], [{"name": "Critical", "value": 1}])
        self.assertEqual(payload["issueDrilldown"][0]["id"], "iss-3")
        self.assertIn("All projects", payload["filters"]["projects"])

    def test_risk_and_quality_payloads_are_deterministic(self) -> None:
        payload = build_insights_page(_snapshot())
        repeated = next(row for row in payload["riskMatrix"] if row["issue"] == "Missing close-out photo")

        self.assertEqual(repeated["repeatCount"], 2)
        self.assertEqual(repeated["openCount"], 2)
        self.assertGreater(repeated["riskScore"], 0)
        self.assertEqual(payload["dataQuality"]["totalRows"], 4)
        self.assertIn(payload["dataQuality"]["health"], {"Good", "Needs review"})

    def test_open_issue_metrics_only_count_open_status(self) -> None:
        snapshot = _snapshot()
        snapshot.findings[0]["status"] = "In Progress"
        payload = build_insights_page(snapshot)

        open_kpi = next(item for item in payload["kpis"] if item["key"] == "open")
        repeated = next(row for row in payload["riskMatrix"] if row["issue"] == "Missing close-out photo")
        self.assertEqual(open_kpi["value"], 3)
        self.assertEqual(repeated["openCount"], 1)


if __name__ == "__main__":
    unittest.main()
