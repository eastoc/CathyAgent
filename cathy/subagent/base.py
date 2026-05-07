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
from typing import Any


@dataclass
class SubagentResult:
    """子 agent 一次 run() 的产出。"""

    final_answer: str
    finished: bool = True
    trace: list[dict] = field(default_factory=list)

    def add(self, step_type: str, payload: dict) -> None:
        self.trace.append({"type": step_type, **payload})


class Subagent(ABC):
    """所有 subagent 的最小协议。"""

    name: str
    description: str
    input_schema: dict[str, Any]

    @abstractmethod
    def run(self, params: dict[str, Any]) -> SubagentResult:
        """执行子任务。返回 final_answer + trace；不抛业务错误，错误信息放进 final_answer。"""
        raise NotImplementedError
