"""与模型供应商无关的请求、响应和客户端协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence, runtime_checkable

from .content import AttachmentResolver


ModelMessage = Mapping[str, Any]


@dataclass(frozen=True)
class ModelTool:
    """供应商无关的函数工具定义。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    strict: bool | None = None
    async_execution: bool = False


@dataclass(frozen=True)
class ModelTurnState:
    """由模型适配器产生、主循环只负责回传的不透明轮次状态。"""

    value: Any = field(repr=False)


@dataclass(frozen=True)
class ModelRequest:
    """一次模型生成请求。

    ``messages`` 使用供应商无关的 role/content 结构；附件只保存稳定引用，
    provider adapter 通过 ``attachment_resolver`` 按需读取二进制内容。
    """

    messages: Sequence[ModelMessage]
    tools: Sequence[ModelTool] | None = None
    tool_choice: str | None = "auto"
    stage: str = "model.generate"
    turn_state: ModelTurnState | None = None
    delta_messages: Sequence[ModelMessage] | None = None
    attachment_resolver: AttachmentResolver | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class ModelToolCall:
    """归一化后的函数工具调用。"""

    id: str
    name: str
    arguments: Mapping[str, Any]
    raw_arguments: str = "{}"
    async_execution: bool = False


@dataclass(frozen=True)
class ModelResponse:
    """归一化后的模型输出。"""

    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    reasoning: str | None = None
    next_turn_state: ModelTurnState | None = None
    raw: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ModelEvent:
    """模型流事件；provider adapter 负责把供应商事件归一化到该结构。"""

    type: str
    text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    response: ModelResponse | None = None
    raw: Any = field(default=None, repr=False, compare=False)


@runtime_checkable
class ModelClient(Protocol):
    """CathyAgent 所依赖的最小模型客户端协议。"""

    model: str

    def generate(self, request: ModelRequest) -> ModelResponse:
        """生成一轮归一化模型输出。"""


@runtime_checkable
class AsyncModelClient(Protocol):
    """原生异步模型客户端协议；同步客户端由调用适配层在线程池中兼容。"""

    model: str

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        """异步生成一轮归一化模型输出。"""

    def astream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """流式生成归一化模型事件。"""
