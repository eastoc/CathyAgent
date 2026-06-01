"""LLM 客户端封装：OpenAI Chat Completions（兼容 DeepSeek / qwen 等 OpenAI 兼容端点）。"""

from __future__ import annotations

from typing import Any

from openai import OpenAI


def _normalize_model(model: str) -> str:
    return (model or "").strip().lower()


def uses_max_completion_tokens(model: str) -> bool:
    """OpenAI 新模型 / reasoning 模型使用 max_completion_tokens。"""
    m = _normalize_model(model)
    return (
        m.startswith("gpt-5")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
    )


def supports_configurable_temperature(model: str) -> bool:
    """部分 OpenAI 新模型只支持默认 temperature，不应显式传入。"""
    m = _normalize_model(model)
    return not (
        m.startswith("gpt-5")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
    )


def apply_token_limit(kwargs: dict[str, Any], *, model: str, max_tokens: int | None) -> None:
    if not max_tokens:
        return
    key = "max_completion_tokens" if uses_max_completion_tokens(model) else "max_tokens"
    kwargs[key] = max_tokens


def apply_temperature(kwargs: dict[str, Any], *, model: str, temperature: float | None) -> None:
    if temperature is None or not supports_configurable_temperature(model):
        return
    kwargs["temperature"] = temperature


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
        apply_token_limit(kwargs, model=self.model, max_tokens=self.max_tokens)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return self._client.chat.completions.create(**kwargs)
