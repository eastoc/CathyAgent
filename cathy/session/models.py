"""Session / Message 数据模型。

设计原则：
- 不持久化 system 消息（每次启动由 ContextAssembler 重新装配）。
- assistant 的 tool_calls / tool 的 tool_call_id+name 也要 roundtrip。
- 会话层只输出供应商无关的模型消息；线协议转换由模型适配器负责。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from ..contracts.content import (
    ContentBlock,
    coerce_content_blocks,
    content_blocks_to_model_content,
    content_blocks_to_text,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_session_id() -> str:
    """8 位短 ID，足够本地使用。"""
    return uuid.uuid4().hex[:8]


@dataclass
class Message:
    role: str  # user / assistant / tool
    content: Sequence[ContentBlock] = field(default_factory=tuple)
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    reasoning: str | None = None
    created_at: str = field(default_factory=_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.content, str):
            raise TypeError("Message.content 必须是 ContentBlock 序列；请使用 text_content()")
        self.content = coerce_content_blocks(self.content)
        self.metadata = dict(self.metadata)
        try:
            json.dumps(self.metadata, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Message.metadata 必须可 JSON 序列化") from exc

    @property
    def text(self) -> str:
        """供 Hook、日志和 UI 使用的可读投影，不作为持久化真源。"""
        return content_blocks_to_text(self.content)

    def to_model_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role,
            "content": content_blocks_to_model_content(self.content),
        }
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        if self.reasoning:
            d["reasoning"] = self.reasoning
        return d


@dataclass
class Session:
    id: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)

    def append(self, message: Message) -> None:
        self.messages.append(message)
        self.updated_at = _now_iso()
