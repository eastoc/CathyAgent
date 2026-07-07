import unittest

from robot_sdk.assembly.constraints import build_cadquery_assembly_constraint_plan
from robot_sdk.assembly.cq_adapter import (
    CadQueryConstraintCall,
    apply_cadquery_constraint_calls,
    apply_layout_constraints_to_assembly,
    build_cadquery_constraint_calls,
    build_layout_cadquery_constraint_calls,
)
from robot_sdk.cad.cq_parts import build_cadquery_parts
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import DHParam

from tests.test_robot_sdk_cq_parts import FakeCadQuery


class FakeAssembly:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def constrain(self, *args):
        self.calls.append(args)
        return self


class RobotSdkCadQueryAdapterTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def _parts_and_plan(self):
        layout = self._layout()
        part_catalog = build_cadquery_parts(layout, cq_module=FakeCadQuery)
        plan = build_cadquery_assembly_constraint_plan(layout)
        return layout, part_catalog, plan

    def test_builds_cadquery_constrain_calls_from_plan(self) -> None:
        _, part_catalog, plan = self._parts_and_plan()

        calls = build_cadquery_constraint_calls(plan, part_catalog)

        first = calls[0]
        self.assertEqual(first.constraint_id, "base_top_to_J1_bottom_plane")
        self.assertEqual(first.kind, "Plane")
        self.assertEqual(first.fixed_query, "base?top")
        self.assertEqual(first.moving_query, "J1?bottom")
        self.assertEqual(first.args(), ("base?top", "J1?bottom", "Plane"))

    def test_axis_constraint_uses_face_normal_queries(self) -> None:
        _, part_catalog, plan = self._parts_and_plan()

        calls = build_cadquery_constraint_calls(plan, part_catalog)
        axis_call = next(call for call in calls if call.constraint_id == "base_axis_to_J1_axis")

        self.assertEqual(axis_call.fixed_query, "base@faces@>Z")
        self.assertEqual(axis_call.moving_query, "J1@faces@>X")
        self.assertEqual(axis_call.args(), ("base@faces@>Z", "J1@faces@>X", "Axis"))

    def test_call_args_include_param_when_offset_exists(self) -> None:
        call = CadQueryConstraintCall(
            constraint_id="offset",
            kind="Plane",
            fixed_query="base?top",
            moving_query="J1?bottom",
            param=5.0,
            rationale="Offset coverage.",
        )

        self.assertEqual(call.args(), ("base?top", "J1?bottom", "Plane", 5.0))

    def test_fixed_call_is_unary(self) -> None:
        call = CadQueryConstraintCall(
            constraint_id="fixed",
            kind="Fixed",
            fixed_query="base?top",
            rationale="Fix base coverage.",
        )

        self.assertEqual(call.args(), ("base?top", "Fixed"))

    def test_apply_calls_to_assembly_like_object(self) -> None:
        calls = [
            CadQueryConstraintCall(
                constraint_id="c1",
                kind="Plane",
                fixed_query="base?top",
                moving_query="J1?bottom",
                rationale="Plane coverage.",
            ),
            CadQueryConstraintCall(
                constraint_id="c2",
                kind="Axis",
                fixed_query="base@faces@>Z",
                moving_query="J1@faces@>X",
                rationale="Axis coverage.",
            ),
        ]
        assembly = FakeAssembly()

        result = apply_cadquery_constraint_calls(assembly, calls)

        self.assertIs(result, assembly)
        self.assertEqual(
            assembly.calls,
            [
                ("base?top", "J1?bottom", "Plane"),
                ("base@faces@>Z", "J1@faces@>X", "Axis"),
            ],
        )

    def test_apply_layout_constraints_to_assembly(self) -> None:
        layout, part_catalog, _ = self._parts_and_plan()
        assembly = FakeAssembly()

        apply_layout_constraints_to_assembly(assembly, layout, part_catalog)

        self.assertEqual(len(assembly.calls), len(layout.assembly_constraints))
        self.assertEqual(assembly.calls[0], ("base?top", "J1?bottom", "Plane"))

    def test_build_layout_calls_matches_plan_calls(self) -> None:
        layout, part_catalog, plan = self._parts_and_plan()

        from_layout = build_layout_cadquery_constraint_calls(layout, part_catalog)
        from_plan = build_cadquery_constraint_calls(plan, part_catalog)

        self.assertEqual([call.args() for call in from_layout], [call.args() for call in from_plan])

    def test_rejects_assembly_without_constrain(self) -> None:
        with self.assertRaises(TypeError):
            apply_cadquery_constraint_calls(object(), [])


if __name__ == "__main__":
    unittest.main()
