from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fitz
import pandas as pd
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts" / "ocr_benchmark"
PAGE_IMAGE_DIR = ARTIFACTS_DIR / "page_images"
OCR_OUTPUT_DIR = ARTIFACTS_DIR / "ocr_outputs"
_ENGINE_CACHE: dict[str, object] = {}


TEST_DOCS = [
    {
        "path": Path(r"C:\Projects\Soterra\Reports data\Council Inspection - Kauri Apartments 09-04-24.pdf"),
        "label": "Council Inspection - Kauri Apartments 09-04-24.pdf",
        "expected_project": "Kauri Apartments",
        "expected_date": "09-04-2024",
        "expected_tokens": ["kauri apartments", "inspection", "09-04-2024"],
    },
    {
        "path": Path(r"C:\Projects\Soterra\Reports data\Fire Inspection - 07 Kauri Apartments.pdf"),
        "label": "Fire Inspection - 07 Kauri Apartments.pdf",
        "expected_project": "Kauri Apartments",
        "expected_date": "07",
        "expected_tokens": ["kauri apartments", "fire", "inspection"],
    },
    {
        "path": Path(r"C:\Projects\Soterra\Reports data\Services Inspection – Kauri Apartments – 09 April 2024.pdf"),
        "label": "Services Inspection – Kauri Apartments – 09 April 2024.pdf",
        "expected_project": "Kauri Apartments",
        "expected_date": "09 April 2024",
        "expected_tokens": ["kauri apartments", "services", "inspection"],
    },
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
    r"\bdefect\b",
    r"\bpressure\b",
]

DATE_PATTERNS = [
    r"\b\d{2}-\d{2}-\d{4}\b",
    r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b",
]


class ExtractedDocument(BaseModel):
    source_file_name: str
    project_name: str | None = None
    inspection_date: str | None = None
    address: str | None = None
    inspection_type: str | None = None
    finding_candidates: list[str] = []


@dataclass
class EngineResult:
    engine: str
    document: str
    pages: int
    runtime_seconds: float
    text_chars: int
    text_lines: int
    non_empty_pages: int
    field_completion: float
    keyword_coverage: float
    finding_candidates: int
    schema_pass: bool
    project_name: str | None
    inspection_date: str | None
    address: str | None
    inspection_type: str | None
    preview: str


def ensure_dirs() -> None:
    for path in [ARTIFACTS_DIR, PAGE_IMAGE_DIR, OCR_OUTPUT_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def render_pdf_pages(pdf_path: Path, dpi: int = 140) -> list[Path]:
    ensure_dirs()
    target_dir = PAGE_IMAGE_DIR / slugify(pdf_path.stem)
    target_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(target_dir.glob("page-*.png"))
    if existing:
      return existing

    doc = fitz.open(pdf_path)
    paths: list[Path] = []
    for index, page in enumerate(doc):
        output = target_dir / f"page-{index + 1:03d}.png"
        pix = page.get_pixmap(dpi=dpi)
        pix.save(output)
        paths.append(output)
    return paths


def extract_native_text(pdf_path: Path) -> tuple[str, int]:
    doc = fitz.open(pdf_path)
    page_texts = []
    non_empty_pages = 0
    for page in doc:
        text = page.get_text("text").strip()
        if text:
            non_empty_pages += 1
        page_texts.append(text)
    return "\n\n".join(page_texts), non_empty_pages


def extract_rapidocr(pdf_path: Path) -> tuple[str, int]:
    from rapidocr_onnxruntime import RapidOCR

    engine = _ENGINE_CACHE.setdefault("rapidocr_onnx", RapidOCR())
    page_paths = render_pdf_pages(pdf_path)
    page_texts: list[str] = []
    non_empty_pages = 0
    for page_path in page_paths:
        result, _ = engine(str(page_path))
        lines = [item[1] for item in (result or [])]
        text = "\n".join(lines).strip()
        if text:
            non_empty_pages += 1
        page_texts.append(text)
    return "\n\n".join(page_texts), non_empty_pages


def extract_easyocr(pdf_path: Path) -> tuple[str, int]:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    import easyocr

    reader = _ENGINE_CACHE.setdefault(
        "easyocr", easyocr.Reader(["en"], gpu=False, verbose=False)
    )
    page_paths = render_pdf_pages(pdf_path)
    page_texts: list[str] = []
    non_empty_pages = 0
    for page_path in page_paths:
        result = reader.readtext(str(page_path), detail=0, paragraph=False)
        text = "\n".join(result).strip()
        if text:
            non_empty_pages += 1
        page_texts.append(text)
    return "\n\n".join(page_texts), non_empty_pages


def extract_doctr(pdf_path: Path) -> tuple[str, int]:
    from doctr.io import DocumentFile
    from doctr.models import ocr_predictor

    page_paths = render_pdf_pages(pdf_path)
    doc = DocumentFile.from_images([str(path) for path in page_paths])
    predictor = _ENGINE_CACHE.setdefault("doctr", ocr_predictor(pretrained=True))
    result = predictor(doc)
    page_texts = result.render().split("\n\n")
    non_empty_pages = sum(1 for text in page_texts if text.strip())
    return result.render(), non_empty_pages


def extract_docling(pdf_path: Path) -> tuple[str, int]:
    from docling.document_converter import DocumentConverter

    converter = _ENGINE_CACHE.setdefault("docling", DocumentConverter())
    result = converter.convert(str(pdf_path))
    text = result.document.export_to_markdown()
    non_empty_pages = text.count("| Checklist Item") or max(1, text.count("\n\n"))
    return text, non_empty_pages


ENGINE_FUNCS: list[tuple[str, Callable[[Path], tuple[str, int]]]] = [
    ("native_pymupdf", extract_native_text),
    ("rapidocr_onnx", extract_rapidocr),
    ("easyocr", extract_easyocr),
    ("doctr", extract_doctr),
    ("docling", extract_docling),
]


def normalize_whitespace(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).replace("\r", "")


def find_first(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def extract_project_name(text: str) -> str | None:
    match = re.search(r"Building name\s*\n?([^\n|]+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip(" :|-")
    for line in text.splitlines():
        if "kauri apartments" in line.lower():
            return "Kauri Apartments"
    return None


def extract_address(text: str) -> str | None:
    match = re.search(
        r"\d+\s+[A-Za-z0-9 ,.-]+Auckland\s+\d{4}",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(0).strip() if match else None


def extract_inspection_type(text: str, file_name: str) -> str | None:
    match = re.search(r"Inspection Type Code\s*\n?([^\n|]+)", text, flags=re.IGNORECASE)
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
    deduped: list[str] = []
    seen = set()
    for finding in findings:
        key = finding.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(finding)
    return deduped[:25]


def build_extracted_document(file_name: str, text: str) -> ExtractedDocument:
    text = normalize_whitespace(text)
    return ExtractedDocument(
        source_file_name=file_name,
        project_name=extract_project_name(text),
        inspection_date=find_first(DATE_PATTERNS, text),
        address=extract_address(text),
        inspection_type=extract_inspection_type(text, file_name),
        finding_candidates=extract_findings(text),
    )


def compute_keyword_coverage(text: str, expected_tokens: list[str]) -> float:
    lower_text = text.lower()
    hits = sum(1 for token in expected_tokens if token.lower() in lower_text)
    return round(hits / len(expected_tokens), 3) if expected_tokens else 0.0


def compute_field_completion(doc: ExtractedDocument) -> float:
    fields = [doc.project_name, doc.inspection_date, doc.address, doc.inspection_type]
    hits = sum(1 for item in fields if item)
    return round(hits / len(fields), 3)


def persist_text(engine_name: str, file_stem: str, text: str) -> None:
    target_dir = OCR_OUTPUT_DIR / engine_name
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{slugify(file_stem)}.txt").write_text(text, encoding="utf-8")


def run_engine_on_document(engine_name: str, func: Callable[[Path], tuple[str, int]], doc_meta: dict) -> EngineResult:
    pdf_path: Path = doc_meta["path"]
    doc = fitz.open(pdf_path)
    pages = doc.page_count
    start = time.perf_counter()
    text, non_empty_pages = func(pdf_path)
    runtime = time.perf_counter() - start
    persist_text(engine_name, pdf_path.stem, text)

    extracted = build_extracted_document(pdf_path.name, text)
    schema_pass = bool(extracted.project_name and extracted.inspection_date and extracted.inspection_type)
    preview_lines = [line.strip() for line in text.splitlines() if line.strip()][:6]

    return EngineResult(
        engine=engine_name,
        document=pdf_path.name,
        pages=pages,
        runtime_seconds=round(runtime, 2),
        text_chars=len(text),
        text_lines=len([line for line in text.splitlines() if line.strip()]),
        non_empty_pages=non_empty_pages,
        field_completion=compute_field_completion(extracted),
        keyword_coverage=compute_keyword_coverage(text, doc_meta["expected_tokens"]),
        finding_candidates=len(extracted.finding_candidates),
        schema_pass=schema_pass,
        project_name=extracted.project_name,
        inspection_date=extracted.inspection_date,
        address=extracted.address,
        inspection_type=extracted.inspection_type,
        preview=" | ".join(preview_lines)[:500],
    )


def run_benchmark() -> tuple[pd.DataFrame, pd.DataFrame]:
    ensure_dirs()
    rows: list[dict] = []
    for engine_name, func in ENGINE_FUNCS:
        for doc_meta in TEST_DOCS:
            result = run_engine_on_document(engine_name, func, doc_meta)
            rows.append(result.__dict__)

    detail_df = pd.DataFrame(rows)
    summary_df = (
        detail_df.groupby("engine", as_index=False)
        .agg(
            documents=("document", "count"),
            pages=("pages", "sum"),
            runtime_seconds=("runtime_seconds", "sum"),
            avg_runtime_per_doc=("runtime_seconds", "mean"),
            total_text_chars=("text_chars", "sum"),
            avg_field_completion=("field_completion", "mean"),
            avg_keyword_coverage=("keyword_coverage", "mean"),
            total_finding_candidates=("finding_candidates", "sum"),
            schema_pass_count=("schema_pass", "sum"),
        )
        .sort_values(
            by=["schema_pass_count", "avg_field_completion", "avg_keyword_coverage", "total_text_chars"],
            ascending=[False, False, False, False],
        )
        .reset_index(drop=True)
    )
    summary_df["schema_pass_rate"] = (summary_df["schema_pass_count"] / summary_df["documents"]).round(3)
    summary_df["avg_runtime_per_doc"] = summary_df["avg_runtime_per_doc"].round(2)
    summary_df["avg_field_completion"] = summary_df["avg_field_completion"].round(3)
    summary_df["avg_keyword_coverage"] = summary_df["avg_keyword_coverage"].round(3)

    detail_df.to_csv(ARTIFACTS_DIR / "benchmark_detail.csv", index=False)
    summary_df.to_csv(ARTIFACTS_DIR / "benchmark_summary.csv", index=False)
    (ARTIFACTS_DIR / "benchmark_detail.json").write_text(
        detail_df.to_json(orient="records", indent=2), encoding="utf-8"
    )
    (ARTIFACTS_DIR / "benchmark_summary.json").write_text(
        summary_df.to_json(orient="records", indent=2), encoding="utf-8"
    )
    return summary_df, detail_df


def build_notion_report(summary_df: pd.DataFrame, detail_df: pd.DataFrame) -> str:
    lines: list[str] = []
    lines.append("# OCR Extraction Benchmark Results")
    lines.append("")
    lines.append("## Corpus")
    lines.append("")
    lines.append("| File | Pages |")
    lines.append("|---|---:|")
    for item in TEST_DOCS:
        pages = fitz.open(item["path"]).page_count
        lines.append(f"| {item['label']} | {pages} |")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Engine | Docs | Pages | Runtime (s) | Avg runtime/doc (s) | Total text chars | Avg field completion | Avg keyword coverage | Finding candidates | Schema pass rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, row in summary_df.iterrows():
        lines.append(
            f"| {row['engine']} | {int(row['documents'])} | {int(row['pages'])} | "
            f"{row['runtime_seconds']:.2f} | {row['avg_runtime_per_doc']:.2f} | "
            f"{int(row['total_text_chars'])} | {row['avg_field_completion']:.3f} | "
            f"{row['avg_keyword_coverage']:.3f} | {int(row['total_finding_candidates'])} | "
            f"{row['schema_pass_rate']:.3f} |"
        )
    lines.append("")
    lines.append("## Per-Document Output")
    lines.append("")
    lines.append("| Engine | Document | Runtime (s) | Text chars | Field completion | Keyword coverage | Findings | Schema pass | Extracted project | Extracted date | Extracted type |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|---|---|---|")
    for _, row in detail_df.iterrows():
        lines.append(
            f"| {row['engine']} | {row['document']} | {row['runtime_seconds']:.2f} | "
            f"{int(row['text_chars'])} | {row['field_completion']:.3f} | {row['keyword_coverage']:.3f} | "
            f"{int(row['finding_candidates'])} | {row['schema_pass']} | "
            f"{row['project_name'] or ''} | {row['inspection_date'] or ''} | {row['inspection_type'] or ''} |"
        )
    lines.append("")
    lines.append("## Sample OCR Preview")
    lines.append("")
    for _, row in detail_df.iterrows():
        lines.append(f"### {row['engine']} :: {row['document']}")
        lines.append("")
        lines.append("```text")
        lines.append(row["preview"])
        lines.append("```")
        lines.append("")
    report = "\n".join(lines)
    (ARTIFACTS_DIR / "notion_benchmark_report.md").write_text(report, encoding="utf-8")
    return report


if __name__ == "__main__":
    summary, detail = run_benchmark()
    print(summary)
    print()
    print(build_notion_report(summary, detail))
