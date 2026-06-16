from __future__ import annotations

import logging
from pathlib import Path

from ..config import Settings
from ..extraction_quality import finalize_extraction
from ..text_extraction import extract_embedded_text
from .base import ExtractionArtifacts, ExtractionRequest
from .docling_parser import ParsedDocument, parsed_document_from_text, parse_with_docling
from .ollama_model import OllamaModelExtractor
from .package_doctr import DoctrRulesPresidioExtractor, _redact_text
from .paddle_ocr_parser import parse_with_paddle_ocr

logger = logging.getLogger("soterra_backend")

STRONG_TEXT_LENGTH = 400


class LocalAIPipelineExtractor:
    def __init__(
        self,
        settings: Settings,
        *,
        model_extractor: OllamaModelExtractor | None = None,
        fallback: DoctrRulesPresidioExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.model_extractor = model_extractor or OllamaModelExtractor(
            base_url=settings.soterra_ollama_base_url,
            model_id=settings.soterra_extraction_model_id,
            api_key=settings.soterra_ollama_api_key,
            timeout_seconds=settings.model_extraction_timeout_seconds,
            temperature=settings.model_extraction_temperature,
            max_findings=settings.model_extraction_max_findings,
        )
        self.fallback = fallback or DoctrRulesPresidioExtractor(settings, use_curated_profiles=False, use_demo_fallback=False)

    def extract(self, request: ExtractionRequest, pdf_path: Path) -> ExtractionArtifacts:
        metadata: dict = {
            "extractor_mode": "local_ai",
            "model_id": self.settings.soterra_extraction_model_id,
            "model_provider": "ollama",
            "ollama_base_url": self.settings.soterra_ollama_base_url,
            "docling_attempted": False,
            "docling_success": False,
            "paddle_ocr_attempted": False,
            "paddle_ocr_success": False,
        }
        try:
            artifacts = self._extract_local_ai(request, pdf_path, metadata)
            return artifacts
        except Exception as exc:
            if not self.settings.local_ai_fallback_to_package:
                raise
            logger.warning("local_ai_fallback_to_package file=%s error=%s", request.filename, exc)
            fallback_artifacts = self.fallback.extract(request, pdf_path)
            return ExtractionArtifacts(
                extraction=fallback_artifacts.extraction,
                raw_text=fallback_artifacts.raw_text,
                extractor_name=f"fallback:package_after_local_ai:{type(exc).__name__}",
                metadata={
                    **metadata,
                    **fallback_artifacts.metadata,
                    "fallback_used": True,
                    "fallback_reason": f"{type(exc).__name__}: {str(exc)[:400]}",
                },
            )

    def _extract_local_ai(self, request: ExtractionRequest, pdf_path: Path, metadata: dict) -> ExtractionArtifacts:
        embedded_text = extract_embedded_text(pdf_path) if pdf_path.suffix.lower() == ".pdf" else ""
        embedded_document = parsed_document_from_text(
            embedded_text,
            provider="embedded-text",
            metadata={"raw_text_length": len(embedded_text.strip())},
        )
        metadata["embedded_text_length"] = len(embedded_text.strip())

        docling_document: ParsedDocument | None = None
        docling_error: str | None = None
        parse_provider = self.settings.soterra_document_parse_provider.strip().lower()
        should_run_docling = parse_provider == "docling" or metadata["embedded_text_length"] < STRONG_TEXT_LENGTH
        if should_run_docling:
            metadata["docling_attempted"] = True
            try:
                docling_document = parse_with_docling(pdf_path)
                metadata["docling_success"] = True
                metadata["docling_text_length"] = len(docling_document.full_text.strip())
            except Exception as exc:
                docling_error = f"{type(exc).__name__}: {exc}"
                metadata["docling_error"] = docling_error[:500]

        final_document = _choose_best_document(embedded_document, docling_document)
        metadata["parse_provider"] = final_document.metadata.get("parse_provider", "unknown")

        if len(final_document.full_text.strip()) < STRONG_TEXT_LENGTH and self.settings.paddle_ocr_enabled:
            metadata["paddle_ocr_attempted"] = True
            if pdf_path.suffix.lower() != ".pdf":
                metadata["paddle_ocr_error"] = "PaddleOCR fallback only supports PDFs."
            else:
                try:
                    ocr_document = parse_with_paddle_ocr(
                        pdf_path,
                        max_pages=self.settings.paddle_ocr_max_pages,
                        lang=self.settings.paddle_ocr_lang,
                    )
                    if len(ocr_document.full_text.strip()) > len(final_document.full_text.strip()):
                        final_document = ocr_document
                    metadata["paddle_ocr_success"] = True
                    metadata["paddle_ocr_text_length"] = len(ocr_document.full_text.strip())
                    metadata["parse_provider"] = final_document.metadata.get("parse_provider", "unknown")
                except Exception as exc:
                    metadata["paddle_ocr_error"] = f"{type(exc).__name__}: {exc}"[:500]

        raw_text = final_document.full_text.strip()
        metadata["raw_text_length"] = len(raw_text)
        if not raw_text:
            raise RuntimeError(f"Local AI extraction could not read document text. Docling error: {docling_error or 'none'}")

        extraction = self.model_extractor.extract(parsed_document=final_document, request=request)
        extraction = finalize_extraction(extraction, request.filename)
        if self.settings.model_extraction_max_findings > 0:
            extraction = extraction.model_copy(update={"findings": extraction.findings[: self.settings.model_extraction_max_findings]})

        metadata["finding_count"] = len(extraction.findings)
        metadata["fallback_used"] = False
        return ExtractionArtifacts(
            extraction=extraction,
            raw_text=_redact_text(raw_text),
            extractor_name=f"local-ai:{metadata['parse_provider']}+ollama:{self.settings.soterra_extraction_model_id}",
            metadata=metadata,
        )


def _choose_best_document(embedded: ParsedDocument, docling: ParsedDocument | None) -> ParsedDocument:
    if docling and len(docling.full_text.strip()) >= max(STRONG_TEXT_LENGTH, len(embedded.full_text.strip())):
        return docling
    if docling and len(embedded.full_text.strip()) < STRONG_TEXT_LENGTH:
        return docling
    return embedded
