"""Session / Message 数据模型。

设计原则：
- 不持久化 system 消息（每次启动由 ContextAssembler 重新装配）。
- assistant 的 tool_calls / tool 的 tool_call_id+name 也要 roundtrip。
- to_openai_dict() 直接产出 LLM 调用所需的消息结构。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_session_id() -> str:
    """8 位短 ID，足够本地使用。"""
    return uuid.uuid4().hex[:8]


@dataclass
class Message:
    role: str  # user / assistant / tool
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    created_at: str = field(default_factory=_now_iso)

    def to_openai_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
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
