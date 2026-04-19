from __future__ import annotations

import re
import tempfile
from collections import Counter
from pathlib import Path

import fitz

from ..config import Settings
from ..demo_extractions import fallback_demo_extraction, match_demo_extraction
from ..models import ExtractedFinding, ExtractionResult, PredictedInspection
from ..text_extraction import extract_embedded_text
from ..utils import parse_report_date, plus_days
from .base import ExtractionArtifacts, ExtractionRequest

_DOCTR_PREDICTOR = None

ISSUE_PATTERNS = (
    "fail",
    "failed",
    "missing",
    "incomplete",
    "incorrect",
    "non-compliant",
    "not compliant",
    "not installed",
    "not acceptable",
    "outstanding",
    "pending",
    "loose",
    "clash",
    "clashes",
    "compressed",
    "crushed",
    "squeezed",
    "requires",
    "required",
)

NOISE_PATTERNS = (
    "inspection details",
    "inspection type code",
    "page ",
    "project name",
    "consultants advice",
    "item description",
    "date of inspection",
    "report date",
    "building name",
)

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
ADDRESS_PATTERN = re.compile(
    r"\b\d+\s+[A-Za-z0-9 .,'/-]+(?:Road|Rd|Street|St|Avenue|Ave|Drive|Dr|Lane|Ln)[,;]?\s*[A-Za-z .'-]+[,;]?\s*Auckland\s+\d{4}\b",
    flags=re.IGNORECASE,
)


class DoctrRulesPresidioExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract(self, request: ExtractionRequest, pdf_path: Path) -> ExtractionArtifacts:
        # Fast-path: many PDFs contain embedded text already. docTR OCR is expensive, so only
        # run OCR when the embedded text is too sparse to be useful.
        embedded_text = extract_embedded_text(pdf_path)
        raw_text = embedded_text
        if len(embedded_text.strip()) < 400:
            try:
                raw_text = _extract_text_with_doctr(pdf_path, max_pages=self.settings.package_max_pages)
            except RuntimeError:
                raw_text = embedded_text

        extraction = _build_rule_extraction(request, raw_text)
        if not extraction.findings:
            demo_match = match_demo_extraction(request.filename, raw_text)
            if demo_match:
                extraction = demo_match
            else:
                extraction = fallback_demo_extraction(request.filename, raw_text)

        return ExtractionArtifacts(
            extraction=extraction,
            raw_text=_redact_text(raw_text),
            extractor_name=f"package:{self.settings.package_extractor}",
        )


def _extract_text_with_doctr(pdf_path: Path, *, max_pages: int = 12) -> str:
    global _DOCTR_PREDICTOR

    try:
        from doctr.io import DocumentFile
        from doctr.models import ocr_predictor
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "python-doctr is not installed. Install the package dependencies before using SOTERRA_EXTRACTOR_MODE=package."
        ) from exc

    if _DOCTR_PREDICTOR is None:
        _DOCTR_PREDICTOR = ocr_predictor(pretrained=True)

    with tempfile.TemporaryDirectory(prefix="soterra-doctr-") as tmp_dir_name:
        page_paths = _render_pdf_pages(pdf_path, Path(tmp_dir_name), max_pages=max_pages)
        document = DocumentFile.from_images([str(path) for path in page_paths])
        result = _DOCTR_PREDICTOR(document)
        return result.render().strip()


def _render_pdf_pages(pdf_path: Path, target_dir: Path, *, max_pages: int = 12) -> list[Path]:
    document = fitz.open(pdf_path)
    page_paths: list[Path] = []

    for index in range(min(document.page_count, max_pages)):
        page = document[index]
        # Lower DPI keeps OCR latency reasonable on CPU for large uploads.
        pixmap = page.get_pixmap(dpi=120, alpha=False)
        output = target_dir / f"page-{index + 1:03d}.png"
        pixmap.save(output)
        page_paths.append(output)

    return page_paths


def _build_rule_extraction(request: ExtractionRequest, raw_text: str) -> ExtractionResult:
    normalized = _normalize_text(raw_text)
    project_name = _extract_project_name(normalized) or request.project_name
    report_date = _extract_report_date(normalized)
    inspection_type = _extract_inspection_type(normalized, request.filename)
    trade = _extract_trade(normalized, inspection_type, request.trade)
    inspector = _extract_inspector(normalized, inspection_type)
    address = _extract_address(normalized) or request.address
    site_name = _extract_site_name(normalized, address, request.site_name, inspection_type)
    units = _extract_units(normalized)
    findings = _extract_findings(normalized, trade)
    predicted_inspections = _build_predicted_inspections(report_date, inspection_type, site_name, findings)
    summary = _build_summary(inspection_type, site_name, findings)

    return ExtractionResult(
        project_name=project_name,
        site_name=site_name,
        address=address,
        inspection_type=inspection_type,
        trade=trade,
        inspector=inspector,
        report_date=report_date,
        summary=summary,
        units=units,
        findings=findings,
        predicted_inspections=predicted_inspections,
    )


def _normalize_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\r", "")).strip()


def _extract_project_name(text: str) -> str | None:
    patterns = [
        r"Building\s*name\s*[:\-]?\s*([^\n|]+)",
        r"PROJECT NAME\s*([^\n|]+)",
        r"Subject:\s*([^\n]+?)\s*-\s*Site Inspection",
    ]
    for pattern in patterns:
        value = _search(text, pattern)
        if value:
            cleaned = value.strip(" :|-")
            if len(cleaned) >= 2:
                return cleaned
    return None


def _extract_report_date(text: str) -> str:
    patterns = [
        r"Date of Inspection\s*([0-9./-]+)",
        r"\bDate:\s*([0-9A-Za-z ./-]+)",
        r"\bDATE\s*([0-9./-]+)",
        r"\b(\d{2}-\d{2}-\d{4})\b",
        r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b",
        r"\b(\d{2}/\d{2}/\d{2})\b",
        r"\b(\d{2}\.\d{2}\.\d{2})\b",
    ]
    for pattern in patterns:
        value = _search(text, pattern)
        if value:
            parsed = parse_report_date(value)
            if parsed:
                return parsed
    return parse_report_date(None)


def _extract_address(text: str) -> str | None:
    value = _search(text, ADDRESS_PATTERN)
    if value:
        return _clean_value(value)

    for line in text.splitlines():
        if "auckland" in line.lower() and any(token in line.lower() for token in ("road", "rd", "street", "st")):
            return _clean_value(line)
    return None


def _extract_inspection_type(text: str, filename: str) -> str:
    extracted = _search(text, r"Inspection Type Code\s*([^\n|]+)")
    lowered_filename = filename.lower()

    if "fire inspection" in lowered_filename:
        return "Fire Inspection"
    if "services inspection" in lowered_filename:
        return "Services Inspection"
    if "council inspection" in lowered_filename and extracted:
        return f"Council - {extracted}"
    if "council inspection" in lowered_filename:
        return "Council Inspection"
    if extracted:
        return extracted.strip(" :|-")
    return Path(filename).stem


def _extract_trade(text: str, inspection_type: str, provided_trade: str) -> str:
    combined = f"{inspection_type} {text}".lower()
    if inspection_type.lower().startswith("fire"):
        return "Passive Fire"
    if any(token in combined for token in ("cavity", "wrap", "membrane", "flashing", "balcony")):
        return "Envelope"
    if any(token in combined for token in ("mechanical", "duct", "hydraulic", "plumbing", "services")):
        return "Mechanical"
    if "passive fire" in combined or inspection_type.lower().startswith("fire"):
        return "Passive Fire"
    return provided_trade or "General"


def _extract_inspector(text: str, inspection_type: str) -> str:
    patterns = [
        r"Inspector'?s name\s*([A-Za-z ]+)",
        r"From:\s*([A-Za-z ]+)",
    ]
    for pattern in patterns:
        value = _search(text, pattern)
        if value and len(value.strip()) >= 2:
            return value.strip(" :|-")
    if "Fire" in inspection_type:
        return "Fire Consultant"
    if "Services" in inspection_type:
        return "Services Consultant"
    if "Council" in inspection_type:
        return "Council Inspector"
    return "Soterra Package Extractor"


def _extract_site_name(text: str, address: str | None, provided_site: str, inspection_type: str) -> str:
    if address:
        return _clean_value(address.replace(", Auckland 0614", "").replace(", Auckland", ""))

    patterns = [
        r"We conducted a site inspection .*? to the following area;\s*([^\n]+)",
        r"Subject:\s*([^\n]+?)\s*-\s*Site Inspection",
        r"Building name\s*[:\-]?\s*([^\n|]+)",
    ]
    for pattern in patterns:
        value = _search(text, pattern)
        if value:
            cleaned = value.strip(" :|-")
            if len(cleaned) >= 2:
                return cleaned

    if provided_site:
        return _clean_value(provided_site)
    return inspection_type


def _extract_units(text: str) -> list[str]:
    units = sorted(set(re.findall(r"\b\d+/\d+\b", text)))
    return units[:20]


def _extract_findings(text: str, default_trade: str) -> list[ExtractedFinding]:
    findings: list[ExtractedFinding] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip(" -*\t")
        if len(line) < 10:
            continue

        lowered = line.lower()
        if any(noise in lowered for noise in NOISE_PATTERNS):
            continue
        if not any(token in lowered for token in ISSUE_PATTERNS):
            continue

        title = _build_finding_title(line)
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)

        findings.append(
            ExtractedFinding(
                title=title,
                description=line,
                severity=_classify_severity(line),  # type: ignore[arg-type]
                category=_classify_category(line),
                trade=_classify_trade(line, default_trade),
                location=_classify_location(line),
                unit_label=_classify_unit(line),
                recurrence_risk=_score_recurrence(line),
            )
        )

    return findings[:20]


def _build_finding_title(line: str) -> str:
    title = re.sub(r"^[0-9]+[).:-]?\s*", "", line).strip()
    title = title.rstrip(".")
    return title[:240]


def _classify_severity(line: str) -> str:
    lowered = line.lower()
    critical_tokens = ("critical", "not compliant", "non-compliant", "fire", "crushed", "compressed")
    high_tokens = ("fail", "failed", "missing", "incorrect", "not installed", "outstanding", "requires")
    medium_tokens = ("pending", "loose", "needs", "verify")

    if any(token in lowered for token in critical_tokens):
        return "Critical"
    if any(token in lowered for token in high_tokens):
        return "High"
    if any(token in lowered for token in medium_tokens):
        return "Medium"
    return "Low"


def _classify_category(line: str) -> str:
    lowered = line.lower()
    if any(token in lowered for token in ("fire", "damper", "fire stop", "fire-collar")):
        return "Passive Fire"
    if any(token in lowered for token in ("duct", "mechanical", "flexi")):
        return "Mechanical Ducting"
    if any(token in lowered for token in ("pipe", "hydraulic", "plumbing", "water")):
        return "Plumbing"
    if any(token in lowered for token in ("membrane", "flashing", "wrap", "cavity", "balcony")):
        return "Envelope"
    if any(token in lowered for token in ("photo", "close-out", "verify", "document")):
        return "Documentation"
    return "General"


def _classify_trade(line: str, default_trade: str) -> str:
    category = _classify_category(line)
    if category == "Passive Fire":
        return "Passive Fire"
    if category == "Mechanical Ducting":
        return "Mechanical"
    if category == "Plumbing":
        return "Plumbing"
    if category == "Envelope":
        return "Envelope"
    if default_trade:
        return default_trade
    return "General"


def _classify_location(line: str) -> str | None:
    lowered = line.lower()
    if "level 3" in lowered:
        return "Level 3"
    if "level 2" in lowered:
        return "Level 2"
    if "ground level corridor" in lowered:
        return "Ground level corridor"
    return None


def _classify_unit(line: str) -> str | None:
    match = re.search(r"\b\d+/\d+\b", line)
    return match.group(0) if match else None


def _score_recurrence(line: str) -> int:
    lowered = line.lower()
    score = 35
    if any(token in lowered for token in ("recurring", "repeat", "again")):
        score += 25
    if any(token in lowered for token in ("fail", "failed", "missing", "incorrect")):
        score += 20
    if any(token in lowered for token in ("critical", "non-compliant", "fire")):
        score += 20
    return min(score, 95)


def _build_predicted_inspections(
    report_date: str,
    inspection_type: str,
    site_name: str,
    findings: list[ExtractedFinding],
) -> list[PredictedInspection]:
    if not findings:
        return []

    severe_count = len([item for item in findings if item.severity in {"High", "Critical"}])
    risk_level = "High" if severe_count >= 3 else "Medium" if severe_count >= 1 else "Low"
    source = f"{severe_count} open finding(s) indicate follow-up work is likely."

    predictions = [
        PredictedInspection(
            inspection_type=_follow_up_type(inspection_type),
            site_name=site_name,
            expected_date=plus_days(report_date, 7 if risk_level == "High" else 14),
            risk_level=risk_level,  # type: ignore[arg-type]
            source=source,
        )
    ]

    categories = Counter(item.category for item in findings)
    if categories.get("Passive Fire"):
        predictions.append(
            PredictedInspection(
                inspection_type="Fire Interface Check",
                site_name=site_name,
                expected_date=plus_days(report_date, 21),
                risk_level="Medium",
                source="Passive fire findings suggest another interface review may be needed.",
            )
        )

    return predictions[:2]


def _follow_up_type(inspection_type: str) -> str:
    if "Reinspection" in inspection_type or "Recheck" in inspection_type:
        return inspection_type
    if "Council" in inspection_type:
        return "Council Recheck"
    if "Fire" in inspection_type:
        return "Fire Reinspection"
    if "Services" in inspection_type:
        return "Services Reinspection"
    return f"{inspection_type} Reinspection"


def _build_summary(inspection_type: str, site_name: str, findings: list[ExtractedFinding]) -> str:
    if not findings:
        return f"{inspection_type} for {site_name}. The document needs manual review because no actionable findings were parsed."

    highest = max(findings, key=lambda item: item.recurrence_risk)
    return (
        f"{inspection_type} for {site_name}. Parsed {len(findings)} actionable issue(s); "
        f"the strongest repeat-risk item is '{highest.title}'."
    )

def _search(text: str, pattern: str | re.Pattern[str]) -> str | None:
    if isinstance(pattern, re.Pattern):
        match = pattern.search(text)
    else:
        match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    value = next((item for item in match.groups() if item), match.group(0))
    return value.strip()


def _clean_value(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" :|-")
    cleaned = re.sub(r"^\d{4}\s+", "", cleaned)
    return cleaned


def _detect_pii(text: str) -> list[RecognizerResult]:
    try:
        from presidio_analyzer import RecognizerResult
    except ModuleNotFoundError:
        return []

    findings: list[RecognizerResult] = []

    for match in EMAIL_PATTERN.finditer(text):
        findings.append(
            RecognizerResult(
                entity_type="EMAIL_ADDRESS",
                start=match.start(),
                end=match.end(),
                score=0.9,
            )
        )

    for match in ADDRESS_PATTERN.finditer(text):
        findings.append(
            RecognizerResult(
                entity_type="ADDRESS",
                start=match.start(),
                end=match.end(),
                score=0.85,
            )
        )

    return findings


def _redact_text(text: str) -> str:
    pii_results = _detect_pii(text)
    if not pii_results:
        return text

    try:
        from presidio_anonymizer import AnonymizerEngine
        from presidio_anonymizer.entities import OperatorConfig
    except ModuleNotFoundError:
        return text

    anonymized = AnonymizerEngine().anonymize(
        text=text,
        analyzer_results=pii_results,
        operators={"DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED]"})},
    )
    return anonymized.text
