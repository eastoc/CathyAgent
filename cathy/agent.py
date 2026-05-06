"""Single-loop ReAct Agent。

主循环职责仅有三件事：
1. 调用 LLM 拿到下一条 assistant 消息；
2. 若包含 tool_calls，逐个执行并把结果以 role=tool 写回 messages，继续循环；
3. 否则返回 assistant.content 作为最终回复。

Phase 3 起，subagent 会作为一个特殊的 tool（task）复用本主循环递归执行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .llm import LLMClient
from .tools.base import ToolRegistry


@dataclass
class AgentConfig:
    system_prompt: str = ""
    max_steps: int = 12


@dataclass
class AgentTrace:
    """一次 run() 的可观测结构，便于后续接日志/审计。"""

    steps: list[dict] = field(default_factory=list)

    def add(self, step_type: str, payload: dict) -> None:
        self.steps.append({"type": step_type, **payload})


def _assistant_msg_to_dict(msg: Any) -> dict:
    d: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return d


class Agent:
    def __init__(
        self,
        *,
        llm: LLMClient,
        tools: ToolRegistry,
        config: AgentConfig | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.config = config or AgentConfig()
        self._on_event = on_event or (lambda _t, _p: None)

    def run(self, user_input: str) -> tuple[str, AgentTrace]:
        messages: list[dict[str, Any]] = []
        if self.config.system_prompt:
            messages.append({"role": "system", "content": self.config.system_prompt})
        messages.append({"role": "user", "content": user_input})

        trace = AgentTrace()
        schemas = self.tools.openai_schemas()

        for step in range(self.config.max_steps):
            response = self.llm.chat(messages, tools=schemas if schemas else None)
            msg = response.choices[0].message

            if not msg.tool_calls:
                content = (msg.content or "").strip()
                trace.add("final", {"step": step, "content": content})
                self._on_event("final", {"content": content})
                return content, trace

            messages.append(_assistant_msg_to_dict(msg))
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                self._on_event("tool_call", {"name": name, "args": args})
                trace.add("tool_call", {"step": step, "name": name, "args": args})

                result = self.tools.call(name, args)

                self._on_event("tool_result", {"name": name, "result": result})
                trace.add("tool_result", {"step": step, "name": name, "result": result})

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

        msg_text = f"[已达到最大步数 {self.config.max_steps}，提前结束]"
        trace.add("max_steps", {"content": msg_text})
        return msg_text, trace
