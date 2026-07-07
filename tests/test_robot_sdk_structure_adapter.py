import unittest

from robot_sdk.layout import (
    STRUCTURE_PLAN_TEMPLATE,
    build_layout_debug_report,
    build_mechanical_layout_from_structure_plan,
    validate_mechanical_layout,
)
from robot_sdk.structure import build_generic_6axis_cobot_structure_plan
from robot_sdk.types import DHParam, JointSpec, KinematicModel, LinkSpec


class RobotSdkStructureAdapterTest(unittest.TestCase):
    def test_builds_valid_mechanical_layout_from_structure_plan(self) -> None:
        model = _planar_6dof_model()
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)

        layout = build_mechanical_layout_from_structure_plan(model, plan)

        report = validate_mechanical_layout(layout)
        self.assertTrue(report.ok, report.error_messages())
        self.assertEqual(layout.metadata["layout_template"], STRUCTURE_PLAN_TEMPLATE)
        self.assertEqual(layout.metadata["structure_plan_name"], plan.name)
        self.assertEqual(len(layout.joints), 6)
        self.assertEqual(len(layout.links), 6)
        self.assertIn("structure_plan", layout.metadata)

    def test_joint_axes_and_link_routes_come_from_structure_plan(self) -> None:
        model = _planar_6dof_model()
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)

        layout = build_mechanical_layout_from_structure_plan(model, plan)
        debug = build_layout_debug_report(layout)
        axes = {joint.joint_id: joint.axis_direction for joint in debug.joints}
        links = {link.id: link for link in layout.links}

        self.assertAlmostEqual(axes["J1"][2], 1.0, places=6)
        self.assertAlmostEqual(axes["J2"][1], 1.0, places=6)
        self.assertAlmostEqual(axes["J4"][0], 1.0, places=6)
        self.assertEqual(links["L1"].metadata["structure_route_type"], "offset")
        self.assertEqual(links["L1"].metadata["primitive_type"], "offset_link")
        self.assertIn("route_vector", links["L1"].metadata["link_primitive"])
        self.assertIn("route_offset_vector", links["L1"].metadata["link_primitive"])
        self.assertEqual(links["L3"].metadata["structure_route_type"], "elbow")
        self.assertEqual(links["L3"].metadata["primitive_type"], "elbow_link")
        self.assertEqual(links["L6"].metadata["structure_route_type"], "tool_stub")
        self.assertEqual(links["L6"].metadata["primitive_type"], "wrist_spacer")

    def test_adds_datum_part_features_from_structure_plan(self) -> None:
        model = _planar_6dof_model()
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)

        layout = build_mechanical_layout_from_structure_plan(model, plan)
        feature_ids = {feature.id for feature in layout.part_features}

        self.assertIn("base.datum.base_mount_plane", feature_ids)
        self.assertIn("J1.datum.J1_axis_datum", feature_ids)
        self.assertIn("end_effector.datum.tool_mount_plane", feature_ids)


def _planar_6dof_model() -> KinematicModel:
    joints = []
    links = []
    dh_params = []
    previous_link = "base"
    for index in range(1, 7):
        joint_id = f"J{index}"
        link_id = f"L{index}"
        joints.append(
            JointSpec(
                id=joint_id,
                type="revolute",
                parent_link=previous_link,
                child_link=link_id,
            )
        )
        links.append(LinkSpec(id=link_id, length=80.0, parent_joint=joint_id))
        dh_params.append(
            DHParam(
                joint_id=joint_id,
                a=80.0,
                alpha=0.0,
                d=0.0,
                theta=0.0,
                variable=f"theta{index}",
            )
        )
        previous_link = link_id
    return KinematicModel(convention="dh", joints=joints, links=links, dh_params=dh_params)


if __name__ == "__main__":
    unittest.main()
