from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from soterra_backend.config import Settings
from soterra_backend.extraction_quality_gate import ExtractionQualityError, validate_extraction_quality
from soterra_backend.extractors.base import ExtractionArtifacts
from soterra_backend.extractors.package_doctr import DoctrRulesPresidioExtractor, _build_rule_extraction, extract_issue_blocks
from soterra_backend.issue_intelligence import enrich_finding
from soterra_backend.models import ExtractedFinding, ExtractionResult, StoredFile
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

    def test_positive_penetration_observation_is_not_actionable(self) -> None:
        finding = {
            "title": "Cable penetration installation looks okay",
            "description": "The fire stop installation looks okay. Cable bundle diameter is less than 20mm.",
            "category": "Passive Fire",
            "trade": "Passive Fire",
            "severity": "Low",
            "status": "Open",
        }

        enriched = enrich_finding(finding)

        self.assertFalse(enriched["is_actionable"])
        self.assertIn("positive observation", enriched["non_actionable_reason"])

    def test_ocr_drawing_footer_noise_is_not_actionable(self) -> None:
        finding = {
            "title": "Whilst Products.tdy the informa useb drawingmaybea",
            "description": "Whilst Products.tdy the informa useb drawingmaybea RyanFire legal proceedings termsa.",
            "category": "Mechanical",
            "trade": "Mechanical",
            "severity": "Medium",
            "status": "Open",
        }

        enriched = enrich_finding(finding)

        self.assertFalse(enriched["is_actionable"])
        self.assertIn("OCR/table/drawing noise", enriched["non_actionable_reason"])

    def test_services_report_intro_text_is_not_actionable(self) -> None:
        finding = {
            "title": "We conducted a site inspection on 09.04.2024 to the following area; Level 3 Mechanical and Hydraulics Services",
            "description": "We conducted a site inspection on 09.04.2024 to the following area; Level 3 Mechanical and Hydraulics Services. We note the following elements discussed on site.",
            "category": "Mechanical",
            "trade": "Mechanical",
            "severity": "Low",
            "status": "Open",
        }

        enriched = enrich_finding(finding)

        self.assertFalse(enriched["is_actionable"])

    def test_resolved_services_observation_is_not_actionable(self) -> None:
        finding = {
            "title": "Data had been installed to level 1 after missing previously",
            "description": "Data had been installed to level 1 after missing previously.",
            "category": "Mechanical",
            "trade": "Mechanical",
            "severity": "High",
            "status": "Open",
        }

        enriched = enrich_finding(finding)

        self.assertFalse(enriched["is_actionable"])

    def test_services_duct_issue_gets_clear_display_title(self) -> None:
        finding = {
            "title": "suitable",
            "description": "suitable. duct is squeezed by pipework amongst other issues. re-routing discussed with main contractor on site to use open space circled red.",
            "category": "Mechanical",
            "trade": "Mechanical",
            "severity": "Low",
            "status": "Open",
        }

        enriched = enrich_finding(finding)

        self.assertTrue(enriched["is_actionable"])
        self.assertEqual(enriched["display_title"], "Duct squeezed by pipework on level 2")

    def test_council_truncated_titles_get_complete_display_labels(self) -> None:
        examples = [
            (
                "The flashing work where the building wrap meets junctions did not meet the council's requirements and must be redone or",
                "Junction flashings failed inspection",
            ),
            (
                "The cavity battens behind the cladding are not installed correctly or do not match the consented plans, so the drained c",
                "Cavity battens do not match consented plans",
            ),
            (
                "The height difference between the deck/balcony and the door threshold does not match the plan, so water can be driven in",
                "Deck/balcony threshold step-down failed inspection",
            ),
        ]

        for title, expected in examples:
            with self.subTest(title=title):
                enriched = enrich_finding(
                    {
                        "title": title,
                        "description": title,
                        "category": "Envelope",
                        "trade": "Envelope",
                        "severity": "High",
                        "status": "Open",
                    }
                )

                self.assertTrue(enriched["is_actionable"])
                self.assertEqual(enriched["display_title"], expected)
                self.assertNotRegex(enriched["plain_english_summary"], r"\b(or|c|in) at ")

    def test_generic_long_title_is_clipped_on_word_boundary(self) -> None:
        title = (
            "The mechanical services ductwork has been installed hard against the framing and needs "
            "coordination review before lining can proceed because the clearance shown on site is not"
        )

        enriched = enrich_finding(
            {
                "title": title,
                "description": title,
                "category": "Mechanical",
                "trade": "Mechanical",
                "severity": "High",
                "status": "Open",
            }
        )

        self.assertNotRegex(enriched["display_title"], r"\b(not|or|and|the|to|in|of|with)$")
        self.assertLessEqual(len(enriched["display_title"]), 120)

    def test_live_threshold_issue_gets_concise_display_title_and_trade(self) -> None:
        enriched = enrich_finding(
            {
                "title": "Deck/balcony threshold step down does not comply with plan",
                "issue_title": "Deck/balcony threshold step down as per plan",
                "description": "The checklist item was recorded as Fail.",
                "plain_english_summary": "The step down from inside the building to the deck/balcony must be correct so water running off the deck cannot drain back into the building.",
                "location": "Exact door/unit not stated in the visible portion of the report.",
                "inspection_type": "Cavity wrap (ICA)",
                "category": "Waterproofing",
                "trade": "General",
                "severity": "High",
                "status": "Open",
                "required_fix": "Review the inspector's photos/comments, measure the threshold step down at each failed door, adjust the deck/balcony structure or threshold detail to meet the approved plan and E2/AS1, then book a re-inspection.",
            }
        )

        self.assertEqual(enriched["display_title"], "Deck/balcony threshold step-down failed inspection")
        self.assertEqual(enriched["trade"], "Envelope")
        self.assertNotIn("report..", enriched["plain_english_summary"])
        self.assertNotIn("cannot drain ba", enriched["display_title"])

    def test_live_duct_clearance_issue_gets_concise_display_title(self) -> None:
        enriched = enrich_finding(
            {
                "title": "Mechanical ducting pressed hard against frame - insufficient clearance",
                "issue_title": "Ducting hard pressed against frame - trim frame to allow clearance",
                "description": "Mechanical ducting is hard pressed against the structural frame with insufficient clearance.",
                "plain_english_summary": "A metal duct is jammed against the building frame with no working clearance.",
                "location": "Sheet location marker M (circled in red) on the Level 3 mechanical plan.",
                "inspection_type": "Services Inspection",
                "category": "Mechanical",
                "trade": "Mechanical",
                "severity": "Medium",
                "status": "Open",
            }
        )

        self.assertEqual(enriched["display_title"], "Ducting pressed against frame without clearance")
        self.assertIn("Create the required duct clearance", enriched["plain_english_summary"])

    def test_construction_taxonomy_covers_common_trade_terms(self) -> None:
        examples = [
            ("Head flashing missing at window opening", "Envelope", "Envelope"),
            ("Deck membrane upstand below 150mm minimum", "Waterproofing", "Envelope"),
            ("Pipe penetration annular gap requires fire sealant", "Passive Fire - Penetrations", "Passive Fire"),
            ("Mechanical ductwork clashing with framing", "Mechanical / Ventilation", "Mechanical"),
            ("Data cabling conduit not installed", "Electrical", "Electrical"),
            ("Hydraulic pipework drainage not isolated", "Plumbing / Drainage", "Plumbing"),
            ("Structural timber framing requires bracing fix", "Structure", "Structure"),
            ("Roof cladding junction at parapet incomplete", "Envelope", "Envelope"),
            ("Gutter outlet and downpipe not installed", "Surface Water / Stormwater", "Plumbing"),
            ("Shower waterproof lining sealant incomplete", "Internal Moisture / Wet Areas", "Plumbing"),
            ("Fire separation wall penetration not sealed", "Passive Fire - Penetrations", "Passive Fire"),
            ("Fire damper breakaway fixing missing", "Passive Fire - Dampers", "Passive Fire"),
            ("HVAC volume control damper requires adjustment", "Mechanical / Ventilation", "Mechanical"),
            ("Extract fan duct not connected to grille", "Mechanical / Ventilation", "Mechanical"),
            ("Socket outlet and light switch missing", "Electrical", "Electrical"),
            ("Cold water backflow valve not installed", "Plumbing / Drainage", "Plumbing"),
            ("Foul water discharge pipe not connected", "Plumbing / Drainage", "Plumbing"),
            ("Foundation footing steel bracing incomplete", "Structure", "Structure"),
            ("Galvanised fixing corrosion risk requires confirmation", "Durability", "General"),
            ("Balustrade barrier height below required level", "Access / Safety", "General"),
            ("Insulation R-value evidence missing", "Energy Efficiency", "General"),
            ("We conducted a site inspection", "General", "General"),
            ("Wrap up comments from site meeting", "General", "General"),
            ("Data sheet provided for product", "General", "General"),
        ]

        for title, expected_category, expected_trade in examples:
            with self.subTest(title=title):
                enriched = enrich_finding(
                    {
                        "title": title,
                        "description": title,
                        "category": "General",
                        "trade": "General",
                        "severity": "Medium",
                        "status": "Open",
                    }
                )

                self.assertEqual(enriched["display_category"], expected_category)
                self.assertEqual(enriched["trade"], expected_trade)

    def test_package_extractor_does_not_run_ocr_when_disabled(self) -> None:
        extractor = DoctrRulesPresidioExtractor(replace(_settings(), package_ocr_enabled=False))
        with patch.dict("os.environ", {"SOTERRA_PACKAGE_OCR_ENABLED": "false"}, clear=False), \
            patch("soterra_backend.extractors.package_doctr.extract_embedded_text", return_value=""), \
            patch("soterra_backend.extractors.package_doctr._extract_text_with_doctr") as ocr:
            artifacts = extractor.extract(_request(), Path("dummy.pdf"))

        ocr.assert_not_called()
        self.assertEqual(artifacts.metadata["text_source"], "embedded-text-sparse-ocr-disabled")
        self.assertFalse(artifacts.metadata["ocr_attempted"])

    def test_package_extractor_runs_ocr_when_enabled(self) -> None:
        extractor = DoctrRulesPresidioExtractor(replace(_settings(), package_ocr_enabled=True, package_ocr_max_pages=3))
        ocr_text = "Date 01/06/2026\n1. Level 2 riser failed fire stop inspection and close-out is required."
        with patch.dict("os.environ", {"SOTERRA_PACKAGE_OCR_ENABLED": "true", "SOTERRA_PACKAGE_OCR_MAX_PAGES": "3"}, clear=False), \
            patch("soterra_backend.extractors.package_doctr.extract_embedded_text", return_value=""), \
            patch("soterra_backend.extractors.package_doctr._extract_text_with_doctr", return_value=ocr_text) as ocr:
            artifacts = extractor.extract(_request(), Path("dummy.pdf"))

        ocr.assert_called_once()
        self.assertEqual(ocr.call_args.kwargs["max_pages"], 3)
        self.assertEqual(artifacts.metadata["text_source"], "ocr")
        self.assertTrue(artifacts.metadata["ocr_attempted"])
        self.assertGreater(artifacts.metadata["raw_text_length"], 0)
        self.assertIn(":ocr", artifacts.extractor_name)

    def test_package_extractor_records_ocr_exception_metadata(self) -> None:
        extractor = DoctrRulesPresidioExtractor(replace(_settings(), package_ocr_enabled=True, package_ocr_max_pages=3))
        with self.assertLogs("soterra_backend", level="WARNING") as logs, \
            patch.dict("os.environ", {"SOTERRA_PACKAGE_OCR_ENABLED": "true"}, clear=False), \
            patch("soterra_backend.extractors.package_doctr.extract_embedded_text", return_value=""), \
            patch("soterra_backend.extractors.package_doctr._extract_text_with_doctr", side_effect=ValueError("ocr exploded")):
            artifacts = extractor.extract(_request(), Path("dummy.pdf"))

        self.assertEqual(artifacts.metadata["text_source"], "embedded-text-sparse-ocr-failed")
        self.assertTrue(artifacts.metadata["ocr_attempted"])
        self.assertIn("ValueError: ocr exploded", artifacts.metadata["ocr_error"])
        self.assertTrue(any("package_ocr_failed" in line for line in logs.output))

    def test_quality_gate_raises_for_issue_text_with_no_findings(self) -> None:
        extraction = _empty_extraction()
        raw_text = "This report says failed fire stop, missing collar, non-compliant penetration and close-out required."
        with self.assertRaises(ExtractionQualityError) as caught:
            validate_extraction_quality(extraction, raw_text)
        self.assertFalse(caught.exception.diagnostics["quality_gate_passed"])
        self.assertGreater(caught.exception.diagnostics["issue_keyword_hits"], 0)

    def test_quality_gate_raises_when_many_findings_are_cut_off(self) -> None:
        extraction = _empty_extraction().model_copy(
            update={
                "findings": [
                    ExtractedFinding(
                        title="Fire collar missing or",
                        description="Fire collar missing or",
                        severity="High",
                        required_fix="Install the missing fire collar and upload evidence.",
                    ),
                    ExtractedFinding(
                        title="Ductwork clash requires re-routing",
                        description="Ductwork is clashing with other services and requires re-routing.",
                        severity="Medium",
                        required_fix="Re-route the ductwork and upload QA photos.",
                    ),
                ]
            }
        )

        with self.assertRaises(ExtractionQualityError) as caught:
            validate_extraction_quality(extraction, "Fire collar missing. Ductwork clash requires re-routing.")

        self.assertEqual(caught.exception.diagnostics["cut_off_finding_count"], 1)

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
