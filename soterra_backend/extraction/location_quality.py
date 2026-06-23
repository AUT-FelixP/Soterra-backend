from __future__ import annotations

import re

from ..models import ExtractedFinding, IssueLocation, IssueQuality

MANUAL_CONFIRMATION_WARNING = "Exact issue location needs manual confirmation."
_BROAD_LOCATIONS = {"site", "project", "building", "all", "general", "various", "throughout", "project-wide"}


def is_exact_location(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text or text.lower() in _BROAD_LOCATIONS:
        return False
    return len(text) >= 4 and not re.fullmatch(r"(?:level|floor|building|block)\s*\w*", text, re.I)


def enrich_location_quality(finding: ExtractedFinding) -> ExtractedFinding:
    current = finding.issue_location
    exact = current.exact_location_text or finding.location or finding.unit_or_area or finding.unit_label
    warnings = list(dict.fromkeys([*finding.extraction_warnings, *current.warnings]))
    if not is_exact_location(exact) and MANUAL_CONFIRMATION_WARNING not in warnings:
        warnings.append(MANUAL_CONFIRMATION_WARNING)
    location = current.model_copy(update={
        "project_name": current.project_name or finding.project_name,
        "level": current.level or finding.level,
        "unit_label": current.unit_label or finding.unit_label,
        "area": current.area or finding.unit_or_area,
        "exact_location_text": exact,
        "source_page": current.source_page or finding.source_page,
        "source_quote": current.source_quote or finding.source_quote,
        "confidence": current.confidence if current.confidence != 0.5 else finding.confidence,
        "warnings": warnings,
    })
    quality = IssueQuality(
        has_source_quote=bool(finding.source_quote or location.source_quote),
        has_exact_location=is_exact_location(exact),
        has_required_fix=bool(finding.required_fix),
        has_evidence_required=bool(finding.evidence_required),
        confidence=finding.confidence,
        warnings=warnings,
    )
    return finding.model_copy(update={"location": exact, "issue_location": location, "quality": quality, "extraction_warnings": warnings})


def score_location_quality(finding: ExtractedFinding) -> int:
    quality = enrich_location_quality(finding).quality
    score = 0
    score += 30 if quality.has_exact_location else 0
    score += 15 if quality.has_source_quote else 0
    score += 10 if finding.source_page else 0
    score += 15 if quality.has_required_fix else 0
    score += 10 if quality.has_evidence_required else 0
    score += round(20 * quality.confidence)
    return min(score, 100)
