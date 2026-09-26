"""OpenAI-compatible Chat Completions 客户端。

用于 Qwen、DeepSeek，以及暴露 ``/v1/chat/completions`` 的 vLLM、SGLang
等服务。该模块只负责线协议适配，向 CathyAgent 暴露统一模型协议。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any, AsyncIterator, Mapping, Sequence

from openai import AsyncOpenAI, OpenAI

from ..contracts import (
    AttachmentResolver,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelToolCall,
)
from ..contracts.content import attachment_from_dict
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
        self._async_client: AsyncOpenAI | None = None
        self._async_client_options = {
            "api_key": api_key,
            "base_url": base_url,
            "timeout": timeout,
        }
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
        kwargs = self._build_request_kwargs(request)
        response = self._create_with_retry(kwargs, stage=request.stage)
        return normalize_chat_response(response)

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        """原生异步执行 Chat Completions 请求。"""
        kwargs = self._build_request_kwargs(request)
        response = await self._acreate_with_retry(kwargs, stage=request.stage)
        return normalize_chat_response(response)

    async def astream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """流式执行 Chat Completions 并归一化文本及工具调用增量。"""
        kwargs = self._build_request_kwargs(request)
        kwargs["stream"] = True
        stream = await self._acreate_with_retry(kwargs, stage=request.stage)
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        last_chunk: Any = None

        yield ModelEvent(type="response_started")
        try:
            async for chunk in stream:
                last_chunk = chunk
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue

                content = getattr(delta, "content", None)
                if content:
                    text = str(content)
                    text_parts.append(text)
                    yield ModelEvent(type="text_delta", text=text, raw=chunk)

                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_text = str(reasoning)
                    reasoning_parts.append(reasoning_text)
                    yield ModelEvent(
                        type="reasoning_delta",
                        text=reasoning_text,
                        raw=chunk,
                    )

                for tool_delta in getattr(delta, "tool_calls", None) or []:
                    index = int(getattr(tool_delta, "index", 0) or 0)
                    state = tool_calls.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    call_id = getattr(tool_delta, "id", None)
                    if call_id:
                        state["id"] = str(call_id)
                    function = getattr(tool_delta, "function", None)
                    name_delta = getattr(function, "name", None)
                    arguments_delta = getattr(function, "arguments", None)
                    if name_delta:
                        state["name"] += str(name_delta)
                    if arguments_delta:
                        state["arguments"] += str(arguments_delta)
                    yield ModelEvent(
                        type="tool_call_delta",
                        text=str(arguments_delta or ""),
                        metadata={
                            "index": index,
                            "call_id": state["id"],
                            "name": state["name"],
                        },
                        raw=chunk,
                    )
        except asyncio.CancelledError:
            raise
        except LLMCallError:
            raise
        except Exception as exc:
            failure = classify_llm_exception(exc, stage=request.stage, attempts=1)
            raise LLMCallError(failure) from exc
        finally:
            await _close_async_stream(stream)

        response = ModelResponse(
            text="".join(text_parts),
            tool_calls=tuple(
                _model_tool_call_from_stream(state)
                for _, state in sorted(tool_calls.items())
            ),
            reasoning="".join(reasoning_parts) or None,
            raw=last_chunk,
        )
        yield ModelEvent(type="response_completed", response=response)

    def _build_request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": convert_chat_messages(
                request.messages,
                attachment_resolver=request.attachment_resolver,
            ),
        }
        apply_temperature(
            kwargs,
            model=self.model,
            temperature=self.temperature,
        )
        apply_token_limit(
            kwargs,
            model=self.model,
            max_tokens=self.max_tokens,
        )
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        if request.tools:
            kwargs["tools"] = [convert_chat_tool(tool) for tool in request.tools]
            kwargs["tool_choice"] = request.tool_choice or "auto"
        return kwargs

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

    async def _acreate_with_retry(
        self,
        kwargs: dict[str, Any],
        *,
        stage: str,
    ) -> Any:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(**self._async_client_options)
        attempts_allowed = self.max_retries + 1
        last_failure = None
        for attempt in range(1, attempts_allowed + 1):
            try:
                return await self._async_client.chat.completions.create(**kwargs)
            except Exception as exc:
                failure = classify_llm_exception(exc, stage=stage, attempts=attempt)
                last_failure = failure
                if not failure.retryable or attempt >= attempts_allowed:
                    raise LLMCallError(failure) from exc
                await self._asleep_before_retry(attempt)
        if last_failure is not None:
            raise LLMCallError(last_failure)
        raise RuntimeError("Async LLM call retry loop ended unexpectedly")

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self.retry_backoff_initial_sec * (2 ** max(0, attempt - 1))
        delay = min(delay, self.retry_backoff_max_sec)
        if delay > 0:
            time.sleep(delay)

    async def _asleep_before_retry(self, attempt: int) -> None:
        delay = self.retry_backoff_initial_sec * (2 ** max(0, attempt - 1))
        delay = min(delay, self.retry_backoff_max_sec)
        if delay > 0:
            await asyncio.sleep(delay)

    def close(self) -> None:
        """关闭同步 HTTP 客户端。"""
        self._client.close()

    async def aclose(self) -> None:
        """关闭已经创建的异步 HTTP 客户端。"""
        if self._async_client is not None:
            await self._async_client.close()
            self._async_client = None


def _model_tool_call_from_stream(state: Mapping[str, str]) -> ModelToolCall:
    raw_arguments = state.get("arguments") or "{}"
    try:
        parsed = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    return ModelToolCall(
        id=state.get("id") or "",
        name=state.get("name") or "",
        arguments=parsed if isinstance(parsed, dict) else {},
        raw_arguments=raw_arguments,
    )


async def _close_async_stream(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def convert_chat_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    attachment_resolver: AttachmentResolver | None = None,
) -> list[dict[str, Any]]:
    """把内部模型消息转换为 Chat Completions 消息。"""
    converted: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {
            "role": str(message.get("role") or "user"),
            "content": _convert_chat_content(
                message.get("content"),
                attachment_resolver=attachment_resolver,
            ),
        }
        if message.get("tool_call_id"):
            item["tool_call_id"] = str(message["tool_call_id"])
        if message.get("name"):
            item["name"] = str(message["name"])
        if message.get("reasoning"):
            item["reasoning_content"] = str(message["reasoning"])
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            item["tool_calls"] = [_convert_chat_tool_call(call) for call in tool_calls]
        converted.append(item)
    return converted


def _convert_chat_content(
    content: Any,
    *,
    attachment_resolver: AttachmentResolver | None,
) -> Any:
    if not isinstance(content, list):
        return content or ""

    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        part_type = str(part.get("type") or "")
        if part_type in {"text", "input_text"}:
            parts.append({"type": "text", "text": str(part.get("text") or "")})
        elif part_type == "image":
            attachment = part.get("attachment")
            if not isinstance(attachment, Mapping):
                raise ValueError("image content block 缺少 attachment")
            if attachment_resolver is None:
                raise ValueError("多模态请求包含附件，但未配置 attachment_resolver")
            image_url: dict[str, Any] = {
                "url": attachment_resolver.data_url(attachment_from_dict(attachment))
            }
            if part.get("detail"):
                image_url["detail"] = str(part["detail"])
            parts.append({"type": "image_url", "image_url": image_url})
        elif part_type == "json":
            body = json.dumps(part.get("value"), ensure_ascii=False, sort_keys=True)
            label = str(part.get("label") or "").strip()
            parts.append(
                {"type": "text", "text": f"{label}: {body}" if label else body}
            )
        elif part_type == "file" and isinstance(part.get("attachment"), Mapping):
            raise ValueError("通用 Chat Completions 适配器不支持 file content block")
        else:
            parts.append(dict(part))
    if all(part.get("type") == "text" for part in parts):
        return "\n".join(str(part.get("text") or "") for part in parts)
    return parts


def convert_chat_tool(tool: ModelTool) -> dict[str, Any]:
    """把内部工具定义转换为 Chat Completions function schema。"""
    function: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "parameters": dict(tool.input_schema),
    }
    if tool.strict is not None:
        function["strict"] = tool.strict
    return {"type": "function", "function": function}


def _convert_chat_tool_call(tool_call: Any) -> dict[str, Any]:
    if not isinstance(tool_call, Mapping):
        raise TypeError("内部 tool_call 必须是映射")
    function = tool_call.get("function")
    source = function if isinstance(function, Mapping) else tool_call
    arguments = source.get("arguments")
    if arguments is None:
        arguments = source.get("raw_arguments") or "{}"
    if isinstance(arguments, Mapping):
        arguments = json.dumps(dict(arguments), ensure_ascii=False)
    return {
        "id": str(tool_call.get("id") or tool_call.get("call_id") or ""),
        "type": "function",
        "function": {
            "name": str(source.get("name") or ""),
            "arguments": str(arguments),
        },
    }


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
        reasoning=getattr(message, "reasoning_content", None),
        raw=response,
    )
