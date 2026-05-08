"""SubagentRunner：无状态、不持久化的 ReAct 子循环（PlannerExecutor.executor 节点内复用）。

与父 Agent 的关键差异：
- **不依赖 SessionStore / ContextAssembler**：调用方直接传入 system_prompt 和 user_input。
- **不写盘**：所有 messages 仅活在本次 run() 的栈上，结束即销毁。
- **接受任意 tools 接口**（鸭子兼容 PluginRegistry / ToolView）。

返回：`SubagentResult`（来自 base.py）。
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from .base import SubagentResult


class _ToolsLike(Protocol):
    def openai_schemas(self) -> list[dict]: ...
    def call(self, tool_name: str, params: dict[str, Any]) -> str: ...


class _LLMLike(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = "auto",
    ) -> Any: ...


def _tool_calls_to_dicts(tool_calls: Any) -> list[dict]:
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


class SubagentRunner:
    """子 agent 的执行器。一次性的、无状态的。

    用法：
        runner = SubagentRunner(llm=llm, tools=tool_view, system_prompt="你是…")
        result = runner.run("请研究 X 并给出 3 行结论")
    """

    def __init__(
        self,
        *,
        llm: _LLMLike,
        tools: _ToolsLike,
        system_prompt: str,
        max_steps: int = 8,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_steps = max(1, int(max_steps))

    def run(self, user_input: str) -> SubagentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]
        result = SubagentResult(final_answer="")
        schemas = self.tools.openai_schemas() or None

        for step in range(self.max_steps):
            response = self.llm.chat(messages, tools=schemas)
            msg = response.choices[0].message

            if not msg.tool_calls:
                content = (msg.content or "").strip()
                result.final_answer = content
                result.add("final", {"step": step, "content": content})
                return result

            tool_calls_dicts = _tool_calls_to_dicts(msg.tool_calls)
            assistant_dict: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": tool_calls_dicts,
            }
            # DeepSeek thinking 模式：reasoning_content 必须随 assistant.tool_calls
            # 一起回灌到下一轮 API，否则服务端 400。
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                assistant_dict["reasoning_content"] = reasoning
            messages.append(assistant_dict)
            result.add(
                "assistant_tool_calls",
                {"step": step, "tool_calls": tool_calls_dicts},
            )

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result.add("tool_call", {"step": step, "name": name, "args": args})

                output = self.tools.call(name, args)

                result.add(
                    "tool_result",
                    {"step": step, "name": name, "result": output},
                )
                messages.append(
                    {
                        "role": "tool",
                        "content": output,
                        "tool_call_id": tc.id,
                        "name": name,
                    }
                )

        result.final_answer = f"[subagent] 已达到最大步数 {self.max_steps}，提前结束。"
        result.finished = False
        result.add("max_steps", {"content": result.final_answer})
        return result
