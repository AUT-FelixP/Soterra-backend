from __future__ import annotations

import importlib.util

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/extraction")
def extraction_health(request: Request) -> dict:
    settings = request.app.state.settings
    package_available = _module_available("fitz")
    if settings.package_extractor == "doctr_rules_presidio":
        package_available = package_available and _module_available("doctr")
    return {
        "extractor_mode": settings.extractor_mode,
        "package_extractor": settings.package_extractor,
        "allow_model_extraction": settings.allow_model_extraction,
        "package_available": package_available,
        "status": "ok" if settings.extractor_mode == "package" and package_available else "configured",
    }


@router.get("/health/agent")
def agent_health(request: Request) -> dict:
    service = request.app.state.agent_service
    status = service.status()
    return {
        "provider": status.get("provider"),
        "enabled": status.get("enabled"),
        "configured": status.get("configured"),
        "mode": status.get("mode", "deterministic" if status.get("provider") == "native" else "model"),
    }


@router.get("/health/insights")
def insights_health(request: Request) -> dict:
    settings = request.app.state.settings
    provider = getattr(settings, "soterra_insights_provider", "native")
    return {
        "provider": provider,
        "mode": "deterministic" if provider == "native" else "model",
        "configured": True if provider == "native" else bool(request.app.state.agent_service.status().get("configured")),
    }


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None
