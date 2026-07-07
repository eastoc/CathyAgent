import unittest

from robot_sdk.assembly.features import (
    CadQueryMateFeatureRule,
    build_cadquery_mate_feature_catalog,
    build_cadquery_mate_feature_rules,
    tag_workplane_feature,
)
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam, PartFeature


class FakeWorkplane:
    def __init__(self) -> None:
        self.tags: list[str] = []

    def tag(self, name: str) -> "FakeWorkplane":
        self.tags.append(name)
        return self


class RobotSdkAssemblyFeaturesTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_builds_cadquery_feature_catalog_from_layout(self) -> None:
        layout = self._layout()

        catalog = build_cadquery_mate_feature_catalog(layout)

        base_top = catalog.require("base.top")
        self.assertEqual(base_top.part_id, "base")
        self.assertEqual(base_top.cad_tag, "top")
        self.assertEqual(base_top.selection_kind, "face")
        self.assertEqual(base_top.constraint_ref, "base.top")
        self.assertEqual(base_top.cq_tag_path, "base@top")
        self.assertIn("base.top", catalog.constraint_refs())

    def test_groups_rules_by_part(self) -> None:
        layout = self._layout()
        catalog = build_cadquery_mate_feature_catalog(layout)

        joint_rules = catalog.for_part("J1")

        self.assertEqual(
            [rule.feature_id for rule in joint_rules],
            [
                "J1.bottom",
                "J1.bottom_tangent",
                "J1.axis",
                "J1.output_flange",
                "J1.output_tangent",
            ],
        )

    def test_rules_preserve_feature_selection_kind(self) -> None:
        features = [
            PartFeature(
                id="part.face",
                part_id="part",
                cad_tag="face",
                type="plane",
                semantic="flange_face",
            ),
            PartFeature(
                id="part.axis",
                part_id="part",
                cad_tag="axis",
                type="axis",
                semantic="joint_axis",
            ),
            PartFeature(
                id="part.point",
                part_id="part",
                cad_tag="point",
                type="point",
                semantic="tool_mount",
            ),
        ]

        rules = build_cadquery_mate_feature_rules(features)

        self.assertEqual([rule.selection_kind for rule in rules], ["face", "axis", "point"])

    def test_rejects_duplicate_feature_rule_ids(self) -> None:
        features = [
            PartFeature(
                id="part.face",
                part_id="part",
                cad_tag="face_a",
                type="plane",
                semantic="flange_face",
            ),
            PartFeature(
                id="part.face",
                part_id="part",
                cad_tag="face_b",
                type="plane",
                semantic="flange_face",
            ),
        ]

        with self.assertRaises(ValueError):
            build_cadquery_mate_feature_rules(features)

    def test_rejects_non_cadquery_friendly_tags(self) -> None:
        with self.assertRaises(ValueError):
            CadQueryMateFeatureRule(
                feature_id="part.bad",
                part_id="part",
                cad_tag="bad-tag",
                selection_kind="face",
                semantic="custom",
            )

    def test_tag_workplane_feature_uses_rule_tag(self) -> None:
        rule = CadQueryMateFeatureRule(
            feature_id="base.top",
            part_id="base",
            cad_tag="top",
            selection_kind="face",
            semantic="mount_face",
        )
        workplane = FakeWorkplane()

        result = tag_workplane_feature(workplane, rule)

        self.assertIs(result, workplane)
        self.assertEqual(workplane.tags, ["top"])

    def test_tag_workplane_feature_requires_tag_method(self) -> None:
        rule = CadQueryMateFeatureRule(
            feature_id="base.top",
            part_id="base",
            cad_tag="top",
            selection_kind="face",
            semantic="mount_face",
        )

        with self.assertRaises(TypeError):
            tag_workplane_feature(object(), rule)


if __name__ == "__main__":
    unittest.main()
