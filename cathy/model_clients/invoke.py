"""统一模型调用入口，并为旧式测试客户端保留临时兼容。"""

from __future__ import annotations

from typing import Any, Sequence

from ..contracts import ModelRequest, ModelResponse, ModelTool, ModelTurnState
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
            )
        )

    chat = getattr(llm, "chat", None)
    if not callable(chat):
        raise TypeError("模型客户端必须实现 generate(ModelRequest)")
    try:
        raw_response = chat(
            convert_chat_messages(messages),
            tools=[convert_chat_tool(tool) for tool in tools] if tools else None,
            tool_choice=tool_choice,
            stage=stage,
        )
    except TypeError as exc:
        if "stage" not in str(exc):
            raise
        raw_response = chat(
            convert_chat_messages(messages),
            tools=[convert_chat_tool(tool) for tool in tools] if tools else None,
            tool_choice=tool_choice,
        )
    return normalize_chat_response(raw_response)
