from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ...config import DEFAULT_PARSE_MODEL_ID, Settings
from ...text_extraction import extract_embedded_text, render_page_image_paths, render_page_pil_images
from ..package_doctr import _extract_text_with_doctr

NEMOTRON_PARSE_PROMPT_NO_TEXT_IN_PIC = "</s><s><predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>"
NEMOTRON_PARSE_PROMPT_TEXT_IN_PIC = "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"


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


class NemotronParseDocumentTextExtractor:
    _local_models: dict[tuple[str, str], tuple[Any, Any, Any, str]] = {}

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
            if self.provider in {"local_transformers", "transformers_local", "local-hf", "local_hf"}:
                return self._extract_with_local_transformers(pdf_path)
            if self.provider in {"openai_compatible", "openai-compatible", "vllm"}:
                return self._extract_with_openai_compatible(pdf_path)
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

    def _extract_with_local_transformers(self, pdf_path: Path) -> DocumentTextResult:
        try:
            import torch
            from transformers import AutoModel, AutoProcessor, GenerationConfig
        except ModuleNotFoundError as exc:
            raise RuntimeError("transformers is required for local Nemotron Parse extraction.") from exc

        prompt = _nemotron_parse_prompt(self.text_in_pictures)
        device = os.getenv("SOTERRA_DOCUMENT_PARSE_DEVICE") or ("cuda:0" if torch.cuda.is_available() else "cpu")
        model, processor, generation_config, device = self._load_local_transformers_model(
            model_id=self.model_id,
            device=device,
            auto_model=AutoModel,
            auto_processor=AutoProcessor,
            generation_config_cls=GenerationConfig,
        )
        generation_config.max_new_tokens = self.max_new_tokens
        page_texts: list[str] = []
        for page_number, image in enumerate(render_page_pil_images(pdf_path, self.max_pages, dpi=120), start=1):
            inputs = processor(images=[image], text=prompt, return_tensors="pt", add_special_tokens=False).to(device)
            outputs = model.generate(**inputs, generation_config=generation_config)
            page_text = processor.batch_decode(outputs, skip_special_tokens=True)[0].strip()
            if page_text:
                page_texts.append(f"--- Page {page_number} ---\n{page_text}")
        return DocumentTextResult(
            text="\n\n".join(page_texts).strip(),
            source="nemotron-parse",
            metadata={"parse_provider": self.provider, "parse_model_id": self.model_id},
        )

    def _load_local_transformers_model(
        self,
        *,
        model_id: str,
        device: str,
        auto_model: Any,
        auto_processor: Any,
        generation_config_cls: Any,
    ) -> tuple[Any, Any, Any, str]:
        torch_dtype = os.getenv("SOTERRA_DOCUMENT_PARSE_TORCH_DTYPE", os.getenv("SOTERRA_LOCAL_MODEL_TORCH_DTYPE", "auto"))
        key = (model_id, f"{device}:{torch_dtype}")
        if key in self.__class__._local_models:
            return self.__class__._local_models[key]

        model = auto_model.from_pretrained(
            model_id,
            trust_remote_code=True,
            dtype=_torch_dtype(torch_dtype),
        ).to(device).eval()
        processor = auto_processor.from_pretrained(model_id, trust_remote_code=True)
        generation_config = generation_config_cls.from_pretrained(model_id, trust_remote_code=True)
        loaded = (model, processor, generation_config, device)
        self.__class__._local_models[key] = loaded
        return loaded

    def _extract_with_openai_compatible(self, pdf_path: Path) -> DocumentTextResult:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError("openai is required for OpenAI-compatible Nemotron Parse extraction.") from exc

        base_url = os.getenv(
            "SOTERRA_DOCUMENT_PARSE_BASE_URL",
            "http://localhost:8000/v1",
        )
        api_key = os.getenv(
            "SOTERRA_DOCUMENT_PARSE_API_KEY",
            "local",
        )
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=int(os.getenv("SOTERRA_DOCUMENT_PARSE_TIMEOUT_SECONDS", "120")))
        prompt = _nemotron_parse_prompt(self.text_in_pictures)
        page_texts: list[str] = []

        import tempfile

        with tempfile.TemporaryDirectory(prefix="soterra-nemotron-parse-") as tmp_dir_name:
            page_paths = render_page_image_paths(pdf_path, Path(tmp_dir_name), self.max_pages, dpi=120)
            for page_number, page_path in enumerate(page_paths, start=1):
                image_b64 = base64.b64encode(page_path.read_bytes()).decode("utf-8")
                response = client.chat.completions.create(
                    model=self.model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                            ],
                        }
                    ],
                    max_tokens=self.max_new_tokens,
                    temperature=0.0,
                    extra_body={
                        "repetition_penalty": 1.1,
                        "top_k": 1,
                        "skip_special_tokens": False,
                    },
                )
                page_text = response.choices[0].message.content or ""
                if page_text.strip():
                    page_texts.append(f"--- Page {page_number} ---\n{page_text.strip()}")

        return DocumentTextResult(
            text="\n\n".join(page_texts).strip(),
            source="nemotron-parse-openai-compatible",
            metadata={"parse_provider": self.provider, "parse_model_id": self.model_id},
        )


def build_document_text_extractor(settings: Settings) -> DocumentTextExtractor:
    package_fallback = PackageDocumentTextExtractor(max_pages=settings.package_max_pages)
    return NemotronParseDocumentTextExtractor(
        provider=settings.soterra_document_parse_provider,
        model_id=settings.soterra_document_parse_model_id,
        max_pages=settings.document_parse_max_pages,
        max_new_tokens=settings.document_parse_max_new_tokens,
        text_in_pictures=settings.document_parse_text_in_pictures,
        fallback=package_fallback,
    )


def _nemotron_parse_prompt(text_in_pictures: bool) -> str:
    if text_in_pictures:
        return NEMOTRON_PARSE_PROMPT_TEXT_IN_PIC
    return NEMOTRON_PARSE_PROMPT_NO_TEXT_IN_PIC


def _torch_dtype(value: str | None) -> object:
    if not value or value == "auto":
        return "auto"
    try:
        import torch
    except ModuleNotFoundError:
        return value
    normalized = value.strip().lower()
    mapping = {
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float16": torch.float16,
        "fp16": torch.float16,
        "float32": torch.float32,
        "fp32": torch.float32,
    }
    return mapping.get(normalized, value)
