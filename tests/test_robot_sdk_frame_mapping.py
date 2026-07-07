import unittest

from robot_sdk.layout.frame_mapping import (
    map_base_kinematic_to_mount,
    map_end_effector_kinematic_to_mount,
    map_joint_kinematic_to_axis,
    map_joint_kinematic_to_link_body,
)


class RobotSdkFrameMappingTest(unittest.TestCase):
    def test_base_mapping_has_rationale(self) -> None:
        mapping = map_base_kinematic_to_mount(mount_frame_id="base_mount")

        self.assertEqual(mapping.kinematic_frame_id, "base_kinematic")
        self.assertEqual(mapping.mechanical_frame_id, "base_mount")
        self.assertTrue(mapping.rationale)

    def test_joint_axis_mapping_says_not_cad_mate(self) -> None:
        mapping = map_joint_kinematic_to_axis(
            template="tabletop_serial_arm",
            joint_id="J1",
            kinematic_frame_id="J1_kinematic",
            axis_frame_id="J1_axis",
        )

        self.assertEqual(mapping.mechanical_frame_id, "J1_axis")
        self.assertIn("not a claim that the DH transform is a CAD mate", mapping.rationale)

    def test_link_body_mapping_has_half_span_offset(self) -> None:
        mapping = map_joint_kinematic_to_link_body(
            joint_id="J1",
            link_id="L1",
            kinematic_frame_id="J1_kinematic",
            link_body_frame_id="L1_body",
            link_span=120,
        )

        self.assertEqual(mapping.mechanical_frame_id, "L1_body")
        self.assertEqual(mapping.transform_offset.translation, (60.0, 0.0, 0.0))
        self.assertTrue(mapping.rationale)

    def test_end_effector_mapping_has_rationale(self) -> None:
        mapping = map_end_effector_kinematic_to_mount(
            kinematic_frame_id="end_effector_kinematic",
            mount_frame_id="end_effector_mount",
        )

        self.assertEqual(mapping.mechanical_frame_id, "end_effector_mount")
        self.assertTrue(mapping.rationale)


if __name__ == "__main__":
    unittest.main()
