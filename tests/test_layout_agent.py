import unittest
import json
from types import SimpleNamespace
from typing import Any

from robot_sdk.kinematics.scaling import (
    KinematicScalingRequest,
    build_profile_kinematic_model,
)
from robot_sdk.structure import build_generic_6axis_cobot_structure_plan
from robot_sdk.types import DHParam, JointSpec, KinematicModel, LinkSpec
from robot_sdk.validation.structure import validate_robot_structure
from robot_sdk.layout.decision_adapter import build_mechanical_layout_with_decision
from robot_sdk.assembly.local_solve import default_local_subassembly_specs
from robot_sdk.cad.cq_parts import build_cadquery_parts
from robot_sdk.cad.source_robot_builder import build_source_robot_from_layout
from subagents.layout_agent import LayoutAgent
from subagents.layout_agent.schema import LinkMorphologyDecision
from tests.test_robot_sdk_cq_parts import FakeCadQuery


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


class LayoutAgentTest(unittest.TestCase):
    def test_schema_rejects_unknown_link_morphology(self) -> None:
        with self.assertRaises(ValueError):
            LinkMorphologyDecision(
                link_id="L1",
                morphology="not_a_link_type",
                reason="bad",
                confidence=0.5,
            )

    def test_ur3e_decision_assigns_wrist_morphology(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(profile_name="ur3e")
        )
        agent = LayoutAgent()

        result = agent.build_result(
            {
                "request": "基于 UR3e 生成 6DoF 机械臂 CAD。",
                "kinematic_model": scaling.model,
                "profile_name": "ur3e",
                "source_mode": "exact_profile",
            }
        )

        by_link = {item.link_id: item.morphology for item in result.decision.links}
        by_joint = {item.joint_id: item.morphology for item in result.decision.joints}
        self.assertEqual(result.decision.layout_source, "layout_agent")
        self.assertEqual(result.decision.robot_family, "ur_style_6axis_cobot")
        self.assertEqual(by_joint["J6"], "tool_flange_joint")
        self.assertEqual(by_link["L2"], "upper_arm_link")
        self.assertEqual(by_link["L3"], "forearm_link")
        self.assertEqual(by_link["L4"], "wrist1_offset_housing")
        self.assertEqual(by_link["L5"], "wrist2_elbow_cylinder")
        self.assertEqual(by_link["L6"], "wrist3_tool_flange")
        self.assertEqual(
            [item.name for item in result.decision.subassemblies],
            [
                "J6_L6_end_effector",
                "J5_L5_J6",
                "J4_L4_J5",
                "J3_L3_J4",
                "wrist_group_J4_to_end_effector",
            ],
        )
        self.assertIsNotNone(result.structure_plan)
        self.assertEqual(result.structure_plan.family, "ur_style_6axis_cobot")
        self.assertEqual(result.structure_plan.source, "layout_agent")
        self.assertEqual(
            {station.role for station in result.structure_plan.stations},
            {"base", "shoulder", "elbow", "wrist1", "wrist2", "wrist3", "tool"},
        )
        structure_report = validate_robot_structure(
            kinematic_model=scaling.model,
            structure_plan=result.structure_plan,
        )
        self.assertTrue(structure_report.ok, [issue.message for issue in structure_report.errors])
        self.assertIn("axis_topology_valid", {issue.code for issue in structure_report.passes})

    def test_generic_6dof_decision_gets_structure_plan_even_for_planar_dh(self) -> None:
        model = _planar_6dof_model()
        agent = LayoutAgent()

        result = agent.build_result(
            {
                "request": "做一个 6DOF 机械臂，工作空间 500mm。",
                "kinematic_model": model,
                "source_mode": "template_fallback",
            }
        )

        self.assertEqual(result.decision.robot_family, "generic_serial_arm")
        self.assertIsNotNone(result.structure_plan)
        self.assertEqual(result.structure_plan.metadata["reach_mm"], 500.0)
        self.assertEqual(result.structure_plan.metadata["reach_source"], "request_text")
        structure_report = validate_robot_structure(
            kinematic_model=model,
            structure_plan=result.structure_plan,
        )
        self.assertTrue(structure_report.ok, [issue.message for issue in structure_report.errors])
        self.assertIn(
            "structure_plan_overrides_planar_template",
            {issue.code for issue in structure_report.passes},
        )

    def test_llm_structure_plan_is_parsed_into_result(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(profile_name="ur3e")
        )
        structure_plan = build_generic_6axis_cobot_structure_plan(reach_mm=500).to_dict()
        structure_plan["name"] = "llm_structure"
        structure_plan["family"] = "llm_6axis_cobot"
        structure_plan["source"] = "layout_agent"
        payload = {
            "layout_source": "layout_agent",
            "robot_family": "llm_6axis_cobot",
            "confidence": 0.9,
            "joints": [
                {
                    "joint_id": f"J{index}",
                    "morphology": "generic_revolute_joint",
                    "reason": "LLM test joint decision.",
                    "confidence": 0.7,
                    "source_signals": ["test"],
                }
                for index in range(1, 7)
            ],
            "links": [
                {
                    "link_id": f"L{index}",
                    "morphology": "generic_straight_link",
                    "reason": "LLM test link decision.",
                    "confidence": 0.7,
                    "source_signals": ["test"],
                }
                for index in range(1, 7)
            ],
            "interfaces": [],
            "subassemblies": [],
            "structure_plan": structure_plan,
            "assumptions": [],
            "warnings": [],
        }
        agent = LayoutAgent(llm=_ScriptedLLM([json.dumps(payload)]))

        result = agent.build_result(
            {
                "request": "LLM 输出结构计划",
                "kinematic_model": scaling.model,
                "profile_name": "ur3e",
                "source_mode": "exact_profile",
            }
        )

        self.assertIsNotNone(result.structure_plan)
        self.assertEqual(result.structure_plan.name, "llm_structure")
        self.assertEqual(result.structure_plan.family, "llm_6axis_cobot")
        self.assertEqual(result.trace[-2]["structure_validation"]["ok"], True)

    def test_decision_adapter_writes_morphology_metadata(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(profile_name="ur3e")
        )
        decision = LayoutAgent().build_result(
            {
                "request": "UR3e 6DoF CAD",
                "kinematic_model": scaling.model,
                "profile_name": "ur3e",
                "source_mode": "exact_profile",
            }
        ).decision

        layout = build_mechanical_layout_with_decision(scaling.model, decision)

        by_link = {link.id: link for link in layout.links}
        by_joint = {joint.id: joint for joint in layout.joints}
        self.assertEqual(layout.metadata["layout_source"], "layout_agent")
        self.assertEqual(layout.metadata["robot_family"], "ur_style_6axis_cobot")
        self.assertEqual(by_joint["J6"].metadata["morphology"], "tool_flange_joint")
        self.assertEqual(by_link["L5"].metadata["morphology"], "wrist2_elbow_cylinder")
        self.assertIn("layout_decision", by_link["L5"].metadata)
        self.assertEqual(
            [item["name"] for item in layout.metadata["local_subassemblies"]],
            [
                "J6_L6_end_effector",
                "J5_L5_J6",
                "J4_L4_J5",
                "J3_L3_J4",
                "wrist_group_J4_to_end_effector",
            ],
        )
        self.assertEqual(
            [spec.name for spec in default_local_subassembly_specs(layout)],
            [
                "J6_L6_end_effector",
                "J5_L5_J6",
                "J4_L4_J5",
                "J3_L3_J4",
                "wrist_group_J4_to_end_effector",
                "base_J1_mount",
            ],
        )

    def test_cad_parts_consume_layout_morphology_before_primitive(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(profile_name="ur3e")
        )
        decision = LayoutAgent().build_result(
            {
                "request": "UR3e 6DoF CAD",
                "kinematic_model": scaling.model,
                "profile_name": "ur3e",
                "source_mode": "exact_profile",
            }
        ).decision
        layout = build_mechanical_layout_with_decision(scaling.model, decision)

        catalog = build_cadquery_parts(layout, cq_module=FakeCadQuery)
        l5 = catalog.require("L5")
        ops = [op[0] for op in l5.solid.ops]

        self.assertEqual(l5.metadata["morphology"], "wrist2_elbow_cylinder")
        self.assertNotIn("box", ops)
        self.assertIn("circle", ops)
        self.assertIn("extrude", ops)

    def test_cad_parts_consume_joint_morphology(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(profile_name="ur3e")
        )
        decision = LayoutAgent().build_result(
            {
                "request": "UR3e 6DoF CAD",
                "kinematic_model": scaling.model,
                "profile_name": "ur3e",
                "source_mode": "exact_profile",
            }
        ).decision
        layout = build_mechanical_layout_with_decision(scaling.model, decision)

        catalog = build_cadquery_parts(layout, cq_module=FakeCadQuery)
        shoulder = catalog.require("J2")
        tool_flange = catalog.require("J6")
        shoulder_ops = [op[0] for op in shoulder.solid.ops]
        tool_ops = [op[0] for op in tool_flange.solid.ops]

        self.assertEqual(shoulder.metadata["morphology"], "shoulder_joint")
        self.assertEqual(tool_flange.metadata["morphology"], "tool_flange_joint")
        self.assertIn("box", shoulder_ops)
        self.assertNotIn("box", tool_ops)
        self.assertIn("circle", tool_ops)

    def test_source_parts_consume_layout_morphology(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(profile_name="ur3e")
        )
        decision = LayoutAgent().build_result(
            {
                "request": "UR3e 6DoF CAD",
                "kinematic_model": scaling.model,
                "profile_name": "ur3e",
                "source_mode": "exact_profile",
            }
        ).decision
        layout = build_mechanical_layout_with_decision(scaling.model, decision)

        source_result = build_source_robot_from_layout(layout)
        shoulder = source_result.part_catalog.require("J2")
        wrist_link = source_result.part_catalog.require("L5")

        self.assertEqual(shoulder.metadata["morphology"], "shoulder_joint")
        self.assertEqual(shoulder.metadata["primitive_family"], "shoulder_block")
        self.assertEqual(wrist_link.metadata["morphology"], "wrist2_elbow_cylinder")
        self.assertEqual(wrist_link.metadata["primitive_family"], "wrist_elbow_cylinder")
        self.assertIn("bent_cylindrical_housing", wrist_link.metadata["visual_features"])
        self.assertIn("dual_flanges", wrist_link.metadata["visual_features"])
        self.assertIn("pilot_hub", wrist_link.metadata["visual_features"])


if __name__ == "__main__":
    unittest.main()


def _planar_6dof_model() -> KinematicModel:
    joints = []
    links = []
    dh_params = []
    previous_link = "base"
    for index in range(1, 7):
        joint_id = f"J{index}"
        link_id = f"L{index}"
        joints.append(
            JointSpec(
                id=joint_id,
                type="revolute",
                parent_link=previous_link,
                child_link=link_id,
            )
        )
        links.append(LinkSpec(id=link_id, length=80.0, parent_joint=joint_id))
        dh_params.append(
            DHParam(
                joint_id=joint_id,
                a=80.0,
                alpha=0.0,
                d=0.0,
                theta=0.0,
                variable=f"theta{index}",
            )
        )
        previous_link = link_id
    return KinematicModel(convention="dh", joints=joints, links=links, dh_params=dh_params)
