"""Constraint graph analysis for local subassemblies and whole assemblies."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from robot_sdk.assembly.constraints import (
    AssemblyMateConstraintRule,
    build_assembly_mate_constraint_plan,
)
from robot_sdk.assembly.local_solve import LocalSubassemblyPlan
from robot_sdk.types import MechanicalLayout


FullAssemblySolveGateStatus = Literal["clear_for_fixture", "blocked"]


@dataclass(frozen=True)
class ConstraintGraphEdge:
    """One unique part-to-part edge in a constraint graph."""

    part_a: str
    part_b: str
    constraint_ids: list[str]

    @property
    def key(self) -> tuple[str, str]:
        return tuple(sorted((self.part_a, self.part_b)))  # type: ignore[return-value]

    @property
    def constraint_count(self) -> int:
        return len(self.constraint_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "part_a": self.part_a,
            "part_b": self.part_b,
            "constraint_ids": list(self.constraint_ids),
            "constraint_count": self.constraint_count,
        }


@dataclass(frozen=True)
class LocalSubassemblyConstraintGraph:
    """Connectivity summary for one local subassembly plan."""

    name: str
    part_ids: list[str]
    anchor_part_id: str
    edges: list[ConstraintGraphEdge]
    isolated_part_ids: list[str]
    component_count: int
    cycle_count: int
    dense_edges: list[ConstraintGraphEdge] = field(default_factory=list)

    @property
    def connected(self) -> bool:
        return bool(self.part_ids) and self.component_count == 1 and not self.isolated_part_ids

    @property
    def underconstrained(self) -> bool:
        return not self.connected

    @property
    def has_cycles(self) -> bool:
        return self.cycle_count > 0

    @property
    def potentially_overconstrained(self) -> bool:
        return bool(self.dense_edges)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "part_ids": list(self.part_ids),
            "anchor_part_id": self.anchor_part_id,
            "edge_count": len(self.edges),
            "constraint_count": sum(len(edge.constraint_ids) for edge in self.edges),
            "isolated_part_ids": list(self.isolated_part_ids),
            "component_count": self.component_count,
            "cycle_count": self.cycle_count,
            "dense_edges": [edge.to_dict() for edge in self.dense_edges],
            "connected": self.connected,
            "underconstrained": self.underconstrained,
            "has_cycles": self.has_cycles,
            "potentially_overconstrained": self.potentially_overconstrained,
        }


@dataclass(frozen=True)
class WholeAssemblyConstraintGraph:
    """Connectivity summary for the whole production assembly graph."""

    name: str
    part_ids: list[str]
    anchor_part_id: str
    terminal_part_id: str
    edges: list[ConstraintGraphEdge]
    isolated_part_ids: list[str]
    component_count: int
    cycle_count: int
    dense_edges: list[ConstraintGraphEdge] = field(default_factory=list)
    anchor_to_terminal_connected: bool = False

    @property
    def connected(self) -> bool:
        return bool(self.part_ids) and self.component_count == 1 and not self.isolated_part_ids

    @property
    def underconstrained(self) -> bool:
        return not self.connected or not self.anchor_to_terminal_connected

    @property
    def has_cycles(self) -> bool:
        return self.cycle_count > 0

    @property
    def potentially_overconstrained(self) -> bool:
        return bool(self.dense_edges)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "part_ids": list(self.part_ids),
            "anchor_part_id": self.anchor_part_id,
            "terminal_part_id": self.terminal_part_id,
            "edge_count": len(self.edges),
            "constraint_count": sum(len(edge.constraint_ids) for edge in self.edges),
            "isolated_part_ids": list(self.isolated_part_ids),
            "component_count": self.component_count,
            "cycle_count": self.cycle_count,
            "dense_edges": [edge.to_dict() for edge in self.dense_edges],
            "connected": self.connected,
            "underconstrained": self.underconstrained,
            "has_cycles": self.has_cycles,
            "potentially_overconstrained": self.potentially_overconstrained,
            "anchor_to_terminal_connected": self.anchor_to_terminal_connected,
        }


@dataclass(frozen=True)
class ConstraintGraphReport:
    """Graph validation summary for all local subassemblies."""

    graphs: list[LocalSubassemblyConstraintGraph]
    whole_graph: WholeAssemblyConstraintGraph | None = None

    @property
    def underconstrained(self) -> list[LocalSubassemblyConstraintGraph]:
        return [graph for graph in self.graphs if graph.underconstrained]

    @property
    def cyclic(self) -> list[LocalSubassemblyConstraintGraph]:
        return [graph for graph in self.graphs if graph.has_cycles]

    @property
    def potentially_overconstrained(self) -> list[LocalSubassemblyConstraintGraph]:
        return [graph for graph in self.graphs if graph.potentially_overconstrained]

    @property
    def whole_underconstrained(self) -> bool:
        return bool(self.whole_graph and self.whole_graph.underconstrained)

    @property
    def whole_cyclic(self) -> bool:
        return bool(self.whole_graph and self.whole_graph.has_cycles)

    @property
    def whole_potentially_overconstrained(self) -> bool:
        return bool(self.whole_graph and self.whole_graph.potentially_overconstrained)


@dataclass(frozen=True)
class FullAssemblySolveGate:
    """Precondition status before attempting full-assembly constraint solve."""

    status: FullAssemblySolveGateStatus
    reasons: list[str]
    blocking_graphs: list[dict[str, object]] = field(default_factory=list)

    @property
    def clear_for_fixture(self) -> bool:
        return self.status == "clear_for_fixture"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "clear_for_fixture": self.clear_for_fixture,
            "reasons": list(self.reasons),
            "blocking_graphs": list(self.blocking_graphs),
        }


def build_constraint_graph_report(
    plans: list[LocalSubassemblyPlan],
    *,
    dense_pair_constraint_limit: int = 2,
    layout: MechanicalLayout | None = None,
    whole_dense_pair_constraint_limit: int = 2,
) -> ConstraintGraphReport:
    """Analyze local and optional whole-assembly constraints as part graphs."""

    return ConstraintGraphReport(
        graphs=[
            build_local_subassembly_constraint_graph(
                plan,
                dense_pair_constraint_limit=dense_pair_constraint_limit,
            )
            for plan in plans
        ],
        whole_graph=build_whole_assembly_constraint_graph(
            layout,
            dense_pair_constraint_limit=whole_dense_pair_constraint_limit,
        )
        if layout is not None
        else None,
    )


def evaluate_full_assembly_solve_gate(
    report: ConstraintGraphReport,
) -> FullAssemblySolveGate:
    """Return whether graph checks allow starting a full-solve fixture.

    This does not enable full assembly solve. It only says whether the local
    subassembly graph health is clean enough to run the next fixture experiment.
    """

    reasons: list[str] = []
    blocking_graphs: list[dict[str, object]] = []
    if report.underconstrained:
        reasons.append("underconstrained local subassembly graph")
        blocking_graphs.extend(graph.to_dict() for graph in report.underconstrained)
    if report.cyclic:
        reasons.append("cyclic local subassembly graph")
        blocking_graphs.extend(graph.to_dict() for graph in report.cyclic)
    if report.potentially_overconstrained:
        reasons.append("dense part-pair constraints")
        blocking_graphs.extend(
            graph.to_dict() for graph in report.potentially_overconstrained
        )
    if report.whole_graph is not None:
        if report.whole_graph.underconstrained:
            reasons.append("underconstrained whole assembly graph")
            blocking_graphs.append(report.whole_graph.to_dict())
        if report.whole_graph.has_cycles:
            reasons.append("cyclic whole assembly graph")
            blocking_graphs.append(report.whole_graph.to_dict())
        if report.whole_graph.potentially_overconstrained:
            reasons.append("dense whole-assembly part-pair constraints")
            blocking_graphs.append(report.whole_graph.to_dict())
    if reasons:
        return FullAssemblySolveGate(
            status="blocked",
            reasons=reasons,
            blocking_graphs=blocking_graphs,
        )
    return FullAssemblySolveGate(
        status="clear_for_fixture",
        reasons=[
            (
                "local subassembly and whole assembly graphs are clear; full "
                "assembly solve remains disabled until fixture validation passes"
            )
        ],
    )


def build_whole_assembly_constraint_graph(
    layout: MechanicalLayout,
    *,
    anchor_part_id: str = "base",
    terminal_part_id: str = "end_effector",
    dense_pair_constraint_limit: int = 2,
) -> WholeAssemblyConstraintGraph:
    """Build graph metrics for every part connected by layout mate constraints."""

    plan = build_assembly_mate_constraint_plan(layout)
    part_ids = _whole_assembly_part_ids(layout, plan.rules)
    edges = _constraint_edges_from_rules(plan.rules)
    connected_parts = {part_id for edge in edges for part_id in (edge.part_a, edge.part_b)}
    isolated = sorted(set(part_ids) - connected_parts)
    component_count = _component_count(part_ids, edges)
    cycle_count = _cycle_count(component_count, len(edges), len(part_ids))
    dense_edges = [
        edge
        for edge in edges
        if len(edge.constraint_ids) > dense_pair_constraint_limit
    ]
    return WholeAssemblyConstraintGraph(
        name="whole_assembly",
        part_ids=part_ids,
        anchor_part_id=anchor_part_id,
        terminal_part_id=terminal_part_id,
        edges=edges,
        isolated_part_ids=isolated,
        component_count=component_count,
        cycle_count=cycle_count,
        dense_edges=dense_edges,
        anchor_to_terminal_connected=_parts_connected(
            part_ids,
            edges,
            anchor_part_id,
            terminal_part_id,
        ),
    )


def build_local_subassembly_constraint_graph(
    plan: LocalSubassemblyPlan,
    *,
    dense_pair_constraint_limit: int = 2,
) -> LocalSubassemblyConstraintGraph:
    """Build graph metrics for one local subassembly plan."""

    edges = _constraint_edges_from_rules(plan.applied_rules)
    connected_parts = {part_id for edge in edges for part_id in (edge.part_a, edge.part_b)}
    isolated = sorted(set(plan.part_ids) - connected_parts)
    component_count = _component_count(plan.part_ids, edges)
    cycle_count = _cycle_count(component_count, len(edges), len(plan.part_ids))
    dense_edges = [
        edge
        for edge in edges
        if len(edge.constraint_ids) > dense_pair_constraint_limit
    ]
    return LocalSubassemblyConstraintGraph(
        name=plan.name,
        part_ids=plan.part_ids,
        anchor_part_id=plan.anchor_part_id,
        edges=edges,
        isolated_part_ids=isolated,
        component_count=component_count,
        cycle_count=cycle_count,
        dense_edges=dense_edges,
    )


def _whole_assembly_part_ids(
    layout: MechanicalLayout,
    rules: list[AssemblyMateConstraintRule],
) -> list[str]:
    part_ids = {feature.part_id for feature in layout.part_features}
    for rule in rules:
        part_ids.add(rule.fixed_mate.part_id)
        part_ids.add(rule.moving_mate.part_id)
    return sorted(part_ids)


def _constraint_edges_from_rules(
    rules: list[AssemblyMateConstraintRule],
) -> list[ConstraintGraphEdge]:
    edge_constraints: dict[tuple[str, str], list[str]] = defaultdict(list)
    for rule in rules:
        fixed_part = rule.fixed_mate.part_id
        moving_part = rule.moving_mate.part_id
        if fixed_part == moving_part:
            continue
        key = tuple(sorted((fixed_part, moving_part)))
        edge_constraints[key].append(rule.constraint_id)
    return [
        ConstraintGraphEdge(
            part_a=key[0],
            part_b=key[1],
            constraint_ids=constraint_ids,
        )
        for key, constraint_ids in sorted(edge_constraints.items())
    ]


def _component_count(part_ids: list[str], edges: list[ConstraintGraphEdge]) -> int:
    parent = {part_id: part_id for part_id in part_ids}

    def find(part_id: str) -> str:
        while parent[part_id] != part_id:
            parent[part_id] = parent[parent[part_id]]
            part_id = parent[part_id]
        return part_id

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for edge in edges:
        if edge.part_a in parent and edge.part_b in parent:
            union(edge.part_a, edge.part_b)
    return len({find(part_id) for part_id in part_ids})


def _parts_connected(
    part_ids: list[str],
    edges: list[ConstraintGraphEdge],
    left: str,
    right: str,
) -> bool:
    if left not in part_ids or right not in part_ids:
        return False
    parent = _component_parent(part_ids, edges)
    return _find(parent, left) == _find(parent, right)


def _component_parent(
    part_ids: list[str],
    edges: list[ConstraintGraphEdge],
) -> dict[str, str]:
    parent = {part_id: part_id for part_id in part_ids}

    for edge in edges:
        if edge.part_a not in parent or edge.part_b not in parent:
            continue
        left_root = _find(parent, edge.part_a)
        right_root = _find(parent, edge.part_b)
        if left_root != right_root:
            parent[right_root] = left_root
    return parent


def _find(parent: dict[str, str], part_id: str) -> str:
    while parent[part_id] != part_id:
        parent[part_id] = parent[parent[part_id]]
        part_id = parent[part_id]
    return part_id


def _cycle_count(component_count: int, edge_count: int, node_count: int) -> int:
    if node_count <= 0:
        return 0
    return max(0, edge_count - node_count + component_count)


__all__ = [
    "ConstraintGraphEdge",
    "ConstraintGraphReport",
    "FullAssemblySolveGate",
    "FullAssemblySolveGateStatus",
    "LocalSubassemblyConstraintGraph",
    "WholeAssemblyConstraintGraph",
    "build_constraint_graph_report",
    "build_local_subassembly_constraint_graph",
    "build_whole_assembly_constraint_graph",
    "evaluate_full_assembly_solve_gate",
]
