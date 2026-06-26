from __future__ import annotations

import json
import os
import re
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import httpx
from pydantic import ValidationError
from pypdf import PdfReader

from ..config import Settings
from ..extraction_quality import finalize_extraction
from ..models import ExtractionResult
from ..extraction.location_quality import enrich_location_quality
from .base import ExtractionArtifacts, ExtractionRequest
from .model.prompts import SYSTEM_PROMPT, build_user_prompt
from .model.quality import dedupe_findings

STRONG_TEXT_LENGTH = 400
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


@dataclass(frozen=True)
class TextExtraction:
    text: str
    source: str
    metadata: dict[str, Any]


class OllamaTextExtractor:
    """Lightweight external-Ollama extractor for constrained production hosts."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.base_url = settings.soterra_ollama_base_url.rstrip("/")
        self.model_id = settings.soterra_extraction_model_id
        self.vision_model_id = settings.soterra_extraction_vision_model_id or self.model_id
        self.api_key = settings.soterra_ollama_api_key
        self.timeout_seconds = settings.model_extraction_timeout_seconds
        self.temperature = settings.model_extraction_temperature
        self.max_findings = settings.model_extraction_max_findings

    def extract(self, request: ExtractionRequest, pdf_path: Path) -> ExtractionArtifacts:
        document_text = extract_text_for_ollama(pdf_path, request=request)
        raw_text = document_text.text.strip()
        page_images: list[str] = []
        if not raw_text:
            if pdf_path.suffix.lower() != ".pdf":
                raise RuntimeError("ollama_text extraction could not read embedded document text.")
            page_images = render_page_images_for_ollama(
                pdf_path,
                max_pages=max(
                    1,
                    min(
                        self.settings.document_parse_max_pages,
                        self.settings.ollama_text_image_max_pages,
                    ),
                ),
                dpi=max(60, min(self.settings.ollama_text_image_dpi, 150)),
            )
            if not page_images:
                raise RuntimeError("ollama_text extraction could not read document text or render page images.")
            raw_text = "[image-only PDF; extraction requested from rendered page images]"
            document_text = TextExtraction(
                text=raw_text,
                source="pymupdf-page-images",
                metadata={
                    **document_text.metadata,
                    "image_fallback_used": True,
                    "image_page_count": len(page_images),
                },
            )

        payload, retry_count = self._extract_payload(request=request, raw_text=raw_text, images=page_images)
        extraction = ExtractionResult.model_validate(payload)
        extraction = dedupe_findings(finalize_extraction(extraction, request.filename))
        extraction = extraction.model_copy(update={"findings": [enrich_location_quality(item) for item in extraction.findings]})
        extracted_count = len(extraction.findings)
        metadata_warnings: list[str] = []
        if self.max_findings > 0:
            extraction = extraction.model_copy(update={"findings": extraction.findings[: self.max_findings]})
            if extracted_count > self.max_findings:
                metadata_warnings.append(
                    f"Findings truncated from {extracted_count} to configured maximum {self.max_findings}."
                )
            elif extracted_count == self.max_findings:
                metadata_warnings.append(
                    f"Finding count reached configured maximum {self.max_findings}; review the report for possible truncation."
                )

        metadata = {
            "extractor_mode": "ollama_text",
            "model_provider": "ollama",
            "model_id": self.model_id,
            "vision_model_id": self.vision_model_id if page_images else None,
            "ollama_base_url": self.base_url,
            "extraction_source": document_text.source,
            "raw_text_length": len(raw_text),
            "finding_count": len(extraction.findings),
            "finding_count_before_limit": extracted_count,
            "retry_count": retry_count,
            "fallback_used": False,
            "warnings": metadata_warnings,
            "document_text": document_text.metadata,
        }
        return ExtractionArtifacts(
            extraction=extraction,
            raw_text=_redact_text_light(raw_text),
            extractor_name=f"ollama-text:{document_text.source}+ollama:{self.model_id}",
            metadata=metadata,
        )

    def _extract_payload(self, *, request: ExtractionRequest, raw_text: str, images: list[str] | None = None) -> tuple[dict, int]:
        attempts = max(1, self.settings.model_extraction_retry_count + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                user_prompt = (
                    _build_image_prompt(request=request, max_findings=self.max_findings)
                    if images
                    else build_user_prompt(request=request, raw_text=raw_text, max_findings=self.max_findings)
                )
                if attempt > 0:
                    user_prompt = _retry_prompt(user_prompt, last_error)
                payload = _normalize_payload(self._call_ollama(user_prompt=user_prompt, images=images or []), request=request)
                ExtractionResult.model_validate(payload)
                return payload, attempt
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
        assert last_error is not None
        raise RuntimeError(f"Ollama returned invalid extraction JSON after retry: {last_error}") from last_error

    def _call_ollama(self, *, user_prompt: str, images: list[str] | None = None) -> dict:
        schema = ExtractionResult.model_json_schema()
        model_id = self.vision_model_id if images else self.model_id
        user_message: dict[str, Any] = {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                "Return one JSON object only. Do not wrap it in markdown. "
                "Use the required schema supplied in the API format parameter. "
                "Every title, description, fix, and summary must be complete words and complete sentences."
            ),
        }
        if images:
            user_message["images"] = images
        try:
            response = _post_ollama_chat(
                base_url=self.base_url,
                headers=self._headers(),
                payload={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        user_message,
                    ],
                    "stream": False,
                    "format": schema,
                    "options": _ollama_options(self.temperature),
                },
                timeout_seconds=self.timeout_seconds,
                client=self.client,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self.base_url}. Set SOTERRA_OLLAMA_BASE_URL and SOTERRA_OLLAMA_API_KEY."
            ) from exc
        if response.status_code >= 400:
            if images and "does not support image input" in response.text:
                raise RuntimeError(
                    "Ollama image extraction requires a vision-capable model. "
                    "Set SOTERRA_EXTRACTION_VISION_MODEL_ID=llama3.2-vision or another Ollama vision model."
                )
            raise RuntimeError(f"Ollama request failed with HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        content = str((data.get("message") or {}).get("content") or data.get("response") or "")
        return _loads_json_object(content)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}


def _post_ollama_chat(
    *,
    base_url: str,
    headers: dict[str, str],
    payload: dict,
    timeout_seconds: int,
    client: httpx.Client | None,
) -> httpx.Response:
    if client is not None:
        return client.post(f"{base_url}/api/chat", headers=headers, json=payload, timeout=timeout_seconds)
    return httpx.post(f"{base_url}/api/chat", headers=headers, json=payload, timeout=timeout_seconds)


def _ollama_options(temperature: float) -> dict[str, Any]:
    options: dict[str, Any] = {"temperature": temperature}
    for env_key, option_key in (
        ("SOTERRA_OLLAMA_NUM_CTX", "num_ctx"),
        ("SOTERRA_OLLAMA_NUM_PREDICT", "num_predict"),
    ):
        value = os.getenv(env_key)
        if not value:
            continue
        try:
            options[option_key] = int(value)
        except ValueError:
            continue
    return options


def render_page_images_for_ollama(pdf_path: Path, *, max_pages: int, dpi: int = 110) -> list[str]:
    document = fitz.open(pdf_path)
    try:
        images = []
        for index in range(min(document.page_count, max_pages)):
            pixmap = document[index].get_pixmap(dpi=dpi, alpha=False)
            images.append(base64.b64encode(pixmap.tobytes("png")).decode("ascii"))
        return images
    finally:
        document.close()


def extract_text_for_ollama(pdf_path: Path, *, request: ExtractionRequest | None = None) -> TextExtraction:
    if pdf_path.suffix.lower() == ".pdf":
        pymupdf_text = _extract_with_pymupdf(pdf_path)
        if len(pymupdf_text.strip()) >= STRONG_TEXT_LENGTH:
            return TextExtraction(
                text=pymupdf_text,
                source="pymupdf",
                metadata={"pymupdf_text_length": len(pymupdf_text.strip()), "pypdf_attempted": False},
            )

        pypdf_text = _extract_with_pypdf(pdf_path)
        selected_text = pypdf_text if len(pypdf_text.strip()) > len(pymupdf_text.strip()) else pymupdf_text
        selected_source = "pypdf" if selected_text == pypdf_text and pypdf_text.strip() else "pymupdf"
        return TextExtraction(
            text=selected_text,
            source=selected_source,
            metadata={
                "pymupdf_text_length": len(pymupdf_text.strip()),
                "pypdf_attempted": True,
                "pypdf_text_length": len(pypdf_text.strip()),
            },
        )

    content = request.content if request is not None else pdf_path.read_bytes()
    text = _decode_text_document(content)
    return TextExtraction(
        text=text,
        source="plain-text",
        metadata={"plain_text_length": len(text.strip()), "source_format": pdf_path.suffix.lower().lstrip(".")},
    )


def _extract_with_pymupdf(pdf_path: Path) -> str:
    document = fitz.open(pdf_path)
    try:
        parts = [page.get_text("text").strip() for page in document]
    finally:
        document.close()
    return "\n\n".join(part for part in parts if part).strip()


def _extract_with_pypdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts).strip()


def _decode_text_document(content: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return content.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return ""


def _redact_text_light(text: str) -> str:
    return EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)


def _build_image_prompt(*, request: ExtractionRequest, max_findings: int) -> str:
    return (
        f"Filename: {request.filename}\n"
        f"Uploaded project name: {request.project_name}\n"
        f"Uploaded site name: {request.site_name}\n"
        f"Uploaded trade: {request.trade}\n"
        f"Uploaded address: {request.address or 'Not provided'}\n"
        f"Maximum findings to return: {max_findings}\n\n"
        "Task: extract structured construction inspection data from the attached PDF page images. "
        "Read visible text from the page images. Use report metadata where present. "
        "Use uploaded values only when the report does not state them. "
        "Extract only failed, missing, incomplete, non-compliant, outstanding, or close-out items. "
        "Return findings as [] only when the visible report pages contain no supported issues. "
        "Return JSON only."
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
    normalized["overall_outcome"] = _report_status_or_default(normalized.get("overall_outcome"))
    normalized["units"] = normalized.get("units") if isinstance(normalized.get("units"), list) else []
    findings = normalized.get("findings") if isinstance(normalized.get("findings"), list) else []
    normalized["findings"] = [
        _normalize_finding_payload(finding)
        for finding in findings
        if isinstance(finding, dict) and not _is_passed_finding(finding)
    ]
    normalized["predicted_inspections"] = (
        normalized.get("predicted_inspections") if isinstance(normalized.get("predicted_inspections"), list) else []
    )
    return normalized


def _string_or_default(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _report_status_or_default(value: object) -> str:
    if value in {"Reviewing", "Completed", "In progress"}:
        return str(value)
    text = str(value or "").strip().lower()
    if any(token in text for token in ("complete", "closed", "passed", "no action")):
        return "Completed"
    if any(token in text for token in ("progress", "processing")):
        return "In progress"
    return "Reviewing"


def _is_passed_finding(finding: dict) -> bool:
    text = " ".join(
        str(finding.get(key) or "")
        for key in ("title", "description", "source_quote", "required_fix")
    ).lower()
    pass_markers = (
        "passed",
        "pass -",
        "no action required",
        "complete. no action",
        "installation complete",
        "compliant - no",
    )
    return any(marker in text for marker in pass_markers)


def _normalize_finding_payload(finding: dict) -> dict:
    normalized = dict(finding)
    normalized.setdefault("issue_location", {})
    normalized.setdefault("analytics", {})
    normalized.setdefault("quality", {})
    text = " ".join(
        str(normalized.get(key) or "")
        for key in ("title", "description", "category", "trade", "root_cause", "source_quote")
    ).lower()
    if not str(normalized.get("category") or "").strip() or str(normalized.get("category")).strip() == "General":
        normalized["category"] = _infer_category(text)
    if not str(normalized.get("trade") or "").strip() or str(normalized.get("trade")).strip() == "General":
        normalized["trade"] = _infer_trade(text)
    return normalized


def _infer_category(text: str) -> str:
    if "damper" in text or "breakaway" in text:
        return "Passive Fire - Dampers"
    if "penetration" in text or "collar" in text or "sealant" in text or "annular gap" in text or "fire stopping" in text:
        return "Passive Fire - Penetrations"
    if "plasterboard" in text or "bulkhead" in text:
        return "Passive Fire - Linings"
    if "flashing" in text or "membrane" in text or "threshold" in text:
        return "Envelope"
    return "General"


def _infer_trade(text: str) -> str:
    if "fire" in text or "damper" in text or "penetration" in text or "collar" in text:
        return "Passive Fire"
    if "duct" in text or "mechanical" in text:
        return "Mechanical"
    if "flashing" in text or "membrane" in text:
        return "Envelope"
    return "General"


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
