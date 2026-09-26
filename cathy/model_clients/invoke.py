"""统一模型调用入口，并为旧式测试客户端保留临时兼容。"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, AsyncIterator, Sequence

from ..contracts import (
    AttachmentResolver,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelTurnState,
)
from .openai_compatible_chat import (
    convert_chat_messages,
    convert_chat_tool,
    normalize_chat_response,
)


def generate_model_response(
    llm: Any,
    messages: Sequence[dict[str, Any]],
    *,
    tools: Sequence[ModelTool] | None = None,
    tool_choice: str | None = "auto",
    stage: str,
    turn_state: ModelTurnState | None = None,
    delta_messages: Sequence[dict[str, Any]] | None = None,
    attachment_resolver: AttachmentResolver | None = None,
) -> ModelResponse:
    """调用统一 ``ModelClient``；旧 ``chat`` 对象只用于迁移期兼容。"""
    generate = getattr(llm, "generate", None)
    if callable(generate):
        return generate(
            ModelRequest(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                stage=stage,
                turn_state=turn_state,
                delta_messages=delta_messages,
                attachment_resolver=attachment_resolver,
            )
        )

    chat = getattr(llm, "chat", None)
    if not callable(chat):
        raise TypeError("模型客户端必须实现 generate(ModelRequest)")
    try:
        raw_response = chat(
            convert_chat_messages(messages, attachment_resolver=attachment_resolver),
            tools=[convert_chat_tool(tool) for tool in tools] if tools else None,
            tool_choice=tool_choice,
            stage=stage,
        )
    except TypeError as exc:
        if "stage" not in str(exc):
            raise
        raw_response = chat(
            convert_chat_messages(messages, attachment_resolver=attachment_resolver),
            tools=[convert_chat_tool(tool) for tool in tools] if tools else None,
            tool_choice=tool_choice,
        )
    return normalize_chat_response(raw_response)


async def agenerate_model_response(
    llm: Any,
    messages: Sequence[dict[str, Any]],
    *,
    tools: Sequence[ModelTool] | None = None,
    tool_choice: str | None = "auto",
    stage: str,
    turn_state: ModelTurnState | None = None,
    delta_messages: Sequence[dict[str, Any]] | None = None,
    attachment_resolver: AttachmentResolver | None = None,
) -> ModelResponse:
    """优先调用原生 agenerate，同步客户端在线程池中兼容。

    兼容路径不能强制终止已经开始的同步网络请求，只用于迁移；provider
    adapter 应逐步实现原生异步接口。
    """

    request = ModelRequest(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        stage=stage,
        turn_state=turn_state,
        delta_messages=delta_messages,
        attachment_resolver=attachment_resolver,
    )
    agenerate = getattr(llm, "agenerate", None)
    if callable(agenerate):
        return await agenerate(request)

    loop = asyncio.get_running_loop()
    call = functools.partial(
        generate_model_response,
        llm,
        messages,
        tools=tools,
        tool_choice=tool_choice,
        stage=stage,
        turn_state=turn_state,
        delta_messages=delta_messages,
        attachment_resolver=attachment_resolver,
    )
    return await loop.run_in_executor(None, call)


async def astream_model_response(
    llm: Any,
    messages: Sequence[dict[str, Any]],
    *,
    tools: Sequence[ModelTool] | None = None,
    tool_choice: str | None = "auto",
    stage: str,
    turn_state: ModelTurnState | None = None,
    delta_messages: Sequence[dict[str, Any]] | None = None,
    attachment_resolver: AttachmentResolver | None = None,
) -> AsyncIterator[ModelEvent]:
    """优先转发原生模型流，否则把一次完整生成合成为标准事件流。"""
    request = ModelRequest(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        stage=stage,
        turn_state=turn_state,
        delta_messages=delta_messages,
        attachment_resolver=attachment_resolver,
    )
    astream = getattr(llm, "astream", None)
    if callable(astream):
        async for event in astream(request):
            yield event
        return

    yield ModelEvent(type="response_started")
    response = await agenerate_model_response(
        llm,
        messages,
        tools=tools,
        tool_choice=tool_choice,
        stage=stage,
        turn_state=turn_state,
        delta_messages=delta_messages,
        attachment_resolver=attachment_resolver,
    )
    if response.text:
        yield ModelEvent(type="text_delta", text=response.text)
    yield ModelEvent(type="response_completed", response=response)
