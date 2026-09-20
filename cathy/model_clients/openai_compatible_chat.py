"""OpenAI-compatible Chat Completions 客户端。

用于 Qwen、DeepSeek，以及暴露 ``/v1/chat/completions`` 的 vLLM、SGLang
等服务。该模块只负责线协议适配，向 CathyAgent 暴露统一模型协议。
"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from openai import OpenAI

from ..contracts import ModelRequest, ModelResponse, ModelToolCall
from ..llm_errors import LLMCallError, classify_llm_exception


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
    """按 Chat Completions 模型族写入正确的 token 限制字段。"""
    if not max_tokens:
        return
    key = "max_completion_tokens" if uses_max_completion_tokens(model) else "max_tokens"
    kwargs[key] = max_tokens


def apply_temperature(kwargs: dict[str, Any], *, model: str, temperature: float | None) -> None:
    """仅在目标模型支持时写入 temperature。"""
    if temperature is None or not supports_configurable_temperature(model):
        return
    kwargs["temperature"] = temperature


class OpenAICompatibleChatClient:
    """把 OpenAI-compatible Chat Completions 适配为 ``ModelClient``。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = 4096,
        timeout: float = 60.0,
        max_retries: int = 2,
        retry_backoff_initial_sec: float = 1.0,
        retry_backoff_max_sec: float = 20.0,
        extra_body: Mapping[str, Any] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("LLM api_key 未配置")
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_initial_sec = max(0.0, float(retry_backoff_initial_sec))
        self.retry_backoff_max_sec = max(0.0, float(retry_backoff_max_sec))
        self.extra_body = dict(extra_body or {})

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = "auto",
        stage: str = "llm.chat",
    ) -> Any:
        """兼容旧调用方的 Chat Completions 原始响应接口。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        apply_temperature(kwargs, model=self.model, temperature=self.temperature)
        apply_token_limit(kwargs, model=self.model, max_tokens=self.max_tokens)
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return self._create_with_retry(kwargs, stage=stage)

    def generate(self, request: ModelRequest) -> ModelResponse:
        """执行请求并把 Chat Completions 响应归一化为模型协议。"""
        response = self.chat(
            [dict(message) for message in request.messages],
            tools=(
                [dict(tool) for tool in request.tools]
                if request.tools is not None
                else None
            ),
            tool_choice=request.tool_choice,
            stage=request.stage,
        )
        return normalize_chat_response(response)

    def _create_with_retry(self, kwargs: dict[str, Any], *, stage: str) -> Any:
        attempts_allowed = self.max_retries + 1
        last_failure = None
        for attempt in range(1, attempts_allowed + 1):
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                failure = classify_llm_exception(exc, stage=stage, attempts=attempt)
                last_failure = failure
                if not failure.retryable or attempt >= attempts_allowed:
                    raise LLMCallError(failure) from exc
                self._sleep_before_retry(attempt)
        if last_failure is not None:
            raise LLMCallError(last_failure)
        raise RuntimeError("LLM call retry loop ended unexpectedly")

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self.retry_backoff_initial_sec * (2 ** max(0, attempt - 1))
        delay = min(delay, self.retry_backoff_max_sec)
        if delay > 0:
            time.sleep(delay)


def normalize_chat_response(response: Any) -> ModelResponse:
    """把 OpenAI-compatible Chat Completions 输出转换成稳定契约。"""
    message = response.choices[0].message
    tool_calls: list[ModelToolCall] = []

    for tool_call in getattr(message, "tool_calls", None) or []:
        function = tool_call.function
        raw_arguments = getattr(function, "arguments", None) or "{}"
        if isinstance(raw_arguments, Mapping):
            parsed_arguments = dict(raw_arguments)
            raw_arguments_text = json.dumps(parsed_arguments, ensure_ascii=False)
        else:
            raw_arguments_text = str(raw_arguments)
            try:
                parsed = json.loads(raw_arguments_text)
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            parsed_arguments = parsed if isinstance(parsed, dict) else {}

        tool_calls.append(
            ModelToolCall(
                id=str(tool_call.id),
                name=str(function.name),
                arguments=parsed_arguments,
                raw_arguments=raw_arguments_text,
            )
        )

    return ModelResponse(
        text=str(getattr(message, "content", None) or ""),
        tool_calls=tuple(tool_calls),
        reasoning_content=getattr(message, "reasoning_content", None),
        raw=response,
    )
