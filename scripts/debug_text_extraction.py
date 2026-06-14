from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import fitz

from soterra_backend.text_extraction import extract_embedded_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug embedded text and docTR OCR extraction for a PDF.")
    parser.add_argument("pdf_path", type=Path, help="Path to the PDF to inspect.")
    args = parser.parse_args()

    pdf_path = args.pdf_path.expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    embedded = extract_embedded_text(pdf_path)
    print(f"embedded_text_length={len(embedded.strip())}")
    print("embedded_text_preview:")
    print(embedded[:1000])

    try:
        ocr_text = _extract_text_with_doctr(pdf_path, max_pages=3)
    except Exception as exc:
        print("ocr_error:")
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"ocr_text_length={len(ocr_text.strip())}")
    print("ocr_text_preview:")
    print(ocr_text[:1000])
    return 0


def _extract_text_with_doctr(pdf_path: Path, *, max_pages: int) -> str:
    from doctr.io import DocumentFile
    from doctr.models import ocr_predictor

    with tempfile.TemporaryDirectory(prefix="soterra-debug-ocr-") as tmp_dir_name:
        page_paths = _render_pdf_pages(pdf_path, Path(tmp_dir_name), max_pages=max_pages)
        document = DocumentFile.from_images([str(path) for path in page_paths])
        predictor = ocr_predictor(pretrained=True)
        result = predictor(document)
        return result.render().strip()


def _render_pdf_pages(pdf_path: Path, target_dir: Path, *, max_pages: int) -> list[Path]:
    document = fitz.open(pdf_path)
    page_paths: list[Path] = []
    for index in range(min(document.page_count, max_pages)):
        page = document[index]
        pixmap = page.get_pixmap(dpi=120, alpha=False)
        output = target_dir / f"page-{index + 1:03d}.png"
        pixmap.save(output)
        page_paths.append(output)
    return page_paths


if __name__ == "__main__":
    raise SystemExit(main())
