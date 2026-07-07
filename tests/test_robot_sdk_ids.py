import unittest

from robot_sdk.structure.ids import (
    body_frame_id,
    end_effector_frame_id,
    generate_indexed_ids,
    generate_interface_ids,
    generate_joint_ids,
    generate_link_ids,
    generate_serial_chain_ids,
    interface_id,
    joint_axis_frame_id,
    kinematic_frame_id,
    mount_frame_id,
)


class RobotSdkIdsTest(unittest.TestCase):
    def test_generate_joint_and_link_ids(self) -> None:
        self.assertEqual(generate_joint_ids(4), ["J1", "J2", "J3", "J4"])
        self.assertEqual(generate_link_ids(3), ["L1", "L2", "L3"])
        self.assertEqual(generate_interface_ids(2), ["I1", "I2"])

    def test_generate_indexed_ids_with_width(self) -> None:
        self.assertEqual(
            generate_indexed_ids("J", 3, start=2, width=2),
            ["J02", "J03", "J04"],
        )

    def test_generate_rejects_invalid_counts(self) -> None:
        with self.assertRaises(ValueError):
            generate_joint_ids(0)
        with self.assertRaises(ValueError):
            generate_link_ids(0)
        with self.assertRaises(ValueError):
            generate_indexed_ids("J", -1)

    def test_frame_id_helpers(self) -> None:
        self.assertEqual(joint_axis_frame_id("J1"), "J1_axis")
        self.assertEqual(body_frame_id("L1"), "L1_body")
        self.assertEqual(kinematic_frame_id("J1"), "J1_kinematic")
        self.assertEqual(mount_frame_id(), "base_mount")
        self.assertEqual(end_effector_frame_id(), "end_effector_tool")
        self.assertEqual(end_effector_frame_id("mount"), "end_effector_mount")

    def test_interface_id_is_semantic(self) -> None:
        self.assertEqual(
            interface_id("J1", "L1", "flange"),
            "J1_to_L1_flange",
        )
        self.assertEqual(
            interface_id("L4", None, "open_end"),
            "L4_to_open_open_end",
        )

    def test_serial_chain_ids_for_4dof_mvp(self) -> None:
        ids = generate_serial_chain_ids(4)

        self.assertEqual(ids.joints, ["J1", "J2", "J3", "J4"])
        self.assertEqual(ids.links, ["L1", "L2", "L3", "L4"])
        self.assertEqual(ids.base_link, "base")
        self.assertEqual(ids.end_effector_link, "end_effector")
        self.assertEqual(
            ids.joint_axis_frames,
            ["J1_axis", "J2_axis", "J3_axis", "J4_axis"],
        )
        self.assertEqual(
            ids.link_body_frames,
            ["L1_body", "L2_body", "L3_body", "L4_body"],
        )
        self.assertEqual(
            ids.kinematic_frames,
            [
                "base_kinematic",
                "J1_kinematic",
                "J2_kinematic",
                "J3_kinematic",
                "J4_kinematic",
                "end_effector_kinematic",
            ],
        )
        self.assertEqual(
            ids.interfaces,
            [
                "base_to_J1_flange",
                "J1_to_L1_flange",
                "L1_to_J2_flange",
                "J2_to_L2_flange",
                "L2_to_J3_flange",
                "J3_to_L3_flange",
                "L3_to_J4_flange",
                "J4_to_L4_flange",
                "L4_to_end_effector_mount",
            ],
        )


if __name__ == "__main__":
    unittest.main()
