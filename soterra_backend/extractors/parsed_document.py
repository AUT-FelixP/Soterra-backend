from __future__ import annotations

import os

from pydantic import BaseModel, Field


class ParsedPage(BaseModel):
    page_number: int
    text: str
    tables: list[dict] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    full_text: str
    pages: list[ParsedPage] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


def document_for_llm(parsed: ParsedDocument, *, max_chars: int | None = None) -> str:
    if max_chars is None:
        try:
            max_chars = max(12000, int(os.getenv("SOTERRA_OLLAMA_TEXT_MAX_CHARS", "120000")))
        except ValueError:
            max_chars = 120000
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
    if len(output) <= max_chars:
        return output
    clipped = output[:max_chars]
    boundary = max(clipped.rfind("\n\n"), clipped.rfind(". "), clipped.rfind("\n"), clipped.rfind(" "))
    if boundary > int(max_chars * 0.85):
        return clipped[:boundary].rstrip()
    return clipped.rstrip()
