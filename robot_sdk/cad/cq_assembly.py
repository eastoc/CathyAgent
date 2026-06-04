"""CadQuery assembly construction and solving."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from robot_sdk.assembly.cq_adapter import (
    CadQueryConstraintCall,
    build_layout_cadquery_constraint_calls,
)
from robot_sdk.cad.cq_parts import (
    DEFAULT_BASE_THICKNESS_MM,
    CadQueryPartCatalog,
    build_cadquery_parts,
)
from robot_sdk.types import FrameSpec, MechanicalLayout


@dataclass(frozen=True)
class CadQueryAssemblyResult:
    """Result of building and optionally solving a CadQuery Assembly."""

    assembly: Any
    part_catalog: CadQueryPartCatalog
    constraint_calls: list[CadQueryConstraintCall]
    solved: bool
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
    solve: bool = False,
    solve_verbosity: int = 0,
    raise_on_solve_error: bool = True,
) -> CadQueryAssemblyResult:
    """Build a CadQuery Assembly from `MechanicalLayout`.

    The function generates parts and adds them to a CadQuery Assembly using
    deterministic positions derived from `MechanicalLayout` frames. Neutral
    assembly constraints are still translated and returned for traceability,
    but they are only applied to CadQuery when `solve=True`.
    """

    cq = cq_module or _import_cadquery()
    part_catalog = build_cadquery_parts(layout, cq_module=cq)
    assembly = cq.Assembly()
    placements = _part_placements(cq, layout, part_catalog)

    for part_id in part_catalog.part_ids():
        part = part_catalog.require(part_id)
        assembly.add(part.solid, name=part_id, loc=placements[part_id])

    constraint_calls = build_layout_cadquery_constraint_calls(layout, part_catalog)

    solved = False
    solve_error: str | None = None
    if solve:
        try:
            for call in constraint_calls:
                assembly.constrain(*call.args())
            assembly.solve(solve_verbosity)
            solved = True
        except Exception as exc:
            solve_error = str(exc)
            if raise_on_solve_error:
                raise

    return CadQueryAssemblyResult(
        assembly=assembly,
        part_catalog=part_catalog,
        constraint_calls=constraint_calls,
        part_locations=placements,
        solved=solved,
        solve_error=solve_error,
        metadata={
            "units": layout.units,
            "part_ids": part_catalog.part_ids(),
            "placement_mode": "deterministic_layout_frames",
            "solve_requested": solve,
        },
    )


def _part_placements(
    cq: Any,
    layout: MechanicalLayout,
    part_catalog: CadQueryPartCatalog,
) -> dict[str, Any]:
    """Return deterministic part placements from mechanical layout frames."""

    frame_positions = _global_frame_positions(layout.frames)
    common_z = _common_robot_center_z(layout)
    placements: dict[str, Any] = {}
    for part_id in part_catalog.part_ids():
        part = part_catalog.require(part_id)
        if part.kind == "base":
            point = (0.0, 0.0, DEFAULT_BASE_THICKNESS_MM / 2.0)
        elif part.kind == "joint":
            joint = _joint_by_id(layout, part_id)
            x, y, _z = frame_positions[joint.axis_frame]
            point = (x, y, common_z)
        elif part.kind == "link":
            link = _link_by_id(layout, part_id)
            x, y, _z = frame_positions[link.body_frame]
            point = (x, y, common_z)
        elif part.kind == "end_effector":
            frame_id = "end_effector_mount"
            x, y, _z = frame_positions.get(frame_id, _chain_end_position(layout, frame_positions))
            point = (x, y, common_z)
        else:
            point = (0.0, 0.0, common_z)
        placements[part_id] = cq.Location(cq.Vector(*point))
    return placements


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
