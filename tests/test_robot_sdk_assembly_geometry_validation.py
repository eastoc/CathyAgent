import unittest
from dataclasses import replace

from robot_sdk.cad.cq_assembly import build_cadquery_assembly
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam
from robot_sdk.validation.assembly_geometry import build_assembly_geometry_report
from robot_sdk.validation.basic import validate_robot_design

from tests.test_robot_sdk_cq_assembly import FakeCadQueryWithAssembly


class RobotSdkAssemblyGeometryValidationTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_geometry_report_detects_solved_part_drift(self) -> None:
        layout = self._layout()
        cad_result = build_cadquery_assembly(
            layout,
            cq_module=FakeCadQueryWithAssembly,
        )
        solved_locations = dict(cad_result.part_locations)
        moved_l2 = dict(solved_locations["L2"])
        x, y, z = moved_l2["loc"]
        moved_l2["loc"] = (x + 50.0, y, z)
        solved_locations["L2"] = moved_l2

        report = build_assembly_geometry_report(
            layout,
            initial_locations=cad_result.part_locations,
            solved_locations=solved_locations,
        )

        self.assertGreaterEqual(report.max_origin_delta_mm, 50.0)
        self.assertGreater(report.cluster_count, 1)
        self.assertFalse(report.base_to_terminal_connected)
        self.assertTrue(report.component_cluster_report["failing_edges"])

    def test_validation_fails_disconnected_production_full_semantic_geometry(self) -> None:
        cad_result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            production_assembly_source="full_semantic_solve",
        )
        cluster_report = {
            "cluster_count": 2,
            "base_to_terminal_connected": False,
            "failing_edges": [
                {
                    "constraint_id": "L2_output_to_end_effector_mount_plane",
                    "fixed_part_id": "L2",
                    "moving_part_id": "end_effector",
                    "origin_delta_mm": 50.0,
                }
            ],
        }
        metadata = {
            **cad_result.metadata,
            "production_full_semantic_solve_component_cluster_report": cluster_report,
        }
        report = validate_robot_design(cad_result=replace(cad_result, metadata=metadata))

        self.assertFalse(report.ok)
        self.assertIn(
            "production_full_semantic_assembly_geometry_failed",
            {issue.code for issue in report.errors},
        )

    def test_validation_fails_production_full_semantic_compiler_conflict(self) -> None:
        cad_result = build_cadquery_assembly(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
            production_assembly_source="full_semantic_solve",
        )
        compiler_report = {
            "ok": False,
            "conflicts": [
                {
                    "part_id": "L2",
                    "mate_id": "test_conflicting_mate",
                    "existing_delta": (0.0, 0.0, 0.0),
                    "proposed_delta": (5.0, 0.0, 0.0),
                    "delta_error_mm": 5.0,
                }
            ],
            "unresolved_part_ids": ["end_effector"],
            "metadata": {
                "compiler_mode": "translation_origin_propagation",
                "conflict_count": 1,
                "unresolved_part_count": 1,
            },
        }
        metadata = {
            **cad_result.metadata,
            "production_full_semantic_solve_assembly_compiler": compiler_report,
        }
        report = validate_robot_design(cad_result=replace(cad_result, metadata=metadata))

        self.assertFalse(report.ok)
        issue = next(
            issue
            for issue in report.errors
            if issue.code == "production_full_semantic_assembly_compiler_failed"
        )
        failure = issue.details["assembly_compiler_failure"]
        self.assertEqual(failure["conflicts"][0]["mate_id"], "test_conflicting_mate")
        self.assertEqual(failure["unresolved_part_ids"], ["end_effector"])


if __name__ == "__main__":
    unittest.main()
