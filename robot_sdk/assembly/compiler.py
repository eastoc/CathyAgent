"""Compile source-first assembly plans into deterministic placement deltas.

This is the first stage of the semantic assembly compiler. It compiles
fixed->moving mate relationships through part-local datum offsets and
normal/tangent frames, then emits deterministic placement objects when the
input location representation can be updated safely.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from robot_sdk.assembly.plan import AssemblyPlan, build_assembly_plan
from robot_sdk.types import MechanicalLayout


Vector3 = tuple[float, float, float]
Basis3 = tuple[Vector3, Vector3, Vector3]
DEFAULT_CONFLICT_TOLERANCE_MM = 1.0e-6
DEFAULT_ORIENTATION_CONFLICT_TOLERANCE_DEG = 1.0e-4


@dataclass(frozen=True)
class AssemblyCompileConflict:
    """A conflicting placement delta inferred from multiple mate paths."""

    part_id: str
    mate_id: str
    existing_delta: Vector3
    proposed_delta: Vector3
    delta_error_mm: float
    orientation_error_deg: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "mate_id": self.mate_id,
            "existing_delta": self.existing_delta,
            "proposed_delta": self.proposed_delta,
            "delta_error_mm": self.delta_error_mm,
            "orientation_error_deg": self.orientation_error_deg,
        }


@dataclass(frozen=True)
class _PartPose:
    origin: Vector3
    basis: Basis3


@dataclass(frozen=True)
class _LocalDatum:
    origin: Vector3
    normal: Vector3
    tangent: Vector3

    @property
    def basis(self) -> Basis3:
        return _datum_basis(self.normal, self.tangent)


@dataclass(frozen=True)
class AssemblyCompilerResult:
    """Result of compiling an AssemblyPlan into placement deltas."""

    assembly_plan: AssemblyPlan
    root_part_id: str
    compiled_part_deltas: dict[str, Vector3]
    compiled_part_orientations: dict[str, dict[str, Vector3]]
    compiled_locations: dict[str, Any]
    unresolved_part_ids: list[str] = field(default_factory=list)
    conflicts: list[AssemblyCompileConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.conflicts and not self.unresolved_part_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "assembly_plan": self.assembly_plan.to_dict(),
            "root_part_id": self.root_part_id,
            "compiled_part_deltas": dict(self.compiled_part_deltas),
            "compiled_part_orientations": {
                part_id: dict(orientation)
                for part_id, orientation in self.compiled_part_orientations.items()
            },
            "compiled_locations": {
                part_id: _location_to_payload(location)
                for part_id, location in self.compiled_locations.items()
            },
            "unresolved_part_ids": list(self.unresolved_part_ids),
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
            "ok": self.ok,
        }


def compile_assembly_plan(
    layout: MechanicalLayout,
    *,
    initial_locations: dict[str, Any] | None = None,
    root_part_id: str = "base",
    terminal_part_id: str = "end_effector",
    conflict_tolerance_mm: float = DEFAULT_CONFLICT_TOLERANCE_MM,
    orientation_conflict_tolerance_deg: float = DEFAULT_ORIENTATION_CONFLICT_TOLERANCE_DEG,
) -> AssemblyCompilerResult:
    """Compile a MechanicalLayout into deterministic placement deltas."""

    plan = build_assembly_plan(
        layout,
        root_part_id=root_part_id,
        terminal_part_id=terminal_part_id,
    )
    initial_poses = _initial_part_poses(initial_locations or {}, plan)
    local_datums = _part_local_datums(plan, initial_poses)
    compiled_poses, conflicts = _compile_part_poses(
        plan,
        initial_poses=initial_poses,
        local_datums=local_datums,
        root_part_id=root_part_id,
        conflict_tolerance_mm=conflict_tolerance_mm,
        orientation_conflict_tolerance_deg=orientation_conflict_tolerance_deg,
    )
    part_ids = [occurrence.part_id for occurrence in plan.occurrences]
    unresolved = [part_id for part_id in part_ids if part_id not in compiled_poses]
    warnings = [
        f"AssemblyCompiler could not infer a placement delta for `{part_id}`."
        for part_id in unresolved
    ]
    deltas = _compiled_part_deltas(initial_poses, compiled_poses)
    orientations = _compiled_part_orientations(compiled_poses)
    compiled_locations = _compiled_locations(
        initial_locations or {},
        compiled_poses,
        warnings,
    )
    return AssemblyCompilerResult(
        assembly_plan=plan,
        root_part_id=root_part_id,
        compiled_part_deltas=deltas,
        compiled_part_orientations=orientations,
        compiled_locations=compiled_locations,
        unresolved_part_ids=unresolved,
        conflicts=conflicts,
        warnings=warnings,
        metadata={
            "source": "assembly_compiler",
            "compiler_mode": "translation_origin_propagation",
            "datum_mode": "part_local_origin_normal_tangent",
            "conflict_tolerance_mm": conflict_tolerance_mm,
            "orientation_conflict_tolerance_deg": orientation_conflict_tolerance_deg,
            "occurrence_count": len(part_ids),
            "mate_count": len(plan.mates),
            "compiled_part_count": len(deltas),
            "unresolved_part_count": len(unresolved),
            "conflict_count": len(conflicts),
        },
    )


def _compile_part_poses(
    plan: AssemblyPlan,
    *,
    initial_poses: dict[str, _PartPose],
    local_datums: dict[str, _LocalDatum],
    root_part_id: str,
    conflict_tolerance_mm: float,
    orientation_conflict_tolerance_deg: float,
) -> tuple[dict[str, _PartPose], list[AssemblyCompileConflict]]:
    poses: dict[str, _PartPose] = {
        root_part_id: initial_poses.get(root_part_id, _identity_pose())
    }
    conflicts: list[AssemblyCompileConflict] = []
    changed = True
    while changed:
        changed = False
        for mate in plan.mates:
            fixed_pose = poses.get(mate.fixed_part_id)
            moving_pose = poses.get(mate.moving_part_id)
            fixed_datum = local_datums[mate.fixed_datum_id]
            moving_datum = local_datums[mate.moving_datum_id]
            if fixed_pose is not None:
                proposed = _pose_for_moving_part(
                    fixed_pose=fixed_pose,
                    fixed_datum=fixed_datum,
                    moving_datum=moving_datum,
                )
                changed |= _assign_pose(
                    poses,
                    conflicts,
                    part_id=mate.moving_part_id,
                    mate_id=mate.id,
                    proposed=proposed,
                    initial_pose=initial_poses.get(mate.moving_part_id, _identity_pose()),
                    conflict_tolerance_mm=conflict_tolerance_mm,
                    orientation_conflict_tolerance_deg=orientation_conflict_tolerance_deg,
                )
            elif moving_pose is not None:
                proposed = _pose_for_fixed_part(
                    moving_pose=moving_pose,
                    fixed_datum=fixed_datum,
                    moving_datum=moving_datum,
                )
                changed |= _assign_pose(
                    poses,
                    conflicts,
                    part_id=mate.fixed_part_id,
                    mate_id=mate.id,
                    proposed=proposed,
                    initial_pose=initial_poses.get(mate.fixed_part_id, _identity_pose()),
                    conflict_tolerance_mm=conflict_tolerance_mm,
                    orientation_conflict_tolerance_deg=orientation_conflict_tolerance_deg,
                )
    return poses, conflicts


def _assign_pose(
    poses: dict[str, _PartPose],
    conflicts: list[AssemblyCompileConflict],
    *,
    part_id: str,
    mate_id: str,
    proposed: _PartPose,
    initial_pose: _PartPose,
    conflict_tolerance_mm: float,
    orientation_conflict_tolerance_deg: float,
) -> bool:
    existing = poses.get(part_id)
    if existing is None:
        poses[part_id] = proposed
        return True
    error = _distance(existing.origin, proposed.origin)
    orientation_error = _basis_angle_error_deg(existing.basis, proposed.basis)
    if (
        error > conflict_tolerance_mm
        or orientation_error > orientation_conflict_tolerance_deg
    ) and not _conflict_recorded(conflicts, part_id, mate_id):
        conflicts.append(
            AssemblyCompileConflict(
                part_id=part_id,
                mate_id=mate_id,
                existing_delta=_sub(existing.origin, initial_pose.origin),
                proposed_delta=_sub(proposed.origin, initial_pose.origin),
                delta_error_mm=error,
                orientation_error_deg=orientation_error,
            )
        )
    return False


def _compiled_locations(
    initial_locations: dict[str, Any],
    compiled_poses: dict[str, _PartPose],
    warnings: list[str],
) -> dict[str, Any]:
    compiled: dict[str, Any] = {}
    for part_id, location in initial_locations.items():
        pose = compiled_poses.get(part_id, _pose_from_location(location))
        compiled_location = _rewrite_location(location, pose)
        if compiled_location is None:
            warnings.append(
                f"AssemblyCompiler could not rewrite placement for `{part_id}`; "
                "kept the original location object."
            )
            compiled[part_id] = location
        else:
            compiled[part_id] = compiled_location
    return compiled


def _rewrite_location(location: Any, pose: _PartPose) -> Any | None:
    if isinstance(location, dict):
        result = dict(location)
        if "loc" in result:
            result["loc"] = pose.origin
        elif "origin" in result:
            result["origin"] = pose.origin
        else:
            result["loc"] = pose.origin
        result["xDir"] = pose.basis[0]
        result["normal"] = pose.basis[2]
        return result
    if isinstance(location, tuple) and len(location) == 3:
        return pose.origin
    return None


def _pose_for_moving_part(
    *,
    fixed_pose: _PartPose,
    fixed_datum: _LocalDatum,
    moving_datum: _LocalDatum,
) -> _PartPose:
    fixed_datum_basis = _basis_mul(fixed_pose.basis, fixed_datum.basis)
    moving_basis = _basis_mul(fixed_datum_basis, _basis_transpose(moving_datum.basis))
    fixed_origin = _transform_point(fixed_pose, fixed_datum.origin)
    moving_origin = _sub(fixed_origin, _basis_vec(moving_basis, moving_datum.origin))
    return _PartPose(origin=moving_origin, basis=moving_basis)


def _pose_for_fixed_part(
    *,
    moving_pose: _PartPose,
    fixed_datum: _LocalDatum,
    moving_datum: _LocalDatum,
) -> _PartPose:
    moving_datum_basis = _basis_mul(moving_pose.basis, moving_datum.basis)
    fixed_basis = _basis_mul(moving_datum_basis, _basis_transpose(fixed_datum.basis))
    moving_origin = _transform_point(moving_pose, moving_datum.origin)
    fixed_origin = _sub(moving_origin, _basis_vec(fixed_basis, fixed_datum.origin))
    return _PartPose(origin=fixed_origin, basis=fixed_basis)


def _initial_part_poses(
    initial_locations: dict[str, Any],
    plan: AssemblyPlan,
) -> dict[str, _PartPose]:
    poses: dict[str, _PartPose] = {}
    for occurrence in plan.occurrences:
        location = initial_locations.get(occurrence.part_id)
        poses[occurrence.part_id] = (
            _pose_from_location(location)
            if location is not None
            else _identity_pose()
        )
    return poses


def _part_local_datums(
    plan: AssemblyPlan,
    initial_poses: dict[str, _PartPose],
) -> dict[str, _LocalDatum]:
    local: dict[str, _LocalDatum] = {}
    for datum in plan.datums:
        pose = initial_poses.get(datum.part_id, _identity_pose())
        local[datum.id] = _LocalDatum(
            origin=_basis_transpose_vec(pose.basis, _sub(datum.origin, pose.origin)),
            normal=_basis_transpose_vec(pose.basis, datum.normal),
            tangent=_basis_transpose_vec(pose.basis, datum.tangent),
        )
    return local


def _compiled_part_deltas(
    initial_poses: dict[str, _PartPose],
    compiled_poses: dict[str, _PartPose],
) -> dict[str, Vector3]:
    return {
        part_id: _sub(pose.origin, initial_poses.get(part_id, _identity_pose()).origin)
        for part_id, pose in compiled_poses.items()
    }


def _compiled_part_orientations(
    compiled_poses: dict[str, _PartPose],
) -> dict[str, dict[str, Vector3]]:
    return {
        part_id: {
            "xDir": pose.basis[0],
            "yDir": pose.basis[1],
            "normal": pose.basis[2],
        }
        for part_id, pose in compiled_poses.items()
    }


def _pose_from_location(location: Any) -> _PartPose:
    origin = _location_origin(location) or (0.0, 0.0, 0.0)
    if isinstance(location, dict):
        x_dir = _vector(location.get("xDir")) or (1.0, 0.0, 0.0)
        normal = _vector(location.get("normal")) or _normal_for_x_dir(x_dir)
        return _PartPose(origin=origin, basis=_basis_from_x_normal(x_dir, normal))
    return _PartPose(origin=origin, basis=_identity_basis())


def _location_origin(location: Any) -> Vector3 | None:
    if isinstance(location, dict):
        raw = location.get("loc") or location.get("origin")
        return _vector(raw)
    return _vector(location)


def _location_to_payload(location: Any) -> Any:
    if isinstance(location, dict):
        return dict(location)
    return location


def _identity_pose() -> _PartPose:
    return _PartPose(origin=(0.0, 0.0, 0.0), basis=_identity_basis())


def _identity_basis() -> Basis3:
    return (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )


def _basis_from_x_normal(x_dir: Vector3, normal: Vector3) -> Basis3:
    z_axis = _unit(normal)
    x_axis = _orthogonal_tangent(z_axis, x_dir)
    y_axis = _unit(_cross(z_axis, x_axis))
    return (x_axis, y_axis, z_axis)


def _datum_basis(normal: Vector3, tangent: Vector3) -> Basis3:
    z_axis = _unit(normal)
    x_axis = _orthogonal_tangent(z_axis, tangent)
    y_axis = _unit(_cross(z_axis, x_axis))
    return (x_axis, y_axis, z_axis)


def _orthogonal_tangent(normal: Vector3, preferred: Vector3) -> Vector3:
    """Return a stable unit tangent perpendicular to the given normal."""

    z_axis = _unit(normal)
    preferred_axis = _unit(preferred)
    projected = _sub(preferred_axis, _scale(z_axis, _dot(preferred_axis, z_axis)))
    if _distance(projected, (0.0, 0.0, 0.0)) > 1.0e-9:
        return _unit(projected)
    fallback = (1.0, 0.0, 0.0) if abs(z_axis[0]) < 0.9 else (0.0, 1.0, 0.0)
    projected_fallback = _sub(fallback, _scale(z_axis, _dot(fallback, z_axis)))
    return _unit(projected_fallback)


def _basis_vec(basis: Basis3, vector: Vector3) -> Vector3:
    return _add(
        _add(_scale(basis[0], vector[0]), _scale(basis[1], vector[1])),
        _scale(basis[2], vector[2]),
    )


def _basis_transpose_vec(basis: Basis3, vector: Vector3) -> Vector3:
    return (_dot(vector, basis[0]), _dot(vector, basis[1]), _dot(vector, basis[2]))


def _basis_transpose(basis: Basis3) -> Basis3:
    return (
        (basis[0][0], basis[1][0], basis[2][0]),
        (basis[0][1], basis[1][1], basis[2][1]),
        (basis[0][2], basis[1][2], basis[2][2]),
    )


def _basis_mul(left: Basis3, right: Basis3) -> Basis3:
    return (
        _basis_vec(left, right[0]),
        _basis_vec(left, right[1]),
        _basis_vec(left, right[2]),
    )


def _transform_point(pose: _PartPose, point: Vector3) -> Vector3:
    return _add(pose.origin, _basis_vec(pose.basis, point))


def _basis_angle_error_deg(left: Basis3, right: Basis3) -> float:
    return max(_angle_deg(left[index], right[index]) for index in range(3))


def _normal_for_x_dir(x_dir: Vector3) -> Vector3:
    x_axis = _unit(x_dir)
    candidate = (0.0, 0.0, 1.0)
    if abs(_dot(x_axis, candidate)) > 0.95:
        candidate = (0.0, 1.0, 0.0)
    return _unit(_sub(candidate, _scale(x_axis, _dot(candidate, x_axis))))


def _vector(value: Any) -> Vector3 | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _unit(vector: Vector3) -> Vector3:
    length = _distance(vector, (0.0, 0.0, 0.0))
    if length <= 1.0e-12:
        return (1.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _dot(left: Vector3, right: Vector3) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _scale(vector: Vector3, factor: float) -> Vector3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _angle_deg(left: Vector3, right: Vector3) -> float:
    left_unit = _unit(left)
    right_unit = _unit(right)
    dot = max(-1.0, min(1.0, _dot(left_unit, right_unit)))
    return math.degrees(math.acos(dot))


def _conflict_recorded(
    conflicts: list[AssemblyCompileConflict],
    part_id: str,
    mate_id: str,
) -> bool:
    return any(
        conflict.part_id == part_id and conflict.mate_id == mate_id
        for conflict in conflicts
    )


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


__all__ = [
    "AssemblyCompileConflict",
    "AssemblyCompilerResult",
    "compile_assembly_plan",
]
