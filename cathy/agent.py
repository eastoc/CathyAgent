"""Single-loop ReAct Agent（session-aware）。

主循环职责：
1. 用 ContextAssembler 把 [system] + 历史(预算内) + 当前 user 输入 拼成 messages；
2. 调用 LLM 得到下一条 assistant 消息；
3. 若包含 tool_calls，逐个执行并把结果以 role=tool 写回 messages，并**实时持久化**到 Session；
4. 否则把最终 assistant 消息持久化并返回。

Phase 3 起，subagent 会作为一个特殊的 tool（task）复用本主循环递归执行；
那时 subagent 会在内存里跑一份独立 messages，不持久化进父 session。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .context import ContextAssembler
from .llm import LLMClient
from .plugins import PluginRegistry
from .session.models import Message, Session
from .session.store import SessionStore


@dataclass
class AgentConfig:
    max_steps: int = 12


@dataclass
class AgentTrace:
    """一次 run() 的可观测结构，便于后续接日志/审计。"""

    steps: list[dict] = field(default_factory=list)

    def add(self, step_type: str, payload: dict) -> None:
        self.steps.append({"type": step_type, **payload})


def _openai_tool_calls_to_dicts(tool_calls: Any) -> list[dict]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        }
        for tc in tool_calls
    ]


class Agent:
    def __init__(
        self,
        *,
        llm: LLMClient,
        tools: PluginRegistry,
        assembler: ContextAssembler,
        store: SessionStore,
        config: AgentConfig | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.assembler = assembler
        self.store = store
        self.config = config or AgentConfig()
        self._on_event = on_event or (lambda _t, _p: None)

    def run(self, session: Session, user_input: str) -> tuple[str, AgentTrace]:
        # 1) 持久化 user 消息
        user_msg = Message(role="user", content=user_input)
        self.store.append_message(session.id, user_msg)
        session.append(user_msg)

        # 2) 装配本轮 messages（system + 历史，含上面刚追加的 user）
        messages = self.assembler.assemble(session)
        trace = AgentTrace()
        schemas = self.tools.openai_schemas() or None

        for step in range(self.config.max_steps):
            response = self.llm.chat(messages, tools=schemas)
            msg = response.choices[0].message
            #print("[msg]:",msg.reasoning_content)
            if not msg.tool_calls:
                content = (msg.content or "").strip()
                assistant_msg = Message(role="assistant", content=content)
                self.store.append_message(session.id, assistant_msg)
                session.append(assistant_msg)
                trace.add("final", {"step": step, "content": content})
                self._on_event("final", {"content": content})
                return content, trace

            # 持久化 assistant.tool_calls
            tool_calls_dicts = _openai_tool_calls_to_dicts(msg.tool_calls)
            assistant_msg = Message(
                role="assistant",
                content=msg.content or "",
                tool_calls=tool_calls_dicts,
            )
            self.store.append_message(session.id, assistant_msg)
            session.append(assistant_msg)
            messages.append(assistant_msg.to_openai_dict())

            # 逐个执行工具
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

                tool_msg = Message(
                    role="tool",
                    content=result,
                    tool_call_id=tc.id,
                    name=name,
                )
                self.store.append_message(session.id, tool_msg)
                session.append(tool_msg)
                messages.append(tool_msg.to_openai_dict())

        msg_text = f"[已达到最大步数 {self.config.max_steps}，提前结束]"
        trace.add("max_steps", {"content": msg_text})
        return msg_text, trace
