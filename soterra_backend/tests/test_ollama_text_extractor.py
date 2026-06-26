from __future__ import annotations

import builtins
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import fitz
import httpx

from soterra_backend.config import Settings
from soterra_backend.extractors import build_extractor
from soterra_backend.extractors.base import ExtractionRequest
from soterra_backend.extractors.ollama_text import OllamaTextExtractor, extract_text_for_ollama
from soterra_backend.models import ExtractionResult


FORBIDDEN_IMPORTS = ("docling", "paddleocr", "torch", "torchvision", "transformers", "nvidia")


def _settings(**updates) -> Settings:
    base = replace(
        Settings.from_env(),
        extractor_mode="ollama_text",
        soterra_extraction_provider="ollama",
        soterra_extraction_model_id="gpt-oss:20b",
        soterra_extraction_vision_model_id="llama3.2-vision",
        soterra_ollama_base_url="https://ollama.com",
        soterra_ollama_api_key="test-key",
        model_extraction_retry_count=0,
        model_extraction_max_findings=40,
    )
    return replace(base, **updates)


def _request(content: bytes = b"%PDF") -> ExtractionRequest:
    return ExtractionRequest(
        filename="inspection.pdf",
        content=content,
        project_name="Kauri Apartments",
        site_name="Level 1",
        trade="Passive Fire",
    )


def _payload() -> dict:
    return {
        "project_name": "Kauri Apartments",
        "site_name": "Level 1",
        "address": None,
        "inspection_type": "Passive Fire Inspection",
        "trade": "Passive Fire",
        "inspector": "Inspector",
        "report_date": "2026-06-01",
        "summary": "The inspection found passive fire issues requiring close-out.",
        "overall_outcome": "Reviewing",
        "units": ["Level 1"],
        "findings": [
            {
                "title": "Fire collar missing",
                "description": "Fire collar is missing in the Level 1 riser.",
                "severity": "High",
                "category": "General",
                "trade": "General",
                "location": "Riser",
                "unit_label": "Level 1",
                "status": "Open",
                "recurrence_risk": 70,
                "project_name": None,
                "issue_title": None,
                "plain_english_summary": None,
                "level": None,
                "unit_or_area": None,
                "inspection_type": None,
                "root_cause": None,
                "required_fix": "Install the missing fire collar and upload close-out evidence.",
                "evidence_required": ["after photo", "installer sign-off"],
                "source_document": None,
                "source_page": 1,
                "source_quote": "Fire collar missing in riser.",
                "confidence": 0.88,
                "extraction_warnings": [],
            }
        ],
        "predicted_inspections": [],
    }


def _write_pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    for index, line in enumerate([text[i : i + 90] for i in range(0, len(text), 90)][:20]):
        page.insert_text((72, 72 + (index * 18)), line)
    document.save(path)
    document.close()


class OllamaTextExtractorTest(unittest.TestCase):
    def test_ollama_text_mode_builds_without_heavy_ai_imports(self) -> None:
        real_import = builtins.__import__
        attempted: list[str] = []

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            root = name.split(".", 1)[0]
            if level == 0 and root in FORBIDDEN_IMPORTS:
                attempted.append(name)
                raise AssertionError(f"ollama_text imported forbidden package: {name}")
            return real_import(name, globals, locals, fromlist, level)

        with patch.object(builtins, "__import__", guarded_import):
            extractor = build_extractor(_settings())

        self.assertIsInstance(extractor, OllamaTextExtractor)
        self.assertEqual(attempted, [])

    def test_text_extraction_uses_pymupdf_for_simple_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "inspection.pdf"
            _write_pdf(pdf_path, "Fire collar missing in Level 1 riser. " * 20)

            extracted = extract_text_for_ollama(pdf_path, request=_request(pdf_path.read_bytes()))

        self.assertEqual(extracted.source, "pymupdf")
        self.assertIn("Fire collar missing", extracted.text)
        self.assertFalse(extracted.metadata["pypdf_attempted"])

    def test_weak_pymupdf_text_falls_back_to_pypdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "inspection.pdf"
            _write_pdf(pdf_path, "short")

            with patch("soterra_backend.extractors.ollama_text._extract_with_pymupdf", return_value="short"), patch(
                "soterra_backend.extractors.ollama_text._extract_with_pypdf",
                return_value="Fire collar missing in Level 1 riser. Repair required.",
            ):
                extracted = extract_text_for_ollama(pdf_path, request=_request(pdf_path.read_bytes()))

        self.assertEqual(extracted.source, "pypdf")
        self.assertTrue(extracted.metadata["pypdf_attempted"])
        self.assertIn("Repair required", extracted.text)

    def test_ollama_api_call_can_be_mocked_and_returns_schema(self) -> None:
        calls = []

        def fake_post_ollama_chat(**kwargs):
            calls.append(kwargs)
            return httpx.Response(200, json={"message": {"content": json.dumps(_payload())}})

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "inspection.pdf"
            _write_pdf(pdf_path, "Fire collar missing in Level 1 riser. Repair required. " * 20)
            extractor = OllamaTextExtractor(_settings())

            with patch("soterra_backend.extractors.ollama_text._post_ollama_chat", side_effect=fake_post_ollama_chat):
                artifacts = extractor.extract(_request(pdf_path.read_bytes()), pdf_path)

        self.assertIsInstance(artifacts.extraction, ExtractionResult)
        self.assertEqual(artifacts.extraction.findings[0].title, "Fire collar missing")
        self.assertEqual(artifacts.extraction.findings[0].source_document, "inspection.pdf")
        self.assertEqual(artifacts.metadata["extractor_mode"], "ollama_text")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(calls[0]["payload"]["model"], "gpt-oss:20b")
        self.assertNotIn('"properties"', calls[0]["payload"]["messages"][1]["content"])

    def test_ollama_options_include_configured_context_and_output_budget(self) -> None:
        calls = []

        def fake_post_ollama_chat(**kwargs):
            calls.append(kwargs)
            return httpx.Response(200, json={"message": {"content": json.dumps(_payload())}})

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "inspection.pdf"
            _write_pdf(pdf_path, "Fire collar missing in Level 1 riser. Repair required. " * 20)
            extractor = OllamaTextExtractor(_settings())

            with patch.dict("os.environ", {"SOTERRA_OLLAMA_NUM_CTX": "64000", "SOTERRA_OLLAMA_NUM_PREDICT": "12000"}, clear=False), patch(
                "soterra_backend.extractors.ollama_text._post_ollama_chat",
                side_effect=fake_post_ollama_chat,
            ):
                extractor.extract(_request(pdf_path.read_bytes()), pdf_path)

        self.assertEqual(calls[0]["payload"]["options"]["num_ctx"], 64000)
        self.assertEqual(calls[0]["payload"]["options"]["num_predict"], 12000)

    def test_image_only_pdf_sends_rendered_pages_to_ollama(self) -> None:
        calls = []

        def fake_post_ollama_chat(**kwargs):
            calls.append(kwargs)
            return httpx.Response(200, json={"message": {"content": json.dumps(_payload())}})

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "scan.pdf"
            _write_pdf(pdf_path, "tiny")
            extractor = OllamaTextExtractor(_settings(document_parse_max_pages=2))

            with patch("soterra_backend.extractors.ollama_text._extract_with_pymupdf", return_value=""), patch(
                "soterra_backend.extractors.ollama_text._extract_with_pypdf",
                return_value="",
            ), patch("soterra_backend.extractors.ollama_text.render_page_images_for_ollama", return_value=["base64-page"]), patch(
                "soterra_backend.extractors.ollama_text._post_ollama_chat",
                side_effect=fake_post_ollama_chat,
            ):
                artifacts = extractor.extract(_request(pdf_path.read_bytes()), pdf_path)

        user_message = calls[0]["payload"]["messages"][1]
        self.assertEqual(calls[0]["payload"]["model"], "llama3.2-vision")
        self.assertEqual(user_message["images"], ["base64-page"])
        self.assertEqual(artifacts.metadata["extraction_source"], "pymupdf-page-images")
        self.assertTrue(artifacts.metadata["document_text"]["image_fallback_used"])


if __name__ == "__main__":
    unittest.main()
