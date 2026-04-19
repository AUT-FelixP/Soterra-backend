from __future__ import annotations

from pathlib import Path

from ..ai import extract_with_openai
from ..config import Settings
from ..text_extraction import extract_document_text
from .base import ExtractionArtifacts, ExtractionRequest


class OpenAIModelExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract(self, request: ExtractionRequest, pdf_path: Path) -> ExtractionArtifacts:
        raw_text, extraction_source = extract_document_text(pdf_path)
        extraction = extract_with_openai(self.settings, pdf_path, request.filename, raw_text)
        return ExtractionArtifacts(
            extraction=extraction,
            raw_text=raw_text,
            extractor_name=f"model:openai:{self.settings.openai_model}:{extraction_source}",
        )
