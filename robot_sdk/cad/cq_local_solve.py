"""CadQuery local subassembly solve adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from robot_sdk.assembly.cq_adapter import (
    CadQueryConstraintCall,
    build_layout_cadquery_constraint_calls,
)
from robot_sdk.assembly.local_solve import (
    LocalSubassemblyPlan,
    LocalSubassemblySpec,
    build_local_subassembly_plans,
)
from robot_sdk.cad.cq_assembly import _assembly_object_locations, _part_placements
from robot_sdk.cad.cq_parts import CadQueryPartCatalog, build_cadquery_parts
from robot_sdk.types import MechanicalLayout


@dataclass(frozen=True)
class CadQueryLocalSubassemblySolveResult:
    """Result of solving one local subassembly."""

    name: str
    part_ids: list[str]
    anchor_part_id: str
    assembly: Any
    constraint_calls: list[CadQueryConstraintCall]
    applied_constraint_ids: list[str]
    skipped_constraint_ids: list[str]
    skipped_reasons: dict[str, str]
    solved: bool
    residual_reports: list[dict[str, object]] = field(default_factory=list)
    pose_delta_report: dict[str, object] = field(default_factory=dict)
    part_locations: dict[str, Any] = field(default_factory=dict)
    solve_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def constraint_count(self) -> int:
        return len(self.constraint_calls)

    @property
    def semantic_constraint_count(self) -> int:
        return len(self.applied_constraint_ids)


def solve_local_subassemblies(
    layout: MechanicalLayout,
    *,
    specs: list[LocalSubassemblySpec] | None = None,
    cq_module: Any | None = None,
    solve_verbosity: int = 0,
    raise_on_solve_error: bool = False,
) -> list[CadQueryLocalSubassemblySolveResult]:
    """Solve default or provided local subassemblies with CadQuery."""

    cq = cq_module or _import_cadquery()
    part_catalog = build_cadquery_parts(layout, cq_module=cq)
    placements = _part_placements(cq, layout, part_catalog)
    all_semantic_calls = build_layout_cadquery_constraint_calls(layout, part_catalog)
    call_by_id = {call.constraint_id: call for call in all_semantic_calls}
    plans = build_local_subassembly_plans(layout, specs=specs)
    return [
        solve_local_subassembly_plan(
            plan,
            cq_module=cq,
            part_catalog=part_catalog,
            placements=placements,
            call_by_id=call_by_id,
            solve_verbosity=solve_verbosity,
            raise_on_solve_error=raise_on_solve_error,
        )
        for plan in plans
    ]


def solve_local_subassembly_plan(
    plan: LocalSubassemblyPlan,
    *,
    cq_module: Any,
    part_catalog: CadQueryPartCatalog,
    placements: dict[str, Any],
    call_by_id: dict[str, CadQueryConstraintCall],
    solve_verbosity: int = 0,
    raise_on_solve_error: bool = False,
) -> CadQueryLocalSubassemblySolveResult:
    """Build and solve one CadQuery local subassembly."""

    assembly = cq_module.Assembly()
    for part_id in plan.part_ids:
        part = part_catalog.require(part_id)
        assembly.add(part.solid, name=part_id, loc=placements[part_id])

    constraint_calls = [
        CadQueryConstraintCall(
            constraint_id=f"{plan.name}_{plan.anchor_part_id}_fixed_seed",
            kind="Fixed",
            fixed_query=plan.anchor_part_id,
            rationale=(
                f"Use {plan.anchor_part_id} as the fixed seed for local "
                f"subassembly `{plan.name}`."
            ),
            metadata={"constraint_scope": "local_subassembly_fixed_seed"},
        )
    ]
    constraint_calls.extend(
        call_by_id[constraint_id]
        for constraint_id in plan.applied_constraint_ids
        if constraint_id in call_by_id
    )

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

    subassembly_placements = {
        part_id: placements[part_id]
        for part_id in plan.part_ids
    }
    part_locations = _assembly_object_locations(assembly, subassembly_placements)
    pose_delta_report = _pose_delta_report(
        initial_locations=subassembly_placements,
        solved_locations=part_locations,
    )
    residual_reports = [residual.to_dict() for residual in plan.residuals]
    spec_metadata = dict(plan.spec.metadata)
    return CadQueryLocalSubassemblySolveResult(
        name=plan.name,
        part_ids=plan.part_ids,
        anchor_part_id=plan.anchor_part_id,
        assembly=assembly,
        constraint_calls=constraint_calls,
        applied_constraint_ids=plan.applied_constraint_ids,
        skipped_constraint_ids=plan.skipped_constraint_ids,
        skipped_reasons=plan.skipped_reasons,
        solved=solved,
        solve_error=solve_error,
        residual_reports=residual_reports,
        pose_delta_report=pose_delta_report,
        part_locations=part_locations,
        metadata={
            "assembly_mode": "local_constraint_solve",
            "semantic_constraint_application": "local_subassembly",
            "part_ids": plan.part_ids,
            "anchor_part_id": plan.anchor_part_id,
            "spec_metadata": spec_metadata,
            "role": spec_metadata.get("role"),
            "source": spec_metadata.get("source"),
            "semantic_constraint_count": len(plan.applied_constraint_ids),
            "skipped_constraint_count": len(plan.skipped_constraint_ids),
            "residual_reports": residual_reports,
            "pose_delta_report": pose_delta_report,
            "warnings": list(plan.warnings),
        },
    )


def _pose_delta_report(
    *,
    initial_locations: dict[str, Any],
    solved_locations: dict[str, Any],
) -> dict[str, object]:
    part_deltas: dict[str, dict[str, object]] = {}
    max_translation_delta = 0.0
    comparable_count = 0
    for part_id, initial in initial_locations.items():
        solved = solved_locations.get(part_id, initial)
        delta = _location_translation_delta(initial, solved)
        comparable = delta is not None
        if delta is not None:
            comparable_count += 1
            max_translation_delta = max(max_translation_delta, delta)
        part_deltas[part_id] = {
            "translation_delta_mm": delta,
            "comparable": comparable,
        }
    return {
        "max_translation_delta_mm": max_translation_delta,
        "comparable_part_count": comparable_count,
        "part_deltas": part_deltas,
    }


def _location_translation_delta(initial: Any, solved: Any) -> float | None:
    initial_xyz = _location_xyz(initial)
    solved_xyz = _location_xyz(solved)
    if initial_xyz is None or solved_xyz is None:
        return None
    return (
        (initial_xyz[0] - solved_xyz[0]) ** 2
        + (initial_xyz[1] - solved_xyz[1]) ** 2
        + (initial_xyz[2] - solved_xyz[2]) ** 2
    ) ** 0.5


def _location_xyz(location: Any) -> tuple[float, float, float] | None:
    if isinstance(location, dict):
        value = location.get("loc")
        if isinstance(value, (tuple, list)) and len(value) == 3:
            return (float(value[0]), float(value[1]), float(value[2]))
    if isinstance(location, (tuple, list)) and len(location) == 3:
        return (float(location[0]), float(location[1]), float(location[2]))
    return None


def _import_cadquery() -> Any:
    try:
        import cadquery as cq
    except ImportError as exc:
        raise RuntimeError("cadquery is required to solve local subassemblies") from exc
    return cq


__all__ = [
    "CadQueryLocalSubassemblySolveResult",
    "solve_local_subassemblies",
    "solve_local_subassembly_plan",
]
