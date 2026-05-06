"""LLM 客户端封装：OpenAI Chat Completions（兼容 qwen / DeepSeek 等）。"""

from __future__ import annotations

from typing import Any

from openai import OpenAI


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
            "temperature": self.temperature,
        }
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return self._client.chat.completions.create(**kwargs)
