import unittest
from dataclasses import replace

from robot_sdk.cad.cq_full_solve import solve_full_assembly_fixture
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam

from tests.test_robot_sdk_cq_assembly import FakeCadQueryWithAssembly


class RobotSdkFullAssemblyFixtureTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_solves_full_assembly_fixture_when_gate_is_clear(self) -> None:
        result = solve_full_assembly_fixture(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
        )

        self.assertEqual(result.gate.status, "clear_for_fixture")
        self.assertTrue(result.ran)
        self.assertTrue(result.solved)
        self.assertEqual(result.anchor_part_id, "base")
        self.assertEqual(result.constraint_count, 11)
        self.assertEqual(result.semantic_constraint_count, 10)
        self.assertEqual(len(result.assembly.parts), 6)
        self.assertEqual(len(result.assembly.constraints), 11)
        self.assertEqual(result.assembly.constraints[0], ("base", "Fixed"))
        self.assertEqual(len(result.residual_reports), 10)
        self.assertEqual(result.metadata["semantic_constraint_application"], "full_assembly_fixture")
        self.assertEqual(result.metadata["gate"]["status"], "clear_for_fixture")
        self.assertEqual(len(result.metadata["residual_reports"]), 10)
        self.assertTrue(result.metadata["assembly_compiler_ok"])
        self.assertEqual(
            result.metadata["assembly_compiler"]["metadata"]["compiler_mode"],
            "translation_origin_propagation",
        )
        self.assertEqual(
            result.metadata["assembly_compiler"]["unresolved_part_ids"],
            [],
        )
        self.assertEqual(result.metadata["assembly_compiler"]["conflicts"], [])
        self.assertEqual(
            result.metadata["component_cluster_report"]["cluster_count"],
            1,
        )

    def test_skips_full_assembly_fixture_when_gate_is_blocked(self) -> None:
        layout = self._layout()
        broken_layout = replace(
            layout,
            assembly_constraints=[
                constraint
                for constraint in layout.assembly_constraints
                if "end_effector" not in constraint.id
            ],
        )

        result = solve_full_assembly_fixture(
            broken_layout,
            cq_module=FakeCadQueryWithAssembly,
        )

        self.assertEqual(result.gate.status, "blocked")
        self.assertFalse(result.ran)
        self.assertFalse(result.solved)
        self.assertIsNone(result.assembly)
        self.assertEqual(result.constraint_count, 0)
        self.assertEqual(result.semantic_constraint_count, 0)
        self.assertIn("blocked", result.metadata["skipped_reason"])


if __name__ == "__main__":
    unittest.main()
