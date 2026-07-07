import unittest

from robot_sdk.structure import build_generic_6axis_cobot_structure_plan
from robot_sdk.types import DHParam, JointSpec, KinematicModel, LinkSpec, RobotRequirement
from robot_sdk.validation.structure import (
    is_planar_high_dof_template,
    validate_robot_structure,
)


class RobotSdkStructureValidationTest(unittest.TestCase):
    def test_rejects_high_dof_planar_template_without_structure_plan(self) -> None:
        model = _planar_template_model(dof=6)
        report = validate_robot_structure(
            requirement=RobotRequirement(task="6dof arm", dof=6, reach=500),
            kinematic_model=model,
            production_cad=True,
        )

        self.assertFalse(report.ok)
        self.assertIn("six_axis_planar_template_rejected", {issue.code for issue in report.errors})
        self.assertIn("structure_plan_missing", {issue.code for issue in report.errors})

    def test_generic_structure_plan_allows_planar_kinematic_template_to_be_overridden(self) -> None:
        model = _planar_template_model(dof=6)
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)

        report = validate_robot_structure(
            requirement=RobotRequirement(task="6dof arm", dof=6, reach=500),
            kinematic_model=model,
            structure_plan=plan,
            production_cad=True,
        )

        self.assertTrue(report.ok, [issue.message for issue in report.errors])
        self.assertIn("axis_topology_valid", {issue.code for issue in report.passes})
        self.assertIn("link_routes_valid", {issue.code for issue in report.passes})
        self.assertIn("structure_plan_overrides_planar_template", {issue.code for issue in report.passes})

    def test_low_dof_planar_template_does_not_require_structure_plan(self) -> None:
        model = _planar_template_model(dof=4)

        report = validate_robot_structure(
            requirement=RobotRequirement(task="4dof desktop arm", dof=4, reach=400),
            kinematic_model=model,
            production_cad=True,
        )

        self.assertTrue(report.ok)
        self.assertIn("structure_plan_not_required", {issue.code for issue in report.passes})

    def test_planar_template_detection_is_high_dof_only(self) -> None:
        self.assertFalse(is_planar_high_dof_template(_planar_template_model(dof=4)))
        self.assertTrue(is_planar_high_dof_template(_planar_template_model(dof=6)))


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
        links.append(LinkSpec(id=link_id, length=100.0, parent_joint=joint_id))
        dh_params.append(
            DHParam(
                joint_id=joint_id,
                a=100.0,
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
