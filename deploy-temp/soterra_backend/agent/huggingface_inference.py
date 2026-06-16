from __future__ import annotations

from typing import Any

from smolagents import ChatMessage, MessageRole, Model
from smolagents.models import ChatMessageToolCall, ChatMessageToolCallFunction

from ..huggingface import normalize_inference_model_id


class HuggingFaceInferenceChatModel(Model):
    def __init__(
        self,
        *,
        model_id: str,
        api_key: str,
        timeout_seconds: int,
        max_tokens: int,
        temperature: float,
    ) -> None:
        super().__init__(model_id=model_id, max_tokens=max_tokens)
        try:
            from huggingface_hub import InferenceClient
        except ModuleNotFoundError as exc:
            raise RuntimeError("huggingface_hub is required for Hugging Face agent chat.") from exc
        self.inference_model_id = normalize_inference_model_id(model_id)
        self.client = InferenceClient(api_key=api_key, timeout=timeout_seconds, provider="auto")
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Any] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        completion_kwargs = self._prepare_completion_kwargs(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            **kwargs,
        )
        clean_messages = completion_kwargs.pop("messages")
        response = self.client.chat.completions.create(
            model=self.inference_model_id,
            messages=clean_messages,
            temperature=self.temperature,
            max_tokens=int(kwargs.get("max_tokens") or kwargs.get("max_new_tokens") or self.max_tokens),
            stop=completion_kwargs.pop("stop", None),
            tools=completion_kwargs.pop("tools", None),
            tool_choice=completion_kwargs.pop("tool_choice", None),
            response_format=completion_kwargs.pop("response_format", None),
            **completion_kwargs,
        )
        message = response.choices[0].message
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=getattr(message, "content", None) or "",
            tool_calls=_to_smolagents_tool_calls(getattr(message, "tool_calls", None)),
            raw=response,
        )


def _to_smolagents_tool_calls(tool_calls: Any) -> list[ChatMessageToolCall] | None:
    if not tool_calls:
        return None
    converted: list[ChatMessageToolCall] = []
    for index, tool_call in enumerate(tool_calls):
        function = getattr(tool_call, "function", None)
        name = getattr(function, "name", None) if function else None
        arguments = getattr(function, "arguments", None) if function else None
        if not name:
            continue
        converted.append(
            ChatMessageToolCall(
                id=getattr(tool_call, "id", None) or f"hf-tool-call-{index}",
                type=getattr(tool_call, "type", None) or "function",
                function=ChatMessageToolCallFunction(name=name, arguments=arguments),
            )
        )
    return converted or None
