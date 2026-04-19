from __future__ import annotations

from ..config import Settings
from .base import ExtractorBackend
from .demo import DemoExtractor
from .openai_model import OpenAIModelExtractor
from .package_doctr import DoctrRulesPresidioExtractor


def build_extractor(settings: Settings) -> ExtractorBackend:
    mode = settings.extractor_mode.strip().lower()

    if mode == "package":
        if settings.package_extractor == "doctr_rules_presidio":
            return DoctrRulesPresidioExtractor(settings)
        raise RuntimeError(f"Unsupported package extractor: {settings.package_extractor}")

    if mode in {"openai", "model"}:
        if not settings.allow_model_extraction:
            raise RuntimeError(
                "Model extraction is currently disabled. Keep SOTERRA_EXTRACTOR_MODE=package until approval is granted."
            )
        provider = settings.model_extractor if mode == "model" else "openai"
        if provider == "openai":
            return OpenAIModelExtractor(settings)
        raise RuntimeError(f"Unsupported model extractor: {provider}")

    if mode == "demo":
        return DemoExtractor()

    raise RuntimeError(f"Unsupported extractor mode: {settings.extractor_mode}")
