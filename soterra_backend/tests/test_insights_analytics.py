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

    def test_training_and_table_metadata_are_available(self) -> None:
        payload = build_insights_page(_snapshot())

        self.assertTrue(payload["severityLegend"])
        self.assertIn("sortOptions", payload["tableControls"])
        self.assertTrue(payload["projectComparisons"])
        self.assertTrue(payload["lessonsFromPastProjects"])
        self.assertEqual(payload["export"]["fileName"], "inspection-insights-report.json")


if __name__ == "__main__":
    unittest.main()
