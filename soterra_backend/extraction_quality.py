from __future__ import annotations

from .models import ExtractedFinding, ExtractionResult


def finalize_extraction(extraction: ExtractionResult, source_document: str) -> ExtractionResult:
    """Apply one evidence and action policy to findings from every extractor."""
    findings = [
        _finalize_finding(
            finding,
            project_name=extraction.project_name,
            inspection_type=extraction.inspection_type,
            source_document=source_document,
        )
        for finding in extraction.findings
    ]
    return extraction.model_copy(update={"findings": findings})


def _finalize_finding(
    finding: ExtractedFinding,
    *,
    project_name: str,
    inspection_type: str,
    source_document: str,
) -> ExtractedFinding:
    warnings = list(finding.extraction_warnings)
    confidence = finding.confidence
    if not finding.location and not finding.unit_label:
        warnings.append("Exact location was not stated in the source text.")
        confidence = min(confidence, 0.65)
    if finding.trade in {"", "General"}:
        warnings.append("Responsible trade needs manual confirmation.")
        confidence = min(confidence, 0.65)
    if not finding.source_quote:
        warnings.append("Source quote was derived from the extracted issue text.")
        confidence = min(confidence, 0.75)
    required_fix = finding.required_fix
    if required_fix and _is_vague_action(required_fix):
        warnings.append("Generic required fix was replaced with a construction-specific close-out action.")
        required_fix = None

    return finding.model_copy(
        update={
            "project_name": finding.project_name or project_name,
            "issue_title": finding.issue_title or finding.title,
            "plain_english_summary": finding.plain_english_summary or finding.description,
            "unit_or_area": finding.unit_or_area or finding.unit_label or finding.location,
            "inspection_type": finding.inspection_type or inspection_type,
            "root_cause": finding.root_cause or _root_cause(finding),
            "required_fix": required_fix or recommended_fix(finding.title),
            "evidence_required": finding.evidence_required or evidence_required(finding.title),
            "source_document": finding.source_document or source_document,
            "source_quote": finding.source_quote or finding.description,
            "confidence": confidence,
            "extraction_warnings": list(dict.fromkeys(warnings)),
        }
    )


def _is_vague_action(action: str) -> bool:
    normalized = action.lower()
    return "assign the responsible trade" in normalized and "upload close-out evidence" in normalized


def recommended_fix(title: str) -> str:
    text = title.lower()
    if "flash" in text:
        return "Rectify flashing installation against the approved details and upload after photos before reinspection."
    if "membrane" in text or "upstand" in text:
        return "Correct the membrane upstand to the required detail and upload measured after photos."
    if "damper" in text or "breakaway" in text:
        return "Install compliant fire damper fixings and upload close-out photos with trade sign-off."
    if "penetration" in text or "fire stop" in text or "collar" in text:
        return "Complete the passive fire treatment to the approved system and upload labelled close-out photos."
    if "duct" in text or "cabling" in text or "clearance" in text:
        return "Re-coordinate the services route, restore the required clearance, and upload QA photos."
    if "lagging" in text:
        return "Install the specified acoustic lagging and upload after photos."
    return "Rectify the recorded defect against the approved detail and upload after photos for review."


def evidence_required(title: str) -> list[str]:
    text = title.lower()
    evidence = ["after photos"]
    if any(token in text for token in ("fire", "damper", "penetration", "collar")):
        evidence.extend(["trade sign-off", "fire system close-out evidence"])
    elif any(token in text for token in ("flash", "membrane", "upstand", "cavity")):
        evidence.extend(["trade sign-off", "reinspection close-out evidence"])
    else:
        evidence.append("trade QA sign-off")
    return evidence


def _root_cause(finding: ExtractedFinding) -> str:
    text = f"{finding.title} {finding.category}".lower()
    if any(token in text for token in ("flash", "membrane", "cavity", "upstand")):
        return "Weather-tightness detail installation"
    if any(token in text for token in ("fire", "damper", "penetration", "collar")):
        return "Passive fire close-out"
    if any(token in text for token in ("duct", "cabling", "clearance", "service")):
        return "Services coordination"
    return finding.category or "General defect"
