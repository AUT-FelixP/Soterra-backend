from __future__ import annotations

import re
from collections import Counter

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
        "source_quote_coverage": _source_quote_coverage(extraction),
        "missing_location_count": _missing_location_count(extraction),
        "low_confidence_count": _low_confidence_count(extraction),
        "duplicate_finding_count": _duplicate_finding_count(extraction),
        "general_trade_count": _general_trade_count(extraction),
        "quality_warnings": [],
        "quality_gate_passed": False,
    }

    if diagnostics["raw_text_length"] < 80 and not extraction.findings:
        raise ExtractionQualityError("Extracted document text is too short for reliable inspection parsing.", diagnostics)

    if hits and not extraction.findings:
        raise ExtractionQualityError(
            "Issue-like text was found, but no findings were extracted. Manual review is required.",
            diagnostics,
        )

    warnings = diagnostics["quality_warnings"]
    if extraction.findings and diagnostics["source_quote_coverage"] < 0.6:
        warnings.append("Less than 60% of findings include source quotes.")
    if diagnostics["missing_location_count"]:
        warnings.append(f"{diagnostics['missing_location_count']} finding(s) are missing a specific location.")
    if diagnostics["low_confidence_count"]:
        warnings.append(f"{diagnostics['low_confidence_count']} finding(s) have confidence below 0.5.")
    if diagnostics["duplicate_finding_count"]:
        warnings.append(f"{diagnostics['duplicate_finding_count']} duplicate or near-duplicate finding(s) were detected.")
    if extraction.findings and diagnostics["general_trade_count"] / max(1, len(extraction.findings)) > 0.6:
        warnings.append("More than 60% of findings use the generic General trade.")

    diagnostics["quality_gate_passed"] = True
    return diagnostics


def _issue_keyword_hits(text: str) -> int:
    lowered = text.lower()
    return sum(len(re.findall(rf"\b{re.escape(token)}\b", lowered)) for token in ISSUE_HINTS)


def _source_quote_coverage(extraction: ExtractionResult) -> float:
    if not extraction.findings:
        return 0.0
    with_quote = sum(1 for finding in extraction.findings if (finding.source_quote or "").strip())
    return round(with_quote / len(extraction.findings), 3)


def _missing_location_count(extraction: ExtractionResult) -> int:
    return sum(1 for finding in extraction.findings if not finding.location and not finding.unit_label and not finding.unit_or_area)


def _low_confidence_count(extraction: ExtractionResult) -> int:
    return sum(1 for finding in extraction.findings if finding.confidence < 0.5)


def _general_trade_count(extraction: ExtractionResult) -> int:
    return sum(1 for finding in extraction.findings if not finding.trade or finding.trade == "General")


def _duplicate_finding_count(extraction: ExtractionResult) -> int:
    keys = [_dedupe_key(finding.title, finding.location or finding.unit_label or "") for finding in extraction.findings]
    counts = Counter(key for key in keys if key)
    return sum(count - 1 for count in counts.values() if count > 1)


def _dedupe_key(title: str, location: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", f"{title} {location}".lower())
    tokens = [token for token in text.split() if token not in {"the", "a", "an", "is", "are", "to", "for", "and"}]
    return " ".join(tokens[:12])
