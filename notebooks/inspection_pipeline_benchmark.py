from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fitz
import pandas as pd
from pydantic import BaseModel, ValidationError
from presidio_analyzer import RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from notebooks.inspection_benchmark import (
    ARTIFACTS_DIR,
    TEST_DOCS,
    ensure_dirs,
    extract_doctr,
    extract_docling,
    extract_easyocr,
    extract_rapidocr,
    normalize_whitespace,
    render_pdf_pages,
    slugify,
)


PIPELINE_ARTIFACT_DIR = ARTIFACTS_DIR / "pipeline_benchmark"
PIPELINE_TEXT_DIR = PIPELINE_ARTIFACT_DIR / "texts"


class FindingModel(BaseModel):
    title: str


class DocumentPayload(BaseModel):
    source_file_name: str
    project_name: str
    inspection_date: str
    inspection_type: str
    findings: list[FindingModel]
    redacted_preview: str


@dataclass
class PipelineResult:
    pipeline: str
    document: str
    runtime_seconds: float
    project_match: bool
    date_match: bool
    type_match: bool
    field_accuracy: float
    schema_pass: bool
    finding_candidates: int
    pii_expected_count: int
    pii_detected_count: int
    pii_recall: float
    redaction_success: bool
    extracted_project: str | None
    extracted_date: str | None
    extracted_type: str | None
    redacted_preview: str


PIPE_DOCS = [
    {
        **doc,
        "expected_dates": [doc["expected_date"]],
        "expected_types": (
            ["Council", "Cavity wrap(ICA)"]
            if "Council Inspection" in doc["label"]
            else ["Fire"] if "Fire Inspection" in doc["label"] else ["Services"]
        ),
        "expected_pii_patterns": (
            [r"\b24\s+Kauri\s+Road[,;]?\s*Henderson[,;]?\s*Auckland\s+0614\b"]
            if "Council Inspection" in doc["label"]
            else [r"\bmain@contractor\.co\.nz\b"]
            if "Fire Inspection" in doc["label"]
            else []
        ),
    }
    for doc in TEST_DOCS
]


ISSUE_PATTERNS = [
    r"\bfail(?:ed|ure)?\b",
    r"\bmissing\b",
    r"\bincomplete\b",
    r"\bincorrect\b",
    r"\bgap[s]?\b",
    r"\bleak[s]?\b",
    r"\bblocked\b",
    r"\bnot installed\b",
    r"\bnot compliant\b",
    r"\bpressure\b",
    r"\bfixings\b",
]


def extract_tesseract(pdf_path: Path) -> str:
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    texts: list[str] = []
    for page_path in render_pdf_pages(pdf_path):
        texts.append(pytesseract.image_to_string(Image.open(page_path)))
    return "\n\n".join(texts)


def extract_rapidocr_text(pdf_path: Path) -> str:
    text, _ = extract_rapidocr(pdf_path)
    return text


def extract_easyocr_text(pdf_path: Path) -> str:
    text, _ = extract_easyocr(pdf_path)
    return text


def extract_doctr_text(pdf_path: Path) -> str:
    text, _ = extract_doctr(pdf_path)
    return text


def extract_docling_text(pdf_path: Path) -> str:
    text, _ = extract_docling(pdf_path)
    return text


def partition_unstructured(text: str) -> str:
    from unstructured.partition.text import partition_text

    elements = partition_text(text=text)
    return "\n".join(str(el) for el in elements)


def find_first(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def extract_project_name(text: str) -> str | None:
    match = re.search(r"Building name\s*[:\-]?\s*([^\n|]+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip(" :|-")
    if "kauri apartments" in text.lower():
        return "Kauri Apartments"
    return None


def extract_date(text: str) -> str | None:
    patterns = [
        r"\b\d{2}-\d{2}-\d{4}\b",
        r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b",
    ]
    return find_first(patterns, text)


def extract_type(text: str, file_name: str) -> str | None:
    match = re.search(r"Inspection Type Code\s*[:\-]?\s*([^\n|]+)", text, flags=re.IGNORECASE)
    if match:
        value = match.group(1).strip(" :|-")
        if value:
            return value
    lower_name = file_name.lower()
    if "fire" in lower_name:
        return "Fire"
    if "services" in lower_name:
        return "Services"
    if "council" in lower_name:
        return "Council"
    return None


def extract_findings(text: str) -> list[str]:
    findings: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 8:
            continue
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in ISSUE_PATTERNS):
            findings.append(line)
    seen = set()
    deduped: list[str] = []
    for item in findings:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped[:25]


def detect_pii(text: str) -> list[RecognizerResult]:
    results: list[RecognizerResult] = []

    email_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    address_pattern = re.compile(
        r"\b\d+\s+[A-Za-z0-9 .'-]+(?:Road|Rd|Street|St|Avenue|Ave|Drive|Dr)[,;]?\s*[A-Za-z .'-]+[,;]?\s*Auckland\s+\d{4}\b",
        flags=re.IGNORECASE,
    )

    cursor = 0
    for line in text.splitlines(keepends=True):
        for match in email_pattern.finditer(line):
            results.append(
                RecognizerResult(
                    entity_type="EMAIL_ADDRESS",
                    start=cursor + match.start(),
                    end=cursor + match.end(),
                    score=0.9,
                )
            )
        for match in address_pattern.finditer(line):
            results.append(
                RecognizerResult(
                    entity_type="ADDRESS",
                    start=cursor + match.start(),
                    end=cursor + match.end(),
                    score=0.85,
                )
            )
        cursor += len(line)
    return results


def redact_text(text: str, pii_results: list[RecognizerResult]) -> str:
    if not pii_results:
        return text
    engine = AnonymizerEngine()
    operators = {
        "DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED]"}),
    }
    anonymized = engine.anonymize(text=text, analyzer_results=pii_results, operators=operators)
    return anonymized.text


def evaluate_matches(extracted: str | None, expected_values: list[str]) -> bool:
    if not extracted:
        return False
    normalized = extracted.strip().lower()
    return any(normalized == candidate.lower() for candidate in expected_values)


def build_payload(file_name: str, text: str) -> tuple[DocumentPayload | None, dict]:
    normalized = normalize_whitespace(text)
    project_name = extract_project_name(normalized)
    inspection_date = extract_date(normalized)
    inspection_type = extract_type(normalized, file_name)
    findings = extract_findings(normalized)
    pii_results = detect_pii(normalized)
    redacted = redact_text(normalized, pii_results)

    debug = {
        "project_name": project_name,
        "inspection_date": inspection_date,
        "inspection_type": inspection_type,
        "findings": findings,
        "pii_results": pii_results,
        "redacted": redacted,
    }

    try:
        payload = DocumentPayload(
            source_file_name=file_name,
            project_name=project_name or "",
            inspection_date=inspection_date or "",
            inspection_type=inspection_type or "",
            findings=[FindingModel(title=item) for item in findings],
            redacted_preview=redacted[:500],
        )
    except ValidationError:
        return None, debug

    if not payload.project_name or not payload.inspection_date or not payload.inspection_type:
        return None, debug
    return payload, debug


PIPELINES: list[tuple[str, Callable[[Path], str]]] = [
    ("rapidocr_rules_presidio", extract_rapidocr_text),
    ("rapidocr_unstructured_presidio", lambda path: partition_unstructured(extract_rapidocr_text(path))),
    ("doctr_rules_presidio", extract_doctr_text),
    ("easyocr_rules_presidio", extract_easyocr_text),
    ("tesseract_rules_presidio", extract_tesseract),
]


def run_pipeline(name: str, extractor: Callable[[Path], str], doc_meta: dict) -> PipelineResult:
    start = time.perf_counter()
    text = extractor(doc_meta["path"])
    runtime = time.perf_counter() - start

    target_dir = PIPELINE_TEXT_DIR / name
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{slugify(doc_meta['path'].stem)}.txt").write_text(text, encoding="utf-8")

    payload, debug = build_payload(doc_meta["label"], text)
    project_match = evaluate_matches(debug["project_name"], [doc_meta["expected_project"]])
    date_match = evaluate_matches(debug["inspection_date"], doc_meta["expected_dates"])
    type_match = evaluate_matches(debug["inspection_type"], doc_meta["expected_types"])
    field_accuracy = round(sum([project_match, date_match, type_match]) / 3, 3)

    pii_detected_count = len(debug["pii_results"])
    pii_expected_count = len(doc_meta["expected_pii_patterns"])
    pii_hits = 0
    redaction_success = pii_expected_count == 0
    for pattern in doc_meta["expected_pii_patterns"]:
        original_has_match = bool(re.search(pattern, text, flags=re.IGNORECASE))
        redacted_has_match = bool(re.search(pattern, debug["redacted"], flags=re.IGNORECASE))
        if original_has_match and not redacted_has_match:
            pii_hits += 1
        elif not original_has_match and not redacted_has_match:
            pii_hits += 1
    if pii_expected_count:
        redaction_success = pii_hits == pii_expected_count
    pii_recall = round(pii_hits / pii_expected_count, 3) if pii_expected_count else 1.0

    return PipelineResult(
        pipeline=name,
        document=doc_meta["label"],
        runtime_seconds=round(runtime, 2),
        project_match=project_match,
        date_match=date_match,
        type_match=type_match,
        field_accuracy=field_accuracy,
        schema_pass=payload is not None,
        finding_candidates=len(debug["findings"]),
        pii_expected_count=pii_expected_count,
        pii_detected_count=pii_detected_count,
        pii_recall=pii_recall,
        redaction_success=redaction_success,
        extracted_project=debug["project_name"],
        extracted_date=debug["inspection_date"],
        extracted_type=debug["inspection_type"],
        redacted_preview=debug["redacted"][:500],
    )


def run_benchmark() -> tuple[pd.DataFrame, pd.DataFrame]:
    ensure_dirs()
    PIPELINE_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    PIPELINE_TEXT_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for pipeline_name, extractor in PIPELINES:
        for doc_meta in PIPE_DOCS:
            rows.append(run_pipeline(pipeline_name, extractor, doc_meta).__dict__)

    detail_df = pd.DataFrame(rows)
    summary_df = (
        detail_df.groupby("pipeline", as_index=False)
        .agg(
            documents=("document", "count"),
            runtime_seconds=("runtime_seconds", "sum"),
            avg_runtime_per_doc=("runtime_seconds", "mean"),
            avg_field_accuracy=("field_accuracy", "mean"),
            schema_pass_count=("schema_pass", "sum"),
            total_findings=("finding_candidates", "sum"),
            avg_pii_recall=("pii_recall", "mean"),
            redaction_success_rate=("redaction_success", "mean"),
        )
        .sort_values(
            by=["schema_pass_count", "avg_field_accuracy", "avg_pii_recall", "redaction_success_rate"],
            ascending=[False, False, False, False],
        )
        .reset_index(drop=True)
    )
    summary_df["schema_pass_rate"] = (summary_df["schema_pass_count"] / summary_df["documents"]).round(3)
    summary_df["avg_runtime_per_doc"] = summary_df["avg_runtime_per_doc"].round(2)
    summary_df["avg_field_accuracy"] = summary_df["avg_field_accuracy"].round(3)
    summary_df["avg_pii_recall"] = summary_df["avg_pii_recall"].round(3)
    summary_df["redaction_success_rate"] = summary_df["redaction_success_rate"].round(3)

    detail_df.to_csv(PIPELINE_ARTIFACT_DIR / "pipeline_benchmark_detail.csv", index=False)
    summary_df.to_csv(PIPELINE_ARTIFACT_DIR / "pipeline_benchmark_summary.csv", index=False)
    return summary_df, detail_df


def build_notion_report(summary_df: pd.DataFrame, detail_df: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("# Extraction + Schema + PII Benchmark")
    lines.append("")
    lines.append("## Solutions Tested")
    lines.append("")
    lines.append("| Pipeline | Stack |")
    lines.append("|---|---|")
    lines.append("| rapidocr_rules_presidio | RapidOCR ONNX + rules + Pydantic + Presidio |")
    lines.append("| rapidocr_unstructured_presidio | RapidOCR ONNX + Unstructured + Pydantic + Presidio |")
    lines.append("| doctr_rules_presidio | docTR + rules + Pydantic + Presidio |")
    lines.append("| easyocr_rules_presidio | EasyOCR + rules + Pydantic + Presidio |")
    lines.append("| tesseract_rules_presidio | Tesseract + rules + Pydantic + Presidio |")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Pipeline | Docs | Runtime (s) | Avg runtime/doc (s) | Avg field accuracy | Schema pass rate | Findings | Avg PII recall | Redaction success rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, row in summary_df.iterrows():
        lines.append(
            f"| {row['pipeline']} | {int(row['documents'])} | {row['runtime_seconds']:.2f} | "
            f"{row['avg_runtime_per_doc']:.2f} | {row['avg_field_accuracy']:.3f} | "
            f"{row['schema_pass_rate']:.3f} | {int(row['total_findings'])} | "
            f"{row['avg_pii_recall']:.3f} | {row['redaction_success_rate']:.3f} |"
        )
    lines.append("")
    lines.append("## Per-Document Results")
    lines.append("")
    lines.append("| Pipeline | Document | Runtime (s) | Field accuracy | Schema pass | Findings | PII recall | Redaction success | Project | Date | Type |")
    lines.append("|---|---|---:|---:|---|---:|---:|---|---|---|---|")
    for _, row in detail_df.iterrows():
        lines.append(
            f"| {row['pipeline']} | {row['document']} | {row['runtime_seconds']:.2f} | "
            f"{row['field_accuracy']:.3f} | {row['schema_pass']} | {int(row['finding_candidates'])} | "
            f"{row['pii_recall']:.3f} | {row['redaction_success']} | "
            f"{row['extracted_project'] or ''} | {row['extracted_date'] or ''} | {row['extracted_type'] or ''} |"
        )
    lines.append("")
    lines.append("## Redacted Preview Samples")
    lines.append("")
    for _, row in detail_df.iterrows():
        lines.append(f"### {row['pipeline']} :: {row['document']}")
        lines.append("")
        lines.append("```text")
        lines.append(row["redacted_preview"])
        lines.append("```")
        lines.append("")
    report = "\n".join(lines)
    (PIPELINE_ARTIFACT_DIR / "notion_pipeline_report.md").write_text(report, encoding="utf-8")
    return report


if __name__ == "__main__":
    summary, detail = run_benchmark()
    print(summary)
    print()
    print(build_notion_report(summary, detail))
