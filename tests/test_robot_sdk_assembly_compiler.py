import unittest
from dataclasses import replace

from robot_sdk.assembly.compiler import compile_assembly_plan
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import AssemblyConstraint, DHParam, PartFeature


class RobotSdkAssemblyCompilerTest(unittest.TestCase):
    def assertVectorAlmostEqual(self, left, right, places: int = 6) -> None:
        self.assertEqual(len(left), len(right))
        for left_value, right_value in zip(left, right):
            self.assertAlmostEqual(left_value, right_value, places=places)

    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_compiles_all_occurrences_from_root(self) -> None:
        result = compile_assembly_plan(self._layout())

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(result.root_part_id, "base")
        self.assertEqual(result.unresolved_part_ids, [])
        self.assertEqual(result.conflicts, [])
        self.assertEqual(
            set(result.compiled_part_deltas),
            {"base", "J1", "L1", "J2", "L2", "end_effector"},
        )
        self.assertEqual(result.metadata["compiler_mode"], "translation_origin_propagation")

    def test_rewrites_dict_locations_with_compiled_deltas(self) -> None:
        layout = self._layout()
        constraint = next(
            item
            for item in layout.assembly_constraints
            if item.fixed == "base.top" and item.moving == "J1.bottom"
        )
        features = []
        for feature in layout.part_features:
            if feature.id == "base.top":
                features.append(feature)
            elif feature.id == "J1.bottom":
                features.append(replace(feature, frame="end_effector_mount"))
        shifted_layout = replace(
            layout,
            part_features=features,
            assembly_constraints=[constraint],
        )
        initial_locations = {
            "base": {"loc": (0.0, 0.0, 0.0), "xDir": (1.0, 0.0, 0.0)},
            "J1": {"loc": (0.0, 0.0, 0.0), "xDir": (1.0, 0.0, 0.0)},
        }

        result = compile_assembly_plan(
            shifted_layout,
            initial_locations=initial_locations,
            terminal_part_id="J1",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(result.compiled_locations["base"]["loc"], (0.0, 0.0, 0.0))
        self.assertNotEqual(result.compiled_locations["J1"]["loc"], (0.0, 0.0, 0.0))
        self.assertVectorAlmostEqual(
            result.compiled_locations["J1"]["xDir"],
            (0.0, 0.0, 1.0),
        )

    def test_rewrites_dict_location_orientation_from_datum_alignment(self) -> None:
        layout = self._layout()
        rotated_layout = replace(
            layout,
            part_features=[
                PartFeature(
                    id="base.top",
                    part_id="base",
                    cad_tag="top",
                    type="plane",
                    semantic="mount_face",
                    metadata={"normal": (0.0, 0.0, 1.0)},
                ),
                PartFeature(
                    id="J1.bottom",
                    part_id="J1",
                    cad_tag="bottom",
                    type="plane",
                    semantic="flange_face",
                    metadata={"normal": (1.0, 0.0, 0.0)},
                ),
            ],
            assembly_constraints=[
                AssemblyConstraint(
                    id="base_to_j1_plane",
                    fixed="base.top",
                    moving="J1.bottom",
                    kind="Plane",
                    rationale="Align base top to J1 bottom for orientation compilation.",
                )
            ],
        )
        initial_locations = {
            "base": {
                "loc": (0.0, 0.0, 0.0),
                "xDir": (1.0, 0.0, 0.0),
                "normal": (0.0, 0.0, 1.0),
            },
            "J1": {
                "loc": (0.0, 0.0, 0.0),
                "xDir": (1.0, 0.0, 0.0),
                "normal": (0.0, 0.0, 1.0),
            },
        }

        result = compile_assembly_plan(
            rotated_layout,
            initial_locations=initial_locations,
            terminal_part_id="J1",
        )

        self.assertTrue(result.ok, result.to_dict())
        self.assertVectorAlmostEqual(
            result.compiled_locations["J1"]["xDir"],
            (0.0, 0.0, 1.0),
        )
        self.assertVectorAlmostEqual(
            result.compiled_locations["J1"]["normal"],
            (-1.0, 0.0, 0.0),
        )
        self.assertEqual(
            result.metadata["datum_mode"],
            "part_local_origin_normal_tangent",
        )


if __name__ == "__main__":
    unittest.main()
