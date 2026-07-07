import unittest

from robot_sdk.structure import (
    axes_are_parallel,
    build_generic_6axis_cobot_structure_plan,
    normalized_axis,
)


class RobotSdkStructurePlanTest(unittest.TestCase):
    def test_generic_6axis_cobot_plan_has_non_planar_topology(self) -> None:
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)

        self.assertEqual(plan.family, "generic_6axis_cobot")
        self.assertEqual(plan.source, "generic_6axis_cobot_profile")
        self.assertEqual(plan.units, "mm")
        self.assertEqual(len(plan.joint_axes), 6)
        self.assertEqual(len(plan.link_routes), 6)
        self.assertEqual(plan.metadata["reach_mm"], 500.0)
        self.assertEqual(
            {station.role for station in plan.stations},
            {"base", "shoulder", "elbow", "wrist1", "wrist2", "wrist3", "tool"},
        )
        self.assertIn("elbow", {route.route_type for route in plan.link_routes})
        self.assertIn("wrist_spacer", {route.route_type for route in plan.link_routes})
        self.assertIn("tool_stub", {route.route_type for route in plan.link_routes})
        origins = {station.id: station.origin for station in plan.stations}
        self.assertEqual(origins["shoulder"], (0.0, 0.0, 90.0))
        self.assertEqual(origins["elbow"], (210.0, 0.0, 150.0))
        self.assertEqual(origins["wrist1"], (380.0, 0.0, 120.0))

    def test_axis_helpers_normalize_and_detect_parallel_axes(self) -> None:
        self.assertEqual(normalized_axis((0.0, 0.0, 2.0)), (0.0, 0.0, 1.0))
        self.assertTrue(axes_are_parallel((0.0, 0.0, 1.0), (0.0, 0.0, -2.0)))
        self.assertFalse(axes_are_parallel((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)))

    def test_rejects_invalid_reach(self) -> None:
        with self.assertRaises(ValueError):
            build_generic_6axis_cobot_structure_plan(reach_mm=0)


if __name__ == "__main__":
    unittest.main()
