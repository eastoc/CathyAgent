import unittest

from robot_sdk.assembly.local_solve import (
    build_local_subassembly_plans,
    default_local_subassembly_specs,
)
from robot_sdk.cad.cq_local_solve import solve_local_subassemblies
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam

from tests.test_robot_sdk_cq_assembly import FakeCadQueryWithAssembly


class RobotSdkLocalSubassemblySolveTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id=f"J{index}", a=80, alpha=0, d=0, theta=0)
                for index in range(1, 7)
            ]
        )

    def test_default_specs_prioritize_terminal_and_wrist_roll(self) -> None:
        specs = default_local_subassembly_specs(self._layout())

        self.assertEqual(
            [(spec.name, spec.part_ids, spec.anchor_part_id) for spec in specs],
            [
                ("J6_L6_end_effector", ["J6", "L6", "end_effector"], "J6"),
                ("J5_L5_J6", ["J5", "L5", "J6"], "J5"),
                ("J4_L4_J5", ["J4", "L4", "J5"], "J4"),
                ("J3_L3_J4", ["J3", "L3", "J4"], "J3"),
                (
                    "wrist_group_J4_to_end_effector",
                    ["J4", "L4", "J5", "L5", "J6", "L6", "end_effector"],
                    "J4",
                ),
                ("base_J1_mount", ["base", "J1"], "base"),
            ],
        )

    def test_local_plans_apply_safe_planes_and_skip_unsafe_axes(self) -> None:
        plans = build_local_subassembly_plans(self._layout())
        terminal = plans[0]
        wrist = plans[1]

        self.assertEqual(
            terminal.applied_constraint_ids,
            [
                "J6_output_to_L6_input_plane",
                "J6_output_tangent_to_L6_input_tangent_axis",
                "L6_output_to_end_effector_mount_plane",
                "L6_output_tangent_to_end_effector_mount_tangent_axis",
            ],
        )
        self.assertEqual(terminal.skipped_constraint_ids, [])
        self.assertEqual(
            wrist.applied_constraint_ids,
            [
                "J5_output_to_L5_input_plane",
                "J5_output_tangent_to_L5_input_tangent_axis",
                "L5_output_to_J6_bottom_plane",
                "L5_output_tangent_to_J6_bottom_tangent_axis",
            ],
        )
        self.assertEqual(wrist.skipped_constraint_ids, [])
        self.assertEqual(plans[2].applied_constraint_ids[0], "J4_output_to_L4_input_plane")
        self.assertEqual(plans[3].applied_constraint_ids[0], "J3_output_to_L3_input_plane")
        self.assertEqual(plans[4].name, "wrist_group_J4_to_end_effector")
        self.assertEqual(len(plans[4].applied_constraint_ids), 12)
        self.assertEqual(plans[5].name, "base_J1_mount")
        self.assertEqual(
            plans[5].applied_constraint_ids,
            ["base_top_to_J1_bottom_plane", "base_axis_to_J1_axis"],
        )
        self.assertEqual(terminal.residuals[0].origin_delta_mm, 0.0)
        self.assertTrue(
            all(residual.normal_angle_deg == 0.0 for residual in terminal.residuals)
        )
        self.assertTrue(
            all(residual.tangent_angle_deg == 0.0 for residual in terminal.residuals)
        )

    def test_solves_local_subassemblies_with_fixed_seed_and_safe_constraints(self) -> None:
        results = solve_local_subassemblies(
            self._layout(),
            cq_module=FakeCadQueryWithAssembly,
        )

        terminal = results[0]
        wrist = results[1]

        self.assertEqual(len(results), 6)
        self.assertTrue(terminal.solved)
        self.assertTrue(wrist.solved)
        self.assertEqual(terminal.metadata["semantic_constraint_application"], "local_subassembly")
        self.assertEqual(terminal.metadata["role"], "terminal_tool")
        self.assertEqual(terminal.metadata["spec_metadata"], {"role": "terminal_tool"})
        self.assertEqual(terminal.constraint_count, 5)
        self.assertEqual(terminal.constraint_calls[0].kind, "Fixed")
        self.assertEqual(terminal.constraint_calls[0].fixed_query, "J6")
        self.assertEqual(
            terminal.assembly.constraints,
            [
                ("J6", "Fixed"),
                ("J6?output_flange", "L6?input_face", "Plane"),
                ("J6@faces@>X", "L6@faces@>X", "Axis"),
                ("L6?output_face", "end_effector?mount", "Plane"),
                ("L6@faces@>X", "end_effector@faces@>X", "Axis"),
            ],
        )
        self.assertEqual(wrist.constraint_count, 5)
        self.assertEqual(wrist.metadata["role"], "joint_link_joint")
        self.assertEqual(
            wrist.assembly.constraints,
            [
                ("J5", "Fixed"),
                ("J5?output_flange", "L5?input_face", "Plane"),
                ("J5@faces@>X", "L5@faces@>X", "Axis"),
                ("L5?output_face", "J6?bottom", "Plane"),
                ("L5@faces@>X", "J6@faces@>X", "Axis"),
            ],
        )
        self.assertEqual(terminal.metadata["residual_reports"][0]["origin_delta_mm"], 0.0)
        self.assertTrue(
            all(
                report["normal_angle_deg"] == 0.0
                for report in terminal.metadata["residual_reports"]
            )
        )
        self.assertTrue(
            all(
                report["tangent_angle_deg"] == 0.0
                for report in terminal.metadata["residual_reports"]
            )
        )
        self.assertEqual(
            terminal.metadata["pose_delta_report"]["max_translation_delta_mm"],
            0.0,
        )
        self.assertEqual(results[4].name, "wrist_group_J4_to_end_effector")
        base_mount = results[5]
        self.assertEqual(base_mount.name, "base_J1_mount")
        self.assertEqual(base_mount.metadata["role"], "base_mount")
        self.assertEqual(base_mount.metadata["source"], "sdk_promoted_fixture")
        self.assertEqual(base_mount.constraint_count, 3)
        self.assertEqual(
            base_mount.assembly.constraints,
            [
                ("base", "Fixed"),
                ("base?top", "J1?bottom", "Plane"),
                ("base@faces@>Z", "J1@faces@>X", "Axis"),
            ],
        )
        self.assertTrue(
            all(
                report["origin_delta_mm"] == 0.0
                and report["normal_angle_deg"] == 0.0
                and report["tangent_angle_deg"] == 0.0
                for report in base_mount.metadata["residual_reports"]
            )
        )


if __name__ == "__main__":
    unittest.main()
