"""SubagentRunner：无状态、不持久化的 ReAct 子循环（PlannerExecutor.executor 节点内复用）。

与父 Agent 的关键差异：
- **不依赖 SessionStore / ContextAssembler**：调用方直接传入 system_prompt 和 user_input。
- **不写盘**：所有 messages 仅活在本次 run() 的栈上，结束即销毁。
- **接受任意 tools 接口**（鸭子兼容 PluginRegistry / ToolView）。

返回：`SubagentResult`（来自 base.py）。
"""

from __future__ import annotations

from typing import Any, Protocol

from ..contracts import ModelClient, ModelTool, ModelToolCall
from ..llm_errors import LLMCallError
from ..model_clients import generate_model_response
from .base import SubagentResult


class _ToolsLike(Protocol):
    def model_tools(self) -> list[ModelTool]: ...
    def call(self, tool_name: str, params: dict[str, Any]) -> str: ...


def _tool_calls_to_dicts(tool_calls: tuple[ModelToolCall, ...]) -> list[dict]:
    return [
        {
            "id": tc.id,
            "name": tc.name,
            "arguments": dict(tc.arguments),
            "raw_arguments": tc.raw_arguments,
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
        llm: ModelClient,
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
        model_tools = self.tools.model_tools() or None
        turn_state = None
        delta_messages: list[dict[str, Any]] | None = None

        for step in range(self.max_steps):
            try:
                response = generate_model_response(
                    self.llm,
                    messages,
                    tools=model_tools,
                    stage="subagent_runner",
                    turn_state=turn_state,
                    delta_messages=delta_messages,
                )
            except LLMCallError as exc:
                result.final_answer = (
                    f"[subagent] LLM 调用失败: {exc.failure.error_type}: "
                    f"{exc.failure.message}"
                )
                result.finished = False
                result.status = "failed"
                result.failure = exc.failure
                result.add("llm_error", {"step": step, "failure": exc.failure.to_dict()})
                return result
            if not response.tool_calls:
                content = (response.text or "").strip()
                result.final_answer = content
                result.add("final", {"step": step, "content": content})
                return result

            tool_calls_dicts = _tool_calls_to_dicts(response.tool_calls)
            assistant_dict: dict[str, Any] = {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": tool_calls_dicts,
            }
            if response.reasoning:
                assistant_dict["reasoning"] = response.reasoning
            messages.append(assistant_dict)
            result.add(
                "assistant_tool_calls",
                {"step": step, "tool_calls": tool_calls_dicts},
            )

            tool_result_messages: list[dict[str, Any]] = []
            for tc in response.tool_calls:
                name = tc.name
                args = dict(tc.arguments)
                result.add("tool_call", {"step": step, "name": name, "args": args})

                output = self.tools.call(name, args)

                result.add(
                    "tool_result",
                    {"step": step, "name": name, "result": output},
                )
                tool_message = {
                    "role": "tool",
                    "content": output,
                    "tool_call_id": tc.id,
                    "name": name,
                }
                messages.append(tool_message)
                tool_result_messages.append(tool_message)

            turn_state = response.next_turn_state
            delta_messages = tool_result_messages

        result.final_answer = f"[subagent] 已达到最大步数 {self.max_steps}，提前结束。"
        result.finished = False
        result.status = "incomplete"
        result.add("max_steps", {"content": result.final_answer})
        return result
