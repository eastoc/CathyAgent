"""与训练框架无关的运行日志契约。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from .agent import AgentEvent


@dataclass(frozen=True)
class RunRecord:
    """一次 Harness 执行段；多个 continuation 可共享同一 root_run_id。"""

    run_id: str
    root_run_id: str
    session_id: str
    status: str = "running"
    parent_run_id: str | None = None
    episode_id: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    model_metadata: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.run_id or not self.root_run_id or not self.session_id:
            raise ValueError("run_id、root_run_id 和 session_id 不能为空")
        if self.schema_version <= 0:
            raise ValueError("schema_version 必须是正整数")
        model_metadata = dict(self.model_metadata)
        context = dict(self.context)
        try:
            json.dumps(model_metadata, ensure_ascii=False)
            json.dumps(context, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("RunRecord 元数据必须可 JSON 序列化") from exc
        object.__setattr__(self, "model_metadata", model_metadata)
        object.__setattr__(self, "context", context)


@runtime_checkable
class EventSink(Protocol):
    """Agent 依赖的最小运行事实持久化接口。"""

    async def astart_run(self, run: RunRecord) -> None:
        ...

    async def aappend_event(self, event: AgentEvent) -> None:
        ...

    async def aflush(self) -> None:
        ...
