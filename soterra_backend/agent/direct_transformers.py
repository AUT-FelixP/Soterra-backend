from __future__ import annotations

from typing import Any

from smolagents import ChatMessage, MessageRole, Model
from smolagents.monitoring import TokenUsage


class DirectTransformersChatModel(Model):
    _models: dict[tuple[str, str | None, str | None, bool], tuple[Any, Any]] = {}

    def __init__(
        self,
        *,
        model_id: str,
        device_map: str | None,
        torch_dtype: str | None,
        trust_remote_code: bool,
        max_new_tokens: int,
        temperature: float,
    ) -> None:
        super().__init__(flatten_messages_as_text=True, model_id=model_id, max_new_tokens=max_new_tokens)
        self.device_map = device_map
        self.torch_dtype = torch_dtype
        self.trust_remote_code = trust_remote_code
        self.temperature = temperature
        self.tokenizer, self.model = self._load_model(model_id)

    def generate(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Any] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        if response_format is not None:
            raise ValueError("Direct Transformers chat does not support structured response_format.")
        completion_kwargs = self._prepare_completion_kwargs(
            messages=_normalize_messages(messages),
            stop_sequences=stop_sequences,
            tools_to_call_from=tools_to_call_from,
            tool_choice=None,
            **kwargs,
        )
        clean_messages = completion_kwargs.pop("messages")
        stop_sequences = completion_kwargs.pop("stop", None)
        tools = completion_kwargs.pop("tools", None)
        completion_kwargs.pop("tool_choice", None)
        max_new_tokens = (
            kwargs.get("max_new_tokens")
            or kwargs.get("max_tokens")
            or self.kwargs.get("max_new_tokens")
            or self.kwargs.get("max_tokens")
            or 1200
        )

        inputs = self.tokenizer.apply_chat_template(
            clean_messages,
            tools=tools,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)
        generate_kwargs: dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": self.temperature > 0,
            **completion_kwargs,
        }
        if self.temperature > 0:
            generate_kwargs["temperature"] = self.temperature
        outputs = self.model.generate(**inputs, **generate_kwargs)
        input_token_count = inputs["input_ids"].shape[-1]
        generated_tokens = outputs[0][input_token_count:]
        output_text = self.tokenizer.decode(generated_tokens, skip_special_tokens=True)
        if stop_sequences:
            output_text = _remove_after_stop_sequences(output_text, stop_sequences)
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=output_text,
            raw={"out": output_text},
            token_usage=TokenUsage(input_tokens=input_token_count, output_tokens=len(generated_tokens)),
        )

    def _load_model(self, model_id: str) -> tuple[Any, Any]:
        key = (model_id, self.device_map, self.torch_dtype, self.trust_remote_code)
        if key in self.__class__._models:
            return self.__class__._models[key]
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ModuleNotFoundError as exc:
            raise RuntimeError("transformers is required for local agent chat.") from exc
        try:
            from transformers import AutoModelForMultimodalLM  # type: ignore[attr-defined]
        except ImportError:
            AutoModelForMultimodalLM = AutoModelForCausalLM

        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=self.trust_remote_code)
        model_kwargs: dict[str, Any] = {"trust_remote_code": self.trust_remote_code}
        if self.device_map:
            model_kwargs["device_map"] = self.device_map
        if self.torch_dtype:
            model_kwargs["dtype"] = _torch_dtype(self.torch_dtype)
        model = AutoModelForMultimodalLM.from_pretrained(model_id, **model_kwargs).eval()
        loaded = (tokenizer, model)
        self.__class__._models[key] = loaded
        return loaded


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


def _normalize_messages(messages: list[ChatMessage | dict]) -> list[ChatMessage | dict]:
    normalized: list[ChatMessage | dict] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            normalized.append(message)
            continue
        normalized.append(
            {
                **message,
                "content": [{"type": "text", "text": message["content"]}],
            }
        )
    return normalized


def _remove_after_stop_sequences(text: str, stop_sequences: list[str]) -> str:
    stop_indexes = [text.find(stop) for stop in stop_sequences if stop in text]
    if not stop_indexes:
        return text
    return text[: min(stop_indexes)]
