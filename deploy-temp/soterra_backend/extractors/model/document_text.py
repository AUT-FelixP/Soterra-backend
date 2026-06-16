from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ...config import DEFAULT_PARSE_MODEL_ID, Settings
from ...huggingface import normalize_inference_model_id
from ...text_extraction import extract_embedded_text, render_page_image_paths
from ..package_doctr import _extract_text_with_doctr

DOCUMENT_PARSE_PROMPT = (
    "Extract the visible text from this construction inspection report page. "
    "Preserve headings, tables, checklist items, defect descriptions, locations, dates, outcomes, and page order. "
    "Return only the extracted document text in plain markdown. Do not summarize and do not add facts."
)


@dataclass(frozen=True)
class DocumentTextResult:
    text: str
    source: str
    metadata: dict = field(default_factory=dict)


class DocumentTextExtractor(Protocol):
    def extract_text(self, pdf_path: Path) -> DocumentTextResult:
        ...


class PackageDocumentTextExtractor:
    def __init__(self, *, max_pages: int) -> None:
        self.max_pages = max_pages

    def extract_text(self, pdf_path: Path) -> DocumentTextResult:
        embedded_text = extract_embedded_text(pdf_path)
        if len(embedded_text.strip()) >= 400:
            return DocumentTextResult(text=embedded_text, source="embedded-text")
        try:
            ocr_text = _extract_text_with_doctr(pdf_path, max_pages=self.max_pages)
        except RuntimeError as exc:
            return DocumentTextResult(
                text=embedded_text,
                source="embedded-text-sparse",
                metadata={"fallback_error": str(exc)[:500]},
            )
        if ocr_text:
            return DocumentTextResult(text=ocr_text, source="doctr")
        return DocumentTextResult(text=embedded_text, source="empty")


class HuggingFaceDocumentTextExtractor:
    def __init__(
        self,
        *,
        provider: str,
        model_id: str,
        max_pages: int,
        max_new_tokens: int,
        text_in_pictures: bool,
        fallback: DocumentTextExtractor,
    ) -> None:
        self.provider = provider.strip().lower()
        self.model_id = model_id or DEFAULT_PARSE_MODEL_ID
        self.max_pages = max_pages
        self.max_new_tokens = max_new_tokens
        self.text_in_pictures = text_in_pictures
        self.fallback = fallback

    def extract_text(self, pdf_path: Path) -> DocumentTextResult:
        try:
            if self.provider in {"package", "package_fallback", "package-only", "package_only"}:
                return self.fallback.extract_text(pdf_path)
            if self.provider in {"huggingface", "hf_inference", "huggingface_inference"}:
                return self._extract_with_huggingface(pdf_path)
            raise RuntimeError(f"Unsupported document parse provider: {self.provider}")
        except Exception as exc:
            fallback_result = self.fallback.extract_text(pdf_path)
            metadata = {
                **fallback_result.metadata,
                "parse_provider": self.provider,
                "parse_model_id": self.model_id,
                "parse_error_type": type(exc).__name__,
                "parse_error": str(exc)[:500],
            }
            return DocumentTextResult(
                text=fallback_result.text,
                source=f"{fallback_result.source}-after-parse-fallback",
                metadata=metadata,
            )

    def _extract_with_huggingface(self, pdf_path: Path) -> DocumentTextResult:
        api_key = os.getenv("HF_TOKEN")
        if not api_key:
            raise RuntimeError("HF_TOKEN is required for Hugging Face document parsing.")
        try:
            from huggingface_hub import InferenceClient
        except ModuleNotFoundError as exc:
            raise RuntimeError("huggingface_hub is required for Hugging Face document parsing.") from exc

        client = InferenceClient(
            api_key=api_key,
            timeout=int(os.getenv("SOTERRA_DOCUMENT_PARSE_TIMEOUT_SECONDS", "120")),
            provider="auto",
        )
        model_id = normalize_inference_model_id(self.model_id)
        page_texts: list[str] = []

        import tempfile

        with tempfile.TemporaryDirectory(prefix="soterra-hf-parse-") as tmp_dir_name:
            page_paths = render_page_image_paths(pdf_path, Path(tmp_dir_name), self.max_pages, dpi=120)
            for page_number, page_path in enumerate(page_paths, start=1):
                image_b64 = base64.b64encode(page_path.read_bytes()).decode("utf-8")
                response = client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                                {"type": "text", "text": DOCUMENT_PARSE_PROMPT},
                            ],
                        }
                    ],
                    max_tokens=self.max_new_tokens,
                    temperature=0.0,
                )
                page_text = response.choices[0].message.content or ""
                if page_text.strip():
                    page_texts.append(f"--- Page {page_number} ---\n{page_text.strip()}")

        return DocumentTextResult(
            text="\n\n".join(page_texts).strip(),
            source="huggingface-vision",
            metadata={"parse_provider": self.provider, "parse_model_id": self.model_id},
        )


def build_document_text_extractor(settings: Settings) -> DocumentTextExtractor:
    package_fallback = PackageDocumentTextExtractor(max_pages=settings.package_max_pages)
    return HuggingFaceDocumentTextExtractor(
        provider=settings.soterra_document_parse_provider,
        model_id=settings.soterra_document_parse_model_id,
        max_pages=settings.document_parse_max_pages,
        max_new_tokens=settings.document_parse_max_new_tokens,
        text_in_pictures=settings.document_parse_text_in_pictures,
        fallback=package_fallback,
    )
