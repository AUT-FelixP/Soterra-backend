from __future__ import annotations

import json
import re
from typing import Protocol

from ...config import ModelExtractionConfig, Settings
from ...huggingface import normalize_inference_model_id


class ModelExtractionClient(Protocol):
    def extract_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        model_id: str,
        temperature: float,
    ) -> dict:
        ...


class HuggingFaceInferenceExtractionClient:
    def __init__(self, *, api_key: str | None, timeout_seconds: int, missing_key_message: str) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.missing_key_message = missing_key_message

    def extract_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        model_id: str,
        temperature: float,
    ) -> dict:
        if not self.api_key:
            raise RuntimeError(self.missing_key_message)
        try:
            from huggingface_hub import InferenceClient
        except ModuleNotFoundError as exc:
            raise RuntimeError("huggingface_hub is required for Hugging Face model extraction.") from exc

        client = InferenceClient(api_key=self.api_key, timeout=self.timeout_seconds, provider="auto")
        response = client.chat.completions.create(
            model=normalize_inference_model_id(model_id),
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"{user_prompt}\n\n"
                        "Return one JSON object only. Do not wrap it in markdown. "
                        "Do not return or copy the JSON schema. Fill the object with values extracted from the document. "
                        "The object must match this JSON schema:\n"
                        f"{json.dumps(schema)[:16000]}"
                    ),
                },
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        return _loads_json_object(content)


def build_model_extraction_client(settings: Settings, config: ModelExtractionConfig) -> ModelExtractionClient:
    provider = config.provider.strip().lower()
    if provider in {"huggingface", "hf_inference", "huggingface_inference"}:
        import os

        return HuggingFaceInferenceExtractionClient(
            api_key=os.getenv("HF_TOKEN"),
            timeout_seconds=settings.model_extraction_timeout_seconds,
            missing_key_message="HF_TOKEN is required for Hugging Face model extraction.",
        )
    raise RuntimeError(f"Unsupported model extraction provider: {config.provider}")


def _loads_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise

