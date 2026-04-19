from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models import ExtractionResult


@dataclass(frozen=True)
class ExtractionRequest:
    filename: str
    content: bytes
    project_name: str
    site_name: str
    trade: str
    address: str | None = None


@dataclass(frozen=True)
class ExtractionArtifacts:
    extraction: ExtractionResult
    raw_text: str
    extractor_name: str


class ExtractorBackend(Protocol):
    def extract(self, request: ExtractionRequest, pdf_path: Path) -> ExtractionArtifacts:
        ...
