from __future__ import annotations

import hashlib
from pathlib import Path

from ..demo_extractions import fallback_demo_extraction, match_demo_extraction
from ..text_extraction import extract_embedded_text
from .base import ExtractionArtifacts, ExtractionRequest


class DemoExtractor:
    def extract(self, request: ExtractionRequest, pdf_path: Path) -> ExtractionArtifacts:
        # Demo mode should be deterministic and fast. The curated demo profiles match on filename,
        # so avoid OCR fallbacks (easyocr/docTR) that slow down local test runs.
        raw_text = extract_embedded_text(pdf_path)
        extraction_source = "embedded-text" if raw_text else "empty"
        demo_match = match_demo_extraction(request.filename, raw_text)
        if demo_match:
            return ExtractionArtifacts(
                extraction=demo_match,
                raw_text=raw_text,
                extractor_name=f"demo:{_document_signature(request.content)}:{extraction_source}",
            )

        return ExtractionArtifacts(
            extraction=fallback_demo_extraction(request.filename, raw_text),
            raw_text=raw_text,
            extractor_name=f"demo-fallback:{extraction_source}",
        )


def _document_signature(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()[:10]
