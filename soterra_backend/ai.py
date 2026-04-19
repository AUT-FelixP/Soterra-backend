from __future__ import annotations

from pathlib import Path

from .config import Settings
from .models import ExtractionResult
from .text_extraction import render_page_images


def extract_with_openai(
    settings: Settings,
    pdf_path: Path,
    filename: str,
    extracted_text: str,
) -> ExtractionResult:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required when SOTERRA_EXTRACTOR_MODE=openai.")

    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The openai package is not installed. Run the Python dependency install step first."
        ) from exc

    client = OpenAI(api_key=settings.openai_api_key)

    system_prompt = (
        "Extract construction inspection metadata and action-required defects from the provided report. "
        "Return only valid JSON matching the requested schema. Do not invent missing facts. "
        "If a report says an item is acceptable or looks okay, do not create a finding for it."
    )

    user_content: list[dict] = [
        {
            "type": "input_text",
            "text": (
                f"Filename: {filename}\n"
                "Use the extracted text below when it is readable. If it looks incomplete, also read the attached page images.\n\n"
                f"{extracted_text[:48000]}"
            ),
        }
    ]

    if len(extracted_text.strip()) < 400:
        for encoded_page in render_page_images(pdf_path, max_pages=settings.openai_max_pages):
            user_content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{encoded_page}",
                    "detail": "high",
                }
            )

    response = client.responses.create(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "inspection_extraction",
                "schema": _openai_strict_schema(ExtractionResult.model_json_schema()),
                "strict": True,
            }
        },
    )

    return ExtractionResult.model_validate_json(response.output_text)


def _openai_strict_schema(schema: dict) -> dict:
    if isinstance(schema, dict):
        normalized = {key: _openai_strict_schema(value) for key, value in schema.items()}
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties.keys())
            normalized["additionalProperties"] = False
        return normalized

    if isinstance(schema, list):
        return [_openai_strict_schema(item) for item in schema]

    return schema
