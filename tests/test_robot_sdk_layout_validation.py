import unittest
from dataclasses import replace

from robot_sdk.kinematics.scaling import (
    KinematicScalingRequest,
    build_profile_kinematic_model,
)
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.layout.validation import (
    assert_valid_mechanical_layout,
    validate_mechanical_layout,
)
from robot_sdk.types import (
    AssemblyConstraint,
    DHParam,
    FrameMapping,
    FrameSpec,
    InterfaceSpec,
    PartFeature,
)


class RobotSdkLayoutValidationTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_valid_tabletop_layout_passes(self) -> None:
        layout = self._layout()

        result = validate_mechanical_layout(layout)

        self.assertTrue(result.ok, result.error_messages())
        assert_valid_mechanical_layout(layout)

    def test_rejects_missing_joint_axis_frame(self) -> None:
        layout = self._layout()
        broken_joint = replace(layout.joints[0], axis_frame="missing_axis")
        broken_layout = replace(layout, joints=[broken_joint, *layout.joints[1:]])

        result = validate_mechanical_layout(broken_layout)

        self.assertFalse(result.ok)
        self.assertIn("missing_joint_axis_frame", {issue.code for issue in result.errors})

    def test_rejects_missing_part_feature_frame(self) -> None:
        layout = self._layout()
        broken_feature = replace(layout.part_features[0], frame="missing_frame")
        broken_layout = replace(
            layout,
            part_features=[broken_feature, *layout.part_features[1:]],
        )

        result = validate_mechanical_layout(broken_layout)

        self.assertFalse(result.ok)
        self.assertIn("missing_part_feature_frame", {issue.code for issue in result.errors})

    def test_rejects_constraint_with_missing_feature_refs(self) -> None:
        layout = self._layout()
        broken_constraint = AssemblyConstraint(
            id="broken_constraint",
            fixed="base.top",
            moving="missing.feature",
            kind="Plane",
            rationale="Intentional broken reference for validation coverage.",
        )
        broken_layout = replace(
            layout,
            assembly_constraints=[*layout.assembly_constraints, broken_constraint],
        )

        result = validate_mechanical_layout(broken_layout)

        self.assertFalse(result.ok)
        self.assertIn(
            "unknown_constraint_moving_feature",
            {issue.code for issue in result.errors},
        )

    def test_rejects_mapping_with_missing_frames(self) -> None:
        layout = self._layout()
        broken_mapping = FrameMapping(
            kinematic_frame_id="missing_kinematic",
            mechanical_frame_id="missing_mechanical",
            rationale="Intentional broken mapping for validation coverage.",
        )
        broken_layout = replace(
            layout,
            frame_mappings=[*layout.frame_mappings, broken_mapping],
        )

        result = validate_mechanical_layout(broken_layout)

        codes = {issue.code for issue in result.errors}
        self.assertIn("missing_mapping_kinematic_frame", codes)
        self.assertIn("missing_mapping_mechanical_frame", codes)

    def test_warns_for_unreachable_frame(self) -> None:
        layout = self._layout()
        broken_layout = replace(
            layout,
            frames=[
                *layout.frames,
                FrameSpec(
                    id="floating_frame",
                    parent=None,
                    semantic="interface",
                ),
            ],
        )

        result = validate_mechanical_layout(broken_layout)

        self.assertTrue(result.ok, result.error_messages())
        self.assertIn("unreachable_frame", {issue.code for issue in result.warnings})

    def test_duplicate_feature_ids_are_errors(self) -> None:
        layout = self._layout()
        duplicate = PartFeature(
            id=layout.part_features[0].id,
            part_id="duplicate_part",
            cad_tag="duplicate",
            type="plane",
            semantic="custom",
        )
        broken_layout = replace(
            layout,
            part_features=[*layout.part_features, duplicate],
        )

        result = validate_mechanical_layout(broken_layout)

        self.assertFalse(result.ok)
        self.assertIn("duplicate_part_feature_id", {issue.code for issue in result.errors})

    def test_warns_for_invalid_link_body_axis_assumption(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="scaled_profile",
                target_reach=500,
            )
        )
        from robot_sdk.layout.mechanical_layout import (
            build_tabletop_serial_mechanical_layout_from_model,
        )

        layout = build_tabletop_serial_mechanical_layout_from_model(scaling.model)

        result = validate_mechanical_layout(layout)

        self.assertTrue(result.ok, result.error_messages())
        codes = {issue.code for issue in result.warnings}
        self.assertIn("invalid_axis_assumption", codes)
        self.assertIn("wrist_layout_warning", codes)
        warning_text = "\n".join(result.warning_messages())
        self.assertIn("J3 -> L3 -> J4", warning_text)
        self.assertIn("link.body_axis == joint.axis", warning_text)

    def test_warns_for_degenerate_link_envelope(self) -> None:
        layout = self._layout()
        broken_link = replace(
            layout.links[0],
            envelope=replace(
                layout.links[0].envelope,
                dimensions={**layout.links[0].envelope.dimensions, "length": 0.5},
            ),
        )
        broken_layout = replace(
            layout,
            links=[broken_link, *layout.links[1:]],
            envelopes=[
                broken_link.envelope
                if envelope.id == broken_link.envelope.id
                else envelope
                for envelope in layout.envelopes
            ],
        )

        result = validate_mechanical_layout(broken_layout)

        self.assertTrue(result.ok, result.error_messages())
        self.assertIn(
            "zero_or_degenerate_link_warning",
            {issue.code for issue in result.warnings},
        )

    def test_generated_interfaces_have_direction_metadata(self) -> None:
        layout = self._layout()

        result = validate_mechanical_layout(layout)

        codes = {issue.code for issue in result.warnings}
        self.assertNotIn("missing_interface_direction_metadata", codes)
        self.assertNotIn("interface_direction_error", codes)
        for interface in layout.interfaces:
            self.assertIn("normal", interface.metadata)
            self.assertIn("tangent", interface.metadata)

    def test_generated_links_have_primitive_metadata(self) -> None:
        layout = self._layout()

        result = validate_mechanical_layout(layout)

        codes = {issue.code for issue in result.warnings}
        self.assertNotIn("missing_link_primitive_metadata", codes)
        for link in layout.links:
            self.assertIn("primitive_type", link.metadata)
            self.assertIn("link_primitive", link.metadata)

    def test_warns_for_invalid_interface_direction_metadata(self) -> None:
        layout = self._layout()
        broken_interface = replace(
            layout.interfaces[0],
            metadata={"normal": (2.0, 0.0, 0.0), "tangent": (1.0, 0.0, 0.0)},
        )
        broken_layout = replace(
            layout,
            interfaces=[broken_interface, *layout.interfaces[1:]],
        )

        result = validate_mechanical_layout(broken_layout)

        self.assertTrue(result.ok, result.error_messages())
        self.assertIn(
            "interface_direction_error",
            {issue.code for issue in result.warnings},
        )

    def test_warns_for_missing_interface_direction_metadata(self) -> None:
        layout = self._layout()
        broken_interface = InterfaceSpec(
            id=layout.interfaces[0].id,
            frame=layout.interfaces[0].frame,
            type=layout.interfaces[0].type,
            mates_to=layout.interfaces[0].mates_to,
            envelope=layout.interfaces[0].envelope,
        )
        broken_layout = replace(
            layout,
            interfaces=[broken_interface, *layout.interfaces[1:]],
        )

        result = validate_mechanical_layout(broken_layout)

        self.assertTrue(result.ok, result.error_messages())
        self.assertIn(
            "missing_interface_direction_metadata",
            {issue.code for issue in result.warnings},
        )


if __name__ == "__main__":
    unittest.main()
