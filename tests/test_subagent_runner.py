"""SubagentRunner 主循环测试（不联网，伪造 LLM）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.plugins.base import ToolPlugin  # noqa: E402
from cathy.plugins.manifest import Execution, PluginManifest, ToolSpec  # noqa: E402
from cathy.plugins.registry import PluginRegistry, ToolView  # noqa: E402
from cathy.subagent.runner import SubagentRunner  # noqa: E402


class _StaticEchoPlugin(ToolPlugin):
    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        return f"echo:{params.get('msg', '')}"


def _make_manifest() -> PluginManifest:
    return PluginManifest(
        name="echo_p",
        version="0.0.1",
        description="echo",
        tools=[
            ToolSpec(
                name="echo",
                description="echo a message",
                input_schema={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                    "additionalProperties": False,
                },
            )
        ],
        permissions={},
        execution=Execution(runtime="python", entrypoint="<internal>:Echo"),
        metadata={"trust_level": "builtin"},
        source_dir=Path("."),
    )


def _build_view() -> ToolView:
    reg = PluginRegistry(plugins_dirs=[])
    reg.register_internal_plugin(_make_manifest(), _StaticEchoPlugin())
    return ToolView(reg)


def _make_tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _make_response(*, content: str = "", tool_calls: list[Any] | None = None) -> SimpleNamespace:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _ScriptedLLM:
    """按 chat 调用顺序返回预设 response。"""

    def __init__(self, scripted: list[SimpleNamespace]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, *, tools=None, tool_choice="auto"):  # noqa: D401
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._scripted:
            raise AssertionError("LLM 调用次数超出脚本预设")
        return self._scripted.pop(0)


class SubagentRunnerTest(unittest.TestCase):
    def test_immediate_final_answer(self) -> None:
        llm = _ScriptedLLM([_make_response(content="42")])
        runner = SubagentRunner(
            llm=llm, tools=_build_view(), system_prompt="你是子 agent", max_steps=4
        )
        result = runner.run("hi")
        self.assertEqual(result.final_answer, "42")
        self.assertTrue(result.finished)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(result.trace[-1]["type"], "final")

    def test_one_tool_call_then_final(self) -> None:
        llm = _ScriptedLLM(
            [
                _make_response(
                    tool_calls=[
                        _make_tool_call("c1", "echo", '{"msg": "ping"}')
                    ]
                ),
                _make_response(content="结论：echo:ping"),
            ]
        )
        runner = SubagentRunner(
            llm=llm, tools=_build_view(), system_prompt="sys", max_steps=4
        )
        result = runner.run("请调用 echo")
        self.assertTrue(result.finished)
        self.assertEqual(result.final_answer, "结论：echo:ping")

        types = [s["type"] for s in result.trace]
        self.assertIn("assistant_tool_calls", types)
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        self.assertEqual(types[-1], "final")

        # 第二轮调用应当看到 tool 消息已被 append
        second_messages = llm.calls[1]["messages"]
        roles = [m["role"] for m in second_messages]
        self.assertEqual(roles[:2], ["system", "user"])
        self.assertEqual(roles[-2], "assistant")
        self.assertEqual(roles[-1], "tool")
        self.assertEqual(second_messages[-1]["content"], "echo:ping")

    def test_max_steps_exit_when_loop_never_returns(self) -> None:
        infinite_call = _make_response(
            tool_calls=[_make_tool_call("c1", "echo", '{"msg": "x"}')]
        )
        llm = _ScriptedLLM([infinite_call, infinite_call])
        runner = SubagentRunner(
            llm=llm, tools=_build_view(), system_prompt="sys", max_steps=2
        )
        result = runner.run("loop")
        self.assertFalse(result.finished)
        self.assertIn("最大步数", result.final_answer)
        self.assertEqual(len(llm.calls), 2)

    def test_blocked_tool_returns_error_text(self) -> None:
        reg = PluginRegistry(plugins_dirs=[])
        reg.register_internal_plugin(_make_manifest(), _StaticEchoPlugin())
        view = ToolView(reg, blocked=["echo"])

        llm = _ScriptedLLM(
            [
                _make_response(
                    tool_calls=[_make_tool_call("c1", "echo", '{"msg": "x"}')]
                ),
                _make_response(content="完成"),
            ]
        )
        runner = SubagentRunner(
            llm=llm, tools=view, system_prompt="sys", max_steps=4
        )
        result = runner.run("尝试调用 echo")
        tool_results = [s for s in result.trace if s["type"] == "tool_result"]
        self.assertEqual(len(tool_results), 1)
        self.assertIn("不在", tool_results[0]["result"])


if __name__ == "__main__":
    unittest.main()
