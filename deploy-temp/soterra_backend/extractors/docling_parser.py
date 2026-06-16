from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ParsedPage(BaseModel):
    page_number: int
    text: str
    tables: list[dict] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    full_text: str
    pages: list[ParsedPage] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


def parse_with_docling(document_path: Path) -> ParsedDocument:
    try:
        from docling.document_converter import DocumentConverter
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Docling is required for SOTERRA_DOCUMENT_PARSE_PROVIDER=docling. "
            'Install it with: pip install -e ".[local-ai]" or pip install docling'
        ) from exc

    try:
        converted = DocumentConverter().convert(str(document_path))
        doc = converted.document
        markdown = _export_markdown(doc)
        pages = _pages_from_docling(doc, markdown)
        metadata = {
            "parse_provider": "docling",
            "source_format": document_path.suffix.lower().lstrip("."),
            "page_count": len(pages),
        }
        return ParsedDocument(full_text=markdown, pages=pages, metadata=metadata)
    except Exception as exc:
        raise RuntimeError(f"Docling failed to parse {document_path.name}: {exc}") from exc


def parsed_document_from_text(text: str, *, provider: str, metadata: dict | None = None) -> ParsedDocument:
    return ParsedDocument(
        full_text=text.strip(),
        pages=[ParsedPage(page_number=1, text=text.strip())] if text.strip() else [],
        metadata={"parse_provider": provider, **(metadata or {})},
    )


def merge_documents(*documents: ParsedDocument) -> ParsedDocument:
    selected = [doc for doc in documents if doc.full_text.strip()]
    if not selected:
        return ParsedDocument(full_text="", pages=[], metadata={"parse_provider": "empty"})
    pages: list[ParsedPage] = []
    parts: list[str] = []
    metadata: dict[str, Any] = {"parse_providers": []}
    for doc in selected:
        provider = doc.metadata.get("parse_provider") or "unknown"
        metadata["parse_providers"].append(provider)
        metadata.update({f"{provider}_{key}": value for key, value in doc.metadata.items() if key != "parse_provider"})
        for page in doc.pages:
            pages.append(ParsedPage(page_number=len(pages) + 1, text=page.text, tables=page.tables))
        parts.append(doc.full_text.strip())
    metadata["parse_provider"] = "+".join(dict.fromkeys(metadata["parse_providers"]))
    return ParsedDocument(full_text="\n\n".join(parts).strip(), pages=pages, metadata=metadata)


def document_for_llm(parsed: ParsedDocument, *, max_chars: int = 42000) -> str:
    if parsed.pages:
        parts = []
        for page in parsed.pages:
            text = page.text.strip()
            if page.tables:
                table_text = "\n".join(str(table.get("markdown") or table.get("text") or table) for table in page.tables)
                text = f"{text}\n\nTables:\n{table_text}".strip()
            if text:
                parts.append(f"--- Page {page.page_number} ---\n{text}")
        output = "\n\n".join(parts).strip()
    else:
        output = parsed.full_text.strip()
    return output[:max_chars]


def _export_markdown(doc: Any) -> str:
    if hasattr(doc, "export_to_markdown"):
        return str(doc.export_to_markdown() or "").strip()
    if hasattr(doc, "export_to_text"):
        return str(doc.export_to_text() or "").strip()
    return str(doc or "").strip()


def _pages_from_docling(doc: Any, markdown: str) -> list[ParsedPage]:
    pages = _pages_from_document_object(doc)
    if pages:
        return pages
    split_pages = _split_markdown_pages(markdown)
    if split_pages:
        return split_pages
    return [ParsedPage(page_number=1, text=markdown)] if markdown.strip() else []


def _pages_from_document_object(doc: Any) -> list[ParsedPage]:
    raw_pages = getattr(doc, "pages", None)
    if not raw_pages:
        return []
    items = raw_pages.values() if isinstance(raw_pages, dict) else raw_pages
    pages: list[ParsedPage] = []
    for index, page in enumerate(items, start=1):
        text = _page_text(page)
        tables = _page_tables(page)
        if text or tables:
            pages.append(ParsedPage(page_number=index, text=text, tables=tables))
    return pages


def _page_text(page: Any) -> str:
    for attr in ("text", "content"):
        value = getattr(page, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if hasattr(page, "export_to_markdown"):
        return str(page.export_to_markdown() or "").strip()
    return ""


def _page_tables(page: Any) -> list[dict]:
    tables = []
    for table in getattr(page, "tables", []) or []:
        markdown = ""
        if hasattr(table, "export_to_markdown"):
            markdown = str(table.export_to_markdown() or "").strip()
        tables.append({"markdown": markdown, "text": str(table)[:2000]})
    return tables


def _split_markdown_pages(markdown: str) -> list[ParsedPage]:
    if not markdown.strip():
        return []
    markers = list(re.finditer(r"(?im)^-{0,3}\s*page\s+(\d+)\s*-{0,3}\s*$", markdown))
    if not markers:
        return []
    pages: list[ParsedPage] = []
    for index, marker in enumerate(markers):
        start = marker.end()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(markdown)
        pages.append(ParsedPage(page_number=int(marker.group(1)), text=markdown[start:end].strip()))
    return pages
