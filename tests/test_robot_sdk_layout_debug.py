import unittest

from robot_sdk.kinematics.scaling import (
    KinematicScalingRequest,
    build_profile_kinematic_model,
)
from robot_sdk.layout.debug import (
    build_layout_debug_report,
    format_layout_debug_report,
)
from robot_sdk.layout.mechanical_layout import (
    build_tabletop_serial_mechanical_layout,
    build_tabletop_serial_mechanical_layout_from_model,
)
from robot_sdk.types import DHParam


class RobotSdkLayoutDebugTest(unittest.TestCase):
    def test_report_lists_basic_joint_link_joint_segments(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

        report = build_layout_debug_report(layout)
        segment = report.find_segment(
            from_joint_id="J1",
            link_id="L1",
            to_joint_id="J2",
        )

        self.assertEqual(segment.segment_id, "J1 -> L1 -> J2")
        self.assertIsNotNone(segment.interface_gap)
        self.assertIsNotNone(segment.axis_mismatch_deg)
        self.assertEqual(segment.link_body_direction, (1.0, 0.0, 0.0))
        self.assertEqual(segment.next_joint_axis_direction, (0.0, 0.0, 1.0))

    def test_ur3e_scaled_report_exposes_j3_l3_j4_axis_mismatch(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="scaled_profile",
                target_reach=500,
            )
        )
        layout = build_tabletop_serial_mechanical_layout_from_model(scaling.model)

        report = build_layout_debug_report(layout)
        segment = report.find_segment(
            from_joint_id="J3",
            link_id="L3",
            to_joint_id="J4",
        )

        self.assertEqual(segment.segment_id, "J3 -> L3 -> J4")
        self.assertIsNotNone(segment.link_output_origin)
        self.assertIsNotNone(segment.next_joint_input_origin)
        self.assertIsNotNone(segment.interface_gap)
        self.assertAlmostEqual(segment.interface_gap, 0.0)
        self.assertEqual(segment.link_body_direction, (-1.0, 0.0, 0.0))
        self.assertIsNotNone(segment.next_joint_axis_direction)
        assert segment.next_joint_axis_direction is not None
        for actual, expected in zip(segment.next_joint_axis_direction, (0.0, -1.0, 0.0)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(segment.axis_mismatch_deg, 90.0)
        self.assertTrue(segment.warnings)
        self.assertIn("J3 -> L3 -> J4", "\n".join(report.warnings))

        interface = report.find_interface("L3_to_J4_flange")
        self.assertIsNotNone(interface.origin)
        self.assertIsNotNone(interface.normal)
        self.assertIsNotNone(interface.tangent)
        assert interface.normal is not None
        assert interface.tangent is not None
        for actual, expected in zip(interface.normal, (-1.0, 0.0, 0.0)):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(interface.tangent, (0.0, -1.0, 0.0)):
            self.assertAlmostEqual(actual, expected)

    def test_format_report_includes_reviewable_segment_data(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="scaled_profile",
                target_reach=500,
            )
        )
        layout = build_tabletop_serial_mechanical_layout_from_model(scaling.model)
        report = build_layout_debug_report(layout)

        text = format_layout_debug_report(report)

        self.assertIn("J3 -> L3 -> J4", text)
        self.assertIn("axis_mismatch=90", text)
        self.assertIn("L3_to_J4_flange", text)
        self.assertIn("link.body_axis == joint.axis", text)


if __name__ == "__main__":
    unittest.main()
