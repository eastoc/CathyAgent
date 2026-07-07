import unittest
from dataclasses import replace

from robot_sdk.assembly.constraints import (
    build_assembly_mate_constraint_plan,
    build_assembly_mate_constraint_rules,
    normalize_constraint_kind,
)
from robot_sdk.assembly.mate_frames import build_mate_frame_catalog
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import AssemblyConstraint, DHParam


class RobotSdkConstraintRulesTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_normalizes_legacy_constraint_kinds(self) -> None:
        self.assertEqual(normalize_constraint_kind("Plane"), "MatePlane")
        self.assertEqual(normalize_constraint_kind("Axis"), "MateAxis")
        self.assertEqual(normalize_constraint_kind("Fixed"), "FixedSeed")
        self.assertEqual(normalize_constraint_kind("Coaxial"), "Coaxial")

    def test_builds_mate_constraint_plan_from_layout(self) -> None:
        plan = build_assembly_mate_constraint_plan(self._layout())

        rule = plan.require("J1_output_to_L1_input_plane")

        self.assertEqual(rule.kind, "Plane")
        self.assertEqual(rule.normalized_kind, "MatePlane")
        self.assertEqual(rule.fixed_feature_id, "J1.output_flange")
        self.assertEqual(rule.moving_feature_id, "L1.input_face")
        self.assertEqual(rule.fixed_mate.part_id, "J1")
        self.assertEqual(rule.moving_mate.part_id, "L1")

    def test_groups_by_original_or_normalized_kind(self) -> None:
        plan = build_assembly_mate_constraint_plan(self._layout())

        self.assertEqual(len(plan.by_kind("Plane")), 5)
        self.assertEqual(len(plan.by_kind("MatePlane")), 5)
        self.assertEqual(len(plan.by_kind("MateAxis")), 5)

    def test_layout_no_longer_uses_body_axis_joint_axis_rules(self) -> None:
        plan = build_assembly_mate_constraint_plan(self._layout())

        unsafe_ids = [rule.constraint_id for rule in plan.unsafe_rules]

        self.assertEqual(unsafe_ids, [])
        self.assertIn(
            "L1_output_tangent_to_J2_bottom_tangent_axis",
            [rule.constraint_id for rule in plan.by_kind("MateAxis")],
        )

    def test_rejects_missing_mate_feature_reference(self) -> None:
        layout = self._layout()
        catalog = build_mate_frame_catalog(layout)
        constraint = AssemblyConstraint(
            id="bad_ref",
            fixed="base.top",
            moving="missing.feature",
            kind="Plane",
            rationale="Broken mate reference coverage.",
        )

        with self.assertRaises(KeyError):
            build_assembly_mate_constraint_rules([constraint], catalog)

    def test_rejects_kind_feature_mismatch(self) -> None:
        layout = self._layout()
        catalog = build_mate_frame_catalog(layout)
        constraint = AssemblyConstraint(
            id="bad_kind",
            fixed="base.top",
            moving="J1.axis",
            kind="Plane",
            rationale="Plane constraints must reference plane-like mate frames.",
        )

        with self.assertRaises(ValueError):
            build_assembly_mate_constraint_rules([constraint], catalog)

    def test_rejects_duplicate_constraint_ids(self) -> None:
        layout = self._layout()
        catalog = build_mate_frame_catalog(layout)
        constraint = AssemblyConstraint(
            id="duplicate",
            fixed="base.top",
            moving="J1.bottom",
            kind="Plane",
            rationale="Duplicate coverage.",
        )

        with self.assertRaises(ValueError):
            build_assembly_mate_constraint_rules([constraint, replace(constraint)], catalog)


if __name__ == "__main__":
    unittest.main()
