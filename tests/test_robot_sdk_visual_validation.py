import unittest

from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.layout.structure_adapter import build_mechanical_layout_from_structure_plan
from robot_sdk.structure import build_generic_6axis_cobot_structure_plan
from robot_sdk.types import DHParam, JointSpec, KinematicModel, LinkSpec
from robot_sdk.validation.visual import validate_visual_geometry


class RobotSdkVisualValidationTest(unittest.TestCase):
    def test_rejects_high_dof_horizontal_bead_layout_without_structure_plan(self) -> None:
        model = _planar_template_model(dof=6)
        layout = build_tabletop_serial_mechanical_layout(model.dh_params)

        report = validate_visual_geometry(
            layout=layout,
            kinematic_model=model,
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "planar_serial_chain_visual_rejected",
            {issue.code for issue in report.errors},
        )

    def test_structure_plan_layout_passes_visual_topology_screen(self) -> None:
        model = _planar_template_model(dof=6)
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)
        layout = build_mechanical_layout_from_structure_plan(model, plan)

        report = validate_visual_geometry(
            layout=layout,
            kinematic_model=model,
            structure_plan=plan,
        )

        self.assertTrue(report.ok, [issue.message for issue in report.errors])
        self.assertIn(
            "layout_visual_topology_screened",
            {issue.code for issue in report.passes},
        )
        self.assertIn(
            "structure_visual_cues_screened",
            {issue.code for issue in report.passes},
        )
        self.assertIn(
            "snapshot_render_not_configured",
            {issue.code for issue in report.warnings},
        )

    def test_structure_plan_with_flat_station_elevation_is_rejected(self) -> None:
        model = _planar_template_model(dof=6)
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)
        for station in plan.stations:
            x, y, _z = station.origin
            station.origin = (x, y, 0.0)
        for axis in plan.joint_axes:
            if axis.origin is None:
                continue
            x, y, _z = axis.origin
            axis.origin = (x, y, 0.0)
        layout = build_mechanical_layout_from_structure_plan(model, plan)

        report = validate_visual_geometry(
            layout=layout,
            kinematic_model=model,
            structure_plan=plan,
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "structure_body_elevation_rejected",
            {issue.code for issue in report.errors},
        )

    def test_structure_plan_missing_key_station_role_is_rejected(self) -> None:
        model = _planar_template_model(dof=6)
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)
        plan.stations = [
            station
            for station in plan.stations
            if station.role != "wrist2"
        ]

        report = validate_visual_geometry(
            kinematic_model=model,
            structure_plan=plan,
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "structure_visual_roles_missing",
            {issue.code for issue in report.errors},
        )


def _planar_template_model(*, dof: int) -> KinematicModel:
    joints = []
    links = []
    dh_params = []
    previous_link = "base"
    for index in range(1, dof + 1):
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
