"""PlannerExecutorSubagent (LangGraph) 集成测试。

策略：用一个 _RoutingLLM 把不同节点的 prompt 路由到不同的预设回复。
- 节点判别只看 system prompt 的开头关键字（"规划 agent" / "执行 agent" / "复盘 agent"）。
- 不真正调网，工具用 echo 模拟。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.plugins.base import ToolPlugin  # noqa: E402
from cathy.plugins.manifest import Execution, PluginManifest, ToolSpec  # noqa: E402
from cathy.plugins.registry import PluginRegistry, ToolView  # noqa: E402
from subagents.planner_executor.agent import (  # noqa: E402
    PlannerExecutorSubagent,
    _parse_plan,
    _parse_replan_json,
)


# ---------- 工具/视图 ----------

class _EchoPlugin(ToolPlugin):
    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        return f"echo:{params.get('msg', '')}"


def _echo_manifest() -> PluginManifest:
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
    reg.register_internal_plugin(_echo_manifest(), _EchoPlugin())
    return ToolView(reg)


def _make_response(*, content: str = "", tool_calls: list[Any] | None = None) -> SimpleNamespace:
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _final(text: str) -> SimpleNamespace:
    return _make_response(content=text)


# ---------- 路由式 LLM ----------

class _RoutingLLM:
    """根据 system prompt 中的标识把每次 chat 路由到对应队列。"""

    def __init__(
        self,
        *,
        planner: list[SimpleNamespace],
        executor: list[SimpleNamespace],
        replanner: list[SimpleNamespace],
    ) -> None:
        self._queues: dict[str, list[SimpleNamespace]] = {
            "planner": list(planner),
            "executor": list(executor),
            "replanner": list(replanner),
        }
        self.call_log: list[dict[str, Any]] = []

    def chat(self, messages, *, tools=None, tool_choice="auto"):
        system_text = messages[0]["content"] if messages else ""
        node = self._classify(system_text)
        self.call_log.append(
            {"node": node, "messages": list(messages), "tools": tools}
        )
        queue = self._queues.get(node)
        if not queue:
            raise AssertionError(
                f"路由 LLM 用尽了 {node} 队列；message[0]:\n{system_text[:120]}"
            )
        return queue.pop(0)

    @staticmethod
    def _classify(system_text: str) -> str:
        if "规划 agent" in system_text:
            return "planner"
        if "复盘 agent" in system_text:
            return "replanner"
        if "执行 agent" in system_text:
            return "executor"
        # 默认归 executor（SubagentRunner 用任意 prompt）
        return "executor"


# ---------- 单测 ----------

class HelpersTest(unittest.TestCase):
    def test_parse_plan_numbered(self) -> None:
        self.assertEqual(
            _parse_plan("1. 第一\n2. 第二\n3. 第三"),
            ["第一", "第二", "第三"],
        )

    def test_parse_plan_bullets(self) -> None:
        self.assertEqual(_parse_plan("- a\n- b"), ["a", "b"])

    def test_parse_plan_empty(self) -> None:
        self.assertEqual(_parse_plan("没有列表的纯文本"), [])

    def test_parse_replan_json_strips_codeblock(self) -> None:
        s = '```json\n{"action":"finish","response":"ok"}\n```'
        self.assertEqual(
            _parse_replan_json(s),
            {"action": "finish", "response": "ok"},
        )

    def test_parse_replan_json_invalid(self) -> None:
        self.assertIsNone(_parse_replan_json("not json"))


class PlannerExecutorTest(unittest.TestCase):
    def _build_pe(self, llm: Any) -> PlannerExecutorSubagent:
        return PlannerExecutorSubagent(
            llm=llm,
            tools=_build_view(),
            executor_step_max=2,
            default_max_iterations=4,
        )

    def test_happy_path_two_steps_then_finish(self) -> None:
        llm = _RoutingLLM(
            planner=[_final("1. 找数据\n2. 整理结论")],
            executor=[
                _final("步骤1结果：数据已收集"),
                _final("步骤2结果：结论已整理"),
            ],
            replanner=[
                _final('{"action":"finish","response":"最终答案：完成 X"}'),
            ],
        )
        pe = self._build_pe(llm)
        result = pe.run({"goal": "完成 X"})
        self.assertTrue(result.finished)
        self.assertEqual(result.final_answer, "最终答案：完成 X")

        nodes = [c["node"] for c in llm.call_log]
        # 至少出现：planner, executor*2, replanner
        self.assertEqual(nodes[0], "planner")
        self.assertIn("executor", nodes)
        self.assertEqual(nodes[-1], "replanner")
        self.assertEqual(nodes.count("executor"), 2)

    def test_replan_continue_then_finish(self) -> None:
        """replanner 返回 continue + 新计划，重新进入 executor。"""
        llm = _RoutingLLM(
            planner=[_final("1. 探索")],
            executor=[
                _final("探索结果：得到线索 A"),
                _final("深入结果：线索 A 指向答案 B"),
            ],
            replanner=[
                _final('{"action":"continue","plan":["深入分析线索 A"]}'),
                _final('{"action":"finish","response":"答案：B"}'),
            ],
        )
        pe = self._build_pe(llm)
        result = pe.run({"goal": "找到 B"})
        self.assertTrue(result.finished)
        self.assertEqual(result.final_answer, "答案：B")
        self.assertEqual(
            [c["node"] for c in llm.call_log].count("executor"), 2
        )

    def test_plan_parse_fallback_uses_goal_as_single_step(self) -> None:
        """planner 输出无法解析时，直接把 goal 当作单步执行。"""
        llm = _RoutingLLM(
            planner=[_final("没有任何编号列表的废话")],
            executor=[_final("勉强执行了一次")],
            replanner=[_final('{"action":"finish","response":"已尽力"}')],
        )
        pe = self._build_pe(llm)
        result = pe.run({"goal": "目标 Z"})
        self.assertEqual(result.final_answer, "已尽力")

        # 第一个 step 应等于原 goal
        plan_trace = [t for t in result.trace if t["type"] == "plan"]
        self.assertEqual(plan_trace[0]["plan"], ["目标 Z"])

    def test_replan_invalid_json_fallback_synthesis(self) -> None:
        """replanner 返回非 JSON 时，应该兜底用 past_steps 做综合输出。"""
        llm = _RoutingLLM(
            planner=[_final("1. 干活")],
            executor=[_final("干完了")],
            replanner=[_final("我是无效 JSON")],
        )
        pe = self._build_pe(llm)
        result = pe.run({"goal": "做点事"})
        self.assertIn("已执行的步骤", result.final_answer)
        self.assertIn("干完了", result.final_answer)

    def test_max_iterations_forces_finish(self) -> None:
        """超过 max_iterations 不再继续；replanner 被强制走 finish 路径。"""
        llm = _RoutingLLM(
            planner=[_final("1. a\n2. b\n3. c\n4. d\n5. e")],
            executor=[_final(f"r{i}") for i in range(5)],
            replanner=[
                # 前 N 次都说 continue，等到 max_iter 触发后强制 finish
                _final('{"action":"continue","plan":["继续 a"]}'),
                _final('{"action":"continue","plan":["继续 b"]}'),
                _final('{"action":"finish","response":"被迫总结"}'),
            ],
        )
        pe = PlannerExecutorSubagent(
            llm=llm,
            tools=_build_view(),
            executor_step_max=2,
            default_max_iterations=2,  # 小阈值触发兜底
        )
        result = pe.run({"goal": "无限任务"})
        self.assertTrue(result.finished)
        # iteration=2 时 replanner 进入 finish 分支
        self.assertEqual(result.final_answer, "被迫总结")


class PlannerExecutorAsToolTest(unittest.TestCase):
    """通过 SubagentToolPlugin 注册到 PluginRegistry，验证 OpenAI schema 正确。"""

    def test_registered_tool_schema(self) -> None:
        from cathy.subagent import SubagentToolPlugin, build_subagent_tool_manifest

        llm = _RoutingLLM(planner=[_final("1. a")], executor=[_final("done")], replanner=[_final('{"action":"finish","response":"ok"}')])
        pe = PlannerExecutorSubagent(llm=llm, tools=_build_view())
        registry = PluginRegistry(plugins_dirs=[])
        registry.register_internal_plugin(
            build_subagent_tool_manifest(pe),
            SubagentToolPlugin(pe),
        )
        schemas = registry.openai_schemas()
        names = [s["function"]["name"] for s in schemas]
        self.assertEqual(names, ["planner_executor"])

        # 缺 goal 应被 schema 拦截
        bad = registry.call("planner_executor", {})
        self.assertTrue(bad.startswith("[ToolError:planner_executor]"))

        good = registry.call("planner_executor", {"goal": "test"})
        payload = json.loads(good)
        self.assertEqual(payload["type"], "subagent_result")
        self.assertEqual(payload["subagent"], "planner_executor")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["final_answer"], "ok")


if __name__ == "__main__":
    unittest.main()
