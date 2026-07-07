import unittest
from dataclasses import replace

from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import AssemblyConstraint, DHParam
from robot_sdk.validation.assembly import validate_assembly_semantics


class RobotSdkAssemblyValidationTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_validates_mate_frames_and_constraint_plan(self) -> None:
        report = validate_assembly_semantics(self._layout())

        self.assertTrue(report.ok)
        self.assertEqual(report.semantic_constraint_application, "none")
        self.assertIn("mate_frames_complete", [issue.code for issue in report.passes])
        self.assertIn("constraint_plan_resolved", [issue.code for issue in report.passes])
        self.assertIn("local_subassembly_graphs_valid", [issue.code for issue in report.passes])
        self.assertIn(
            "full_assembly_solve_gate_clear_for_fixture",
            [issue.code for issue in report.passes],
        )

    def test_warns_when_semantic_constraints_are_not_applied(self) -> None:
        report = validate_assembly_semantics(self._layout())

        self.assertIn(
            "semantic_constraints_not_applied",
            [issue.code for issue in report.warnings],
        )

    def test_does_not_warn_about_removed_body_axis_joint_axis_constraints(self) -> None:
        report = validate_assembly_semantics(self._layout())

        self.assertNotIn(
            "unsafe_body_axis_joint_axis",
            [issue.code for issue in report.warnings],
        )

    def test_warns_when_local_subassembly_graph_is_underconstrained(self) -> None:
        layout = self._layout()
        broken_layout = replace(
            layout,
            assembly_constraints=[
                constraint
                for constraint in layout.assembly_constraints
                if "end_effector" not in constraint.id
            ],
        )

        report = validate_assembly_semantics(broken_layout)

        self.assertIn(
            "local_subassembly_graph_underconstrained",
            [issue.code for issue in report.warnings],
        )
        self.assertIn(
            "full_assembly_solve_gate_blocked",
            [issue.code for issue in report.warnings],
        )

    def test_warns_when_local_subassembly_graph_has_cycle(self) -> None:
        layout = self._layout()
        cyclic_layout = replace(
            layout,
            assembly_constraints=[
                *layout.assembly_constraints,
                AssemblyConstraint(
                    id="J2_output_to_end_effector_mount_direct_plane",
                    fixed="J2.output_flange",
                    moving="end_effector.mount",
                    kind="Plane",
                    rationale="Cycle fixture for graph validation.",
                ),
            ],
        )

        report = validate_assembly_semantics(cyclic_layout)

        self.assertIn(
            "local_subassembly_graph_cycle",
            [issue.code for issue in report.warnings],
        )
        self.assertIn(
            "full_assembly_solve_gate_blocked",
            [issue.code for issue in report.warnings],
        )

    def test_warns_when_local_subassembly_graph_has_dense_pair(self) -> None:
        layout = self._layout()
        dense_layout = replace(
            layout,
            assembly_constraints=[
                *layout.assembly_constraints,
                AssemblyConstraint(
                    id="J2_output_to_L2_input_extra_plane",
                    fixed="J2.output_flange",
                    moving="L2.input_face",
                    kind="Plane",
                    rationale="Dense edge fixture for graph validation.",
                ),
            ],
        )

        report = validate_assembly_semantics(dense_layout)

        self.assertIn(
            "local_subassembly_graph_dense_pairs",
            [issue.code for issue in report.warnings],
        )
        self.assertIn(
            "full_assembly_solve_gate_blocked",
            [issue.code for issue in report.warnings],
        )


if __name__ == "__main__":
    unittest.main()
