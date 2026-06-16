from __future__ import annotations

from ..config import Settings
from .base import ExtractorBackend
from .demo import DemoExtractor
from .model import ModelExtractor


def build_extractor(settings: Settings) -> ExtractorBackend:
    mode = settings.extractor_mode.strip().lower()

    if mode == "package":
        if settings.package_extractor == "doctr_rules_presidio":
            from .doctr_rules_presidio import DoctrRulesPresidioExtractor

            return DoctrRulesPresidioExtractor(settings)
        raise RuntimeError(f"Unsupported package extractor: {settings.package_extractor}")

    if mode == "model":
        if not settings.allow_model_extraction:
            raise RuntimeError("Model extraction is disabled.")
        return ModelExtractor(settings)

    if mode == "local_ai":
        if settings.soterra_extraction_provider.strip().lower() != "ollama":
            raise RuntimeError("SOTERRA_EXTRACTOR_MODE=local_ai requires SOTERRA_EXTRACTION_PROVIDER=ollama.")
        from .local_ai_pipeline import LocalAIPipelineExtractor

        return LocalAIPipelineExtractor(settings)

    if mode == "ollama_text":
        if settings.soterra_extraction_provider.strip().lower() != "ollama":
            raise RuntimeError("SOTERRA_EXTRACTOR_MODE=ollama_text requires SOTERRA_EXTRACTION_PROVIDER=ollama.")
        from .ollama_text import OllamaTextExtractor

        return OllamaTextExtractor(settings)

    if mode == "demo":
        if settings.app_env != "test":
            raise RuntimeError("Demo extraction is only available when SOTERRA_ENV=test.")
        return DemoExtractor()

    raise RuntimeError(f"Unsupported extractor mode: {settings.extractor_mode}")
