from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from soterra_backend.config import Settings
from soterra_backend.extraction_quality_gate import ExtractionQualityError, validate_extraction_quality
from soterra_backend.extractors.base import ExtractionArtifacts
from soterra_backend.extractors.package_doctr import _build_rule_extraction, extract_issue_blocks
from soterra_backend.issue_intelligence import enrich_finding
from soterra_backend.models import ExtractionResult, StoredFile
from soterra_backend.services.report_service import IngestionStart, ReportIngestionService, UploadContext


class NativeExtractionQualityTest(unittest.TestCase):
    def test_package_extraction_finds_required_issue_hints(self) -> None:
        text = """
        Project name Kauri Apartments
        Date 01/06/2026
        1. Level 2 riser failed fire stop inspection.
        2. Missing collar to pipe penetration.
        3. Non-compliant flashing to balcony requires close-out evidence.
        """
        extraction = _build_rule_extraction(_request(), text)
        titles = " ".join(item.title.lower() for item in extraction.findings)
        self.assertGreaterEqual(len(extraction.findings), 3)
        self.assertIn("failed", titles)
        self.assertIn("missing", titles)
        self.assertIn("non-compliant", titles)
        self.assertLessEqual(len(extraction.findings), 40)

    def test_multi_line_issue_block_extraction(self) -> None:
        text = """
        Inspection details
        1. Fire damper fixing is non-compliant
        Location: Level 3 corridor
        Close-out photo required before recheck

        Report date 01/06/2026
        """
        blocks = extract_issue_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertIn("Level 3 corridor", blocks[0])
        self.assertIn("Close-out photo required", blocks[0])

    def test_package_extraction_preserves_uploaded_project_and_site_metadata(self) -> None:
        text = """
        Building name Extracted Tower
        Site: Extracted Site
        Date 01/06/2026
        1. Level 2 riser failed fire stop inspection.
        """

        extraction = _build_rule_extraction(_request(), text)

        self.assertEqual(extraction.project_name, "Kauri Apartments")
        self.assertEqual(extraction.site_name, "Kauri")

    def test_issue_summary_does_not_use_site_as_exact_location(self) -> None:
        finding = {
            "title": "Fire collar missing",
            "description": "Fire collar missing and close-out evidence required.",
            "site_name": "Kauri Apartments",
            "location": None,
            "unit_label": None,
        }

        enriched = enrich_finding(finding)

        self.assertIn("Exact project location was not stated", enriched["plain_english_summary"])
        self.assertNotIn("at Kauri Apartments", enriched["plain_english_summary"])

    def test_quality_gate_raises_for_issue_text_with_no_findings(self) -> None:
        extraction = _empty_extraction()
        raw_text = "This report says failed fire stop, missing collar, non-compliant penetration and close-out required."
        with self.assertRaises(ExtractionQualityError) as caught:
            validate_extraction_quality(extraction, raw_text)
        self.assertFalse(caught.exception.diagnostics["quality_gate_passed"])
        self.assertGreater(caught.exception.diagnostics["issue_keyword_hits"], 0)

    def test_report_ingestion_does_not_complete_when_quality_gate_fails(self) -> None:
        repo = FakeRepository()
        service = ReportIngestionService(settings=_settings(), repository=repo, storage=FakeStorage())
        service.extractor = FakeExtractor()
        upload = UploadContext(
            tenant_id="ten-1",
            filename="report.pdf",
            stored_filename="report.pdf",
            content=b"%PDF-1.4 test",
            content_type="application/pdf",
            file_type="pdf",
            project_name="Kauri",
            site_name="Kauri",
            trade="Passive Fire",
        )
        start = IngestionStart(document_id="rpt-1", job_id="job-1", file_hash="hash", file_tag="tag", stored_file=StoredFile(storage_path="local/report.pdf"))

        with self.assertRaises(ExtractionQualityError):
            service.finish_ingest(start, upload)

        self.assertFalse(repo.completed)
        self.assertTrue(repo.failed)
        self.assertIn("Manual review", repo.error_message)


def _request():
    from soterra_backend.extractors.base import ExtractionRequest

    return ExtractionRequest(
        filename="report.pdf",
        content=b"%PDF",
        project_name="Kauri Apartments",
        site_name="Kauri",
        trade="Passive Fire",
    )


def _empty_extraction() -> ExtractionResult:
    return ExtractionResult(
        project_name="Kauri",
        site_name="Kauri",
        inspection_type="Passive Fire",
        trade="Passive Fire",
        inspector="Inspector",
        report_date="2026-06-01",
        summary="Inspection report text was extracted but no findings were parsed.",
        findings=[],
    )


def _settings() -> Settings:
    return replace(Settings.from_env(), extractor_mode="package", process_inline=True)


class FakeExtractor:
    def extract(self, request, pdf_path: Path) -> ExtractionArtifacts:
        return ExtractionArtifacts(
            extraction=_empty_extraction(),
            raw_text="The inspection failed because a fire collar is missing and close-out evidence is required.",
            extractor_name="package:test",
        )


class FakeRepository:
    def __init__(self) -> None:
        self.completed = False
        self.failed = False
        self.error_message = ""

    def complete_document(self, **kwargs) -> None:
        self.completed = True

    def fail_job(self, **kwargs) -> None:
        self.failed = True
        self.error_message = f"Manual review required: {kwargs['error_message']}"

    def get_report(self, tenant_id: str, report_id: str) -> dict | None:
        return {"id": report_id}


class FakeStorage:
    pass


if __name__ == "__main__":
    unittest.main()
