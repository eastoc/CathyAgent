"""Subagent 抽象层。

一个 Subagent 是**有 turn loop 的执行实体**，最小协议：

    class MySubagent(Subagent):
        name = "my_subagent"
        description = "..."   # 父 agent 看到的工具描述
        input_schema = {...}  # 父 agent 看到的入参 JSON Schema

        def run(self, params: dict) -> SubagentResult: ...

通过 `SubagentToolPlugin(subagent)` 把它包装成主 agent 可见的工具。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from ..llm_errors import AgentFailure


SubagentStatus = Literal["ok", "failed", "degraded", "incomplete"]


@dataclass
class SubagentResult:
    """子 agent 一次 run() 的产出。"""

    final_answer: str
    finished: bool = True
    status: SubagentStatus = "ok"
    failure: AgentFailure | None = None
    trace: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.failure is not None and self.status == "ok":
            self.status = "failed" if not self.finished else "degraded"
        elif not self.finished and self.status == "ok":
            self.status = "failed"

    def add(self, step_type: str, payload: dict) -> None:
        self.trace.append({"type": step_type, **payload})

    @property
    def retryable(self) -> bool:
        return bool(self.failure and self.failure.retryable)

    def to_dict(self, *, subagent: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "subagent_result",
            "status": self.status,
            "finished": self.finished,
            "retryable": self.retryable,
            "final_answer": self.final_answer,
            "trace": self.trace,
        }
        if subagent:
            payload["subagent"] = subagent
        if self.failure is not None:
            payload["failure"] = self.failure.to_dict()
        return payload


class Subagent(ABC):
    """所有 subagent 的最小协议。"""

    name: str
    description: str
    input_schema: dict[str, Any]

    @abstractmethod
    def run(self, params: dict[str, Any]) -> SubagentResult:
        """执行子任务。返回 final_answer + trace；不抛业务错误，错误信息放进 final_answer。"""
        raise NotImplementedError
