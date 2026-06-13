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

    if mode == "demo":
        if settings.app_env != "test":
            raise RuntimeError("Demo extraction is only available when SOTERRA_ENV=test.")
        return DemoExtractor()

    raise RuntimeError(f"Unsupported extractor mode: {settings.extractor_mode}")
