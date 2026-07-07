import unittest

from robot_sdk.kinematics.scaling import (
    KinematicScalingRequest,
    build_profile_kinematic_model,
)
from robot_sdk.layout.link_primitives import classify_link_primitives
from robot_sdk.layout.mechanical_layout import (
    build_tabletop_serial_mechanical_layout,
    build_tabletop_serial_mechanical_layout_from_model,
)
from robot_sdk.types import DHParam


class RobotSdkLinkPrimitiveTest(unittest.TestCase):
    def test_planar_tabletop_links_are_straight_by_default(self) -> None:
        layout = build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

        by_id = {link.id: link.metadata for link in layout.links}

        self.assertEqual(by_id["L1"]["primitive_type"], "straight_link")
        self.assertEqual(by_id["L2"]["primitive_type"], "straight_link")
        self.assertEqual(
            by_id["L1"]["link_primitive"]["type"],
            by_id["L1"]["primitive_type"],
        )

    def test_ur3e_wrist_transition_is_not_marked_as_straight_link(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="scaled_profile",
                target_reach=500,
            )
        )
        layout = build_tabletop_serial_mechanical_layout_from_model(scaling.model)

        by_id = {item.link_id: item for item in classify_link_primitives(layout)}

        self.assertEqual(by_id["L3"].primitive_type, "elbow_link")
        self.assertIn("wrist transition", " ".join(by_id["L3"].reasons))
        self.assertEqual(by_id["L4"].primitive_type, "elbow_link")
        self.assertEqual(by_id["L5"].primitive_type, "elbow_link")
        self.assertEqual(by_id["L6"].primitive_type, "wrist_spacer")
        self.assertEqual(layout.links[2].metadata["primitive_type"], "elbow_link")
        self.assertEqual(layout.links[4].metadata["primitive_type"], "elbow_link")


if __name__ == "__main__":
    unittest.main()
