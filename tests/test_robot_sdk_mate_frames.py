import math
import unittest

from robot_sdk.assembly.mate_frames import (
    MateFrame,
    build_mate_frame_catalog,
    build_mate_frames,
)
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam


class RobotSdkMateFramesTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_builds_one_mate_frame_per_part_feature(self) -> None:
        layout = self._layout()

        frames = build_mate_frames(layout)

        self.assertEqual(len(frames), len(layout.part_features))
        self.assertIn("L1.output_face.mate", [frame.id for frame in frames])

    def test_catalog_resolves_by_feature_and_part(self) -> None:
        catalog = build_mate_frame_catalog(self._layout())

        frame = catalog.require_feature("J2.bottom")

        self.assertEqual(frame.id, "J2.bottom.mate")
        self.assertEqual(frame.part_id, "J2")
        self.assertEqual(frame.feature_id, "J2.bottom")
        self.assertIn(frame, catalog.for_part("J2"))

    def test_mate_frame_axes_are_unit_and_orthogonal(self) -> None:
        frame = build_mate_frame_catalog(self._layout()).require_feature("L1.output_face")

        self.assertAlmostEqual(_length(frame.normal), 1.0)
        self.assertAlmostEqual(_length(frame.tangent), 1.0)
        self.assertAlmostEqual(_dot(frame.normal, frame.tangent), 0.0)
        self.assertAlmostEqual(_length(frame.binormal), 1.0)

    def test_base_top_and_first_joint_bottom_mate_frames_align(self) -> None:
        catalog = build_mate_frame_catalog(self._layout())

        base_top = catalog.require_feature("base.top")
        joint_bottom = catalog.require_feature("J1.bottom")

        self.assertEqual(base_top.origin, joint_bottom.origin)
        self.assertAlmostEqual(_dot(base_top.normal, joint_bottom.normal), 1.0)
        self.assertAlmostEqual(_dot(base_top.tangent, joint_bottom.tangent), 1.0)

    def test_end_effector_mount_uses_last_link_output_orientation(self) -> None:
        catalog = build_mate_frame_catalog(self._layout())

        last_link_output = catalog.require_feature("L2.output_face")
        tool_mount = catalog.require_feature("end_effector.mount")
        last_link_tangent = catalog.require_feature("L2.output_tangent")
        tool_mount_tangent = catalog.require_feature("end_effector.mount_tangent")

        self.assertAlmostEqual(_dot(last_link_output.normal, tool_mount.normal), 1.0)
        self.assertAlmostEqual(_dot(last_link_output.tangent, tool_mount.tangent), 1.0)
        self.assertAlmostEqual(_dot(last_link_tangent.normal, tool_mount_tangent.normal), 1.0)
        self.assertAlmostEqual(_dot(last_link_tangent.tangent, tool_mount_tangent.tangent), 1.0)

    def test_rejects_non_orthogonal_axes(self) -> None:
        with self.assertRaises(ValueError):
            MateFrame(
                id="bad",
                part_id="part",
                feature_id="part.face",
                origin=(0.0, 0.0, 0.0),
                normal=(0.0, 0.0, 1.0),
                tangent=(0.0, 0.0, 1.0),
                semantic="custom",
            )


def _length(vector: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(vector, vector))


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


if __name__ == "__main__":
    unittest.main()
