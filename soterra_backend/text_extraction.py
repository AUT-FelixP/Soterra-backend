from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import fitz


def extract_embedded_text(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    parts: list[str] = []
    for page in document:
        text = page.get_text("text").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def render_page_images(pdf_path: Path, max_pages: int = 8) -> list[str]:
    document = fitz.open(pdf_path)
    encoded_pages: list[str] = []

    for index in range(min(document.page_count, max_pages)):
        page = document[index]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        png_bytes = pixmap.tobytes("png")
        encoded_pages.append(base64.b64encode(png_bytes).decode("utf-8"))

    return encoded_pages


def extract_text_with_easyocr(pdf_path: Path, max_pages: int = 12) -> str:
    try:
        import easyocr
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "easyocr is not installed. Install the demo extras or configure OPENAI_API_KEY."
        ) from exc

    reader = easyocr.Reader(["en"], gpu=False)
    document = fitz.open(pdf_path)
    parts: list[str] = []

    with tempfile.TemporaryDirectory(prefix="soterra-ocr-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        for index in range(min(document.page_count, max_pages)):
            page = document[index]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image_path = tmp_dir / f"page-{index + 1}.png"
            pixmap.save(image_path)
            page_text = reader.readtext(str(image_path), detail=0, paragraph=True)
            parts.append("\n".join(item.strip() for item in page_text if item.strip()))

    return "\n\n".join(part for part in parts if part).strip()


def extract_document_text(pdf_path: Path) -> tuple[str, str]:
    embedded_text = extract_embedded_text(pdf_path)
    if len(embedded_text) >= 400:
        return embedded_text, "embedded-text"

    ocr_text = extract_text_with_easyocr(pdf_path)
    if ocr_text:
        return ocr_text, "easyocr"

    return embedded_text, "empty"

