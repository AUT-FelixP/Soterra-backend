from __future__ import annotations

from dataclasses import dataclass, field

from ...models import ExtractedFinding, ExtractionResult


ISSUE_WORDS = (
    "fail",
    "failed",
    "defect",
    "required",
    "requires",
    "non-compliant",
    "not compliant",
    "missing",
    "not installed",
    "below minimum",
    "incorrect",
    "incomplete",
    "outstanding",
)

VAGUE_PHRASES = (
    "issue needs attention",
    "needs attention",
    "fix required",
    "rectify issue",
    "something wrong",
    "defect found",
)


@dataclass(frozen=True)
class ExtractionQualityResult:
    score: float
    should_fallback: bool
    reason: str
    warnings: list[str] = field(default_factory=list)


def score_extraction_quality(extraction: ExtractionResult, *, raw_text: str = "") -> ExtractionQualityResult:
    warnings: list[str] = []
    findings = extraction.findings
    score = 1.0

    if len(extraction.summary.strip()) < 20:
        warnings.append("Report summary is too short to be useful.")
        score -= 0.15

    raw_has_issue_language = any(token in raw_text.lower() for token in ISSUE_WORDS)
    if not findings:
        if raw_has_issue_language:
            return ExtractionQualityResult(
                score=0.0,
                should_fallback=True,
                reason="raw_text_contains_issue_language_but_model_returned_no_findings",
                warnings=warnings,
            )
        return ExtractionQualityResult(score=max(0.0, score - 0.1), should_fallback=False, reason="no_findings", warnings=warnings)

    missing_fix = _ratio(findings, lambda item: not _meaningful(item.required_fix))
    low_confidence = _ratio(findings, lambda item: item.confidence < 0.55)
    vague = _ratio(findings, _is_vague_finding)
    duplicate = _duplicate_ratio(findings)

    if missing_fix > 0:
        warnings.append(f"{missing_fix:.0%} of findings are missing required fixes.")
        score -= min(0.3, missing_fix * 0.4)
    if low_confidence > 0:
        warnings.append(f"{low_confidence:.0%} of findings have confidence below 0.55.")
        score -= min(0.25, low_confidence * 0.35)
    if vague > 0:
        warnings.append(f"{vague:.0%} of findings are vague.")
        score -= min(0.3, vague * 0.45)
    if duplicate > 0:
        warnings.append(f"{duplicate:.0%} of findings appear duplicated.")
        score -= min(0.2, duplicate * 0.3)

    for finding in findings:
        if not _meaningful(finding.plain_english_summary):
            warnings.append(f"Finding '{finding.title}' is missing a plain English summary.")
            score -= 0.05
        if not finding.evidence_required:
            warnings.append(f"Finding '{finding.title}' is missing evidence required.")
            score -= 0.05
        if not _meaningful(finding.source_quote):
            warnings.append(f"Finding '{finding.title}' is missing a source quote.")
            score -= 0.04
        if not finding.location and not finding.unit_label and not finding.unit_or_area:
            warnings.append(f"Finding '{finding.title}' is missing a location.")
            score -= 0.02

    should_fallback = missing_fix > 0.4 or low_confidence > 0.4 or vague > 0.4 or duplicate > 0.5
    reason = "quality_passed"
    if should_fallback:
        reason = "quality_threshold_failed"

    return ExtractionQualityResult(
        score=max(0.0, min(1.0, score)),
        should_fallback=should_fallback,
        reason=reason,
        warnings=list(dict.fromkeys(warnings)),
    )


def dedupe_findings(extraction: ExtractionResult) -> ExtractionResult:
    seen: set[str] = set()
    deduped: list[ExtractedFinding] = []
    for finding in extraction.findings:
        key = _dedupe_key(finding)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return extraction.model_copy(update={"findings": deduped})


def _meaningful(value: str | None) -> bool:
    return bool(value and len(value.strip()) >= 8)


def _ratio(findings: list[ExtractedFinding], predicate) -> float:
    if not findings:
        return 0.0
    return len([item for item in findings if predicate(item)]) / len(findings)


def _is_vague_finding(finding: ExtractedFinding) -> bool:
    text = " ".join(
        item or ""
        for item in [
            finding.title,
            finding.description,
            finding.plain_english_summary,
            finding.required_fix,
        ]
    ).lower()
    return any(phrase in text for phrase in VAGUE_PHRASES)


def _duplicate_ratio(findings: list[ExtractedFinding]) -> float:
    if not findings:
        return 0.0
    keys = [_dedupe_key(item) for item in findings]
    return max(0, len(keys) - len(set(keys))) / len(keys)


def _dedupe_key(finding: ExtractedFinding) -> str:
    location = finding.location or finding.unit_label or finding.unit_or_area or ""
    return f"{finding.title.strip().lower()}|{location.strip().lower()}"
