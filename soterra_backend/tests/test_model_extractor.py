from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from soterra_backend.config import DEFAULT_MODEL_ID, DEFAULT_PARSE_MODEL_ID, DEFAULT_REMOTE_MODEL_ID, ModelExtractionConfig, Settings
from soterra_backend.extractors import build_extractor
from soterra_backend.extractors.base import ExtractionArtifacts, ExtractionRequest
from soterra_backend.extractors.demo import DemoExtractor
from soterra_backend.extractors.model import ModelExtractor
from soterra_backend.extractors.model.clients import (
    HuggingFaceInferenceExtractionClient,
    LocalTransformersExtractionClient,
    build_model_extraction_client,
)
from soterra_backend.extractors.model.document_text import (
    DocumentTextResult,
    NemotronParseDocumentTextExtractor,
    PackageDocumentTextExtractor,
)
from soterra_backend.extractors.model.quality import score_extraction_quality
from soterra_backend.models import ExtractedFinding, ExtractionResult


def _settings(*, mode: str = "model", configs: list[ModelExtractionConfig] | None = None) -> Settings:
    model_configs = configs or [ModelExtractionConfig(provider="openai", model_id="test-model", name="primary")]
    return replace(
        Settings.from_env(),
        extractor_mode=mode,
        allow_model_extraction=True,
        model_extraction_models=model_configs,
        model_extraction_max_findings=40,
    )


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        filename="fire-report.pdf",
        content=b"%PDF",
        project_name="Kauri Apartments",
        site_name="Level 1",
        trade="Passive Fire",
    )


def _payload(*, title: str = "Fire-rated pipe penetration annular gap is below the required minimum") -> dict:
    return {
        "project_name": "Kauri Apartments",
        "site_name": "Level 1",
        "address": None,
        "inspection_type": "Passive Fire Inspection",
        "trade": "Passive Fire",
        "inspector": "Fire Consultant",
        "report_date": "2026-06-01",
        "summary": "The inspection identified passive fire defects that require close-out before reinspection.",
        "overall_outcome": "Reviewing",
        "units": [],
        "findings": [
            {
                "title": title,
                "description": "The annular gap around a 100mm metal pipe penetration is below the required minimum.",
                "severity": "High",
                "category": "Passive Fire",
                "trade": "Passive Fire",
                "location": "Service penetration area",
                "unit_label": None,
                "status": "Open",
                "recurrence_risk": 60,
                "project_name": None,
                "issue_title": None,
                "plain_english_summary": "The space around the pipe does not match the approved fire-stopping detail.",
                "level": None,
                "unit_or_area": None,
                "inspection_type": None,
                "root_cause": "Passive fire installation does not match the approved system detail.",
                "required_fix": "Reinstate the annular gap to the approved size and reseal using the specified fire-rated system.",
                "evidence_required": ["Close-up repair photo", "Approved fire-stopping system detail"],
                "source_document": None,
                "source_page": 1,
                "source_quote": "100mm metal pipe penetration annular gap below minimum",
                "confidence": 0.86,
                "extraction_warnings": [],
            }
        ],
        "predicted_inspections": [],
    }


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def extract_json(self, **kwargs) -> dict:
        return self.payload


class FakeDocumentTextExtractor:
    def __init__(self, text: str = "100mm metal pipe penetration annular gap below minimum", source: str = "test-document-text") -> None:
        self.result = DocumentTextResult(text=text, source=source, metadata={"parse_model_id": DEFAULT_PARSE_MODEL_ID})

    def extract_text(self, pdf_path: Path) -> DocumentTextResult:
        return self.result


class ModelExtractorTest(unittest.TestCase):
    def test_default_model_configuration_uses_smollm_and_nemotron_parse(self) -> None:
        with patch("soterra_backend.config._load_env_file", return_value=None), patch.dict("os.environ", {}, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.extractor_mode, "model")
        self.assertTrue(settings.allow_model_extraction)
        self.assertEqual(settings.model_extraction_models[0].provider, "local_transformers")
        self.assertEqual(settings.model_extraction_models[0].model_id, DEFAULT_MODEL_ID)
        self.assertEqual(settings.soterra_agent_model_id, DEFAULT_MODEL_ID)
        self.assertEqual(settings.soterra_document_parse_model_id, DEFAULT_PARSE_MODEL_ID)

    def test_vercel_defaults_use_remote_smollm_and_remote_nemotron_parse_provider(self) -> None:
        with patch("soterra_backend.config._load_env_file", return_value=None), patch.dict("os.environ", {"VERCEL": "1"}, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.model_extraction_models[0].provider, "huggingface")
        self.assertEqual(settings.model_extraction_models[0].model_id, DEFAULT_REMOTE_MODEL_ID)
        self.assertEqual(settings.soterra_agent_provider, "huggingface")
        self.assertEqual(settings.soterra_agent_model_id, DEFAULT_REMOTE_MODEL_ID)
        self.assertEqual(settings.soterra_document_parse_provider, "openai_compatible")
        self.assertEqual(settings.soterra_document_parse_model_id, DEFAULT_PARSE_MODEL_ID)

    def test_document_parse_uses_nemotron_parse_with_package_fallback(self) -> None:
        settings = _settings()
        extractor = NemotronParseDocumentTextExtractor(
            provider=settings.soterra_document_parse_provider,
            model_id=settings.soterra_document_parse_model_id,
            max_pages=settings.document_parse_max_pages,
            max_new_tokens=settings.document_parse_max_new_tokens,
            text_in_pictures=settings.document_parse_text_in_pictures,
            fallback=PackageDocumentTextExtractor(max_pages=settings.package_max_pages),
        )

        self.assertEqual(extractor.model_id, DEFAULT_PARSE_MODEL_ID)
        self.assertEqual(extractor.provider, "local_transformers")

    def test_build_extractor_returns_model_extractor_when_mode_is_model(self) -> None:
        extractor = build_extractor(_settings())
        self.assertIsInstance(extractor, ModelExtractor)

    def test_huggingface_provider_builds_inference_client(self) -> None:
        settings = _settings(configs=[ModelExtractionConfig(provider="huggingface", model_id=DEFAULT_MODEL_ID)])
        client = build_model_extraction_client(settings, settings.model_extraction_models[0])
        self.assertIsInstance(client, HuggingFaceInferenceExtractionClient)

    def test_local_transformers_provider_builds_lazy_local_client(self) -> None:
        settings = _settings(configs=[ModelExtractionConfig(provider="local_transformers", model_id=DEFAULT_MODEL_ID)])
        client = build_model_extraction_client(settings, settings.model_extraction_models[0])
        self.assertIsInstance(client, LocalTransformersExtractionClient)

    def test_package_and_demo_modes_still_work(self) -> None:
        self.assertEqual(build_extractor(replace(_settings(), extractor_mode="package")).__class__.__name__, "DoctrRulesPresidioExtractor")
        self.assertIsInstance(build_extractor(replace(_settings(), app_env="test", extractor_mode="demo")), DemoExtractor)

    def test_model_extraction_validates_json_and_keeps_model_closeout_fields(self) -> None:
        config = ModelExtractionConfig(provider="openai", model_id="test-model")
        extractor = ModelExtractor(
            _settings(configs=[config]),
            clients={("openai", "test-model"): FakeClient(_payload())},
            document_text_extractor=FakeDocumentTextExtractor(),
        )
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_file:
            artifacts = extractor.extract(_request(), Path(tmp_file.name))

        finding = artifacts.extraction.findings[0]
        self.assertEqual(finding.required_fix, _payload()["findings"][0]["required_fix"])
        self.assertEqual(finding.evidence_required, _payload()["findings"][0]["evidence_required"])
        self.assertEqual(artifacts.metadata["model_id"], "test-model")
        self.assertFalse(artifacts.metadata["fallback_used"])

    def test_best_quality_model_is_selected_from_two_configs(self) -> None:
        weak_config = ModelExtractionConfig(provider="openai", model_id="weak-model")
        strong_config = ModelExtractionConfig(provider="openai", model_id="strong-model")
        weak_payload = _payload(title="Issue needs attention")
        weak_payload["findings"][0]["plain_english_summary"] = "Issue needs attention."
        strong_payload = _payload(title="Fire-rated pipe penetration annular gap is below the required minimum")
        extractor = ModelExtractor(
            _settings(configs=[weak_config, strong_config]),
            clients={
                ("openai", "weak-model"): FakeClient(weak_payload),
                ("openai", "strong-model"): FakeClient(strong_payload),
            },
            document_text_extractor=FakeDocumentTextExtractor(),
        )
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_file:
            artifacts = extractor.extract(_request(), Path(tmp_file.name))

        self.assertEqual(artifacts.metadata["model_id"], "strong-model")
        self.assertEqual(len(artifacts.metadata["model_attempts"]), 2)

    def test_poor_model_output_falls_back_to_package_extractor(self) -> None:
        config = ModelExtractionConfig(provider="openai", model_id="test-model")
        extractor = ModelExtractor(
            _settings(configs=[config]),
            clients={("openai", "test-model"): FakeClient({**_payload(), "findings": []})},
            document_text_extractor=FakeDocumentTextExtractor("missing below minimum required defect"),
        )
        extractor.fallback = FakeFallback()
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_file:
            artifacts = extractor.extract(_request(), Path(tmp_file.name))

        self.assertTrue(artifacts.metadata["fallback_used"])
        self.assertTrue(artifacts.extractor_name.startswith("fallback:package_after_model"))

    def test_duplicate_issue_titles_are_deduplicated(self) -> None:
        payload = _payload()
        payload["findings"] = payload["findings"] * 2
        config = ModelExtractionConfig(provider="openai", model_id="test-model")
        extractor = ModelExtractor(
            _settings(configs=[config]),
            clients={("openai", "test-model"): FakeClient(payload)},
            document_text_extractor=FakeDocumentTextExtractor(),
        )
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_file:
            artifacts = extractor.extract(_request(), Path(tmp_file.name))

        self.assertEqual(len(artifacts.extraction.findings), 1)

    def test_quality_flags_vague_findings(self) -> None:
        extraction = ExtractionResult(
            project_name="Kauri Apartments",
            site_name="Level 1",
            inspection_type="Fire Inspection",
            trade="Passive Fire",
            inspector="Fire Consultant",
            report_date="2026-06-01",
            summary="The report contains open construction issues that need close-out.",
            findings=[
                ExtractedFinding(
                    title="Issue needs attention",
                    description="Issue needs attention.",
                    severity="Medium",
                    required_fix="Fix required.",
                    confidence=0.9,
                )
            ],
        )
        quality = score_extraction_quality(extraction, raw_text="defect missing required")
        self.assertTrue(quality.should_fallback)
        self.assertTrue(any("vague" in warning.lower() for warning in quality.warnings))


class FakeFallback:
    def extract(self, request: ExtractionRequest, pdf_path: Path) -> ExtractionArtifacts:
        return ExtractionArtifacts(
            extraction=ExtractionResult(
                project_name=request.project_name,
                site_name=request.site_name,
                inspection_type="Fallback Inspection",
                trade=request.trade,
                inspector="Package Extractor",
                report_date="2026-06-01",
                summary="Fallback package extraction found one actionable issue.",
                findings=[
                    ExtractedFinding(
                        title="Fallback fire stopping issue",
                        description="Fallback fire stopping issue requires close-out.",
                        severity="High",
                        required_fix="Complete the passive fire repair.",
                        evidence_required=["After photo"],
                    )
                ],
            ),
            raw_text="fallback text",
            extractor_name="package:test",
        )


if __name__ == "__main__":
    unittest.main()
