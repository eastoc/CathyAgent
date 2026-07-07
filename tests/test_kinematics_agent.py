from __future__ import annotations

import sys
import unittest
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from robot_sdk.kinematics.dh import estimate_reach  # noqa: E402

HAS_LANGGRAPH = find_spec("langgraph") is not None
if HAS_LANGGRAPH:
    from subagents.kinematics_agent import KinematicsAgent  # noqa: E402
else:
    KinematicsAgent = None  # type: ignore[assignment]


def _make_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


class _ScriptedLLM:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> SimpleNamespace:
        self.calls.append(messages)
        if not self.outputs:
            raise AssertionError("LLM 调用次数超出脚本")
        return _make_response(self.outputs.pop(0))


class _FakeTools:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call(self, tool_name: str, params: dict[str, Any]) -> str:
        self.calls.append({"tool": tool_name, "params": dict(params)})
        return "[search_agent] fake search result"


def _decision_json(
    source_mode: str,
    *,
    profile_name: str | None = None,
    target_reach: float | None = None,
    target_reach_unit: str = "mm",
    search_queries: list[str] | None = None,
    rationale: str = "test decision",
    warnings: list[str] | None = None,
) -> str:
    import json

    return json.dumps(
        {
            "source_mode": source_mode,
            "profile_name": profile_name,
            "target_reach": target_reach,
            "target_reach_unit": target_reach_unit,
            "search_queries": search_queries or [],
            "rationale": rationale,
            "assumptions": [],
            "warnings": warnings or [],
            "needs_user_clarification": False,
            "clarification_question": None,
        },
        ensure_ascii=False,
    )


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph is not installed in this Python environment")
class KinematicsAgentTest(unittest.TestCase):
    def test_exact_profile_decision_builds_ur3e_model(self) -> None:
        agent = KinematicsAgent(
            llm=_ScriptedLLM(
                [_decision_json("exact_profile", profile_name="ur3e")]
            )
        )

        result = agent.build_result({"request": "我要 UR3e 的 DH 模型"})

        self.assertEqual(result.source_mode, "exact_profile")
        self.assertEqual(result.profile_name, "ur3e")
        self.assertEqual(len(result.kinematic_model.dh_params), 6)
        self.assertAlmostEqual(result.kinematic_model.dh_params[1].a, -243.55)
        self.assertEqual(result.scale_factor, 1.0)
        self.assertIn("decision", [item["type"] for item in result.trace])

    def test_scaled_profile_decision_calls_scaling_sdk(self) -> None:
        agent = KinematicsAgent(
            llm=_ScriptedLLM(
                [
                    _decision_json(
                        "scaled_profile",
                        profile_name="UR3 e",
                        target_reach=800,
                    )
                ]
            )
        )

        result = agent.build_result(
            {"request": "参考 UR3e，但臂展做到 800mm", "reach_mm": 800}
        )

        self.assertEqual(result.source_mode, "scaled_profile")
        self.assertEqual(result.profile_name, "ur3e")
        self.assertAlmostEqual(estimate_reach(result.kinematic_model.dh_params), 800)
        self.assertIsNotNone(result.scale_factor)
        self.assertTrue(any("no longer official" in item for item in result.warnings))

    def test_profile_like_template_decision_uses_target_reach(self) -> None:
        agent = KinematicsAgent(
            llm=_ScriptedLLM(
                [
                    _decision_json(
                        "profile_like_template",
                        profile_name="ur3e",
                        target_reach=1200,
                    )
                ]
            )
        )

        result = agent.build_result({"request": "做一个类似 UR3e 结构的 1.2m 机械臂"})

        self.assertEqual(result.source_mode, "profile_like_template")
        self.assertAlmostEqual(estimate_reach(result.kinematic_model.dh_params), 1200)
        self.assertNotAlmostEqual(result.kinematic_model.dh_params[1].a, -243.55)
        self.assertTrue(any("not official" in item for item in result.warnings))

    def test_ur3e_like_custom_reach_guard_overrides_exact_profile(self) -> None:
        agent = KinematicsAgent(
            llm=_ScriptedLLM(
                [_decision_json("exact_profile", profile_name="ur3e")]
            )
        )

        result = agent.build_result(
            {
                "request": "建模一个 UR3E-like 6DOF 机械臂，reach 500mm",
                "dof": 6,
                "reach_mm": 500,
            }
        )

        self.assertEqual(result.source_mode, "profile_like_template")
        self.assertEqual(result.profile_name, "ur3e")
        self.assertAlmostEqual(result.target_reach or 0.0, 500)
        self.assertAlmostEqual(estimate_reach(result.kinematic_model.dh_params), 500)
        self.assertTrue(any("overriding to profile_like_template" in item for item in result.warnings))

    def test_template_fallback_builds_generic_serial_chain(self) -> None:
        agent = KinematicsAgent(
            llm=_ScriptedLLM([_decision_json("template_fallback")])
        )

        result = agent.build_result(
            {"request": "做一个 4 自由度桌面机械臂", "dof": 4, "reach_mm": 500}
        )

        self.assertEqual(result.source_mode, "template_fallback")
        self.assertIsNone(result.profile_name)
        self.assertEqual(len(result.kinematic_model.joints), 4)
        self.assertAlmostEqual(estimate_reach(result.kinematic_model.dh_params), 500)

    def test_search_verified_is_reserved_and_falls_back_to_template(self) -> None:
        tools = _FakeTools()
        agent = KinematicsAgent(
            llm=_ScriptedLLM(
                [
                    _decision_json(
                        "search_verified",
                        search_queries=["KUKA KR6 DH parameters"],
                    )
                ]
            ),
            tools=tools,
        )

        result = agent.build_result({"request": "用 KUKA KR6 的 DH 参数", "dof": 6})

        self.assertEqual(result.source_mode, "template_fallback")
        self.assertEqual([call["tool"] for call in tools.calls], ["search_agent"])
        self.assertTrue(any("not implemented" in item for item in result.warnings))

    def test_run_returns_subagent_result_text(self) -> None:
        agent = KinematicsAgent(
            llm=_ScriptedLLM(
                [_decision_json("exact_profile", profile_name="ur3e")]
            )
        )

        result = agent.run({"request": "我要 UR3e 的 DH 模型"})

        self.assertTrue(result.finished)
        self.assertIn("source_mode: exact_profile", result.final_answer)

    def test_schema_warns_not_to_use_for_cad_modeling(self) -> None:
        agent = KinematicsAgent(llm=_ScriptedLLM([]))

        self.assertIn("仅用于纯运动学", agent.description)
        self.assertIn("应调用 robot_design_agent", agent.description)
        self.assertIn(
            "CAD/STEP/装配/导出/建模请求应交给 robot_design_agent",
            agent.input_schema["properties"]["request"]["description"],
        )


if __name__ == "__main__":
    unittest.main()
