"""Agent 入口层的稳定请求契约。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .content import (
    ContentBlock,
    TextBlock,
    coerce_content_blocks,
    collect_attachment_ids,
    content_blocks_to_text,
)


@dataclass(frozen=True)
class AgentRequest:
    """一次 Agent 请求；TaskSpec 可在外部编译成这个接口。"""

    content: Sequence[ContentBlock]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        metadata = dict(self.metadata)
        try:
            json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("AgentRequest.metadata 必须可 JSON 序列化") from exc
        object.__setattr__(self, "content", coerce_content_blocks(self.content))
        object.__setattr__(self, "metadata", metadata)

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> "AgentRequest":
        return cls(content=(TextBlock(text),), metadata=metadata or {})

    @property
    def text(self) -> str:
        return content_blocks_to_text(self.content)

    @property
    def attachment_ids(self) -> tuple[str, ...]:
        return collect_attachment_ids(self.content)

    def with_text(self, text: str) -> "AgentRequest":
        """替换文本块并保留图片/文件，供 UserPromptSubmit Hook 使用。"""
        updated: list[ContentBlock] = []
        inserted = False
        for block in self.content:
            if isinstance(block, TextBlock):
                if not inserted:
                    updated.append(TextBlock(text))
                    inserted = True
                continue
            updated.append(block)
        if not inserted:
            updated.insert(0, TextBlock(text))
        return AgentRequest(content=tuple(updated), metadata=self.metadata)


@dataclass(frozen=True)
class RunContext:
    """一次 Agent 运行的调用方上下文。

    episode_id 和 env_idx 只作为不透明关联字段存在，Harness 不依赖
    任何机器人或 benchmark 实现。
    """

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    episode_id: str | None = None
    env_idx: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        metadata = dict(self.metadata)
        try:
            json.dumps(metadata, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("RunContext.metadata 必须可 JSON 序列化") from exc
        object.__setattr__(self, "metadata", metadata)


@dataclass(frozen=True)
class AgentEvent:
    """异步主循环向 CLI、机器人适配器和采样器发布的统一事件。"""

    type: str
    run_id: str
    session_id: str
    sequence: int
    timestamp: float
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    """一次 Agent 运行的最终结果；trace 避免与 agent.py 循环依赖。"""

    content: str
    run_id: str
    status: str
    trace: Any = field(repr=False, compare=False)
