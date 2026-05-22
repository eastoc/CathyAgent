"""LLM 客户端封装：OpenAI Chat Completions（兼容 DeepSeek / qwen 等 OpenAI 兼容端点）。"""

from __future__ import annotations

from typing import Any

from openai import OpenAI

# GPT-5 / o-series 等新模型的 Chat Completions 参数约束不同：
# - 使用 max_completion_tokens 而非 max_tokens
# - 仅支持默认 temperature（传非 1 会 400， safest 是直接不传）
_NEW_OPENAI_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _normalized_model_name(model: str) -> str:
    return model.strip().lower()


def is_new_openai_model(model: str) -> bool:
    """Return True for GPT-5 / o-series models with stricter API parameters."""
    name = _normalized_model_name(model)
    return any(name.startswith(prefix) for prefix in _NEW_OPENAI_MODEL_PREFIXES)


def uses_max_completion_tokens(model: str) -> bool:
    """Return True when the model expects ``max_completion_tokens``."""
    return is_new_openai_model(model)


def supports_configurable_temperature(model: str) -> bool:
    """Return True when ``temperature`` can be set to non-default values."""
    return not is_new_openai_model(model)


def apply_temperature(kwargs: dict[str, Any], *, model: str, temperature: float) -> None:
    """Attach temperature only when the target model supports custom values."""
    if supports_configurable_temperature(model):
        kwargs["temperature"] = temperature


def apply_token_limit(kwargs: dict[str, Any], *, model: str, max_tokens: int | None) -> None:
    """Attach the correct token limit field for the target model."""
    if not max_tokens:
        return
    if uses_max_completion_tokens(model):
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["max_tokens"] = max_tokens


class LLMClient:
    """对 openai.OpenAI 的薄封装，统一注入 model / 默认参数。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = 4096,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("LLM api_key 未配置")
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = "auto",
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        apply_temperature(kwargs, model=self.model, temperature=self.temperature)
        if self.max_tokens:
            apply_token_limit(kwargs, model=self.model, max_tokens=self.max_tokens)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return self._client.chat.completions.create(**kwargs)
