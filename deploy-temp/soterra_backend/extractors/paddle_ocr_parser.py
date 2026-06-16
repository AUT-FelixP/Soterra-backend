from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..text_extraction import render_page_image_paths
from .docling_parser import ParsedDocument, ParsedPage


def parse_with_paddle_ocr(pdf_path: Path, *, max_pages: int = 6, lang: str = "en") -> ParsedDocument:
    try:
        from paddleocr import PaddleOCR
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PaddleOCR is required when SOTERRA_PADDLE_OCR_ENABLED=true. "
            'Install it with: pip install -e ".[local-ai]" or pip install paddleocr'
        ) from exc

    try:
        ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
    except TypeError:
        ocr = PaddleOCR(use_angle_cls=True, lang=lang)

    pages: list[ParsedPage] = []
    with tempfile.TemporaryDirectory(prefix="soterra-paddle-ocr-") as tmp_dir_name:
        image_paths = render_page_image_paths(pdf_path, Path(tmp_dir_name), max_pages=max_pages, dpi=180)
        for page_number, image_path in enumerate(image_paths, start=1):
            result = ocr.ocr(str(image_path), cls=True)
            page_text = _extract_text_lines(result)
            pages.append(ParsedPage(page_number=page_number, text=page_text))

    full_text = "\n\n".join(f"--- Page {page.page_number} ---\n{page.text}" for page in pages if page.text).strip()
    return ParsedDocument(
        full_text=full_text,
        pages=pages,
        metadata={"parse_provider": "paddleocr", "page_count": len(pages), "lang": lang},
    )


def _extract_text_lines(result: Any) -> str:
    lines: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, str):
            if node.strip():
                lines.append(node.strip())
            return
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and isinstance(node[1], (list, tuple)) and node[1] and isinstance(node[1][0], str):
                lines.append(node[1][0].strip())
                return
            for item in node:
                visit(item)

    visit(result)
    return "\n".join(dict.fromkeys(line for line in lines if line))
