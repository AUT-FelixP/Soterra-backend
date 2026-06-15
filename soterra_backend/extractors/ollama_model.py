from __future__ import annotations

import json
import os
import re
from typing import Any

import requests
from pydantic import ValidationError

from ..models import ExtractionResult
from .base import ExtractionRequest
from .docling_parser import ParsedDocument, document_for_llm


SYSTEM_PROMPT = """You are extracting construction inspection issues from a PDF or Word report.
Return only valid JSON.
Do not invent issues.
Only extract issues supported by source text.
Do not include passed items, generic report headings, legal footer text, or inspection metadata as defects.
If the report says an item failed, missing, incomplete, not compliant, below minimum, not installed, outstanding, or requires rectification, extract it.
For every finding include title, description, severity, category, trade, location, unit_label, root_cause, required_fix, evidence_required, source_page, source_quote, confidence, and extraction_warnings.
Severity rules:
Critical means failed life safety, fire stopping, waterproofing failure, structural risk, council failure, or urgent reinspection blocker.
High means non-compliance requiring rework, missing installation, failed checklist item, or close-out blocker.
Medium means incomplete evidence, coordination issue, or minor defect requiring confirmation.
Low means observation or minor issue that does not block close-out.
If location is missing, leave it null and add a warning.
If trade is uncertain, set trade to "General" and add a warning.
Keep finding titles concise and construction-specific.
Keep descriptions plain English and actionable."""


class OllamaModelExtractor:
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model_id: str = "qwen2.5:7b-instruct",
        api_key: str | None = None,
        timeout_seconds: int = 90,
        temperature: float = 0.0,
        max_findings: int = 40,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.api_key = api_key or os.getenv("SOTERRA_OLLAMA_API_KEY") or os.getenv("OLLAMA_API_KEY")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_findings = max_findings

    def extract(self, *, parsed_document: ParsedDocument, request: ExtractionRequest) -> ExtractionResult:
        prompt = _build_prompt(parsed_document=parsed_document, request=request, max_findings=self.max_findings)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                payload = self._call_ollama(prompt if attempt == 0 else _retry_prompt(prompt, last_error))
                payload = _normalize_payload(payload, request=request)
                return ExtractionResult.model_validate(payload)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
        assert last_error is not None
        raise RuntimeError(f"Ollama returned invalid extraction JSON after retry: {last_error}") from last_error

    def generate_text(self, *, system_prompt: str, user_prompt: str, timeout_seconds: int | None = None) -> str:
        response = requests.post(
            f"{self.base_url}/api/chat",
            headers=self._headers(),
            json={
                "model": self.model_id,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "options": {"temperature": self.temperature},
            },
            timeout=timeout_seconds or self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Ollama request failed with HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        return str((data.get("message") or {}).get("content") or data.get("response") or "").strip()

    def _call_ollama(self, prompt: str) -> dict:
        schema = ExtractionResult.model_json_schema()
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                headers=self._headers(),
                json={
                    "model": self.model_id,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "format": schema,
                    "options": {"temperature": self.temperature},
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self.base_url}. Start Ollama or set SOTERRA_OLLAMA_BASE_URL."
            ) from exc
        if response.status_code >= 400:
            raise RuntimeError(f"Ollama request failed with HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        content = str((data.get("message") or {}).get("content") or data.get("response") or "")
        return _loads_json_object(content)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}


def _build_prompt(*, parsed_document: ParsedDocument, request: ExtractionRequest, max_findings: int) -> str:
    schema = ExtractionResult.model_json_schema()
    return (
        "Return a single JSON object matching the ExtractionResult schema. "
        "Use null for unknown nullable fields and [] for unknown lists. "
        f"Extract at most {max_findings} findings.\n\n"
        "Known upload context:\n"
        f"- filename: {request.filename}\n"
        f"- project_name: {request.project_name}\n"
        f"- site_name: {request.site_name}\n"
        f"- trade: {request.trade}\n"
        f"- address: {request.address or 'unknown'}\n\n"
        "JSON schema:\n"
        f"{json.dumps(schema)[:16000]}\n\n"
        "Document text:\n"
        f"{document_for_llm(parsed_document)}"
    )


def _retry_prompt(prompt: str, last_error: Exception | None) -> str:
    return (
        "Your previous response was not valid JSON for the required schema. "
        f"Validation error: {last_error}. Return corrected JSON only.\n\n{prompt}"
    )


def _normalize_payload(payload: dict, *, request: ExtractionRequest) -> dict:
    normalized = dict(payload)
    normalized["project_name"] = _string_or_default(normalized.get("project_name"), request.project_name)
    normalized["site_name"] = _string_or_default(normalized.get("site_name"), request.site_name)
    normalized["address"] = normalized.get("address") or request.address
    normalized["inspection_type"] = _string_or_default(normalized.get("inspection_type"), request.trade or "General Inspection")
    normalized["trade"] = _string_or_default(normalized.get("trade"), request.trade or "General")
    normalized["inspector"] = _string_or_default(normalized.get("inspector"), "Unknown inspector")
    normalized["report_date"] = _string_or_default(normalized.get("report_date"), "Unknown")
    normalized["summary"] = _string_or_default(
        normalized.get("summary"),
        "Structured extraction completed from the provided inspection document.",
    )
    normalized["overall_outcome"] = normalized.get("overall_outcome") or "Reviewing"
    normalized["units"] = normalized.get("units") if isinstance(normalized.get("units"), list) else []
    normalized["findings"] = normalized.get("findings") if isinstance(normalized.get("findings"), list) else []
    normalized["predicted_inspections"] = (
        normalized.get("predicted_inspections") if isinstance(normalized.get("predicted_inspections"), list) else []
    )
    return normalized


def _string_or_default(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _loads_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        decoded: Any = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        decoded = json.loads(stripped[start : end + 1])
    if not isinstance(decoded, dict):
        raise ValueError("Ollama JSON response must be an object.")
    return decoded
