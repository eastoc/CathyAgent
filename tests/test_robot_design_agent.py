import tempfile
import unittest
import json
from dataclasses import replace
from pathlib import Path

from robot_sdk.cad.cq_assembly import build_cadquery_assembly
from robot_sdk.cad.source_robot_builder import build_source_robot_from_layout
from robot_sdk.kinematics.scaling import (
    KinematicScalingRequest,
    build_profile_kinematic_model,
)
from robot_sdk.layout.decision_adapter import build_mechanical_layout_with_decision
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam
from subagents.kinematics_agent.schema import KinematicsAgentResult
from subagents.layout_agent import LayoutAgent
from subagents.robot_design_agent import RobotDesignAgent
from subagents.robot_design_agent.agent import (
    _output_path,
    _parse_dof,
    _parse_length_mm,
    _parse_payload_g,
    _safe_session_dir,
)
from subagents.robot_design_agent.rules import (
    build_cad_export_subassembly_specs,
    build_cad_snapshot_subassembly_names,
    cad_local_subassembly_name,
    cad_part_export_name,
    cad_subassembly_name,
)

from tests.test_robot_sdk_cq_assembly import FakeCadQueryWithAssembly


class _FakeKinematicsAgent:
    def __init__(self, mode: str = "exact_profile", target_reach: float | None = None) -> None:
        self.mode = mode
        self.target_reach = target_reach
        self.calls: list[dict] = []

    def build_result(self, params: dict) -> KinematicsAgentResult:
        self.calls.append(dict(params))
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode=self.mode,
                target_reach=self.target_reach,
            )
        )
        return KinematicsAgentResult(
            kinematic_model=scaling.model,
            source_mode=scaling.mode,
            source_summary=f"fake {scaling.mode}",
            profile_name=scaling.profile_name,
            target_reach=scaling.target_reach,
            target_reach_unit=scaling.target_reach_unit,
            scale_factor=scaling.scale_factor,
            assumptions=scaling.assumptions,
            warnings=scaling.warnings,
            trace=[{"type": "fake_kinematics"}],
        )


class _FakeSourceCadResult:
    def __init__(self, source_result) -> None:
        self.part_catalog = source_result.part_catalog
        self.metadata = dict(source_result.metadata)
        self.local_subassembly_results = []


class RobotDesignAgentTest(unittest.TestCase):
    def test_parse_helpers_handle_chinese_robot_request(self) -> None:
        text = "设计一个桌面 4 自由度机械臂，用于搬运 500g 物体，工作半径 400mm，输出 CAD 初版。"

        self.assertEqual(_parse_dof(text), 4)
        self.assertEqual(_parse_payload_g(text), 500.0)
        self.assertEqual(_parse_length_mm(text), 400.0)

    def test_run_fixed_mvp_flow_exports_step_and_validates(self) -> None:
        agent = RobotDesignAgent(cq_module=FakeCadQueryWithAssembly)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = agent.run(
                {
                    "request": "设计一个桌面 4 自由度机械臂，用于搬运 500g 物体，工作半径 400mm，输出 CAD 初版。",
                    "output_dir": temp_dir,
                    "export_filename": "arm.step",
                    "cad_backend": "cadquery",
                }
            )

            output_path = Path(temp_dir) / "arm.step"
            requirement_doc = Path(temp_dir) / "需求文档.md"
            self.assertTrue(result.finished, result.final_answer)
            self.assertTrue(output_path.exists())
            self.assertTrue(requirement_doc.exists())
            requirement_text = requirement_doc.read_text(encoding="utf-8")
            self.assertIn("## 设计需求", requirement_text)
            self.assertIn("## 约束", requirement_text)
            self.assertIn("| DoF | 4 | - |", requirement_text)
            self.assertIn("## DH 模型", requirement_text)
            self.assertIn("Source mode: template_fallback", requirement_text)
            self.assertIn("## MechanicalLayout 决策", requirement_text)
            self.assertIn("Layout source: rule_fallback", requirement_text)
            self.assertIn("### Joint Morphology", requirement_text)
            self.assertIn("### Link Morphology", requirement_text)
            self.assertIn("## Assembly Constraint Graph", requirement_text)
            self.assertIn("Full assembly solve gate", requirement_text)
            self.assertIn("clear_for_fixture", requirement_text)
            self.assertIn("| Local subassembly | Parts | Edges |", requirement_text)
            self.assertIn("## CAD Visual Snapshot", requirement_text)
            self.assertIn("Fallback snapshots:", requirement_text)
            self.assertIn("### Focus Subassemblies", requirement_text)
            self.assertIn("joint4_link4_end_effector", requirement_text)
            self.assertIn("| J1 |", requirement_text)
            self.assertTrue((Path(temp_dir) / "base" / "base_body.step").exists())
            self.assertTrue((Path(temp_dir) / "base" / "base.step").exists())
            self.assertTrue((Path(temp_dir) / "joint1" / "joint1_housing.step").exists())
            self.assertTrue((Path(temp_dir) / "link1" / "link1_body.step").exists())
            self.assertTrue((Path(temp_dir) / "structure_plan.json").exists())
            self.assertTrue((Path(temp_dir) / "mechanical_layout.json").exists())
            self.assertTrue((Path(temp_dir) / "robot_model.py").exists())
            self.assertIn("Source artifacts:", result.final_answer)
            structure_payload = json.loads(
                (Path(temp_dir) / "structure_plan.json").read_text(encoding="utf-8")
            )
            self.assertIsNone(structure_payload["structure_plan"])
            robot_model_source = (Path(temp_dir) / "robot_model.py").read_text(encoding="utf-8")
            self.assertIn("structure_plan.json", robot_model_source)
            self.assertIn("mechanical_layout.json", robot_model_source)
            self.assertIn("固定 MVP 流程已完成", result.final_answer)
            self.assertIn("DOF: 4", result.final_answer)
            self.assertIn("运动学来源: template_fallback", result.final_answer)
            self.assertIn("Assembly mode: constraint_solve", result.final_answer)
            self.assertIn("Production assembly source: full_semantic_solve", result.final_answer)
            self.assertIn("Assembly placement: full_semantic_solve", result.final_answer)
            self.assertIn("Solver constraint mode: semantic_mate_constraints", result.final_answer)
            self.assertIn("Semantic constraints applied: full_assembly", result.final_answer)
            self.assertIn("Assembly.solve: 成功", result.final_answer)

    def test_run_defaults_to_source_joint_backend(self) -> None:
        agent = RobotDesignAgent()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = agent.run(
                {
                    "request": "设计一个桌面 2 自由度机械臂，工作半径 200mm，输出 STEP。",
                    "dof": 2,
                    "reach_mm": 200,
                    "output_dir": temp_dir,
                    "export_filename": "default_source.step",
                }
            )

            output_path = Path(temp_dir) / "default_source.step"
            self.assertTrue(result.finished, result.final_answer)
            self.assertTrue(output_path.exists())
            self.assertIn("Production assembly source: source_joint", result.final_answer)
            cad_trace = next(
                item for item in result.trace if item.get("type") == "cad_assembly"
            )
            self.assertEqual(cad_trace["cad_backend"], "source_joint")

    def test_run_source_joint_backend_exports_resolved_step(self) -> None:
        agent = RobotDesignAgent()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = agent.run(
                {
                    "request": "设计一个桌面 2 自由度机械臂，工作半径 200mm，输出 source joint STEP。",
                    "dof": 2,
                    "reach_mm": 200,
                    "output_dir": temp_dir,
                    "export_filename": "source_arm.step",
                    "cad_backend": "source_joint",
                }
            )

            output_path = Path(temp_dir) / "source_arm.step"
            self.assertTrue(result.finished, result.final_answer)
            self.assertTrue(output_path.exists())
            self.assertIn("Production assembly source: source_joint", result.final_answer)
            self.assertIn("Assembly mode: source_joint", result.final_answer)
            self.assertIn("Assembly placement: source_joint_connect_to", result.final_answer)
            self.assertIn("Solver constraint mode: build123d_native_joints", result.final_answer)
            self.assertIn("Semantic constraints applied: source_joint", result.final_answer)
            self.assertIn("STEP package:", result.final_answer)
            self.assertIn("Visual snapshots:", result.final_answer)
            self.assertTrue((Path(temp_dir) / "base" / "base_body.step").exists())
            self.assertTrue((Path(temp_dir) / "base" / "base.step").exists())
            self.assertTrue((Path(temp_dir) / "joint1" / "joint1_housing.step").exists())
            self.assertTrue((Path(temp_dir) / "joint1" / "joint1.step").exists())
            self.assertTrue((Path(temp_dir) / "link1" / "link1_body.step").exists())
            self.assertTrue((Path(temp_dir) / "link1" / "link1.step").exists())
            cad_trace = next(
                item for item in result.trace if item.get("type") == "cad_assembly"
            )
            self.assertEqual(cad_trace["cad_backend"], "source_joint")
            self.assertEqual(cad_trace["production_assembly_source"], "source_joint")
            self.assertGreater(len(cad_trace["source_mates"]), 0)
            self.assertIn(str(output_path), result.final_answer)
            requirement_text = (Path(temp_dir) / "需求文档.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("## CAD Visual Snapshot", requirement_text)
            snapshot_trace = next(
                item for item in result.trace if item.get("type") == "visual_snapshots"
            )
            self.assertGreater(snapshot_trace["generated_count"], 0)
            export_trace = next(
                item for item in result.trace if item.get("type") == "export"
            )
            self.assertEqual(export_trace["cad_backend"], "source_joint")
            self.assertEqual(export_trace["file_count"], 13)
            self.assertEqual(len(export_trace["subassemblies"]), 6)

            trace_types = [item["type"] for item in result.trace]
            self.assertEqual(
                trace_types,
                [
                    "requirement",
                    "kinematic_model",
                    "mechanical_layout",
                    "requirement_document",
                    "source_artifacts",
                    "cad_assembly",
                    "export",
                    "visual_snapshots",
                    "validation",
                ],
            )
            validation = result.trace[-1]
            self.assertTrue(validation["ok"])
            self.assertTrue(
                any("cad_solve.source_joint_constraints_applied" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("export_geometry.export_bbox_valid" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("export_geometry.step_package_bboxes_consistent" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("source_joint.source_joint_promotion_gate_ready" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("source_joint.source_joint_step_package_ready" in item for item in validation["passes"])
            )

    def test_run_source_joint_backend_exports_6dof_smoke(self) -> None:
        agent = RobotDesignAgent(layout_agent=LayoutAgent())
        with tempfile.TemporaryDirectory() as temp_dir:
            result = agent.run(
                {
                    "request": "建模一个 6DOF 机械臂，工作空间 500mm，输出 source joint STEP。",
                    "dof": 6,
                    "reach_mm": 500,
                    "output_dir": temp_dir,
                    "export_filename": "source_6dof.step",
                    "cad_backend": "source_joint",
                }
            )

            output_path = Path(temp_dir) / "source_6dof.step"
            self.assertTrue(result.finished, result.final_answer)
            self.assertTrue(output_path.exists())
            self.assertIn("DOF: 6", result.final_answer)
            self.assertIn("Production assembly source: source_joint", result.final_answer)
            self.assertIn("Source review bbox: ready", result.final_answer)
            self.assertIn("STEP package:", result.final_answer)
            self.assertIn("Visual snapshots:", result.final_answer)
            self.assertTrue((Path(temp_dir) / "joint6" / "joint6_housing.step").exists())
            self.assertTrue((Path(temp_dir) / "link6" / "link6_body.step").exists())
            self.assertTrue((Path(temp_dir) / "base_shoulder" / "base_shoulder.step").exists())
            self.assertTrue((Path(temp_dir) / "upper_arm" / "upper_arm.step").exists())
            self.assertTrue((Path(temp_dir) / "forearm" / "forearm.step").exists())
            self.assertTrue((Path(temp_dir) / "wrist_l5_j6" / "wrist_l5_j6.step").exists())
            self.assertTrue((Path(temp_dir) / "tool_end" / "tool_end.step").exists())

            cad_trace = next(
                item for item in result.trace if item.get("type") == "cad_assembly"
            )
            self.assertEqual(cad_trace["cad_backend"], "source_joint")
            self.assertEqual(cad_trace["part_count"], 14)
            self.assertEqual(cad_trace["constraint_count"], 13)
            self.assertEqual(len(cad_trace["source_mates"]), 13)
            self.assertEqual(cad_trace["source_builder"], "source_robot_builder_from_layout")
            self.assertEqual(cad_trace["layout_source"], "layout_agent")
            routes = {item["link_id"]: item for item in cad_trace["link_routes"]}
            self.assertEqual(routes["L1"]["route_vector"], (0.0, 0.0, 90.0))
            self.assertGreater(routes["L2"]["route_vector"][2], 0.0)
            self.assertLess(routes["L3"]["route_vector"][2], 0.0)

            snapshot_trace = next(
                item for item in result.trace if item.get("type") == "visual_snapshots"
            )
            self.assertGreater(snapshot_trace["generated_count"], 0)
            self.assertGreater(snapshot_trace["real_step_snapshot_count"], 0)
            snapshot_targets = {
                item["target_name"]
                for item in snapshot_trace["subassembly_snapshots"]
            }
            self.assertTrue(
                {
                    "base_shoulder",
                    "upper_arm",
                    "forearm",
                    "wrist_l5_j6",
                    "tool_end",
                }.issubset(snapshot_targets)
            )
            export_trace = next(
                item for item in result.trace if item.get("type") == "export"
            )
            self.assertEqual(export_trace["file_count"], 50)
            self.assertEqual(len(export_trace["subassemblies"]), 19)
            export_subassemblies = {item["name"] for item in export_trace["subassemblies"]}
            self.assertTrue(
                {
                    "base_shoulder",
                    "upper_arm",
                    "forearm",
                    "wrist_l5_j6",
                    "tool_end",
                }.issubset(export_subassemblies)
            )

            validation = result.trace[-1]
            self.assertTrue(validation["ok"], validation["errors"])
            self.assertTrue(
                any("cad_solve.source_joint_constraints_applied" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("export_geometry.export_bbox_valid" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("export_geometry.step_package_bboxes_consistent" in item for item in validation["passes"])
            )
            self.assertTrue(
                any(
                    "visual_geometry.visual_snapshot_silhouette_screened" in item
                    for item in validation["passes"]
                )
            )
            self.assertTrue(
                any("source_joint.source_joint_promotion_gate_ready" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("source_joint.source_joint_step_package_ready" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("source_joint.source_joint_review_bbox_ratios_ready" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("visual_geometry.source_joint_review_snapshots_ready" in item for item in validation["passes"])
            )
            self.assertTrue(
                any("stage7.stage7_robot_cad_ready" in item for item in validation["passes"])
            )
            self.assertFalse(
                any("stage7.stage7_robot_cad_not_ready" in item for item in validation["warnings"])
            )
            requirement_text = (Path(temp_dir) / "需求文档.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("## CAD Review Metrics", requirement_text)
            self.assertIn("Source joint review bbox gate: pass", requirement_text)
            self.assertIn("| wrist_l5_j6 |", requirement_text)
            self.assertIn("| Review subassembly | x/cross | z/x | y/x | minor/major | bbox x/y/z(mm) |", requirement_text)

    def test_run_consumes_kinematics_agent_result(self) -> None:
        kinematics_agent = _FakeKinematicsAgent()
        agent = RobotDesignAgent(
            cq_module=FakeCadQueryWithAssembly,
            kinematics_agent=kinematics_agent,
            layout_agent=LayoutAgent(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = agent.run(
                {
                    "request": "我要基于 UR3e 的 DH 模型生成 CAD MVP。",
                    "output_dir": temp_dir,
                    "export_filename": "ur3e.step",
                    "cad_backend": "cadquery",
                }
            )

            requirement_doc = Path(temp_dir) / "需求文档.md"
            requirement_text = requirement_doc.read_text(encoding="utf-8")
            self.assertTrue(result.finished, result.final_answer)
            self.assertEqual(len(kinematics_agent.calls), 1)
            self.assertEqual(kinematics_agent.calls[0]["dof"], 4)
            self.assertIn("Source mode: exact_profile", requirement_text)
            self.assertIn("Profile: ur3e", requirement_text)
            self.assertIn("| J2 | -243.55 |", requirement_text)
            self.assertIn("Layout source: layout_agent", requirement_text)
            self.assertIn("Robot family: ur_style_6axis_cobot", requirement_text)
            self.assertIn("- Template: structure_plan", requirement_text)
            self.assertIn("### Structure Plan", requirement_text)
            self.assertIn("| J6 | tool_flange_joint |", requirement_text)
            self.assertIn("| L5 | wrist2_elbow_cylinder |", requirement_text)
            self.assertIn("运动学来源: exact_profile", result.final_answer)
            self.assertIn("Profile: ur3e", result.final_answer)
            self.assertIn("Layout source: layout_agent", result.final_answer)
            self.assertIn("Robot family: ur_style_6axis_cobot", result.final_answer)
            self.assertIn("Layout template: structure_plan", result.final_answer)
            self.assertIn("L5=wrist2_elbow_cylinder", result.final_answer)

            trace = next(item for item in result.trace if item["type"] == "kinematic_model")
            self.assertEqual(trace["source_mode"], "exact_profile")
            self.assertEqual(trace["profile_name"], "ur3e")
            self.assertEqual(trace["kinematics_trace"], [{"type": "fake_kinematics"}])
            layout_trace = next(item for item in result.trace if item["type"] == "mechanical_layout")
            self.assertEqual(layout_trace["layout_source"], "layout_agent")
            self.assertEqual(layout_trace["robot_family"], "ur_style_6axis_cobot")
            self.assertEqual(layout_trace["layout_template"], "structure_plan")
            self.assertIsNotNone(layout_trace["structure_plan"])
            self.assertEqual(layout_trace["link_morphology"]["L5"], "wrist2_elbow_cylinder")

    def test_run_records_scaled_kinematics_metadata(self) -> None:
        kinematics_agent = _FakeKinematicsAgent(mode="scaled_profile", target_reach=800)
        agent = RobotDesignAgent(
            cq_module=FakeCadQueryWithAssembly,
            kinematics_agent=kinematics_agent,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = agent.run(
                {
                    "request": "参考 UR3e，但臂展做到 800mm，生成 CAD MVP。",
                    "reach_mm": 800,
                    "output_dir": temp_dir,
                    "export_filename": "scaled.step",
                    "cad_backend": "cadquery",
                }
            )

            requirement_text = (Path(temp_dir) / "需求文档.md").read_text(encoding="utf-8")
            self.assertTrue(result.finished, result.final_answer)
            self.assertIn("Source mode: scaled_profile", requirement_text)
            self.assertIn("Scale factor:", requirement_text)
            self.assertIn("运动学来源: scaled_profile", result.final_answer)

    def test_run_uses_defaults_when_request_is_vague(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent = RobotDesignAgent(
                cq_module=FakeCadQueryWithAssembly,
                workspace_root=temp_dir,
            )
            agent.attach_session("session-001")
            result = agent.run(
                {
                    "request": "帮我先出一个桌面机械臂 CAD MVP。",
                }
            )

            output_path = Path(temp_dir) / "session-001" / "整机.step"
            requirement_doc = Path(temp_dir) / "session-001" / "需求文档.md"
            self.assertTrue(result.finished, result.final_answer)
            self.assertTrue(output_path.exists())
            self.assertTrue(requirement_doc.exists())
            self.assertTrue((Path(temp_dir) / "session-001" / "base" / "base_body.step").exists())
            self.assertTrue((Path(temp_dir) / "session-001" / "joint1" / "joint1_housing.step").exists())
            self.assertIn(str(output_path), result.final_answer)
            self.assertIn("DOF: 4", result.final_answer)
            self.assertIn("工作半径: 400 mm", result.final_answer)
            self.assertIn("默认使用 4 DOF", result.final_answer)

    def test_run_blocks_6dof_planar_template_before_cad_export(self) -> None:
        agent = RobotDesignAgent(cq_module=FakeCadQueryWithAssembly)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = agent.run(
                {
                    "request": "建模一个 6DOF 机械臂，工作空间 500mm。",
                    "output_dir": temp_dir,
                    "export_filename": "bad.step",
                }
            )

            self.assertFalse(result.finished)
            self.assertIn("已停止 CAD 生成", result.final_answer)
            self.assertIn("six_axis_planar_template_rejected", result.final_answer)
            self.assertFalse((Path(temp_dir) / "bad.step").exists())
            requirement_doc = Path(temp_dir) / "需求文档.md"
            self.assertTrue(requirement_doc.exists())
            requirement_text = requirement_doc.read_text(encoding="utf-8")
            self.assertIn("## Structure Validation", requirement_text)
            self.assertIn("six_axis_planar_template_rejected", requirement_text)
            self.assertEqual(
                [item["type"] for item in result.trace],
                [
                    "requirement",
                    "kinematic_model",
                    "mechanical_layout",
                    "requirement_document",
                    "structure_validation",
                ],
            )

    def test_run_uses_layout_structure_plan_for_generic_6dof(self) -> None:
        agent = RobotDesignAgent(
            layout_agent=LayoutAgent(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = agent.run(
                {
                    "request": "建模一个 6DOF 机械臂，工作空间 500mm。",
                    "output_dir": temp_dir,
                    "export_filename": "generic_6dof.step",
                }
            )

            self.assertTrue(result.finished, result.final_answer)
            self.assertTrue((Path(temp_dir) / "generic_6dof.step").exists())
            self.assertTrue((Path(temp_dir) / "structure_plan.json").exists())
            self.assertTrue((Path(temp_dir) / "mechanical_layout.json").exists())
            self.assertTrue((Path(temp_dir) / "robot_model.py").exists())
            structure_payload = json.loads(
                (Path(temp_dir) / "structure_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(structure_payload["family"], "generic_serial_arm")
            self.assertEqual(structure_payload["metadata"]["reach_mm"], 500.0)
            self.assertEqual(
                structure_payload["metadata"]["reach_source"],
                "explicit_reach_mm",
            )
            mechanical_layout_payload = json.loads(
                (Path(temp_dir) / "mechanical_layout.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                mechanical_layout_payload["metadata"]["layout_template"],
                "structure_plan",
            )
            requirement_text = (Path(temp_dir) / "需求文档.md").read_text(encoding="utf-8")
            self.assertIn("- Template: structure_plan", requirement_text)
            self.assertIn("Structure source: layout_agent", requirement_text)
            layout_trace = next(
                item for item in result.trace if item["type"] == "mechanical_layout"
            )
            self.assertEqual(layout_trace["layout_template"], "structure_plan")
            self.assertIsNotNone(layout_trace["structure_plan"])
            self.assertEqual(
                layout_trace["structure_plan"]["family"],
                "generic_serial_arm",
            )
            validation = result.trace[-1]
            self.assertTrue(validation["ok"], validation["errors"])
            self.assertTrue(
                any("structure.structure_plan_overrides_planar_template" in item for item in validation["passes"])
            )
            self.assertIn("Layout template: structure_plan", result.final_answer)

    def test_empty_request_fails_cleanly(self) -> None:
        agent = RobotDesignAgent(cq_module=FakeCadQueryWithAssembly)

        result = agent.run({"request": ""})

        self.assertFalse(result.finished)
        self.assertIn("request 不能为空", result.final_answer)

    def test_default_output_path_uses_workspace_session_dir(self) -> None:
        self.assertEqual(
            _output_path(
                {},
                workspace_root="/tmp/cathy-cad",
                session_id="session-001",
            ),
            Path("/tmp/cathy-cad/session-001/整机.step"),
        )

    def test_relative_output_dir_stays_inside_workspace_session_dir(self) -> None:
        self.assertEqual(
            _output_path(
                {"output_dir": "exports", "export_filename": "arm"},
                workspace_root="/tmp/cathy-cad",
                session_id="session-001",
            ),
            Path("/tmp/cathy-cad/session-001/exports/arm.step"),
        )

    def test_session_dir_is_sanitized(self) -> None:
        self.assertEqual(_safe_session_dir("../bad/session"), "bad_session")
        self.assertEqual(_safe_session_dir(""), "default")

    def test_cad_naming_rules_use_semantic_names(self) -> None:
        self.assertEqual(cad_subassembly_name("base"), "base")
        self.assertEqual(cad_subassembly_name("J1"), "joint1")
        self.assertEqual(cad_subassembly_name("L2"), "link2")
        self.assertEqual(cad_subassembly_name("left_frontleg"), "left_frontleg")
        self.assertEqual(cad_part_export_name("J1"), "joint1_housing")
        self.assertEqual(cad_part_export_name("L1"), "link1_body")
        self.assertEqual(
            cad_local_subassembly_name("J2_L2_end_effector"),
            "joint2_link2_end_effector",
        )

    def test_export_rules_include_local_solved_subassemblies(self) -> None:
        cad_result = build_cadquery_assembly(
            build_tabletop_serial_mechanical_layout(
                [
                    DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                    DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
                ]
            ),
            cq_module=FakeCadQueryWithAssembly,
        )

        specs = build_cad_export_subassembly_specs(cad_result)

        local_specs = [spec for spec in specs if spec.source_local_subassembly_name]
        self.assertEqual(
            [spec.name for spec in local_specs],
            ["joint2_link2_end_effector", "joint1_link1_joint2", "base_joint1_mount"],
        )
        self.assertEqual(local_specs[0].source_local_subassembly_name, "J2_L2_end_effector")
        self.assertEqual(local_specs[0].part_ids, ["J2", "L2", "end_effector"])
        self.assertEqual(local_specs[0].local_solve_usage, "production_local_solve")
        self.assertEqual(local_specs[2].source_local_subassembly_name, "base_J1_mount")
        self.assertEqual(local_specs[2].part_ids, ["base", "J1"])
        self.assertEqual(local_specs[2].local_solve_usage, "production_local_solve")

    def test_snapshot_rules_focus_local_tool_and_joint_link_subassemblies(self) -> None:
        cad_result = build_cadquery_assembly(
            build_tabletop_serial_mechanical_layout(
                [
                    DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                    DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
                ]
            ),
            cq_module=FakeCadQueryWithAssembly,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            from robot_sdk.cad.export import export_robot_step_package

            package = export_robot_step_package(
                cad_result,
                temp_dir,
                subassemblies=build_cad_export_subassembly_specs(cad_result),
            )

            names = build_cad_snapshot_subassembly_names(package)

        self.assertIn("joint2_link2_end_effector", names)
        self.assertIn("joint1_link1_joint2", names)
        self.assertNotIn("base_joint1_mount", names)

    def test_source_joint_export_rules_add_review_subassemblies(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(profile_name="ur3e")
        )
        decision = LayoutAgent().build_result(
            {
                "request": "UR3e source joint review assemblies",
                "kinematic_model": scaling.model,
                "profile_name": "ur3e",
                "source_mode": "exact_profile",
            }
        ).decision
        layout = build_mechanical_layout_with_decision(scaling.model, decision)
        cad_result = _FakeSourceCadResult(build_source_robot_from_layout(layout))

        specs = build_cad_export_subassembly_specs(cad_result)
        by_name = {spec.name: spec for spec in specs}

        self.assertEqual(by_name["base_shoulder"].part_ids, ["base", "J1", "L1", "J2"])
        self.assertEqual(by_name["upper_arm"].part_ids, ["J2", "L2", "J3"])
        self.assertEqual(by_name["forearm"].part_ids, ["J3", "L3", "J4"])
        self.assertEqual(by_name["wrist_l5_j6"].part_ids, ["J5", "L5", "J6"])
        self.assertEqual(by_name["tool_end"].part_ids, ["J6", "L6", "end_effector"])

    def test_export_rules_mark_non_promoted_local_solve_as_experimental(self) -> None:
        cad_result = build_cadquery_assembly(
            build_tabletop_serial_mechanical_layout(
                [
                    DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                    DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
                ]
            ),
            cq_module=FakeCadQueryWithAssembly,
        )
        local_subassemblies = [
            dict(item)
            for item in cad_result.metadata["local_subassemblies"]
        ]
        local_subassemblies[0] = {
            **local_subassemblies[0],
            "pose_delta_report": {"max_translation_delta_mm": 0.2},
        }
        cad_result = replace(
            cad_result,
            metadata={
                **cad_result.metadata,
                "local_subassemblies": local_subassemblies,
            },
        )

        specs = build_cad_export_subassembly_specs(cad_result)

        local_specs = [spec for spec in specs if spec.source_local_subassembly_name]
        self.assertEqual(
            local_specs[0].local_solve_usage,
            "experimental_local_solve",
        )
        self.assertEqual(
            local_specs[1].local_solve_usage,
            "production_local_solve",
        )

    def test_subagent_schema_exposes_robot_design_agent_contract(self) -> None:
        agent = RobotDesignAgent(cq_module=FakeCadQueryWithAssembly)

        self.assertEqual(agent.name, "robot_design_agent")
        self.assertIn("request", agent.input_schema["required"])
        self.assertIn("output_dir", agent.input_schema["properties"])
        self.assertIn("建模", agent.description)
        self.assertIn("不要只调用 kinematics_agent", agent.description)


if __name__ == "__main__":
    unittest.main()
