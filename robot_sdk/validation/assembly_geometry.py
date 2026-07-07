"""Post-placement geometry checks for semantic robot assemblies.

This module turns source-first assembly mates into measurable residuals. It is
kept independent from CadQuery so it can run in tests and in agent-side failure
reports even when the CAD kernel is unavailable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from robot_sdk.assembly.plan import AssemblyPlan, build_assembly_plan
from robot_sdk.types import MechanicalLayout


Vector3 = tuple[float, float, float]
DEFAULT_MATE_GAP_ERROR_MM = 1.0


@dataclass(frozen=True)
class AssemblyGeometryReport:
    """Geometry residuals and connectivity after assembly placement."""

    assembly_plan: AssemblyPlan
    residual_reports: list[dict[str, Any]]
    component_cluster_report: dict[str, Any]
    placement_available: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def max_origin_delta_mm(self) -> float:
        return _max_metric(self.residual_reports, "origin_delta_mm")

    @property
    def max_normal_angle_deg(self) -> float:
        return _max_metric(self.residual_reports, "normal_angle_deg")

    @property
    def max_tangent_angle_deg(self) -> float:
        return _max_metric(self.residual_reports, "tangent_angle_deg")

    @property
    def cluster_count(self) -> int:
        return int(self.component_cluster_report.get("cluster_count") or 0)

    @property
    def base_to_terminal_connected(self) -> bool:
        return bool(self.component_cluster_report.get("base_to_terminal_connected"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "assembly_plan": self.assembly_plan.to_dict(),
            "residual_reports": list(self.residual_reports),
            "component_cluster_report": dict(self.component_cluster_report),
            "placement_available": self.placement_available,
            "metadata": dict(self.metadata),
            "max_origin_delta_mm": self.max_origin_delta_mm,
            "max_normal_angle_deg": self.max_normal_angle_deg,
            "max_tangent_angle_deg": self.max_tangent_angle_deg,
            "cluster_count": self.cluster_count,
            "base_to_terminal_connected": self.base_to_terminal_connected,
        }


def build_assembly_geometry_report(
    layout: MechanicalLayout,
    *,
    initial_locations: dict[str, Any] | None = None,
    solved_locations: dict[str, Any] | None = None,
    mate_gap_error_mm: float = DEFAULT_MATE_GAP_ERROR_MM,
) -> AssemblyGeometryReport:
    """Build residual and cluster reports for the current assembly placement."""

    plan = build_assembly_plan(layout)
    placement_available = initial_locations is not None and solved_locations is not None
    deltas = _part_translation_deltas(initial_locations or {}, solved_locations or {})
    residual_reports = [
        _mate_residual_report(mate, deltas=deltas, placement_available=placement_available)
        for mate in plan.mates
    ]
    cluster_report = _component_cluster_report(
        plan,
        residual_reports=residual_reports,
        mate_gap_error_mm=mate_gap_error_mm,
    )
    return AssemblyGeometryReport(
        assembly_plan=plan,
        residual_reports=residual_reports,
        component_cluster_report=cluster_report,
        placement_available=placement_available,
        metadata={
            "source": "assembly_geometry",
            "mate_gap_error_mm": mate_gap_error_mm,
            "residual_count": len(residual_reports),
        },
    )


def _mate_residual_report(
    mate: Any,
    *,
    deltas: dict[str, Vector3],
    placement_available: bool,
) -> dict[str, Any]:
    fixed_delta = deltas.get(mate.fixed_part_id, (0.0, 0.0, 0.0))
    moving_delta = deltas.get(mate.moving_part_id, (0.0, 0.0, 0.0))
    fixed_origin = _add(mate.expected_fixed_origin, fixed_delta)
    moving_origin = _add(mate.expected_moving_origin, moving_delta)
    return {
        "constraint_id": mate.id,
        "kind": mate.kind,
        "normalized_kind": mate.normalized_kind,
        "fixed_part_id": mate.fixed_part_id,
        "moving_part_id": mate.moving_part_id,
        "fixed_feature_id": mate.fixed_feature_id,
        "moving_feature_id": mate.moving_feature_id,
        "fixed_datum_id": mate.fixed_datum_id,
        "moving_datum_id": mate.moving_datum_id,
        "origin_delta_mm": _distance(fixed_origin, moving_origin),
        "normal_angle_deg": _angle_deg(mate.expected_fixed_normal, mate.expected_moving_normal),
        "tangent_angle_deg": _angle_deg(
            mate.expected_fixed_tangent,
            mate.expected_moving_tangent,
        ),
        "fixed_origin": fixed_origin,
        "moving_origin": moving_origin,
        "fixed_part_translation_delta_mm": fixed_delta,
        "moving_part_translation_delta_mm": moving_delta,
        "placement_available": placement_available,
    }


def _component_cluster_report(
    plan: AssemblyPlan,
    *,
    residual_reports: list[dict[str, Any]],
    mate_gap_error_mm: float,
) -> dict[str, Any]:
    part_ids = [occurrence.part_id for occurrence in plan.occurrences]
    parent = {part_id: part_id for part_id in part_ids}

    def find(part_id: str) -> str:
        while parent[part_id] != part_id:
            parent[part_id] = parent[parent[part_id]]
            part_id = parent[part_id]
        return part_id

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    failing_edges: list[dict[str, Any]] = []
    for residual in residual_reports:
        fixed = str(residual["fixed_part_id"])
        moving = str(residual["moving_part_id"])
        if fixed == moving:
            continue
        gap = float(residual["origin_delta_mm"])
        if gap <= mate_gap_error_mm:
            union(fixed, moving)
        else:
            failing_edges.append(
                {
                    "constraint_id": residual["constraint_id"],
                    "fixed_part_id": fixed,
                    "moving_part_id": moving,
                    "origin_delta_mm": gap,
                }
            )

    clusters_by_root: dict[str, list[str]] = {}
    for part_id in part_ids:
        clusters_by_root.setdefault(find(part_id), []).append(part_id)
    clusters = [sorted(values) for values in clusters_by_root.values()]
    root = plan.root_part_id
    terminal = plan.terminal_part_id
    connected = (
        root in parent
        and terminal in parent
        and find(root) == find(terminal)
    )
    return {
        "root_part_id": root,
        "terminal_part_id": terminal,
        "part_ids": part_ids,
        "cluster_count": len(clusters),
        "clusters": clusters,
        "base_to_terminal_connected": connected,
        "failing_edges": failing_edges,
        "mate_gap_error_mm": mate_gap_error_mm,
    }


def _part_translation_deltas(
    initial_locations: dict[str, Any],
    solved_locations: dict[str, Any],
) -> dict[str, Vector3]:
    deltas: dict[str, Vector3] = {}
    for part_id, initial in initial_locations.items():
        if part_id not in solved_locations:
            continue
        initial_origin = _location_origin(initial)
        solved_origin = _location_origin(solved_locations[part_id])
        if initial_origin is None or solved_origin is None:
            continue
        deltas[part_id] = _sub(solved_origin, initial_origin)
    return deltas


def _location_origin(location: Any) -> Vector3 | None:
    if isinstance(location, dict):
        raw = location.get("loc") or location.get("origin")
        return _vector(raw)
    raw = getattr(location, "loc", None)
    if raw is not None:
        parsed = _location_origin(raw)
        if parsed is not None:
            return parsed
    wrapped = getattr(location, "wrapped", None)
    if wrapped is not None:
        try:
            translation = wrapped.Transformation().TranslationPart()
            return (
                float(translation.x),
                float(translation.y),
                float(translation.z),
            )
        except Exception:
            return None
    return _vector(location)


def _vector(value: Any) -> Vector3 | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _max_metric(reports: list[dict[str, Any]], key: str) -> float:
    values = [float(report[key]) for report in reports if isinstance(report.get(key), (int, float))]
    return max(values) if values else 0.0


def _add(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _sub(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _distance(left: Vector3, right: Vector3) -> float:
    return math.sqrt(
        (left[0] - right[0]) ** 2
        + (left[1] - right[1]) ** 2
        + (left[2] - right[2]) ** 2
    )


def _angle_deg(left: Vector3, right: Vector3) -> float:
    left_len = math.sqrt(left[0] ** 2 + left[1] ** 2 + left[2] ** 2)
    right_len = math.sqrt(right[0] ** 2 + right[1] ** 2 + right[2] ** 2)
    if left_len <= 1e-12 or right_len <= 1e-12:
        return 0.0
    dot = (
        left[0] * right[0]
        + left[1] * right[1]
        + left[2] * right[2]
    ) / (left_len * right_len)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


__all__ = [
    "AssemblyGeometryReport",
    "DEFAULT_MATE_GAP_ERROR_MM",
    "build_assembly_geometry_report",
]
