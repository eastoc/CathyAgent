import unittest
from pathlib import Path

from robot_sdk.cad.cq_parts import build_cadquery_parts
from robot_sdk.kinematics.scaling import (
    KinematicScalingRequest,
    build_profile_kinematic_model,
)
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout_from_model
from robot_sdk.layout.structure_adapter import build_mechanical_layout_from_structure_plan
from robot_sdk.structure import build_generic_6axis_cobot_structure_plan
from robot_sdk.types import DHParam


class FakeWorkplane:
    def __init__(self, plane: str = "XY", root: "FakeWorkplane | None" = None) -> None:
        self.plane = plane
        self.root = root or self
        self.ops: list[tuple[str, tuple]] = []
        self.tags: list[str] = []
        self.saved: list[dict] = []

    def box(self, *args):
        self.root.ops.append(("box", args))
        return self

    def circle(self, *args):
        self.root.ops.append(("circle", args))
        return self

    def extrude(self, *args):
        self.root.ops.append(("extrude", args))
        return self

    def translate(self, *args):
        self.root.ops.append(("translate", args))
        return self

    def union(self, other):
        self.root.ops.append(("union", (other,)))
        self.root.ops.extend(other.root.ops)
        return self

    def faces(self, *args):
        self.root.ops.append(("faces", args))
        return FakeWorkplane(self.plane, root=self.root)

    def edges(self, *args):
        self.root.ops.append(("edges", args))
        return FakeWorkplane(self.plane, root=self.root)

    def vertices(self, *args):
        self.root.ops.append(("vertices", args))
        return FakeWorkplane(self.plane, root=self.root)

    def tag(self, name: str):
        self.root.tags.append(name)
        return self

    def end(self):
        return self.root

    def save(self, path: str, **kwargs):
        self.root.saved.append({"path": path, **kwargs})
        Path(path).write_text("STEP", encoding="utf-8")
        return self


class FakeCadQuery:
    @staticmethod
    def Workplane(plane: str = "XY"):
        return FakeWorkplane(plane)

    @staticmethod
    def Plane(origin, xDir=None, normal=(0, 0, 1)):
        return {"origin": origin, "xDir": xDir, "normal": normal}

    @staticmethod
    def Vector(*args):
        return tuple(args)

    @staticmethod
    def Location(value):
        if isinstance(value, dict) and "origin" in value:
            return {
                "loc": value["origin"],
                "xDir": value.get("xDir"),
                "normal": value.get("normal"),
            }
        return {"loc": value}


class RobotSdkCadQueryPartsTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_builds_expected_concept_parts(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        self.assertEqual(
            catalog.part_ids(),
            ["base", "J1", "L1", "J2", "L2", "end_effector"],
        )
        self.assertEqual(catalog.require("base").kind, "base")
        self.assertEqual(catalog.require("J1").kind, "joint")
        self.assertEqual(catalog.require("L1").kind, "link")
        self.assertEqual(catalog.require("end_effector").kind, "end_effector")

    def test_base_part_has_top_and_axis_feature_metadata(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        base = catalog.require("base")

        self.assertEqual(base.selector_for("base.top"), ">Z")
        self.assertEqual(base.selector_for("base.axis"), "axis:Z")
        self.assertEqual([rule.feature_id for rule in base.feature_rules], ["base.top", "base.axis"])
        self.assertIn("top", base.solid.tags)
        self.assertIn("axis", base.solid.tags)
        self.assertIn("mount_face", base.metadata["feature_semantics"])
        self.assertIn("mount_face_reference_pad", base.metadata["datum_geometry_applied"])

    def test_joint_and_link_selectors_are_cad_adapter_ready(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        joint = catalog.require("J1")
        link = catalog.require("L1")

        self.assertEqual(joint.selector_for("J1.bottom"), "<X")
        self.assertEqual(joint.selector_for("J1.axis"), "axis:X")
        self.assertEqual(joint.selector_for("J1.output_flange"), ">X")
        self.assertEqual(link.selector_for("L1.input_face"), "<X")
        self.assertEqual(link.selector_for("L1.output_face"), ">X")
        self.assertEqual(link.selector_for("L1.body_axis"), "axis:X")

    def test_builds_joint_cylinder_link_box_and_flange_geometry_ops(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        joint_ops = [op[0] for op in catalog.require("J1").solid.ops]
        link_ops = [op[0] for op in catalog.require("L1").solid.ops]

        self.assertIn("circle", joint_ops)
        self.assertIn("extrude", joint_ops)
        self.assertIn("union", joint_ops)
        self.assertIn("box", link_ops)
        self.assertIn("circle", link_ops)
        self.assertIn("union", link_ops)

    def test_link_parts_expose_primitive_metadata(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        link = catalog.require("L1")

        self.assertEqual(link.metadata["primitive_type"], "straight_link")
        self.assertEqual(link.metadata["link_primitive"]["type"], "straight_link")

    def test_datum_semantics_add_visible_reference_geometry(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        base_ops = [op[0] for op in catalog.require("base").solid.ops]
        joint_ops = [op[0] for op in catalog.require("J1").solid.ops]
        end_effector_ops = [op[0] for op in catalog.require("end_effector").solid.ops]

        self.assertGreaterEqual(base_ops.count("box"), 2)
        self.assertIn("joint_axis_pin", catalog.require("J1").metadata["datum_geometry_applied"])
        self.assertGreaterEqual(joint_ops.count("circle"), 4)
        self.assertIn(
            "tool_mount_register",
            catalog.require("end_effector").metadata["datum_geometry_applied"],
        )
        self.assertGreaterEqual(end_effector_ops.count("circle"), 3)

    def test_link_primitive_changes_concept_geometry(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="scaled_profile",
                target_reach=500,
            )
        )
        layout = build_tabletop_serial_mechanical_layout_from_model(scaling.model)

        catalog = build_cadquery_parts(layout, cq_module=FakeCadQuery)
        elbow_ops = [op[0] for op in catalog.require("L3").solid.ops]
        l5_ops = [op[0] for op in catalog.require("L5").solid.ops]
        wrist_ops = [op[0] for op in catalog.require("L6").solid.ops]

        self.assertEqual(catalog.require("L3").metadata["primitive_type"], "elbow_link")
        self.assertGreaterEqual(elbow_ops.count("box"), 2)
        self.assertEqual(catalog.require("L5").metadata["primitive_type"], "elbow_link")
        self.assertGreaterEqual(l5_ops.count("box"), 2)
        self.assertEqual(catalog.require("L6").metadata["primitive_type"], "wrist_spacer")
        self.assertNotIn("box", wrist_ops)
        self.assertIn("circle", wrist_ops)

    def test_structure_route_offset_direction_changes_link_geometry(self) -> None:
        scaling = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name="ur3e",
                mode="scaled_profile",
                target_reach=500,
            )
        )
        plan = build_generic_6axis_cobot_structure_plan(reach_mm=500)
        for route in plan.link_routes:
            if route.link_id == "L1":
                route.waypoints = [(0.0, 0.0, 150.0)]
        layout = build_mechanical_layout_from_structure_plan(scaling.model, plan)

        catalog = build_cadquery_parts(layout, cq_module=FakeCadQuery)
        link = catalog.require("L1")
        box_ops = [op for op in link.solid.ops if op[0] == "box"]
        translate_ops = [op for op in link.solid.ops if op[0] == "translate"]

        self.assertEqual(link.metadata["primitive_type"], "offset_link")
        self.assertEqual(
            link.metadata["link_primitive"]["route_offset_vector"],
            (0.0, 0.0, 150.0),
        )
        self.assertIn(("box", (40.0, 40.0, 60.0)), box_ops)
        self.assertIn(("translate", ((-22.5, 0.0, -15.0),)), translate_ops)
        self.assertIn(("translate", ((22.5, 0.0, 15.0),)), translate_ops)

    def test_end_effector_has_mount_and_tool_features(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        end_effector = catalog.require("end_effector")

        self.assertEqual(end_effector.selector_for("end_effector.mount"), "<X")
        self.assertEqual(end_effector.selector_for("end_effector.tool"), ">X")
        self.assertIn("mount", end_effector.solid.tags)
        self.assertIn("tool", end_effector.solid.tags)

    def test_unknown_part_id_raises_clear_error(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        with self.assertRaises(KeyError):
            catalog.require("missing")

    def test_unknown_feature_selector_raises_clear_error(self) -> None:
        catalog = build_cadquery_parts(self._layout(), cq_module=FakeCadQuery)

        with self.assertRaises(KeyError):
            catalog.require("base").selector_for("missing.feature")


if __name__ == "__main__":
    unittest.main()
