"""OpenAI Responses API 客户端，面向 GPT-6 Astra 等原生模型。"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping, Sequence

from openai import OpenAI

from ..contracts import (
    AttachmentResolver,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelToolCall,
    ModelTurnState,
)
from ..contracts.content import (
    attachment_from_dict,
    coerce_content_blocks,
    content_blocks_to_text,
)
from ..llm_errors import LLMCallError, classify_llm_exception


_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class OpenAIResponsesClient:
    """把 OpenAI Responses API 适配为 CathyAgent 的 ``ModelClient``。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "gpt-6-astra",
        reasoning_effort: str = "low",
        max_output_tokens: int | None = 8192,
        timeout: float = 300.0,
        max_retries: int = 2,
        retry_backoff_initial_sec: float = 1.0,
        retry_backoff_max_sec: float = 20.0,
        store: bool = True,
        parallel_tool_calls: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("LLM api_key 未配置")
        effort = str(reasoning_effort).strip().lower()
        if effort not in _REASONING_EFFORTS:
            supported = ", ".join(sorted(_REASONING_EFFORTS))
            raise ValueError(f"GPT-6 Astra reasoning_effort 必须是: {supported}")
        if max_output_tokens is not None and int(max_output_tokens) <= 0:
            raise ValueError("max_output_tokens 必须是正整数或 None")

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model = model
        self.reasoning_effort = effort
        self.max_output_tokens = (
            int(max_output_tokens) if max_output_tokens is not None else None
        )
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_initial_sec = max(0.0, float(retry_backoff_initial_sec))
        self.retry_backoff_max_sec = max(0.0, float(retry_backoff_max_sec))
        self.store = bool(store)
        self.parallel_tool_calls = bool(parallel_tool_calls)

    def generate(self, request: ModelRequest) -> ModelResponse:
        """执行一次 Responses 请求并返回供应商无关的模型输出。"""
        input_messages = request.messages
        if request.turn_state and request.delta_messages is not None:
            input_messages = request.delta_messages
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": convert_response_input(
                input_messages,
                attachment_resolver=request.attachment_resolver,
            ),
            "reasoning": {"effort": self.reasoning_effort},
            "store": self.store,
            "parallel_tool_calls": self.parallel_tool_calls,
        }
        if self.max_output_tokens is not None:
            kwargs["max_output_tokens"] = self.max_output_tokens
        if request.turn_state:
            kwargs["previous_response_id"] = str(request.turn_state.value)
        if request.tools:
            kwargs["tools"] = [convert_response_tool(tool) for tool in request.tools]
            kwargs["tool_choice"] = convert_tool_choice(request.tool_choice)

        response = self._create_with_retry(kwargs, stage=request.stage)
        return normalize_responses_response(response)

    def _create_with_retry(self, kwargs: dict[str, Any], *, stage: str) -> Any:
        attempts_allowed = self.max_retries + 1
        last_failure = None
        for attempt in range(1, attempts_allowed + 1):
            try:
                return self._client.responses.create(**kwargs)
            except Exception as exc:
                failure = classify_llm_exception(exc, stage=stage, attempts=attempt)
                last_failure = failure
                if not failure.retryable or attempt >= attempts_allowed:
                    raise LLMCallError(failure) from exc
                self._sleep_before_retry(attempt)
        if last_failure is not None:
            raise LLMCallError(last_failure)
        raise RuntimeError("Responses API retry loop ended unexpectedly")

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = self.retry_backoff_initial_sec * (2 ** max(0, attempt - 1))
        delay = min(delay, self.retry_backoff_max_sec)
        if delay > 0:
            time.sleep(delay)


def convert_response_tool(tool: ModelTool) -> dict[str, Any]:
    """把内部工具定义转换为 Responses function schema。"""
    if not tool.name:
        raise ValueError("工具 schema 缺少 name")

    converted: dict[str, Any] = {
        "type": "function",
        "name": tool.name,
        "parameters": dict(tool.input_schema),
    }
    if tool.description:
        converted["description"] = tool.description
    if tool.strict is not None:
        converted["strict"] = tool.strict
    return converted


def convert_tool_choice(tool_choice: Any) -> Any:
    """把 Chat Completions 的命名工具选择转换为 Responses 格式。"""
    if not isinstance(tool_choice, Mapping):
        return tool_choice or "auto"
    function = tool_choice.get("function")
    if isinstance(function, Mapping) and function.get("name"):
        return {"type": "function", "name": str(function["name"])}
    return dict(tool_choice)


def convert_response_input(
    messages: Sequence[Mapping[str, Any]],
    *,
    attachment_resolver: AttachmentResolver | None = None,
) -> list[dict[str, Any]]:
    """把现有 role/content 历史转换成 Responses input items。"""
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            call_id = message.get("tool_call_id") or message.get("call_id")
            if not call_id:
                raise ValueError("tool 消息缺少 tool_call_id/call_id")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(call_id),
                    "output": _stringify_tool_output(message.get("content")),
                }
            )
            continue

        content = _convert_message_content(
            message.get("content"),
            attachment_resolver=attachment_resolver,
        )
        if content not in (None, "", []):
            items.append({"role": role, "content": content})

        if role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                converted_call = _convert_historical_tool_call(tool_call)
                if converted_call is not None:
                    items.append(converted_call)
    return items


def _convert_message_content(
    content: Any,
    *,
    attachment_resolver: AttachmentResolver | None = None,
) -> Any:
    if not isinstance(content, list):
        return content

    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        part_type = part.get("type")
        if part_type in {"text", "input_text"}:
            parts.append({"type": "input_text", "text": str(part.get("text") or "")})
        elif part_type == "image":
            attachment = part.get("attachment")
            if not isinstance(attachment, Mapping):
                raise ValueError("image content block 缺少 attachment")
            image_url = _resolve_attachment_data_url(
                attachment,
                attachment_resolver=attachment_resolver,
            )
            converted_image: dict[str, Any] = {
                "type": "input_image",
                "image_url": image_url,
            }
            if part.get("detail"):
                converted_image["detail"] = str(part["detail"])
            parts.append(converted_image)
        elif part_type in {"image_url", "input_image"}:
            image = part.get("image_url")
            image_url = image.get("url") if isinstance(image, Mapping) else image
            image_url = image_url or part.get("url")
            if image_url:
                converted = {"type": "input_image", "image_url": str(image_url)}
                detail = part.get("detail")
                if isinstance(image, Mapping):
                    detail = image.get("detail", detail)
                if detail:
                    converted["detail"] = str(detail)
                parts.append(converted)
        elif part_type == "json":
            body = json.dumps(
                part.get("value"),
                ensure_ascii=False,
                sort_keys=True,
            )
            label = str(part.get("label") or "").strip()
            parts.append(
                {
                    "type": "input_text",
                    "text": f"{label}: {body}" if label else body,
                }
            )
        elif part_type in {"file", "input_file"}:
            converted_file = {"type": "input_file"}
            attachment = part.get("attachment")
            if isinstance(attachment, Mapping):
                converted_file["file_data"] = _resolve_attachment_data_url(
                    attachment,
                    attachment_resolver=attachment_resolver,
                )
                if attachment.get("filename"):
                    converted_file["filename"] = str(attachment["filename"])
            for key in ("file_id", "file_data", "filename"):
                if part.get(key) is not None:
                    converted_file[key] = part[key]
            parts.append(converted_file)
        else:
            parts.append(dict(part))
    return parts


def _resolve_attachment_data_url(
    value: Mapping[str, Any],
    *,
    attachment_resolver: AttachmentResolver | None,
) -> str:
    if attachment_resolver is None:
        raise ValueError("多模态请求包含附件，但未配置 attachment_resolver")
    return attachment_resolver.data_url(attachment_from_dict(value))


def _convert_historical_tool_call(tool_call: Any) -> dict[str, Any] | None:
    if not isinstance(tool_call, Mapping):
        return None
    function = tool_call.get("function")
    source = function if isinstance(function, Mapping) else tool_call
    name = source.get("name")
    if not name:
        return None
    arguments = source.get("arguments") or "{}"
    if isinstance(arguments, Mapping):
        arguments = json.dumps(dict(arguments), ensure_ascii=False)
    return {
        "type": "function_call",
        "call_id": str(tool_call.get("call_id") or tool_call.get("id") or ""),
        "name": str(name),
        "arguments": str(arguments),
    }


def _stringify_tool_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list) and all(
        isinstance(part, Mapping)
        and part.get("type") in {"text", "image", "file", "json"}
        for part in output
    ):
        return content_blocks_to_text(coerce_content_blocks(output))
    return json.dumps(output, ensure_ascii=False)


def normalize_responses_response(response: Any) -> ModelResponse:
    """把 Responses API 输出转换成稳定模型契约。"""
    tool_calls: list[ModelToolCall] = []
    text_parts: list[str] = []
    reasoning_parts: list[str] = []

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        text_parts.append(output_text)

    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            raw_arguments = getattr(item, "arguments", None) or "{}"
            raw_arguments_text, parsed_arguments = _parse_arguments(raw_arguments)
            call_id = getattr(item, "call_id", None) or getattr(item, "id", None)
            tool_calls.append(
                ModelToolCall(
                    id=str(call_id or ""),
                    name=str(getattr(item, "name", "")),
                    arguments=parsed_arguments,
                    raw_arguments=raw_arguments_text,
                )
            )
        elif item_type == "message" and not text_parts:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "output_text":
                    text = getattr(part, "text", None)
                    if text:
                        text_parts.append(str(text))
        elif item_type == "reasoning":
            for summary in getattr(item, "summary", None) or []:
                text = getattr(summary, "text", None)
                if text:
                    reasoning_parts.append(str(text))

    return ModelResponse(
        text="".join(text_parts),
        tool_calls=tuple(tool_calls),
        reasoning="\n".join(reasoning_parts) or None,
        next_turn_state=(
            ModelTurnState(str(response.id))
            if getattr(response, "id", None)
            else None
        ),
        raw=response,
    )


def _parse_arguments(raw_arguments: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(raw_arguments, Mapping):
        parsed_arguments = dict(raw_arguments)
        return json.dumps(parsed_arguments, ensure_ascii=False), parsed_arguments
    raw_arguments_text = str(raw_arguments)
    try:
        parsed = json.loads(raw_arguments_text)
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    return raw_arguments_text, parsed if isinstance(parsed, dict) else {}
