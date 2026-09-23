"""Agent 入口层的稳定请求契约。"""

from __future__ import annotations

import json
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
