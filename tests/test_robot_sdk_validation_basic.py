import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from robot_sdk.cad.bbox import CadBoundingBox
from robot_sdk.cad.cq_assembly import build_cadquery_assembly
from robot_sdk.cad.export import CadQueryExportResult, export_robot_step_package
from robot_sdk.layout.structure_adapter import build_mechanical_layout_from_structure_plan
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.structure import build_generic_6axis_cobot_structure_plan
from robot_sdk.types import (
    AssemblyConstraint,
    DHParam,
    JointSpec,
    KinematicModel,
    LinkSpec,
    RobotRequirement,
)
from robot_sdk.validation.basic import validate_robot_design
from subagents.robot_design_agent.rules import build_cad_export_subassembly_specs

from tests.test_robot_sdk_cq_assembly import (
    FailingCadQueryWithAssembly,
    FakeCadQueryWithAssembly,
)


class RobotSdkBasicValidationTest(unittest.TestCase):
    def _requirement(self, *, dof: int = 2, reach: float = 200.0) -> RobotRequirement:
        return RobotRequirement(
            task="desktop pick and place",
            dof=dof,
            reach=reach,
            reach_unit="mm",
        )

    def _kinematic_model(self) -> KinematicModel:
        joints = [
            JointSpec(id="J1", type="revolute", parent_link="base", child_link="L1"),
            JointSpec(id="J2", type="revolute", parent_link="L1", child_link="L2"),
        ]
        links = [
            LinkSpec(id="L1", length=120),
            LinkSpec(id="L2", length=100),
        ]
        dh_params = [
            DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
            DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
        ]
        return KinematicModel(
            convention="dh",
            joints=joints,
            links=links,
            dh_params=dh_params,
        )

    def _planar_6dof_kinematic_model(self) -> KinematicModel:
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
            links.append(LinkSpec(id=link_id, length=80, parent_joint=joint_id))
            dh_params.append(
                DHParam(
                    joint_id=joint_id,
                    a=80,
                    alpha=0,
                    d=0,
                    theta=0,
                    variable=f"theta{index}",
                )
            )
            previous_link = link_id
        return KinematicModel(convention="dh", joints=joints, links=links, dh_params=dh_params)

    def _layout(self):
        return build_tabletop_serial_mechanical_layout(self._kinematic_model().dh_params)

    def test_full_report_passes_for_valid_artifacts(self) -> None:
        layout = self._layout()
        cad_result = build_cadquery_assembly(
            layout,
            cq_module=FakeCadQueryWithAssembly,
            solve=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "robot.step"
            output_path.write_text("STEP", encoding="utf-8")
            export_result = CadQueryExportResult(
                path=output_path,
                export_type="STEP",
                exists=True,
                size_bytes=output_path.stat().st_size,
            )

            report = validate_robot_design(
                requirement=self._requirement(),
                kinematic_model=self._kinematic_model(),
                layout=layout,
                cad_result=cad_result,
                export_result=export_result,
            )

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn("dof_matches", {issue.code for issue in report.passes})
        self.assertIn("reach_covers_requirement", {issue.code for issue in report.passes})
        self.assertIn("layout_valid", {issue.code for issue in report.passes})
        self.assertIn("constraints_resolved", {issue.code for issue in report.passes})
        self.assertIn("cad_solve_passed", {issue.code for issue in report.passes})
        self.assertIn("local_subassembly_solve_passed", {issue.code for issue in report.passes})
        self.assertIn("full_assembly_fixture_solve_passed", {issue.code for issue in report.passes})
        self.assertIn(
            "full_assembly_fixture_ready_for_promotion",
            {issue.code for issue in report.passes},
        )
        self.assertIn(
            "local_subassembly_residuals_within_threshold",
            {issue.code for issue in report.passes},
        )
        self.assertIn(
            "local_subassembly_ready_for_promotion",
            {issue.code for issue in report.passes},
        )
        self.assertIn(
            "joint_link_joint_promotion_ready",
            {issue.code for issue in report.passes},
        )
        self.assertIn("export_exists", {issue.code for issue in report.passes})
        local_solve_issue = next(
            issue
            for issue in report.passes
            if issue.code == "local_subassembly_solve_passed"
        )
        self.assertEqual(local_solve_issue.details["max_local_pose_delta_mm"], 0.0)
        self.assertEqual(local_solve_issue.details["max_local_origin_residual_mm"], 0.0)
        self.assertEqual(local_solve_issue.details["max_local_normal_residual_deg"], 0.0)
        self.assertEqual(local_solve_issue.details["max_local_tangent_residual_deg"], 0.0)
        promotion_issue = next(
            issue
            for issue in report.passes
            if issue.code == "local_subassembly_ready_for_promotion"
        )
        ready_names = {
            item["name"]
            for item in promotion_issue.details["ready_local_subassemblies"]
        }
        self.assertIn("base_J1_mount", ready_names)
        self.assertIn("J1_L1_J2", ready_names)
        joint_link_issue = next(
            issue
            for issue in report.passes
            if issue.code == "joint_link_joint_promotion_ready"
        )
        joint_link_names = {
            item["name"]
            for item in joint_link_issue.details[
                "joint_link_joint_ready_local_subassemblies"
            ]
        }
        self.assertEqual(joint_link_names, {"J1_L1_J2"})

    def test_warns_when_local_subassembly_residual_exceeds_warning_threshold(self) -> None:
        cad_result = self._cad_result_with_local_solve_metrics(
            pose_delta_mm=0.2,
            origin_residual_mm=0.0,
            normal_residual_deg=0.0,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn(
            "local_subassembly_residual_threshold_warning",
            {issue.code for issue in report.warnings},
        )

    def test_fails_when_local_subassembly_residual_exceeds_error_threshold(self) -> None:
        cad_result = self._cad_result_with_local_solve_metrics(
            pose_delta_mm=1.5,
            origin_residual_mm=0.0,
            normal_residual_deg=0.0,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertFalse(report.ok)
        self.assertIn(
            "local_subassembly_residual_threshold_exceeded",
            {issue.code for issue in report.errors},
        )

    def test_warns_when_full_assembly_fixture_metric_exceeds_warning_threshold(
        self,
    ) -> None:
        cad_result = self._cad_result_with_full_fixture_metrics(
            pose_delta_mm=0.2,
            origin_residual_mm=0.0,
            normal_residual_deg=0.0,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn(
            "full_assembly_fixture_threshold_warning",
            {issue.code for issue in report.warnings},
        )

    def test_marks_full_assembly_fixture_ready_when_metrics_are_within_threshold(
        self,
    ) -> None:
        cad_result = self._cad_result_with_full_fixture_metrics(
            pose_delta_mm=0.0,
            origin_residual_mm=0.0,
            normal_residual_deg=0.0,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn(
            "full_assembly_fixture_ready_for_promotion",
            {issue.code for issue in report.passes},
        )

    def test_warns_when_full_assembly_fixture_metric_exceeds_error_threshold(
        self,
    ) -> None:
        cad_result = self._cad_result_with_full_fixture_metrics(
            pose_delta_mm=0.0,
            origin_residual_mm=1.5,
            normal_residual_deg=0.0,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn(
            "full_assembly_fixture_threshold_exceeded",
            {issue.code for issue in report.warnings},
        )

    def test_fails_when_local_subassembly_origin_residual_exceeds_error_threshold(
        self,
    ) -> None:
        cad_result = self._cad_result_with_local_solve_metrics(
            pose_delta_mm=0.0,
            origin_residual_mm=1.5,
            normal_residual_deg=0.0,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertFalse(report.ok)
        self.assertIn(
            "local_subassembly_residual_threshold_exceeded",
            {issue.code for issue in report.errors},
        )

    def test_fails_when_local_subassembly_angle_residual_exceeds_error_threshold(
        self,
    ) -> None:
        cad_result = self._cad_result_with_local_solve_metrics(
            pose_delta_mm=0.0,
            origin_residual_mm=0.0,
            normal_residual_deg=2.5,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertFalse(report.ok)
        self.assertIn(
            "local_subassembly_residual_threshold_exceeded",
            {issue.code for issue in report.errors},
        )

    def test_reports_dof_mismatch(self) -> None:
        report = validate_robot_design(
            requirement=self._requirement(dof=3),
            kinematic_model=self._kinematic_model(),
            layout=self._layout(),
        )

        self.assertFalse(report.ok)
        self.assertIn("dof_mismatch", {issue.code for issue in report.errors})

    def test_reports_reach_shortfall(self) -> None:
        report = validate_robot_design(
            requirement=self._requirement(reach=300),
            kinematic_model=self._kinematic_model(),
        )

        self.assertFalse(report.ok)
        self.assertIn("reach_shortfall", {issue.code for issue in report.errors})

    def test_rejects_high_dof_planar_template_without_structure_plan(self) -> None:
        report = validate_robot_design(
            requirement=self._requirement(dof=6, reach=400),
            kinematic_model=self._planar_6dof_kinematic_model(),
            layout=build_tabletop_serial_mechanical_layout(
                self._planar_6dof_kinematic_model().dh_params
            ),
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "six_axis_planar_template_rejected",
            {issue.code for issue in report.errors},
        )
        self.assertIn(
            "planar_serial_chain_visual_rejected",
            {issue.code for issue in report.errors},
        )

    def test_accepts_high_dof_planar_template_when_structure_plan_is_present(self) -> None:
        report = validate_robot_design(
            requirement=self._requirement(dof=6, reach=400),
            kinematic_model=self._planar_6dof_kinematic_model(),
            structure_plan=build_generic_6axis_cobot_structure_plan(reach_mm=400),
        )

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn("axis_topology_valid", {issue.code for issue in report.passes})

    def test_reports_layout_validation_errors(self) -> None:
        layout = self._layout()
        broken_feature = replace(layout.part_features[0], frame="missing_frame")
        broken_layout = replace(
            layout,
            part_features=[broken_feature, *layout.part_features[1:]],
        )

        report = validate_robot_design(layout=broken_layout)

        self.assertFalse(report.ok)
        self.assertIn("missing_part_feature_frame", {issue.code for issue in report.errors})

    def test_reports_constraint_plan_failure(self) -> None:
        layout = self._layout()
        bad_constraint = AssemblyConstraint(
            id="bad_constraint",
            fixed="base.top",
            moving="missing.feature",
            kind="Plane",
            rationale="Broken constraint coverage.",
        )
        broken_layout = replace(
            layout,
            assembly_constraints=[*layout.assembly_constraints, bad_constraint],
        )

        report = validate_robot_design(layout=broken_layout)

        self.assertFalse(report.ok)
        self.assertIn("constraint_plan_failed", {issue.code for issue in report.errors})

    def test_reports_cad_solve_failure(self) -> None:
        layout = self._layout()
        cad_result = build_cadquery_assembly(
            layout,
            cq_module=FailingCadQueryWithAssembly,
            solve=True,
            raise_on_solve_error=False,
            production_assembly_source="fixed_layout_pose",
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertFalse(report.ok)
        self.assertIn("cad_solve_failed", {issue.code for issue in report.errors})

    def test_reports_production_full_semantic_solve_used(self) -> None:
        cad_result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            production_assembly_source="full_semantic_solve",
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn(
            "production_full_semantic_solve_used",
            {issue.code for issue in report.passes},
        )
        self.assertIn(
            "production_full_semantic_solve_ready",
            {issue.code for issue in report.passes},
        )
        self.assertNotIn(
            "semantic_constraints_not_applied",
            {issue.code for issue in report.warnings},
        )

    def test_warns_when_production_full_semantic_pose_delta_exceeds_warning_threshold(
        self,
    ) -> None:
        cad_result = self._cad_result_with_production_full_semantic_metrics(
            pose_delta_mm=0.2,
            origin_residual_mm=0.0,
            normal_residual_deg=0.0,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn(
            "production_full_semantic_solve_residual_warning",
            {issue.code for issue in report.warnings},
        )

    def test_fails_when_production_full_semantic_residual_exceeds_error_threshold(
        self,
    ) -> None:
        cad_result = self._cad_result_with_production_full_semantic_metrics(
            pose_delta_mm=0.0,
            origin_residual_mm=1.5,
            normal_residual_deg=0.0,
            tangent_residual_deg=0.0,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertFalse(report.ok)
        self.assertIn(
            "production_full_semantic_solve_residual_exceeded",
            {issue.code for issue in report.errors},
        )
        issue = next(
            issue
            for issue in report.errors
            if issue.code == "production_full_semantic_solve_residual_exceeded"
        )
        self.assertEqual(
            issue.details["contradictory_residual_candidates"][0]["constraint_id"],
            "test_production_constraint",
        )

    def test_reports_production_full_semantic_solve_failure(self) -> None:
        layout = self._layout()
        broken_layout = replace(
            layout,
            assembly_constraints=[
                constraint
                for constraint in layout.assembly_constraints
                if "end_effector" not in constraint.id
            ],
        )
        cad_result = build_cadquery_assembly(
            broken_layout,
            cq_module=FakeCadQueryWithAssembly,
            production_assembly_source="full_semantic_solve",
            raise_on_full_semantic_solve_error=False,
        )

        report = validate_robot_design(cad_result=cad_result)

        self.assertFalse(report.ok)
        self.assertIn(
            "production_full_semantic_solve_failed",
            {issue.code for issue in report.errors},
        )

    def test_reports_missing_export_path(self) -> None:
        report = validate_robot_design(export_path="/tmp/definitely_missing_robot.step")

        self.assertFalse(report.ok)
        self.assertIn("export_path_missing_or_empty", {issue.code for issue in report.errors})

    def test_reports_step_package_bbox_consistency(self) -> None:
        layout = self._layout()
        cad_result = build_cadquery_assembly(
            layout,
            cq_module=FakeCadQueryWithAssembly,
            solve=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            package_result = export_robot_step_package(
                cad_result,
                temp_dir,
                subassemblies=build_cad_export_subassembly_specs(cad_result),
            )
            report = validate_robot_design(
                layout=layout,
                cad_result=cad_result,
                export_result=package_result.whole_machine_export,
                step_package_result=package_result,
            )

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn("export_bbox_valid", {issue.code for issue in report.passes})
        self.assertIn(
            "step_package_bboxes_consistent",
            {issue.code for issue in report.passes},
        )

    def test_marks_stage7_robot_cad_ready_for_complete_structure_first_flow(
        self,
    ) -> None:
        model = self._planar_6dof_kinematic_model()
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=480.0)
        layout = build_mechanical_layout_from_structure_plan(model, plan)
        cad_result = build_cadquery_assembly(
            layout,
            cq_module=FakeCadQueryWithAssembly,
            solve=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            package_result = export_robot_step_package(
                cad_result,
                temp_dir,
                subassemblies=build_cad_export_subassembly_specs(cad_result),
            )
            snapshot_result = FakeSnapshotPackage(
                generated_count=3,
                real_count=3,
                fallback_count=0,
            )
            report = validate_robot_design(
                requirement=self._requirement(dof=6, reach=480.0),
                kinematic_model=model,
                structure_plan=plan,
                layout=layout,
                cad_result=cad_result,
                export_result=package_result.whole_machine_export,
                step_package_result=package_result,
                snapshot_result=snapshot_result,
            )

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn("stage7_robot_cad_ready", {issue.code for issue in report.passes})

    def test_stage7_gate_stays_not_ready_without_renderer_backed_snapshots(
        self,
    ) -> None:
        model = self._planar_6dof_kinematic_model()
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=480.0)
        layout = build_mechanical_layout_from_structure_plan(model, plan)
        cad_result = build_cadquery_assembly(
            layout,
            cq_module=FakeCadQueryWithAssembly,
            solve=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            package_result = export_robot_step_package(
                cad_result,
                temp_dir,
                subassemblies=build_cad_export_subassembly_specs(cad_result),
            )
            snapshot_result = FakeSnapshotPackage(
                generated_count=3,
                real_count=0,
                fallback_count=3,
            )
            report = validate_robot_design(
                requirement=self._requirement(dof=6, reach=480.0),
                kinematic_model=model,
                structure_plan=plan,
                layout=layout,
                cad_result=cad_result,
                export_result=package_result.whole_machine_export,
                step_package_result=package_result,
                snapshot_result=snapshot_result,
            )

        self.assertTrue(report.ok, report.messages("error"))
        self.assertIn(
            "stage7_robot_cad_not_ready",
            {issue.code for issue in report.warnings},
        )
        self.assertNotIn("stage7_robot_cad_ready", {issue.code for issue in report.passes})

    def test_reports_degenerate_export_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "bad.step"
            output_path.write_text("STEP", encoding="utf-8")
            export_result = CadQueryExportResult(
                path=output_path,
                export_type="STEP",
                exists=True,
                size_bytes=output_path.stat().st_size,
                bbox=CadBoundingBox(0.0, 0.0, 0.0, 0.0, 1.0, 1.0),
            )

            report = validate_robot_design(export_result=export_result)

        self.assertFalse(report.ok)
        self.assertIn("export_bbox_degenerate", {issue.code for issue in report.errors})

    def test_warnings_do_not_make_report_fail(self) -> None:
        report = validate_robot_design(requirement=self._requirement())

        self.assertTrue(report.ok)
        self.assertIn("no_dof_artifact", {issue.code for issue in report.warnings})
        self.assertIn("missing_kinematic_model", {issue.code for issue in report.warnings})

    def _cad_result_with_local_solve_metrics(
        self,
        *,
        pose_delta_mm: float,
        origin_residual_mm: float,
        normal_residual_deg: float,
        tangent_residual_deg: float,
    ):
        cad_result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            solve=True,
        )
        metadata = {
            **cad_result.metadata,
            "local_subassembly_count": 1,
            "local_subassembly_solved_count": 1,
            "local_subassembly_failed_count": 0,
            "local_subassembly_names": ["test_subassembly"],
            "local_subassemblies": [
                {
                    "name": "test_subassembly",
                    "pose_delta_report": {
                        "max_translation_delta_mm": pose_delta_mm,
                    },
                    "residual_reports": [
                        {
                            "constraint_id": "test_constraint",
                            "origin_delta_mm": origin_residual_mm,
                            "normal_angle_deg": normal_residual_deg,
                            "tangent_angle_deg": tangent_residual_deg,
                        }
                    ],
                }
            ],
        }
        return replace(cad_result, metadata=metadata)

    def _cad_result_with_full_fixture_metrics(
        self,
        *,
        pose_delta_mm: float,
        origin_residual_mm: float,
        normal_residual_deg: float,
        tangent_residual_deg: float,
    ):
        cad_result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            solve=True,
        )
        pose_report = {
            "max_translation_delta_mm": pose_delta_mm,
        }
        residual_reports = [
            {
                "constraint_id": "test_full_constraint",
                "origin_delta_mm": origin_residual_mm,
                "normal_angle_deg": normal_residual_deg,
                "tangent_angle_deg": tangent_residual_deg,
            }
        ]
        fixture = {
            **(cad_result.metadata.get("full_assembly_fixture") or {}),
            "pose_delta_report": pose_report,
            "residual_reports": residual_reports,
        }
        metadata = {
            **cad_result.metadata,
            "full_assembly_fixture_requested": True,
            "full_assembly_fixture_ran": True,
            "full_assembly_fixture_solved": True,
            "full_assembly_fixture_gate_status": "clear_for_fixture",
            "full_assembly_fixture_pose_delta_report": pose_report,
            "full_assembly_fixture_residual_reports": residual_reports,
            "full_assembly_fixture": fixture,
        }
        return replace(cad_result, metadata=metadata)

    def _cad_result_with_production_full_semantic_metrics(
        self,
        *,
        pose_delta_mm: float,
        origin_residual_mm: float,
        normal_residual_deg: float,
        tangent_residual_deg: float,
    ):
        cad_result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            production_assembly_source="full_semantic_solve",
        )
        pose_report = {
            "max_translation_delta_mm": pose_delta_mm,
        }
        residual_reports = [
            {
                "constraint_id": "test_production_constraint",
                "origin_delta_mm": origin_residual_mm,
                "normal_angle_deg": normal_residual_deg,
                "tangent_angle_deg": tangent_residual_deg,
            }
        ]
        metadata = {
            **cad_result.metadata,
            "production_full_semantic_solve_requested": True,
            "production_full_semantic_solve_used": True,
            "production_full_semantic_solve_error": None,
            "production_full_semantic_solve_gate_status": "clear_for_fixture",
            "production_full_semantic_solve_pose_delta_report": pose_report,
            "production_full_semantic_solve_residual_reports": residual_reports,
            "semantic_constraints_applied_to_solver": "full_assembly",
            "semantic_constraint_application": "full_assembly",
            "production_assembly_source": "full_semantic_solve",
        }
        return replace(cad_result, metadata=metadata)


class FakeSnapshotPackage:
    def __init__(self, *, generated_count: int, real_count: int, fallback_count: int) -> None:
        self.generated_count = generated_count
        self.real_step_snapshot_count = real_count
        self.fallback_count = fallback_count
        self.snapshots = []

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_count": self.generated_count,
            "real_step_snapshot_count": self.real_step_snapshot_count,
            "fallback_count": self.fallback_count,
        }


if __name__ == "__main__":
    unittest.main()
