import unittest

from robot_sdk.layout.mechanical_layout import (
    TABLETOP_SERIAL_ARM_TEMPLATE,
    build_tabletop_serial_mechanical_layout,
)
from robot_sdk.structure.ids import generate_serial_chain_ids
from robot_sdk.types import DHParam


class RobotSdkMechanicalLayoutTest(unittest.TestCase):
    def test_build_tabletop_serial_layout_has_mechanical_semantics(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
                DHParam(joint_id="J3", a=80, alpha=0, d=0, theta=0),
                DHParam(joint_id="J4", a=60, alpha=0, d=0, theta=0),
            ]
        )

        frame_by_id = {frame.id: frame for frame in layout.frames}

        self.assertEqual(layout.units, "mm")
        self.assertEqual(layout.base_frame.id, "base_mount")
        self.assertEqual(layout.base_frame.semantic, "mount")
        self.assertIn("J1_axis", frame_by_id)
        self.assertEqual(frame_by_id["J1_axis"].semantic, "joint_axis")
        self.assertIn("L1_body", frame_by_id)
        self.assertEqual(frame_by_id["L1_body"].semantic, "link_body")
        self.assertIn("J1_to_L1_flange_interface", frame_by_id)
        self.assertEqual(
            frame_by_id["J1_to_L1_flange_interface"].semantic,
            "interface",
        )
        self.assertIn("end_effector_mount", frame_by_id)
        self.assertIn("end_effector_tool", frame_by_id)

    def test_layout_uses_mechanical_frames_for_joints_and_links(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

        self.assertEqual([joint.axis_frame for joint in layout.joints], ["J1_axis", "J2_axis"])
        self.assertEqual([link.body_frame for link in layout.links], ["L1_body", "L2_body"])
        self.assertEqual(layout.links[0].from_interface, "J1_to_L1_flange")
        self.assertEqual(layout.links[0].to_interface, "L1_to_J2_flange")
        self.assertEqual(layout.links[-1].to_interface, "L2_to_end_effector_mount")

    def test_layout_uses_dh_fk_station_positions(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=0, alpha=0, d=100, theta=0),
                DHParam(joint_id="J2", a=80, alpha=0, d=0, theta=0),
            ]
        )

        frame_by_id = {frame.id: frame for frame in layout.frames}

        self.assertEqual(frame_by_id["J1_axis"].transform.translation, (0.0, 0.0, 0.0))
        self.assertEqual(frame_by_id["J2_axis"].transform.translation, (0.0, 0.0, 100.0))
        self.assertEqual(frame_by_id["L1_body"].transform.translation, (0.0, 0.0, 50.0))
        self.assertEqual(frame_by_id["L2_body"].transform.translation, (40.0, 0.0, 0.0))

    def test_layout_records_mapping_rationales(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0)]
        )

        self.assertTrue(layout.frame_mappings)
        self.assertTrue(all(mapping.rationale for mapping in layout.frame_mappings))
        self.assertTrue(
            any(
                "not a claim that the DH transform is a CAD mate" in mapping.rationale
                for mapping in layout.frame_mappings
            )
        )
        self.assertIn(TABLETOP_SERIAL_ARM_TEMPLATE, layout.assumptions[0])

    def test_layout_generates_basic_part_features(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

        feature_by_id = {feature.id: feature for feature in layout.part_features}

        self.assertEqual(feature_by_id["base.top"].cad_tag, "top")
        self.assertEqual(feature_by_id["base.top"].semantic, "mount_face")
        self.assertEqual(feature_by_id["base.top"].frame, "base_top_mount_face")
        self.assertEqual(feature_by_id["base.axis"].type, "axis")
        self.assertEqual(feature_by_id["J1.bottom"].frame, "base_to_J1_flange_interface")
        self.assertEqual(feature_by_id["J1.axis"].frame, "J1_axis")
        self.assertEqual(
            feature_by_id["J1.output_flange"].frame,
            "J1_to_L1_flange_interface",
        )
        self.assertEqual(
            feature_by_id["L1.input_face"].frame,
            "J1_to_L1_flange_interface",
        )
        self.assertEqual(
            feature_by_id["L1.output_face"].frame,
            "L1_to_J2_flange_interface",
        )
        self.assertEqual(
            feature_by_id["end_effector.mount"].frame,
            "end_effector_mount",
        )
        self.assertEqual(
            feature_by_id["end_effector.tool"].frame,
            "end_effector_tool",
        )

    def test_layout_generates_basic_assembly_constraints(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

        constraint_by_id = {constraint.id: constraint for constraint in layout.assembly_constraints}

        self.assertEqual(
            constraint_by_id["base_top_to_J1_bottom_plane"].fixed,
            "base.top",
        )
        self.assertEqual(
            constraint_by_id["base_top_to_J1_bottom_plane"].moving,
            "J1.bottom",
        )
        self.assertEqual(constraint_by_id["base_axis_to_J1_axis"].kind, "Axis")
        self.assertEqual(
            constraint_by_id["J1_output_to_L1_input_plane"].fixed,
            "J1.output_flange",
        )
        self.assertEqual(
            constraint_by_id["L1_output_to_J2_bottom_plane"].moving,
            "J2.bottom",
        )
        self.assertEqual(
            constraint_by_id["L2_output_to_end_effector_mount_plane"].moving,
            "end_effector.mount",
        )
        self.assertTrue(all(constraint.rationale for constraint in layout.assembly_constraints))

    def test_assembly_constraints_reference_existing_part_features(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

        feature_ids = {feature.id for feature in layout.part_features}
        for constraint in layout.assembly_constraints:
            self.assertIn(constraint.fixed, feature_ids)
            self.assertIn(constraint.moving, feature_ids)

    def test_zero_length_dh_row_gets_minimum_span_warning(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [DHParam(joint_id="J1", a=0, alpha=0, d=0, theta=0)]
        )

        self.assertTrue(layout.warnings)
        self.assertEqual(layout.links[0].envelope.dimensions["length"], 50.0)

    def test_rejects_mismatched_chain_ids(self) -> None:
        with self.assertRaises(ValueError):
            build_tabletop_serial_mechanical_layout(
                [DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0)],
                chain_ids=generate_serial_chain_ids(2),
            )


if __name__ == "__main__":
    unittest.main()
