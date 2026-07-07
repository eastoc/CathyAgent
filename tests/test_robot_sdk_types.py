import unittest

from robot_sdk.types import (
    AssemblyConstraint,
    DHParam,
    EnvelopeSpec,
    FrameMapping,
    FrameSpec,
    InterfaceSpec,
    JointLayout,
    JointSpec,
    KinematicModel,
    LinkLayout,
    LinkSpec,
    MechanicalLayout,
    PartFeature,
    RobotRequirement,
    Transform,
)


class RobotSdkTypesTest(unittest.TestCase):
    def test_robot_requirement_rejects_invalid_dof(self) -> None:
        with self.assertRaises(ValueError):
            RobotRequirement(task="pick and place", dof=0)

    def test_transform_to_dict_is_json_friendly(self) -> None:
        transform = Transform(translation=(1.0, 2.0, 3.0))
        self.assertEqual(transform.to_dict()["translation"], (1.0, 2.0, 3.0))

    def test_dh_kinematic_model_requires_dh_params(self) -> None:
        joint = JointSpec(
            id="J1",
            type="revolute",
            parent_link="base",
            child_link="L1",
        )
        link = LinkSpec(id="L1", length=100)
        with self.assertRaises(ValueError):
            KinematicModel(convention="dh", joints=[joint], links=[link])

    def test_frame_mapping_requires_rationale(self) -> None:
        with self.assertRaises(ValueError):
            FrameMapping(
                kinematic_frame_id="dh_1",
                mechanical_frame_id="J1_axis",
                rationale="",
            )

    def test_mechanical_layout_captures_required_contract(self) -> None:
        base_frame = FrameSpec(id="base_mount", parent=None, semantic="mount")
        axis_frame = FrameSpec(
            id="J1_axis",
            parent="base_mount",
            semantic="joint_axis",
        )
        link_frame = FrameSpec(
            id="L1_body",
            parent="J1_axis",
            semantic="link_body",
        )
        envelope = EnvelopeSpec(
            id="L1_env",
            shape="box",
            dimensions={"length": 120, "width": 30, "height": 20},
            frame="L1_body",
        )
        interface = InterfaceSpec(
            id="I1",
            frame="J1_axis",
            type="flange",
        )
        layout = MechanicalLayout(
            units="mm",
            base_frame=base_frame,
            frames=[base_frame, axis_frame, link_frame],
            joints=[
                JointLayout(
                    id="J1",
                    type="revolute",
                    axis_frame="J1_axis",
                    parent_link="base",
                    child_link="L1",
                    range=(-3.14, 3.14),
                )
            ],
            links=[
                LinkLayout(
                    id="L1",
                    body_frame="L1_body",
                    from_interface="I1",
                    to_interface=None,
                    envelope=envelope,
                )
            ],
            interfaces=[interface],
            frame_mappings=[
                FrameMapping(
                    kinematic_frame_id="dh_1",
                    mechanical_frame_id="J1_axis",
                    rationale="J1 axis frame is the CAD joint rotation axis.",
                )
            ],
            envelopes=[envelope],
        )

        data = layout.to_dict()
        self.assertEqual(data["units"], "mm")
        self.assertEqual(data["frames"][1]["semantic"], "joint_axis")
        self.assertEqual(
            data["frame_mappings"][0]["mechanical_frame_id"],
            "J1_axis",
        )
        self.assertEqual(data["part_features"], [])
        self.assertEqual(data["assembly_constraints"], [])

    def test_part_feature_requires_cad_tag(self) -> None:
        with self.assertRaises(ValueError):
            PartFeature(
                id="J1.axis",
                part_id="J1",
                cad_tag="",
                type="axis",
                semantic="joint_axis",
            )

    def test_assembly_constraint_requires_rationale(self) -> None:
        with self.assertRaises(ValueError):
            AssemblyConstraint(
                id="base_to_J1_axis",
                fixed="base.axis",
                moving="J1.axis",
                kind="Axis",
                rationale="",
            )

    def test_part_feature_and_constraint_serialize(self) -> None:
        feature = PartFeature(
            id="base.top",
            part_id="base",
            cad_tag="top",
            type="plane",
            semantic="mount_face",
            frame="base_mount",
        )
        constraint = AssemblyConstraint(
            id="base_top_to_J1_bottom",
            fixed="base.top",
            moving="J1.bottom",
            kind="Plane",
            offset=0,
            rationale="Mount J1 on the base top plane.",
        )

        self.assertEqual(feature.to_dict()["cad_tag"], "top")
        self.assertEqual(constraint.to_dict()["kind"], "Plane")

    def test_dh_param_fixed_joint_cannot_have_variable(self) -> None:
        with self.assertRaises(ValueError):
            DHParam(
                joint_id="J0",
                a=0,
                alpha=0,
                d=0,
                theta=0,
                joint_type="fixed",
                variable="theta",
            )

    def test_kinematic_model_uses_readable_end_effector_name(self) -> None:
        joint = JointSpec(
            id="J1",
            type="revolute",
            parent_link="base",
            child_link="L1",
        )
        link = LinkSpec(id="L1", length=100)
        model = KinematicModel(
            convention="dh",
            joints=[joint],
            links=[link],
            dh_params=[
                DHParam(
                    joint_id="J1",
                    a=100,
                    alpha=0,
                    d=0,
                    theta=0,
                )
            ],
        )

        self.assertEqual(model.end_effector_frame_id, "end_effector_kinematic")


if __name__ == "__main__":
    unittest.main()
