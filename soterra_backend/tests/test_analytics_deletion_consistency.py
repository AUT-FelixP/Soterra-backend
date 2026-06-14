from __future__ import annotations

import unittest

from soterra_backend.analytics import (
    build_dashboard_overview,
    build_dashboard_risk,
    build_dashboard_upcoming_risk,
    build_inspection_risk_page,
    build_report_list,
)
from soterra_backend.models import RepositorySnapshot


class AnalyticsDeletionConsistencyTest(unittest.TestCase):
    def test_orphan_predictions_do_not_surface_after_reports_are_deleted(self) -> None:
        snapshot = RepositorySnapshot(
            projects=[
                {
                    "id": "prj-orphan",
                    "tenant_id": "ten-a",
                    "slug": "24-kauri-road-henderson",
                    "name": "24 Kauri Road, Henderson",
                    "site_name": "24 Kauri Road, Henderson",
                }
            ],
            documents=[],
            jobs=[],
            findings=[],
            predicted_inspections=[
                {
                    "id": "pred-orphan",
                    "tenant_id": "ten-a",
                    "project_id": "prj-orphan",
                    "project_name": "24 Kauri Road, Henderson",
                    "project_slug": "24-kauri-road-henderson",
                    "inspection_type": "Council Recheck",
                    "site_name": "24 Kauri Road, Henderson",
                    "expected_date": "2026-05-18",
                    "risk_level": "High",
                    "source": "Deleted report prediction",
                    "created_at": "2026-05-01T00:00:00+00:00",
                }
            ],
        )

        overview = build_dashboard_overview(snapshot)
        risk = build_dashboard_risk(snapshot)
        upcoming = build_dashboard_upcoming_risk(snapshot)
        inspection_risk = build_inspection_risk_page(snapshot)

        self.assertEqual(overview["upcomingRisks"], [])
        self.assertEqual(risk["inspections"], [])
        self.assertEqual(risk["likelyFailures"], [])
        self.assertIn("No uploaded reports", risk["likelyFailureSubtitle"])
        self.assertEqual(upcoming["title"], "No upcoming inspections")
        self.assertEqual(upcoming["likelyFailures"], [])
        self.assertEqual(inspection_risk["upcomingInspections"], [])
        self.assertEqual(inspection_risk["likelyFailureItems"], [])

    def test_report_list_exposes_project_and_site_names_for_client(self) -> None:
        snapshot = RepositorySnapshot(
            projects=[],
            documents=[
                {
                    "id": "rpt-1",
                    "tenant_id": "ten-a",
                    "project_name": "Kauri Apartments",
                    "project_slug": "kauri-apartments",
                    "site_name": "Kauri Site",
                    "source_filename": "inspection.pdf",
                    "inspection_type": "Fire",
                    "trade": "Passive Fire",
                    "inspector": "Inspector",
                    "report_date": "2026-06-14",
                    "status": "Completed",
                    "summary": "Fire report",
                    "units": [],
                    "uploaded_at": "2026-06-14T00:00:00+00:00",
                }
            ],
            jobs=[],
            findings=[],
            predicted_inspections=[],
        )

        item = build_report_list(snapshot)["items"][0]

        self.assertEqual(item["project"], "Kauri Apartments")
        self.assertEqual(item["projectName"], "Kauri Apartments")
        self.assertEqual(item["site"], "Kauri Site")
        self.assertEqual(item["siteName"], "Kauri Site")


if __name__ == "__main__":
    unittest.main()
