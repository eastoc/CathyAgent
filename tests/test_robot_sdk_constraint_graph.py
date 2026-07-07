import unittest
from dataclasses import replace

from robot_sdk.assembly.constraint_graph import (
    build_constraint_graph_report,
    evaluate_full_assembly_solve_gate,
    build_whole_assembly_constraint_graph,
)
from robot_sdk.assembly.local_solve import build_local_subassembly_plans
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import AssemblyConstraint, DHParam


class RobotSdkConstraintGraphTest(unittest.TestCase):
    def _layout(self):
        return build_tabletop_serial_mechanical_layout(
            [
                DHParam(joint_id="J1", a=120, alpha=0, d=0, theta=0),
                DHParam(joint_id="J2", a=100, alpha=0, d=0, theta=0),
            ]
        )

    def test_default_local_subassembly_graphs_are_connected(self) -> None:
        report = build_constraint_graph_report(
            build_local_subassembly_plans(self._layout()),
            layout=self._layout(),
        )

        self.assertEqual(len(report.graphs), 3)
        self.assertIsNotNone(report.whole_graph)
        self.assertTrue(report.whole_graph.connected)
        self.assertTrue(report.whole_graph.anchor_to_terminal_connected)
        self.assertEqual(report.underconstrained, [])
        self.assertEqual(report.cyclic, [])
        self.assertEqual(report.potentially_overconstrained, [])
        self.assertTrue(all(graph.connected for graph in report.graphs))

        gate = evaluate_full_assembly_solve_gate(report)
        self.assertEqual(gate.status, "clear_for_fixture")
        self.assertTrue(gate.clear_for_fixture)
        self.assertEqual(gate.blocking_graphs, [])

    def test_builds_whole_assembly_graph_from_all_layout_constraints(self) -> None:
        graph = build_whole_assembly_constraint_graph(self._layout())

        self.assertEqual(graph.name, "whole_assembly")
        self.assertEqual(
            graph.part_ids,
            ["J1", "J2", "L1", "L2", "base", "end_effector"],
        )
        self.assertEqual(len(graph.edges), 5)
        self.assertTrue(graph.connected)
        self.assertTrue(graph.anchor_to_terminal_connected)
        self.assertEqual(graph.isolated_part_ids, [])
        self.assertEqual(graph.component_count, 1)
        self.assertEqual(graph.cycle_count, 0)

    def test_whole_assembly_graph_blocks_gate_when_chain_is_broken(self) -> None:
        layout = self._layout()
        broken_layout = replace(
            layout,
            assembly_constraints=[
                constraint
                for constraint in layout.assembly_constraints
                if not constraint.id.startswith("L1_output_to_J2")
                and not constraint.id.startswith("L1_output_tangent_to_J2")
            ],
        )

        report = build_constraint_graph_report(
            build_local_subassembly_plans(broken_layout),
            layout=broken_layout,
        )

        self.assertIsNotNone(report.whole_graph)
        self.assertTrue(report.whole_graph.underconstrained)
        self.assertFalse(report.whole_graph.anchor_to_terminal_connected)
        gate = evaluate_full_assembly_solve_gate(report)
        self.assertEqual(gate.status, "blocked")
        self.assertIn("underconstrained whole assembly graph", gate.reasons)

    def test_detects_isolated_parts_when_constraints_are_missing(self) -> None:
        layout = self._layout()
        broken_layout = replace(
            layout,
            assembly_constraints=[
                constraint
                for constraint in layout.assembly_constraints
                if "end_effector" not in constraint.id
            ],
        )

        report = build_constraint_graph_report(
            build_local_subassembly_plans(broken_layout),
            layout=broken_layout,
        )

        self.assertEqual([graph.name for graph in report.underconstrained], ["J2_L2_end_effector"])
        self.assertEqual(report.underconstrained[0].isolated_part_ids, ["end_effector"])

        gate = evaluate_full_assembly_solve_gate(report)
        self.assertEqual(gate.status, "blocked")
        self.assertFalse(gate.clear_for_fixture)
        self.assertIn("underconstrained local subassembly graph", gate.reasons)
        self.assertEqual(gate.blocking_graphs[0]["name"], "J2_L2_end_effector")

    def test_detects_cycles_in_local_subassembly_graphs(self) -> None:
        layout = self._layout()
        cycle_constraint = AssemblyConstraint(
            id="J2_output_to_end_effector_mount_direct_plane",
            fixed="J2.output_flange",
            moving="end_effector.mount",
            kind="Plane",
            rationale="Cycle fixture for graph validation.",
        )
        cyclic_layout = replace(
            layout,
            assembly_constraints=[*layout.assembly_constraints, cycle_constraint],
        )

        report = build_constraint_graph_report(
            build_local_subassembly_plans(cyclic_layout)
        )

        self.assertEqual([graph.name for graph in report.cyclic], ["J2_L2_end_effector"])
        self.assertEqual(report.cyclic[0].cycle_count, 1)

    def test_detects_dense_part_pair_constraints(self) -> None:
        layout = self._layout()
        extra_constraint = AssemblyConstraint(
            id="J2_output_to_L2_input_extra_plane",
            fixed="J2.output_flange",
            moving="L2.input_face",
            kind="Plane",
            rationale="Dense edge fixture for graph validation.",
        )
        dense_layout = replace(
            layout,
            assembly_constraints=[*layout.assembly_constraints, extra_constraint],
        )

        report = build_constraint_graph_report(
            build_local_subassembly_plans(dense_layout)
        )

        self.assertEqual(
            [graph.name for graph in report.potentially_overconstrained],
            ["J2_L2_end_effector"],
        )
        self.assertEqual(
            report.potentially_overconstrained[0].dense_edges[0].constraint_count,
            3,
        )


if __name__ == "__main__":
    unittest.main()
