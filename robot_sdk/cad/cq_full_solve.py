"""Experimental full-assembly semantic constraint solve fixture."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from robot_sdk.assembly.constraint_graph import (
    FullAssemblySolveGate,
    build_constraint_graph_report,
    evaluate_full_assembly_solve_gate,
)
from robot_sdk.assembly.compiler import compile_assembly_plan
from robot_sdk.assembly.constraints import (
    AssemblyMateConstraintRule,
    build_assembly_mate_constraint_plan,
)
from robot_sdk.assembly.cq_adapter import (
    CadQueryConstraintCall,
    build_layout_cadquery_constraint_calls,
)
from robot_sdk.assembly.local_solve import build_local_subassembly_plans
from robot_sdk.cad.cq_assembly import _assembly_object_locations, _part_placements
from robot_sdk.cad.cq_local_solve import _pose_delta_report
from robot_sdk.cad.cq_parts import CadQueryPartCatalog, build_cadquery_parts
from robot_sdk.types import MechanicalLayout
from robot_sdk.validation.assembly_geometry import build_assembly_geometry_report


@dataclass(frozen=True)
class CadQueryFullAssemblyFixtureResult:
    """Result of the experimental full-assembly semantic solve fixture."""

    name: str
    part_ids: list[str]
    anchor_part_id: str
    gate: FullAssemblySolveGate
    assembly: Any | None
    constraint_calls: list[CadQueryConstraintCall]
    semantic_constraint_ids: list[str]
    solved: bool
    ran: bool
    solve_error: str | None = None
    pose_delta_report: dict[str, object] = field(default_factory=dict)
    residual_reports: list[dict[str, object]] = field(default_factory=list)
    part_locations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def constraint_count(self) -> int:
        return len(self.constraint_calls)

    @property
    def semantic_constraint_count(self) -> int:
        return len(self.semantic_constraint_ids)


def solve_full_assembly_fixture(
    layout: MechanicalLayout,
    *,
    cq_module: Any | None = None,
    part_catalog: CadQueryPartCatalog | None = None,
    placements: dict[str, Any] | None = None,
    semantic_constraint_calls: list[CadQueryConstraintCall] | None = None,
    anchor_part_id: str = "base",
    solve_verbosity: int = 0,
    raise_on_solve_error: bool = False,
) -> CadQueryFullAssemblyFixtureResult:
    """Try a gated full-assembly solve with semantic mate constraints only.

    This fixture is intentionally not used for production STEP placement. It is
    a stage-6 experiment for checking whether all semantic mate constraints can
    be consumed by CadQuery's solver after local graph checks pass.
    """

    graph_report = build_constraint_graph_report(
        build_local_subassembly_plans(layout),
        layout=layout,
    )
    gate = evaluate_full_assembly_solve_gate(graph_report)
    if not gate.clear_for_fixture:
        return _skipped_result(
            layout=layout,
            gate=gate,
            anchor_part_id=anchor_part_id,
            reason="full assembly fixture blocked by constraint graph gate",
        )

    cq = cq_module or _import_cadquery()
    catalog = part_catalog or build_cadquery_parts(layout, cq_module=cq)
    part_ids = catalog.part_ids()
    if anchor_part_id not in part_ids:
        return _skipped_result(
            layout=layout,
            gate=gate,
            anchor_part_id=anchor_part_id,
            reason=f"anchor part `{anchor_part_id}` is not present in the part catalog",
        )

    part_placements = placements or _part_placements(cq, layout, catalog)
    compiler_result = compile_assembly_plan(
        layout,
        initial_locations=part_placements,
        root_part_id=anchor_part_id,
    )
    solver_seed_placements = compiler_result.compiled_locations or part_placements
    semantic_calls = semantic_constraint_calls or build_layout_cadquery_constraint_calls(
        layout,
        catalog,
    )
    constraint_calls = [
        CadQueryConstraintCall(
            constraint_id=f"full_assembly_{anchor_part_id}_fixed_seed",
            kind="Fixed",
            fixed_query=anchor_part_id,
            rationale=(
                f"Use {anchor_part_id} as the fixed seed for the experimental "
                "full-assembly semantic solve fixture."
            ),
            metadata={"constraint_scope": "full_assembly_fixture_fixed_seed"},
        ),
        *semantic_calls,
    ]

    assembly = cq.Assembly()
    for part_id in part_ids:
        part = catalog.require(part_id)
        assembly.add(part.solid, name=part_id, loc=solver_seed_placements[part_id])

    solved = False
    solve_error: str | None = None
    try:
        for call in constraint_calls:
            assembly.constrain(*call.args())
        assembly.solve(solve_verbosity)
        solved = True
    except Exception as exc:
        solve_error = str(exc)
        if raise_on_solve_error:
            raise

    part_locations = _assembly_object_locations(assembly, solver_seed_placements)
    pose_delta = _pose_delta_report(
        initial_locations=part_placements,
        solved_locations=part_locations,
    )
    geometry_report = build_assembly_geometry_report(
        layout,
        initial_locations=part_placements,
        solved_locations=part_locations,
    )
    residual_reports = geometry_report.residual_reports
    semantic_ids = [call.constraint_id for call in semantic_calls]
    return CadQueryFullAssemblyFixtureResult(
        name="full_assembly_semantic_fixture",
        part_ids=part_ids,
        anchor_part_id=anchor_part_id,
        gate=gate,
        assembly=assembly,
        constraint_calls=constraint_calls,
        semantic_constraint_ids=semantic_ids,
        solved=solved,
        ran=True,
        solve_error=solve_error,
        pose_delta_report=pose_delta,
        residual_reports=residual_reports,
        part_locations=part_locations,
        metadata={
            "assembly_mode": "full_constraint_solve_fixture",
            "constraint_scope": "full_assembly_semantic_fixture",
            "semantic_constraint_application": "full_assembly_fixture",
            "gate": gate.to_dict(),
            "part_ids": part_ids,
            "anchor_part_id": anchor_part_id,
            "constraint_count": len(constraint_calls),
            "semantic_constraint_count": len(semantic_ids),
            "semantic_constraint_ids": semantic_ids,
            "assembly_compiler": compiler_result.to_dict(),
            "assembly_compiler_mode": compiler_result.metadata["compiler_mode"],
            "assembly_compiler_ok": compiler_result.ok,
            "assembly_compiler_conflicts": [
                conflict.to_dict() for conflict in compiler_result.conflicts
            ],
            "assembly_compiler_unresolved_part_ids": list(
                compiler_result.unresolved_part_ids
            ),
            "assembly_compiler_warnings": list(compiler_result.warnings),
            "ran": True,
            "solved": solved,
            "solve_error": solve_error,
            "pose_delta_report": pose_delta,
            "residual_reports": residual_reports,
            "assembly_plan": geometry_report.assembly_plan.to_dict(),
            "component_cluster_report": geometry_report.component_cluster_report,
            "assembly_geometry_report": geometry_report.to_dict(),
        },
    )


def _skipped_result(
    *,
    layout: MechanicalLayout,
    gate: FullAssemblySolveGate,
    anchor_part_id: str,
    reason: str,
) -> CadQueryFullAssemblyFixtureResult:
    part_ids = sorted({feature.part_id for feature in layout.part_features})
    return CadQueryFullAssemblyFixtureResult(
        name="full_assembly_semantic_fixture",
        part_ids=part_ids,
        anchor_part_id=anchor_part_id,
        gate=gate,
        assembly=None,
        constraint_calls=[],
        semantic_constraint_ids=[],
        solved=False,
        ran=False,
        solve_error=None,
        metadata={
            "assembly_mode": "full_constraint_solve_fixture",
            "constraint_scope": "full_assembly_semantic_fixture",
            "semantic_constraint_application": "none",
            "gate": gate.to_dict(),
            "part_ids": part_ids,
            "anchor_part_id": anchor_part_id,
            "constraint_count": 0,
            "semantic_constraint_count": 0,
            "semantic_constraint_ids": [],
            "ran": False,
            "solved": False,
            "skipped_reason": reason,
        },
    )


def _semantic_residual_reports(layout: MechanicalLayout) -> list[dict[str, object]]:
    plan = build_assembly_mate_constraint_plan(layout)
    return [_constraint_residual_report(rule) for rule in plan.rules]


def _constraint_residual_report(
    rule: AssemblyMateConstraintRule,
) -> dict[str, object]:
    return {
        "constraint_id": rule.constraint_id,
        "fixed_feature_id": rule.fixed_feature_id,
        "moving_feature_id": rule.moving_feature_id,
        "origin_delta_mm": _distance(rule.fixed_mate.origin, rule.moving_mate.origin),
        "normal_angle_deg": _angle_deg(rule.fixed_mate.normal, rule.moving_mate.normal),
        "tangent_angle_deg": _angle_deg(rule.fixed_mate.tangent, rule.moving_mate.tangent),
    }


def _distance(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return math.sqrt(
        (left[0] - right[0]) ** 2
        + (left[1] - right[1]) ** 2
        + (left[2] - right[2]) ** 2
    )


def _angle_deg(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    left_len = math.sqrt(left[0] ** 2 + left[1] ** 2 + left[2] ** 2)
    right_len = math.sqrt(right[0] ** 2 + right[1] ** 2 + right[2] ** 2)
    if left_len <= 1e-12 or right_len <= 1e-12:
        return 0.0
    dot = (
        left[0] * right[0]
        + left[1] * right[1]
        + left[2] * right[2]
    ) / (left_len * right_len)
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


def _import_cadquery() -> Any:
    try:
        import cadquery as cq
    except ImportError as exc:
        raise RuntimeError("cadquery is required to solve full assembly fixture") from exc
    return cq


__all__ = [
    "CadQueryFullAssemblyFixtureResult",
    "solve_full_assembly_fixture",
]
