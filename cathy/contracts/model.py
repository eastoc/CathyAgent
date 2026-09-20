"""与模型供应商无关的请求、响应和客户端协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


ModelMessage = Mapping[str, Any]
ToolSchema = Mapping[str, Any]


@dataclass(frozen=True)
class ModelRequest:
    """一次模型生成请求。

    ``messages`` 暂时兼容现有 role/content 消息结构；后续增加图片等输入时，
    只扩展契约和 provider adapter，不要求 Agent 依赖具体 SDK 类型。
    """

    messages: Sequence[ModelMessage]
    tools: Sequence[ToolSchema] | None = None
    tool_choice: str | None = "auto"
    stage: str = "model.generate"
    continuation_id: str | None = None
    continuation_messages: Sequence[ModelMessage] | None = None


@dataclass(frozen=True)
class ModelToolCall:
    """归一化后的函数工具调用。"""

    id: str
    name: str
    arguments: Mapping[str, Any]
    raw_arguments: str = "{}"


@dataclass(frozen=True)
class ModelResponse:
    """归一化后的模型输出。"""

    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    reasoning_content: str | None = None
    continuation_id: str | None = None
    raw: Any = field(default=None, repr=False, compare=False)


@runtime_checkable
class ModelClient(Protocol):
    """CathyAgent 所依赖的最小模型客户端协议。"""

    model: str

    def generate(self, request: ModelRequest) -> ModelResponse:
        """生成一轮归一化模型输出。"""
