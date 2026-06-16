from __future__ import annotations

import unittest

from soterra_backend.extraction_quality import finalize_extraction
from soterra_backend.models import ExtractedFinding, ExtractionResult
from soterra_backend.services.work_package_service import build_chat_cards, build_todays_fix_list, build_work_packages


def _extraction(finding: ExtractedFinding) -> ExtractionResult:
    return ExtractionResult(
        project_name="Kauri Apartments",
        site_name="Level 1",
        inspection_type="Council Cavity Wrap",
        trade="Envelope",
        inspector="Council Inspector",
        report_date="2026-06-01",
        summary="Council cavity wrap inspection found actionable defects.",
        findings=[finding],
    )


class ExtractionQualityTest(unittest.TestCase):
    def test_finalize_extraction_adds_traceability_actions_and_warnings(self) -> None:
        extraction = finalize_extraction(
            _extraction(
                ExtractedFinding(
                    title="Membrane upstand below minimum height",
                    description="Membrane support upstand is below the required 150mm minimum.",
                    severity="Critical",
                    trade="Envelope",
                )
            ),
            "cavity-wrap.pdf",
        )
        finding = extraction.findings[0]
        self.assertEqual(finding.source_document, "cavity-wrap.pdf")
        self.assertEqual(finding.source_quote, finding.description)
        self.assertIn("measured after photos", finding.required_fix or "")
        self.assertIn("after photos", finding.evidence_required)
        self.assertLess(finding.confidence, 1)
        self.assertTrue(any("location" in warning.lower() for warning in finding.extraction_warnings))

    def test_generic_required_fix_is_replaced_and_flagged(self) -> None:
        extraction = finalize_extraction(
            _extraction(
                ExtractedFinding(
                    title="Fire collar missing",
                    description="A fire collar is missing at the riser penetration.",
                    severity="Critical",
                    trade="Passive Fire",
                    location="Level 1 riser",
                    required_fix="Assign the responsible trade, rectify the item, and upload close-out evidence.",
                )
            ),
            "fire-inspection.pdf",
        )
        finding = extraction.findings[0]
        self.assertNotIn("Assign the responsible trade", finding.required_fix or "")
        self.assertTrue(any("generic required fix" in warning.lower() for warning in finding.extraction_warnings))


class WorkPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.findings = [
            {
                "id": "issue-1",
                "project_name": "Kauri Apartments",
                "trade": "Envelope",
                "severity": "Critical",
                "status": "Open",
                "inspection_type": "Council Cavity Wrap",
                "root_cause": "Weather-tightness detail installation",
                "title": "Junction flashing missing",
                "location": "Level 1 apartments",
            },
            {
                "id": "issue-2",
                "project_name": "Kauri Apartments",
                "trade": "Envelope",
                "severity": "Critical",
                "status": "Open",
                "inspection_type": "Council Cavity Wrap",
                "root_cause": "Weather-tightness detail installation",
                "title": "Balcony flashing incorrect",
                "location": "Level 1 balconies",
            },
        ]

    def test_similar_issues_are_grouped_into_one_work_package(self) -> None:
        packages = build_work_packages(self.findings)
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["issue_count"], 2)
        self.assertEqual(packages[0]["issue_ids"], ["issue-1", "issue-2"])

    def test_fix_list_and_cards_keep_default_chat_compact(self) -> None:
        fix_list = build_todays_fix_list(self.findings)
        cards = build_chat_cards(self.findings)
        self.assertEqual(fix_list["title"], "Today's Fix List")
        self.assertEqual(fix_list["items"][0]["related_issue_count"], 2)
        self.assertEqual(cards["mode"], "summary_mode")
        self.assertIn("Show all issues", cards["actions"])
        self.assertEqual([item["type"] for item in cards["cards"][:2]], ["ProjectSummaryCard", "WorkPackageCard"])


if __name__ == "__main__":
    unittest.main()
