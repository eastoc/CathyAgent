import unittest

from robot_sdk.assembly.plan import build_assembly_plan
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam


class RobotSdkAssemblyPlanTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_builds_source_first_assembly_plan(self) -> None:
        plan = build_assembly_plan(self._layout())

        self.assertEqual(plan.root_part_id, "base")
        self.assertEqual(plan.terminal_part_id, "end_effector")
        self.assertEqual(
            [occurrence.part_id for occurrence in plan.occurrences],
            ["J1", "J2", "L1", "L2", "base", "end_effector"],
        )
        self.assertEqual(len(plan.mates), 10)
        self.assertGreaterEqual(len(plan.datums), len(plan.mates))
        first = plan.mates[0]
        self.assertEqual(first.fixed_part_id, "base")
        self.assertEqual(first.moving_part_id, "J1")
        self.assertEqual(first.fixed_feature_id, "base.top")
        self.assertEqual(first.moving_feature_id, "J1.bottom")
        self.assertTrue(first.rationale)
        self.assertEqual(plan.metadata["constraint_count"], 10)

    def test_to_dict_is_json_ready(self) -> None:
        payload = build_assembly_plan(self._layout()).to_dict()

        self.assertEqual(payload["root_part_id"], "base")
        self.assertEqual(payload["chain"]["terminal_part_id"], "end_effector")
        self.assertGreater(len(payload["datums"]), 0)
        self.assertGreater(len(payload["mates"]), 0)


if __name__ == "__main__":
    unittest.main()
