from __future__ import annotations

import re

from .extractors.package_doctr import ISSUE_HINTS
from .models import ExtractionResult


class ExtractionQualityError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


def validate_extraction_quality(extraction: ExtractionResult, raw_text: str) -> dict:
    normalized = raw_text or ""
    hits = _issue_keyword_hits(normalized)
    diagnostics = {
        "raw_text_length": len(normalized.strip()),
        "finding_count": len(extraction.findings),
        "issue_keyword_hits": hits,
        "quality_gate_passed": False,
    }

    if diagnostics["raw_text_length"] < 80 and not extraction.findings:
        raise ExtractionQualityError("Extracted document text is too short for reliable inspection parsing.", diagnostics)

    if hits and not extraction.findings:
        raise ExtractionQualityError(
            "Issue-like text was found, but no findings were extracted. Manual review is required.",
            diagnostics,
        )

    diagnostics["quality_gate_passed"] = True
    return diagnostics


def _issue_keyword_hits(text: str) -> int:
    lowered = text.lower()
    return sum(len(re.findall(rf"\b{re.escape(token)}\b", lowered)) for token in ISSUE_HINTS)
