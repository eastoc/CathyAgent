import unittest
from dataclasses import replace

from robot_sdk.assembly.constraints import (
    CadQueryAssemblyConstraintRule,
    build_cadquery_assembly_constraint_plan,
    build_cadquery_assembly_constraint_rules,
)
from robot_sdk.assembly.features import build_cadquery_mate_feature_catalog
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import AssemblyConstraint, DHParam


class RobotSdkAssemblyConstraintsTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_builds_constraint_plan_from_layout(self) -> None:
        layout = self._layout()

        plan = build_cadquery_assembly_constraint_plan(layout)

        rule = plan.require("base_top_to_J1_bottom_plane")
        self.assertEqual(rule.kind, "Plane")
        self.assertEqual(rule.fixed_ref, "base.top")
        self.assertEqual(rule.moving_ref, "J1.bottom")
        self.assertEqual(rule.fixed_cq_tag_path, "base@top")
        self.assertEqual(rule.moving_cq_tag_path, "J1@bottom")
        self.assertTrue(rule.rationale)

    def test_groups_constraint_rules_by_kind(self) -> None:
        layout = self._layout()
        plan = build_cadquery_assembly_constraint_plan(layout)

        axis_rules = plan.by_kind("Axis")
        plane_rules = plan.by_kind("Plane")

        self.assertEqual(len(axis_rules), 5)
        self.assertEqual(len(plane_rules), 5)

    def test_constraint_rule_preserves_offset_and_flipped(self) -> None:
        layout = self._layout()
        catalog = build_cadquery_mate_feature_catalog(layout)
        constraint = AssemblyConstraint(
            id="offset_constraint",
            fixed="base.top",
            moving="J1.bottom",
            kind="Plane",
            offset=5.0,
            flipped=True,
            rationale="Intentional offset coverage.",
        )

        rule = CadQueryAssemblyConstraintRule.from_constraint(constraint, catalog)

        self.assertEqual(rule.offset, 5.0)
        self.assertTrue(rule.flipped)
        self.assertEqual(rule.metadata["fixed_feature_id"], "base.top")

    def test_rejects_missing_feature_reference(self) -> None:
        layout = self._layout()
        catalog = build_cadquery_mate_feature_catalog(layout)
        constraint = AssemblyConstraint(
            id="bad_ref",
            fixed="base.top",
            moving="missing.feature",
            kind="Plane",
            rationale="Broken reference for coverage.",
        )

        with self.assertRaises(KeyError):
            build_cadquery_assembly_constraint_rules([constraint], catalog)

    def test_rejects_kind_feature_mismatch(self) -> None:
        layout = self._layout()
        catalog = build_cadquery_mate_feature_catalog(layout)
        constraint = AssemblyConstraint(
            id="bad_kind",
            fixed="base.top",
            moving="J1.axis",
            kind="Plane",
            rationale="Plane constraints must reference face-like features.",
        )

        with self.assertRaises(ValueError):
            build_cadquery_assembly_constraint_rules([constraint], catalog)

    def test_rejects_duplicate_constraint_ids(self) -> None:
        layout = self._layout()
        catalog = build_cadquery_mate_feature_catalog(layout)
        constraint = AssemblyConstraint(
            id="duplicate",
            fixed="base.top",
            moving="J1.bottom",
            kind="Plane",
            rationale="Duplicate coverage.",
        )

        with self.assertRaises(ValueError):
            build_cadquery_assembly_constraint_rules(
                [constraint, replace(constraint)],
                catalog,
            )

    def test_rejects_unknown_constraint_kind(self) -> None:
        layout = self._layout()
        catalog = build_cadquery_mate_feature_catalog(layout)
        constraint = AssemblyConstraint(
            id="unknown_kind",
            fixed="base.top",
            moving="J1.bottom",
            kind="Plane",
            rationale="Unknown kind coverage.",
        )
        bad_constraint = replace(constraint, kind="Coincident")  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            build_cadquery_assembly_constraint_rules([bad_constraint], catalog)


if __name__ == "__main__":
    unittest.main()
