"""CadQuery assembly construction and constraint solving."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from robot_sdk.assembly.cq_adapter import (
    CadQueryConstraintCall,
    build_layout_cadquery_constraint_calls,
)
from robot_sdk.assembly.constraints import build_assembly_mate_constraint_plan
from robot_sdk.assembly.local_promotion import (
    build_local_subassembly_promotion_report,
)
from robot_sdk.cad.cq_parts import (
    DEFAULT_BASE_THICKNESS_MM,
    CadQueryPartCatalog,
    build_cadquery_parts,
)
from robot_sdk.types import FrameSpec, MechanicalLayout


ProductionAssemblySource = Literal["fixed_layout_pose", "full_semantic_solve"]


@dataclass(frozen=True)
class CadQueryAssemblyResult:
    """Result of building and constraint-solving a CadQuery Assembly."""

    assembly: Any
    part_catalog: CadQueryPartCatalog
    constraint_calls: list[CadQueryConstraintCall]
    solved: bool
    local_subassembly_results: list[Any] = field(default_factory=list)
    full_assembly_fixture_result: Any | None = None
    part_locations: dict[str, Any] = field(default_factory=dict)
    solve_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def part_count(self) -> int:
        return len(self.part_catalog.part_ids())

    @property
    def constraint_count(self) -> int:
        return len(self.constraint_calls)


def build_cadquery_assembly(
    layout: MechanicalLayout,
    *,
    cq_module: Any | None = None,
    solve: bool = True,
    solve_verbosity: int = 0,
    raise_on_solve_error: bool = True,
    local_subassembly_solve: bool = True,
    raise_on_local_solve_error: bool = False,
    full_assembly_fixture: bool = True,
    raise_on_full_fixture_error: bool = False,
    production_assembly_source: ProductionAssemblySource = "full_semantic_solve",
    raise_on_full_semantic_solve_error: bool = True,
) -> CadQueryAssemblyResult:
    """Build a CadQuery Assembly from `MechanicalLayout`.

    The function generates parts, adds them to CadQuery assemblies using layout
    frames as the initial solver guess, and solves both the fixed-pose fallback
    assembly and the gated semantic assembly path. By default the production
    assembly uses the full-assembly semantic solve. Passing
    `production_assembly_source="fixed_layout_pose"` keeps the previous
    conservative fixed-pose production export path.
    """

    if not solve:
        raise ValueError("Only constraint_solve assembly mode is supported; solve must be True.")
    if production_assembly_source not in {"fixed_layout_pose", "full_semantic_solve"}:
        raise ValueError(
            "production_assembly_source must be `fixed_layout_pose` or `full_semantic_solve`."
        )

    cq = cq_module or _import_cadquery()
    part_catalog = build_cadquery_parts(layout, cq_module=cq)
    assembly = cq.Assembly()
    placements = _part_placements(cq, layout, part_catalog)

    for part_id in part_catalog.part_ids():
        part = part_catalog.require(part_id)
        assembly.add(part.solid, name=part_id, loc=placements[part_id])

    semantic_constraint_calls = build_layout_cadquery_constraint_calls(layout, part_catalog)
    mate_constraint_plan = build_assembly_mate_constraint_plan(layout)
    constraint_calls = _fixed_layout_pose_constraint_calls(part_catalog.part_ids())

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

    local_subassembly_results: list[Any] = []
    if local_subassembly_solve:
        local_subassembly_results = _solve_local_subassemblies_from_current_parts(
            layout=layout,
            cq_module=cq,
            part_catalog=part_catalog,
            placements=placements,
            semantic_constraint_calls=semantic_constraint_calls,
            solve_verbosity=solve_verbosity,
            raise_on_solve_error=raise_on_local_solve_error,
        )
    local_metadata = _local_subassembly_metadata(local_subassembly_results)
    full_fixture_result: Any | None = None
    full_fixture_requested = (
        full_assembly_fixture or production_assembly_source == "full_semantic_solve"
    )
    if full_fixture_requested:
        full_fixture_result = _solve_full_assembly_fixture_from_current_parts(
            layout=layout,
            cq_module=cq,
            part_catalog=part_catalog,
            placements=placements,
            semantic_constraint_calls=semantic_constraint_calls,
            solve_verbosity=solve_verbosity,
            raise_on_solve_error=raise_on_full_fixture_error,
        )
    full_fixture_metadata = _full_assembly_fixture_metadata(
        full_fixture_result,
        requested=full_fixture_requested,
    )

    production_selection = _select_production_assembly(
        requested_source=production_assembly_source,
        fixed_assembly=assembly,
        fixed_constraint_calls=constraint_calls,
        fixed_part_locations=_assembly_object_locations(assembly, placements),
        fixed_solved=solved,
        fixed_solve_error=solve_error,
        local_metadata=local_metadata,
        full_fixture_result=full_fixture_result,
        raise_on_full_semantic_solve_error=raise_on_full_semantic_solve_error,
    )

    return CadQueryAssemblyResult(
        assembly=production_selection["assembly"],
        part_catalog=part_catalog,
        constraint_calls=production_selection["constraint_calls"],
        local_subassembly_results=local_subassembly_results,
        full_assembly_fixture_result=full_fixture_result,
        part_locations=production_selection["part_locations"],
        solved=bool(production_selection["solved"]),
        solve_error=production_selection["solve_error"],
        metadata={
            "units": layout.units,
            "part_ids": part_catalog.part_ids(),
            "assembly_mode": "constraint_solve",
            "placement_mode": production_selection["placement_mode"],
            "solver_constraint_mode": production_selection["solver_constraint_mode"],
            "constraint_scope": production_selection["constraint_scope"],
            "solve_requested": True,
            "semantic_constraint_count": len(semantic_constraint_calls),
            "mate_frame_count": len(mate_constraint_plan.mate_catalog.by_id),
            "mate_constraint_rule_count": len(mate_constraint_plan.rules),
            "unsafe_mate_constraint_count": len(mate_constraint_plan.unsafe_rules),
            "semantic_constraint_application": production_selection[
                "semantic_application"
            ],
            "semantic_constraints_applied_to_solver": production_selection[
                "semantic_application"
            ],
            "requested_production_assembly_source": production_assembly_source,
            "production_assembly_source": production_selection["source"],
            "production_full_assembly_semantic_solve": bool(
                production_selection["full_semantic_used"]
            ),
            "production_full_semantic_solve_requested": bool(
                production_selection["full_semantic_requested"]
            ),
            "production_full_semantic_solve_used": bool(
                production_selection["full_semantic_used"]
            ),
            "production_full_semantic_solve_error": production_selection[
                "full_semantic_error"
            ],
            "production_full_semantic_solve_gate_status": production_selection[
                "full_semantic_gate_status"
            ],
            "production_full_semantic_solve_pose_delta_report": production_selection[
                "full_semantic_pose_delta_report"
            ],
            "production_full_semantic_solve_residual_reports": production_selection[
                "full_semantic_residual_reports"
            ],
            "production_full_semantic_solve_component_cluster_report": production_selection[
                "full_semantic_component_cluster_report"
            ],
            "production_full_semantic_solve_assembly_plan": production_selection[
                "full_semantic_assembly_plan"
            ],
            "production_full_semantic_solve_geometry_report": production_selection[
                "full_semantic_geometry_report"
            ],
            "production_full_semantic_solve_assembly_compiler": production_selection[
                "full_semantic_assembly_compiler"
            ],
            **local_metadata,
            **full_fixture_metadata,
        },
    )


def _solve_full_assembly_fixture_from_current_parts(
    *,
    layout: MechanicalLayout,
    cq_module: Any,
    part_catalog: CadQueryPartCatalog,
    placements: dict[str, Any],
    semantic_constraint_calls: list[CadQueryConstraintCall],
    solve_verbosity: int,
    raise_on_solve_error: bool,
) -> Any:
    """Run the experimental full-assembly semantic solve fixture."""

    from robot_sdk.cad.cq_full_solve import solve_full_assembly_fixture

    return solve_full_assembly_fixture(
        layout,
        cq_module=cq_module,
        part_catalog=part_catalog,
        placements=placements,
        semantic_constraint_calls=semantic_constraint_calls,
        solve_verbosity=solve_verbosity,
        raise_on_solve_error=raise_on_solve_error,
    )


def _select_production_assembly(
    *,
    requested_source: ProductionAssemblySource,
    fixed_assembly: Any,
    fixed_constraint_calls: list[CadQueryConstraintCall],
    fixed_part_locations: dict[str, Any],
    fixed_solved: bool,
    fixed_solve_error: str | None,
    local_metadata: dict[str, Any],
    full_fixture_result: Any | None,
    raise_on_full_semantic_solve_error: bool,
) -> dict[str, Any]:
    if requested_source == "fixed_layout_pose":
        semantic_application = (
            "local_subassembly"
            if local_metadata["local_subassembly_solved_count"]
            else "none"
        )
        return {
            "assembly": fixed_assembly,
            "constraint_calls": fixed_constraint_calls,
            "part_locations": fixed_part_locations,
            "solved": fixed_solved,
            "solve_error": fixed_solve_error,
            "source": "fixed_layout_pose",
            "placement_mode": "constraint_solve_fixed_layout_pose",
            "solver_constraint_mode": "fixed_layout_pose_constraints",
            "constraint_scope": "fixed_layout_pose_only",
            "semantic_application": semantic_application,
            "full_semantic_requested": False,
            "full_semantic_used": False,
            "full_semantic_error": None,
            "full_semantic_gate_status": _full_fixture_gate_status(full_fixture_result),
            "full_semantic_pose_delta_report": {},
            "full_semantic_residual_reports": [],
            "full_semantic_component_cluster_report": {},
            "full_semantic_assembly_plan": {},
            "full_semantic_geometry_report": {},
            "full_semantic_assembly_compiler": {},
        }

    error = _full_semantic_solve_blocker(full_fixture_result)
    if error is not None:
        if raise_on_full_semantic_solve_error:
            raise RuntimeError(error)
        return {
            "assembly": fixed_assembly,
            "constraint_calls": fixed_constraint_calls,
            "part_locations": fixed_part_locations,
            "solved": False,
            "solve_error": error,
            "source": "full_semantic_solve_failed",
            "placement_mode": "full_semantic_solve_failed",
            "solver_constraint_mode": "semantic_mate_constraints",
            "constraint_scope": "full_assembly_semantic_constraints",
            "semantic_application": "none",
            "full_semantic_requested": True,
            "full_semantic_used": False,
            "full_semantic_error": error,
            "full_semantic_gate_status": _full_fixture_gate_status(full_fixture_result),
            "full_semantic_pose_delta_report": dict(
                getattr(full_fixture_result, "pose_delta_report", {}) or {}
            )
            if full_fixture_result is not None
            else {},
            "full_semantic_residual_reports": list(
                getattr(full_fixture_result, "residual_reports", []) or []
            )
            if full_fixture_result is not None
            else [],
            "full_semantic_component_cluster_report": _full_fixture_metadata_item(
                full_fixture_result,
                "component_cluster_report",
                {},
            ),
            "full_semantic_assembly_plan": _full_fixture_metadata_item(
                full_fixture_result,
                "assembly_plan",
                {},
            ),
            "full_semantic_geometry_report": _full_fixture_metadata_item(
                full_fixture_result,
                "assembly_geometry_report",
                {},
            ),
            "full_semantic_assembly_compiler": _full_fixture_metadata_item(
                full_fixture_result,
                "assembly_compiler",
                {},
            ),
        }

    assert full_fixture_result is not None
    return {
        "assembly": full_fixture_result.assembly,
        "constraint_calls": list(full_fixture_result.constraint_calls),
        "part_locations": dict(full_fixture_result.part_locations),
        "solved": True,
        "solve_error": None,
        "source": "full_semantic_solve",
        "placement_mode": "full_semantic_solve",
        "solver_constraint_mode": "semantic_mate_constraints",
        "constraint_scope": "full_assembly_semantic_constraints",
        "semantic_application": "full_assembly",
        "full_semantic_requested": True,
        "full_semantic_used": True,
        "full_semantic_error": None,
        "full_semantic_gate_status": _full_fixture_gate_status(full_fixture_result),
        "full_semantic_pose_delta_report": dict(
            getattr(full_fixture_result, "pose_delta_report", {}) or {}
        ),
        "full_semantic_residual_reports": list(
            getattr(full_fixture_result, "residual_reports", []) or []
        ),
        "full_semantic_component_cluster_report": _full_fixture_metadata_item(
            full_fixture_result,
            "component_cluster_report",
            {},
        ),
        "full_semantic_assembly_plan": _full_fixture_metadata_item(
            full_fixture_result,
            "assembly_plan",
            {},
        ),
        "full_semantic_geometry_report": _full_fixture_metadata_item(
            full_fixture_result,
            "assembly_geometry_report",
            {},
        ),
        "full_semantic_assembly_compiler": _full_fixture_metadata_item(
            full_fixture_result,
            "assembly_compiler",
            {},
        ),
    }


def _full_semantic_solve_blocker(full_fixture_result: Any | None) -> str | None:
    if full_fixture_result is None:
        return "Production full semantic solve requested, but no full fixture result exists."
    if not bool(getattr(full_fixture_result, "ran", False)):
        return (
            "Production full semantic solve requested, but the full-assembly "
            "semantic fixture was blocked by its precondition gate."
        )
    if not bool(getattr(full_fixture_result, "solved", False)):
        solve_error = getattr(full_fixture_result, "solve_error", None)
        if solve_error:
            return (
                "Production full semantic solve requested, but the full-assembly "
                f"semantic fixture failed: {solve_error}"
            )
        return (
            "Production full semantic solve requested, but the full-assembly "
            "semantic fixture did not solve."
        )
    if getattr(full_fixture_result, "assembly", None) is None:
        return (
            "Production full semantic solve requested, but the full-assembly "
            "semantic fixture has no assembly object to export."
        )
    return None


def _full_fixture_gate_status(full_fixture_result: Any | None) -> str | None:
    if full_fixture_result is None:
        return None
    gate = getattr(full_fixture_result, "gate", None)
    status = getattr(gate, "status", None)
    return str(status) if status is not None else None


def _full_fixture_metadata_item(
    full_fixture_result: Any | None,
    key: str,
    default: Any,
) -> Any:
    metadata = dict(getattr(full_fixture_result, "metadata", {}) or {})
    return metadata.get(key, default)


def _solve_local_subassemblies_from_current_parts(
    *,
    layout: MechanicalLayout,
    cq_module: Any,
    part_catalog: CadQueryPartCatalog,
    placements: dict[str, Any],
    semantic_constraint_calls: list[CadQueryConstraintCall],
    solve_verbosity: int,
    raise_on_solve_error: bool,
) -> list[Any]:
    """Run local subassembly solves using the already-generated part catalog."""

    from robot_sdk.assembly.local_solve import build_local_subassembly_plans
    from robot_sdk.cad.cq_local_solve import solve_local_subassembly_plan

    call_by_id = {
        call.constraint_id: call
        for call in semantic_constraint_calls
    }
    return [
        solve_local_subassembly_plan(
            plan,
            cq_module=cq_module,
            part_catalog=part_catalog,
            placements=placements,
            call_by_id=call_by_id,
            solve_verbosity=solve_verbosity,
            raise_on_solve_error=raise_on_solve_error,
        )
        for plan in build_local_subassembly_plans(layout)
    ]


def _local_subassembly_metadata(results: list[Any]) -> dict[str, Any]:
    solved_count = sum(1 for result in results if bool(getattr(result, "solved", False)))
    failed = [
        result
        for result in results
        if getattr(result, "solve_error", None)
    ]
    summaries: list[dict[str, Any]] = []
    for result in results:
        result_metadata = dict(getattr(result, "metadata", {}) or {})
        spec_metadata = dict(result_metadata.get("spec_metadata") or {})
        summaries.append(
            {
                "name": result.name,
                "part_ids": list(result.part_ids),
                "anchor_part_id": result.anchor_part_id,
                "role": result_metadata.get("role") or spec_metadata.get("role"),
                "source": result_metadata.get("source") or spec_metadata.get("source"),
                "spec_metadata": spec_metadata,
                "solved": bool(result.solved),
                "solve_error": result.solve_error,
                "constraint_count": result.constraint_count,
                "semantic_constraint_count": result.semantic_constraint_count,
                "applied_constraint_ids": list(result.applied_constraint_ids),
                "skipped_constraint_ids": list(result.skipped_constraint_ids),
                "skipped_reasons": dict(result.skipped_reasons),
                "residual_reports": list(result.residual_reports),
                "pose_delta_report": dict(result.pose_delta_report),
            }
        )
    return {
        "local_subassembly_solve_requested": bool(results),
        "local_subassembly_count": len(results),
        "local_subassembly_solved_count": solved_count,
        "local_subassembly_failed_count": len(failed),
        "local_subassembly_names": [result.name for result in results],
        "local_subassemblies": summaries,
        "local_subassembly_promotion": build_local_subassembly_promotion_report(
            summaries
        ),
    }


def _full_assembly_fixture_metadata(
    result: Any | None,
    *,
    requested: bool,
) -> dict[str, Any]:
    if result is None:
        return {
            "full_assembly_fixture_requested": requested,
            "full_assembly_fixture_ran": False,
            "full_assembly_fixture_solved": False,
            "full_assembly_fixture_gate_status": "not_requested",
            "full_assembly_fixture": None,
        }
    metadata = dict(getattr(result, "metadata", {}) or {})
    gate = metadata.get("gate")
    gate_status = ""
    if isinstance(gate, dict):
        gate_status = str(gate.get("status") or "")
    return {
        "full_assembly_fixture_requested": requested,
        "full_assembly_fixture_ran": bool(getattr(result, "ran", False)),
        "full_assembly_fixture_solved": bool(getattr(result, "solved", False)),
        "full_assembly_fixture_gate_status": gate_status or "unknown",
        "full_assembly_fixture_solve_error": getattr(result, "solve_error", None),
        "full_assembly_fixture_constraint_count": int(
            getattr(result, "constraint_count", 0)
        ),
        "full_assembly_fixture_semantic_constraint_count": int(
            getattr(result, "semantic_constraint_count", 0)
        ),
        "full_assembly_fixture_pose_delta_report": dict(
            getattr(result, "pose_delta_report", {}) or {}
        ),
        "full_assembly_fixture_residual_reports": list(
            getattr(result, "residual_reports", []) or []
        ),
        "full_assembly_fixture_component_cluster_report": metadata.get(
            "component_cluster_report",
            {},
        ),
        "full_assembly_fixture_assembly_plan": metadata.get("assembly_plan", {}),
        "full_assembly_fixture_geometry_report": metadata.get(
            "assembly_geometry_report",
            {},
        ),
        "full_assembly_fixture_assembly_compiler": metadata.get(
            "assembly_compiler",
            {},
        ),
        "full_assembly_fixture": metadata,
    }


def _fixed_layout_pose_constraint_calls(
    part_ids: list[str],
) -> list[CadQueryConstraintCall]:
    """Build solver constraints that lock each part to its layout pose."""

    return [
        CadQueryConstraintCall(
            constraint_id=f"{part_id}_fixed_layout_pose",
            kind="Fixed",
            fixed_query=part_id,
            rationale=(
                "Lock the part to the MechanicalLayout-derived pose during "
                "CadQuery constraint solve."
            ),
        )
        for part_id in part_ids
    ]


def _assembly_object_locations(
    assembly: Any,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Return solved CadQuery object locations when available."""

    objects = getattr(assembly, "objects", None)
    if not isinstance(objects, dict):
        return fallback
    locations: dict[str, Any] = {}
    for part_id, fallback_loc in fallback.items():
        obj = objects.get(part_id)
        loc = getattr(obj, "loc", None) if obj is not None else None
        locations[part_id] = loc if loc is not None else fallback_loc
    return locations


def _part_placements(
    cq: Any,
    layout: MechanicalLayout,
    part_catalog: CadQueryPartCatalog,
) -> dict[str, Any]:
    """Return deterministic part placements from mechanical layout frames."""

    frame_positions = _global_frame_positions(layout.frames)
    frame_by_id = {frame.id: frame for frame in layout.frames}
    common_z = _common_robot_center_z(layout)
    placements: dict[str, Any] = {}
    for part_id in part_catalog.part_ids():
        part = part_catalog.require(part_id)
        if part.kind == "base":
            point = (0.0, 0.0, DEFAULT_BASE_THICKNESS_MM / 2.0)
            direction = (1.0, 0.0, 0.0)
        elif part.kind == "joint":
            joint = _joint_by_id(layout, part_id)
            x, y, z = frame_positions[joint.axis_frame]
            point = (x, y, common_z + z)
            direction = _joint_direction(layout, frame_by_id, joint.id)
        elif part.kind == "link":
            link = _link_by_id(layout, part_id)
            x, y, z = frame_positions[link.body_frame]
            point = (x, y, common_z + z)
            direction = _frame_local_direction(frame_by_id, link.body_frame)
        elif part.kind == "end_effector":
            frame_id = "end_effector_mount"
            x, y, z = frame_positions.get(frame_id, _chain_end_position(layout, frame_positions))
            point = (x, y, common_z + z)
            direction = _frame_local_direction(frame_by_id, frame_id)
        else:
            point = (0.0, 0.0, common_z)
            direction = (1.0, 0.0, 0.0)
        placements[part_id] = _location_aligned_to_x(cq, point, direction)
    return placements


def _location_aligned_to_x(
    cq: Any,
    point: tuple[float, float, float],
    direction: tuple[float, float, float],
) -> Any:
    """Return a CadQuery Location whose local X axis follows `direction`."""

    x_dir = _unit_vector(direction)
    normal = _normal_for_x_dir(x_dir)
    if hasattr(cq, "Plane"):
        return cq.Location(cq.Plane(origin=point, xDir=x_dir, normal=normal))
    return cq.Location(cq.Vector(*point))


def _joint_direction(
    layout: MechanicalLayout,
    frame_by_id: dict[str, FrameSpec],
    joint_id: str,
) -> tuple[float, float, float]:
    for link in layout.links:
        if link.from_interface and link.from_interface.startswith(f"{joint_id}_to_"):
            return _frame_local_direction(frame_by_id, link.body_frame)
    return (1.0, 0.0, 0.0)


def _frame_local_direction(
    frame_by_id: dict[str, FrameSpec],
    frame_id: str,
) -> tuple[float, float, float]:
    frame = frame_by_id.get(frame_id)
    if frame is None:
        return (1.0, 0.0, 0.0)
    return frame.transform.translation


def _unit_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = (vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2) ** 0.5
    if length <= 0:
        return (1.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _normal_for_x_dir(
    x_dir: tuple[float, float, float],
) -> tuple[float, float, float]:
    candidate = (0.0, 0.0, 1.0)
    if abs(_dot(x_dir, candidate)) > 0.95:
        candidate = (0.0, 1.0, 0.0)
    projection = _dot(candidate, x_dir)
    normal = (
        candidate[0] - projection * x_dir[0],
        candidate[1] - projection * x_dir[1],
        candidate[2] - projection * x_dir[2],
    )
    return _unit_vector(normal)


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _global_frame_positions(frames: list[FrameSpec]) -> dict[str, tuple[float, float, float]]:
    by_id = {frame.id: frame for frame in frames}
    cache: dict[str, tuple[float, float, float]] = {}

    def resolve(frame_id: str) -> tuple[float, float, float]:
        if frame_id in cache:
            return cache[frame_id]
        frame = by_id[frame_id]
        tx, ty, tz = frame.transform.translation
        if frame.parent and frame.parent in by_id:
            px, py, pz = resolve(frame.parent)
            value = (px + tx, py + ty, pz + tz)
        else:
            value = (tx, ty, tz)
        cache[frame_id] = value
        return value

    return {frame.id: resolve(frame.id) for frame in frames}


def _common_robot_center_z(layout: MechanicalLayout) -> float:
    max_joint_radius = 0.0
    max_link_half_height = 0.0
    for joint in layout.joints:
        if joint.actuator_envelope:
            max_joint_radius = max(
                max_joint_radius,
                joint.actuator_envelope.dimensions.get("radius", 0.0),
            )
    for link in layout.links:
        max_link_half_height = max(
            max_link_half_height,
            link.envelope.dimensions.get("height", 0.0) / 2.0,
        )
    return DEFAULT_BASE_THICKNESS_MM + max(max_joint_radius, max_link_half_height)


def _joint_by_id(layout: MechanicalLayout, joint_id: str):
    for joint in layout.joints:
        if joint.id == joint_id:
            return joint
    raise KeyError(f"Unknown joint `{joint_id}`")


def _link_by_id(layout: MechanicalLayout, link_id: str):
    for link in layout.links:
        if link.id == link_id:
            return link
    raise KeyError(f"Unknown link `{link_id}`")


def _chain_end_position(
    layout: MechanicalLayout,
    frame_positions: dict[str, tuple[float, float, float]],
) -> tuple[float, float, float]:
    if not layout.links:
        return (0.0, 0.0, 0.0)
    last = layout.links[-1]
    return frame_positions.get(last.body_frame, (0.0, 0.0, 0.0))


def _import_cadquery() -> Any:
    try:
        import cadquery as cq  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "cadquery is required to build CadQuery assemblies. Install it or "
            "call build_cadquery_assembly(..., cq_module=...) in tests."
        ) from exc
    return cq
