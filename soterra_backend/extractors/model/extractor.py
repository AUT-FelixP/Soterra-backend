from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ...config import ModelExtractionConfig, Settings
from ...extraction_quality import finalize_extraction
from ...models import ExtractionResult
from ..base import ExtractionArtifacts, ExtractionRequest
from ..package_doctr import DoctrRulesPresidioExtractor, _redact_text
from .clients import ModelExtractionClient, build_model_extraction_client
from .document_text import DocumentTextExtractor, build_document_text_extractor
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .quality import ExtractionQualityResult, dedupe_findings, score_extraction_quality


class ModelExtractor:
    def __init__(
        self,
        settings: Settings,
        *,
        clients: dict[tuple[str, str], ModelExtractionClient] | None = None,
        document_text_extractor: DocumentTextExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.clients = clients or {}
        self.document_text_extractor = document_text_extractor or build_document_text_extractor(settings)
        self.fallback = DoctrRulesPresidioExtractor(
            settings,
            use_curated_profiles=False,
            use_demo_fallback=False,
        )

    def extract(self, request: ExtractionRequest, pdf_path: Path) -> ExtractionArtifacts:
        document_text = self.document_text_extractor.extract_text(pdf_path)
        raw_text = document_text.text
        extraction_source = document_text.source
        attempts: list[dict[str, Any]] = []
        candidates: list[tuple[ExtractionResult, ExtractionQualityResult, ModelExtractionConfig]] = []

        for config in self.settings.model_extraction_models:
            result = self._try_model(config, request, raw_text)
            attempts.append(result["metadata"])
            if result.get("extraction") and result.get("quality"):
                candidates.append((result["extraction"], result["quality"], config))

        usable = [(extraction, quality, config) for extraction, quality, config in candidates if not quality.should_fallback]
        if usable:
            extraction, quality, config = max(usable, key=lambda item: item[1].score)
            return ExtractionArtifacts(
                extraction=extraction,
                raw_text=_redact_text(raw_text),
                extractor_name=f"model:{config.provider}:{config.model_id}:{extraction_source}",
                metadata=_metadata(
                    config=config,
                    mode="model",
                    quality=quality,
                    fallback_used=False,
                    attempts=attempts,
                    extraction_source=extraction_source,
                    document_text_metadata=document_text.metadata,
                ),
            )

        fallback_reason = _fallback_reason(attempts)
        fallback_artifacts = self.fallback.extract(request, pdf_path)
        fallback_extraction = fallback_artifacts.extraction.model_copy(
            update={
                "summary": (
                    f"{fallback_artifacts.extraction.summary} "
                    f"Model extraction did not meet the quality gate, so package fallback was used."
                )[:1200]
            }
        )
        return ExtractionArtifacts(
            extraction=fallback_extraction,
            raw_text=fallback_artifacts.raw_text,
            extractor_name=f"fallback:package_after_model:{fallback_reason}",
            metadata={
                "extractor_mode": "model",
                "fallback_used": True,
                "fallback_reason": fallback_reason,
                "model_attempts": attempts,
                "extraction_source": extraction_source,
                "document_text": document_text.metadata,
            },
        )

    def _try_model(self, config: ModelExtractionConfig, request: ExtractionRequest, raw_text: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "model_provider": config.provider,
            "model_id": config.model_id,
            "model_name": config.name,
            "status": "failed",
        }
        try:
            payload = self._extract_json_with_retries(config, request, raw_text)
            extraction = ExtractionResult.model_validate(payload)
            extraction = dedupe_findings(finalize_extraction(extraction, request.filename))
            if self.settings.model_extraction_max_findings > 0:
                extraction = extraction.model_copy(
                    update={"findings": extraction.findings[: self.settings.model_extraction_max_findings]}
                )
            quality = score_extraction_quality(extraction, raw_text=raw_text)
            metadata.update(
                {
                    "status": "completed",
                    "quality_score": quality.score,
                    "quality_should_fallback": quality.should_fallback,
                    "quality_reason": quality.reason,
                    "quality_warnings": quality.warnings,
                    "finding_count": len(extraction.findings),
                }
            )
            return {"metadata": metadata, "extraction": extraction, "quality": quality}
        except Exception as exc:
            metadata.update({"error_type": type(exc).__name__, "error": str(exc)[:500]})
            return {"metadata": metadata}

    def _extract_json_with_retries(
        self,
        config: ModelExtractionConfig,
        request: ExtractionRequest,
        raw_text: str,
    ) -> dict:
        attempts = max(1, self.settings.model_extraction_retry_count + 1)
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                client = self._client_for(config)
                return client.extract_json(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=build_user_prompt(
                        request=request,
                        raw_text=raw_text,
                        max_findings=self.settings.model_extraction_max_findings,
                    ),
                    schema=ExtractionResult.model_json_schema(),
                    model_id=config.model_id,
                    temperature=self.settings.model_extraction_temperature,
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def _client_for(self, config: ModelExtractionConfig) -> ModelExtractionClient:
        key = (config.provider.lower(), config.model_id)
        if key not in self.clients:
            self.clients[key] = build_model_extraction_client(self.settings, config)
        return self.clients[key]


def _metadata(
    *,
    config: ModelExtractionConfig,
    mode: str,
    quality: ExtractionQualityResult,
    fallback_used: bool,
    attempts: list[dict[str, Any]],
    extraction_source: str,
    document_text_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model_provider": config.provider,
        "model_id": config.model_id,
        "model_config": asdict(config),
        "extractor_mode": mode,
        "quality_score": quality.score,
        "fallback_used": fallback_used,
        "quality_warnings": quality.warnings,
        "quality_reason": quality.reason,
        "model_attempts": attempts,
        "extraction_source": extraction_source,
        "document_text": document_text_metadata,
    }


def _fallback_reason(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return "no_model_configs"
    reasons = [item.get("quality_reason") or item.get("error_type") or "unknown" for item in attempts]
    return str(reasons[0])[:80]

