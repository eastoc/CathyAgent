import tempfile
import unittest
from pathlib import Path

from robot_sdk.cad.source_assembly import SourceAssemblyHelper, export_source_step
from robot_sdk.cad.source_parts import (
    build_minimal_source_part_catalog,
    build_source_base_part,
    build_source_joint_part,
    build_source_link_part,
    build_source_routed_link_part,
    build_source_structure_joint_part,
    build_source_tool_flange_part,
)


class RobotSdkSourcePartsTest(unittest.TestCase):
    def test_source_primitives_register_standard_joints(self) -> None:
        cases = [
            (build_source_base_part(), {"mount", "output", "axis"}),
            (build_source_joint_part("J1"), {"input", "output", "axis"}),
            (build_source_link_part("L1"), {"input", "output"}),
            (build_source_tool_flange_part(), {"input", "tool", "axis"}),
        ]

        for part, expected_frames in cases:
            with self.subTest(part_id=part.part_id):
                self.assertEqual(set(part.frame_names), expected_frames)
            self.assertEqual(set(part.solid.joints.keys()), expected_frames)
            self.assertEqual(part.solid.label, part.part_id)

    def test_structure_primitives_register_station_and_route_datums(self) -> None:
        joint = build_source_structure_joint_part(
            "J2",
            axis_role="shoulder_pitch",
            morphology="shoulder_joint",
        )
        link = build_source_routed_link_part(
            "L2",
            route_vector=(210.0, 0.0, 80.0),
            route_type="straight",
            morphology="upper_arm_link",
        )

        self.assertEqual(set(joint.frame_names), {"input", "output", "axis"})
        self.assertEqual(set(link.frame_names), {"input", "output"})
        self.assertEqual(link.metadata["route_vector"], (210.0, 0.0, 80.0))
        self.assertEqual(link.metadata["source_joint_mode"], "routed_endpoint")
        self.assertAlmostEqual(float(link.solid.joints["output"].location.position.X), 210.0)
        self.assertAlmostEqual(float(link.solid.joints["output"].location.position.Z), 80.0)
        self.assertEqual(joint.metadata["primitive_family"], "shoulder_block")
        self.assertEqual(link.metadata["primitive_family"], "upper_arm_dual_rail")
        self.assertIn("shoulder_support_block", joint.metadata["visual_features"])
        self.assertIn("side_flanges", joint.metadata["visual_features"])
        self.assertIn("dual_rail", link.metadata["visual_features"])
        self.assertIn("end_blocks", link.metadata["visual_features"])
        self.assertIn("cross_brace", link.metadata["visual_features"])
        self.assertGreater(link.solid.bounding_box().size.Y, 24.0)

    def test_wrist_morphology_uses_cylindrical_source_blockout(self) -> None:
        link = build_source_routed_link_part(
            "L5",
            route_vector=(40.0, 0.0, 0.0),
            route_type="elbow",
            morphology="wrist2_elbow_cylinder",
        )

        self.assertEqual(link.metadata["primitive_family"], "wrist_elbow_cylinder")
        self.assertIn("bent_cylindrical_housing", link.metadata["visual_features"])
        self.assertIn("dual_flanges", link.metadata["visual_features"])
        self.assertIn("pilot_hub", link.metadata["visual_features"])
        self.assertEqual(set(link.solid.joints.keys()), {"input", "output"})
        self.assertGreater(link.solid.bounding_box().size.X, 39.0)
        self.assertGreater(link.solid.bounding_box().size.Y, 30.0)
        self.assertGreater(link.solid.bounding_box().size.Z, 30.0)

    def test_generic_wrist_roles_fall_back_to_structure_semantics(self) -> None:
        wrist_joint = build_source_structure_joint_part(
            "J5",
            axis_role="wrist_pitch",
            morphology="generic_revolute_joint",
        )
        tool_joint = build_source_structure_joint_part(
            "J6",
            axis_role="tool_roll",
            morphology="generic_revolute_joint",
        )
        wrist_spacer = build_source_routed_link_part(
            "L4",
            route_vector=(40.0, 0.0, 0.0),
            route_type="wrist_spacer",
            morphology="generic_elbow_link",
        )
        wrist_elbow = build_source_routed_link_part(
            "L5",
            route_vector=(40.0, 0.0, 0.0),
            route_type="elbow",
            morphology="generic_elbow_link",
        )
        tool_spacer = build_source_routed_link_part(
            "L6",
            route_vector=(40.0, 0.0, 0.0),
            route_type="wrist_spacer",
            morphology="generic_wrist_spacer",
        )

        self.assertEqual(wrist_joint.metadata["primitive_family"], "wrist_compact")
        self.assertIn("compact_barrel", wrist_joint.metadata["visual_features"])
        self.assertEqual(tool_joint.metadata["primitive_family"], "tool_flange_joint")
        self.assertIn("tool_flange", tool_joint.metadata["visual_features"])
        self.assertEqual(wrist_spacer.metadata["primitive_family"], "wrist_tool_flange")
        self.assertIn("terminal_flange", wrist_spacer.metadata["visual_features"])
        self.assertEqual(wrist_elbow.metadata["primitive_family"], "wrist_elbow_cylinder")
        self.assertIn("bent_cylindrical_housing", wrist_elbow.metadata["visual_features"])
        self.assertEqual(tool_spacer.metadata["primitive_family"], "terminal_tool_spacer")
        self.assertIn("tool_spacer", tool_spacer.metadata["visual_features"])

    def test_minimal_catalog_can_build_source_joint_chain(self) -> None:
        catalog = build_minimal_source_part_catalog()
        helper = SourceAssemblyHelper("minimal_source_robot")
        base = helper.add(catalog.require("base").solid, "base")
        joint = helper.add(catalog.require("J1").solid, "J1")
        link = helper.add(catalog.require("L1").solid, "L1")
        tool = helper.add(catalog.require("end_effector").solid, "end_effector")

        helper.face_to_face((base, "output"), (joint, "input"))
        helper.face_to_face((joint, "output"), (link, "input"))
        helper.face_to_face((link, "output"), (tool, "input"))
        assembly = helper.build()

        self.assertEqual(len(helper.relations), 3)
        self.assertEqual(len(assembly.assembly_mates), 3)
        self.assertGreater(float(tool.center().X), float(base.center().X))
        self.assertAlmostEqual(float(joint.center().Z), 5.0)

    def test_minimal_catalog_exports_resolved_step(self) -> None:
        catalog = build_minimal_source_part_catalog()
        helper = SourceAssemblyHelper("minimal_source_robot_export")
        base = helper.add(catalog.require("base").solid, "base")
        joint = helper.add(catalog.require("J1").solid, "J1")
        link = helper.add(catalog.require("L1").solid, "L1")
        tool = helper.add(catalog.require("end_effector").solid, "end_effector")
        helper.face_to_face((base, "output"), (joint, "input"))
        helper.face_to_face((joint, "output"), (link, "input"))
        helper.face_to_face((link, "output"), (tool, "input"))

        output_path = Path(tempfile.gettempdir()) / "cathy_minimal_source_robot.step"
        result = export_source_step(helper.build(), output_path)

        self.assertTrue(result.exists)
        self.assertGreater(result.size_bytes or 0, 0)


if __name__ == "__main__":
    unittest.main()
