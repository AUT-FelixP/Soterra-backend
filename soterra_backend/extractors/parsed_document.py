from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedPage(BaseModel):
    page_number: int
    text: str
    tables: list[dict] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    full_text: str
    pages: list[ParsedPage] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


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
