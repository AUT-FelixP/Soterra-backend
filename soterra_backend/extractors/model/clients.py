from __future__ import annotations

import json
import re
from typing import Any, Protocol

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


class LocalTransformersExtractionClient:
    _models: dict[tuple[str, str | None, str | None, bool], tuple[Any, Any]] = {}

    def __init__(
        self,
        *,
        device_map: str | None,
        torch_dtype: str | None,
        max_new_tokens: int,
        trust_remote_code: bool,
    ) -> None:
        self.device_map = device_map or "auto"
        self.torch_dtype = torch_dtype
        self.max_new_tokens = max_new_tokens
        self.trust_remote_code = trust_remote_code

    def extract_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict,
        model_id: str,
        temperature: float,
    ) -> dict:
        tokenizer, model = self._load_model(model_id)
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"{user_prompt}\n\n"
                    "Return only the JSON object. No markdown, no explanation. "
                    "Do not return or copy the JSON schema. Fill the object with values extracted from the document. "
                    "The JSON object must match this schema:\n"
                    f"{json.dumps(schema)[:12000]}"
                ),
            },
        ]
        inputs = tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device)
        generate_kwargs = {"max_new_tokens": self.max_new_tokens, "do_sample": temperature > 0}
        if temperature > 0:
            generate_kwargs["temperature"] = temperature
        outputs = model.generate(**inputs, **generate_kwargs)
        generated = tokenizer.decode(outputs[0][inputs["input_ids"].shape[-1] :], skip_special_tokens=True)
        return _loads_json_object(generated)

    def _load_model(self, model_id: str) -> tuple[Any, Any]:
        key = (model_id, self.device_map, self.torch_dtype, self.trust_remote_code)
        if key in self.__class__._models:
            return self.__class__._models[key]
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "transformers is required for local model extraction. Install the local model dependencies."
            ) from exc
        try:
            from transformers import AutoModelForMultimodalLM  # type: ignore[attr-defined]
        except ImportError:
            AutoModelForMultimodalLM = AutoModelForCausalLM

        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=self.trust_remote_code)
        kwargs = {
            "trust_remote_code": self.trust_remote_code,
        }
        if self.device_map:
            kwargs["device_map"] = self.device_map
        if self.torch_dtype:
            kwargs["dtype"] = _torch_dtype(self.torch_dtype)
        model = AutoModelForMultimodalLM.from_pretrained(model_id, **kwargs).eval()
        loaded = (tokenizer, model)
        self.__class__._models[key] = loaded
        return loaded


def build_model_extraction_client(settings: Settings, config: ModelExtractionConfig) -> ModelExtractionClient:
    provider = config.provider.strip().lower()
    if provider in {"huggingface", "hf_inference", "huggingface_inference"}:
        import os

        return HuggingFaceInferenceExtractionClient(
            api_key=os.getenv("HF_TOKEN"),
            timeout_seconds=settings.model_extraction_timeout_seconds,
            missing_key_message="HF_TOKEN is required for Hugging Face model extraction.",
        )
    if provider in {"local_transformers", "transformers_local", "local-hf", "local_hf"}:
        import os

        return LocalTransformersExtractionClient(
            device_map=os.getenv("SOTERRA_LOCAL_MODEL_DEVICE_MAP", "auto"),
            torch_dtype=os.getenv("SOTERRA_LOCAL_MODEL_TORCH_DTYPE") or None,
            max_new_tokens=int(os.getenv("SOTERRA_LOCAL_MODEL_MAX_NEW_TOKENS", "4096")),
            trust_remote_code=os.getenv("SOTERRA_LOCAL_MODEL_TRUST_REMOTE_CODE", "true").strip().lower()
            in {"1", "true", "yes", "on"},
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


def _torch_dtype(value: str | None) -> object:
    if not value or value == "auto":
        return "auto"
    try:
        import torch
    except ModuleNotFoundError:
        return value
    normalized = value.strip().lower()
    mapping = {
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float16": torch.float16,
        "fp16": torch.float16,
        "float32": torch.float32,
        "fp32": torch.float32,
    }
    return mapping.get(normalized, value)
