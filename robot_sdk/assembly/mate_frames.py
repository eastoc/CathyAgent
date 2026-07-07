"""Mate frame extraction from MechanicalLayout part features.

`PartFeature` tells the CAD layer that a selectable feature exists. `MateFrame`
adds the assembly meaning needed by constraint-driven assembly: each feature
gets a stable local coordinate system with an origin, normal, and tangent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from robot_sdk.types import FrameSpec, MechanicalLayout, PartFeature


Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class MateFrame:
    """Assembly-ready coordinate frame attached to a generated part feature."""

    id: str
    part_id: str
    feature_id: str
    origin: Vector3
    normal: Vector3
    tangent: Vector3
    semantic: str
    source_frame: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("MateFrame.id is required")
        if not self.part_id:
            raise ValueError("MateFrame.part_id is required")
        if not self.feature_id:
            raise ValueError("MateFrame.feature_id is required")
        normal = _unit(self.normal, label=f"{self.id}.normal")
        tangent = _unit(self.tangent, label=f"{self.id}.tangent")
        if abs(_dot(normal, tangent)) > 1e-6:
            raise ValueError(f"MateFrame `{self.id}` normal and tangent must be orthogonal")
        object.__setattr__(self, "normal", normal)
        object.__setattr__(self, "tangent", tangent)

    @property
    def binormal(self) -> Vector3:
        """Return the right-handed third axis for this mate frame."""

        return _unit(_cross(self.normal, self.tangent), label=f"{self.id}.binormal")


@dataclass(frozen=True)
class MateFrameCatalog:
    """Indexed mate frames for constraint planning and validation."""

    by_id: dict[str, MateFrame]
    by_feature_id: dict[str, MateFrame]

    @classmethod
    def from_frames(cls, frames: list[MateFrame]) -> "MateFrameCatalog":
        by_id: dict[str, MateFrame] = {}
        by_feature_id: dict[str, MateFrame] = {}
        for frame in frames:
            if frame.id in by_id:
                raise ValueError(f"Duplicate mate frame id `{frame.id}`")
            if frame.feature_id in by_feature_id:
                raise ValueError(f"Duplicate mate frame feature `{frame.feature_id}`")
            by_id[frame.id] = frame
            by_feature_id[frame.feature_id] = frame
        return cls(by_id=by_id, by_feature_id=by_feature_id)

    def require(self, frame_id: str) -> MateFrame:
        try:
            return self.by_id[frame_id]
        except KeyError as exc:
            raise KeyError(f"Unknown mate frame `{frame_id}`") from exc

    def require_feature(self, feature_id: str) -> MateFrame:
        try:
            return self.by_feature_id[feature_id]
        except KeyError as exc:
            raise KeyError(f"Unknown mate feature `{feature_id}`") from exc

    def for_part(self, part_id: str) -> list[MateFrame]:
        return [frame for frame in self.by_id.values() if frame.part_id == part_id]

    def feature_ids(self) -> set[str]:
        return set(self.by_feature_id)


def build_mate_frames(layout: MechanicalLayout) -> list[MateFrame]:
    """Build deterministic mate frames for every layout part feature."""

    frame_poses = _global_frame_poses(layout.frames)
    return [
        _mate_frame_from_feature(feature, frame_poses=frame_poses)
        for feature in layout.part_features
    ]


def build_mate_frame_catalog(layout: MechanicalLayout) -> MateFrameCatalog:
    """Build an indexed mate-frame catalog from a MechanicalLayout."""

    return MateFrameCatalog.from_frames(build_mate_frames(layout))


def _mate_frame_from_feature(
    feature: PartFeature,
    *,
    frame_poses: dict[str, tuple[Vector3, list[list[float]]]],
) -> MateFrame:
    origin: Vector3 = (0.0, 0.0, 0.0)
    tangent: Vector3 = (1.0, 0.0, 0.0)
    normal: Vector3 = (0.0, 0.0, 1.0)
    if feature.frame and feature.frame in frame_poses:
        origin, rotation = frame_poses[feature.frame]
        tangent = _mat_vec(rotation, (1.0, 0.0, 0.0))
        normal = _mat_vec(rotation, (0.0, 0.0, 1.0))
    elif feature.frame:
        raise KeyError(f"PartFeature `{feature.id}` references unknown frame `{feature.frame}`")

    if feature.type == "axis":
        normal = _feature_axis_direction(feature, normal)
        tangent = _orthogonal_tangent(normal, tangent)
    elif feature.type in {"plane", "face"}:
        normal = _feature_plane_normal(feature, normal)
        tangent = _orthogonal_tangent(normal, tangent)
    elif feature.type == "point":
        tangent = _orthogonal_tangent(normal, tangent)
    elif feature.type == "edge":
        tangent = _unit(tangent, label=f"{feature.id}.edge_tangent")
        normal = _orthogonal_normal(tangent, normal)
    else:
        raise ValueError(f"Unsupported PartFeature.type `{feature.type}`")

    return MateFrame(
        id=f"{feature.id}.mate",
        part_id=feature.part_id,
        feature_id=feature.id,
        origin=origin,
        normal=normal,
        tangent=tangent,
        semantic=feature.semantic,
        source_frame=feature.frame,
        metadata={
            "cad_tag": feature.cad_tag,
            "feature_type": feature.type,
            **dict(feature.metadata),
        },
    )


def _global_frame_poses(
    frames: list[FrameSpec],
) -> dict[str, tuple[Vector3, list[list[float]]]]:
    frame_by_id = {frame.id: frame for frame in frames}
    cache: dict[str, tuple[Vector3, list[list[float]]]] = {}

    def resolve(frame_id: str) -> tuple[Vector3, list[list[float]]]:
        if frame_id in cache:
            return cache[frame_id]
        frame = frame_by_id[frame_id]
        local_translation = tuple(float(v) for v in frame.transform.translation)
        local_rotation = _rpy_matrix(frame.transform.rotation_rpy)
        if frame.parent and frame.parent in frame_by_id:
            parent_origin, parent_rotation = resolve(frame.parent)
            origin = _add(parent_origin, _mat_vec(parent_rotation, local_translation))
            rotation = _mat_mul(parent_rotation, local_rotation)
        else:
            origin = local_translation
            rotation = local_rotation
        cache[frame_id] = (origin, rotation)
        return cache[frame_id]

    for frame in frames:
        resolve(frame.id)
    return cache


def _feature_axis_direction(feature: PartFeature, fallback: Vector3) -> Vector3:
    raw = feature.metadata.get("axis_direction") or feature.metadata.get("normal")
    if raw is None:
        return fallback
    return _vector_from_metadata(raw, fallback=fallback)


def _feature_plane_normal(feature: PartFeature, fallback: Vector3) -> Vector3:
    raw = feature.metadata.get("normal")
    if raw is None:
        return fallback
    return _vector_from_metadata(raw, fallback=fallback)


def _vector_from_metadata(value: Any, *, fallback: Vector3) -> Vector3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return fallback
    return (float(value[0]), float(value[1]), float(value[2]))


def _orthogonal_tangent(normal: Vector3, preferred: Vector3) -> Vector3:
    normal = _unit(normal, label="normal")
    preferred = _unit(preferred, label="preferred_tangent")
    projected = _sub(preferred, _scale(normal, _dot(preferred, normal)))
    if _length(projected) > 1e-9:
        return _unit(projected, label="tangent")
    fallback = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(fallback, normal), label="fallback_tangent")


def _orthogonal_normal(tangent: Vector3, preferred: Vector3) -> Vector3:
    tangent = _unit(tangent, label="tangent")
    preferred = _unit(preferred, label="preferred_normal")
    projected = _sub(preferred, _scale(tangent, _dot(preferred, tangent)))
    if _length(projected) > 1e-9:
        return _unit(projected, label="normal")
    fallback = (0.0, 0.0, 1.0) if abs(tangent[2]) < 0.9 else (0.0, 1.0, 0.0)
    return _unit(_cross(tangent, fallback), label="fallback_normal")


def _rpy_matrix(rpy: tuple[float, float, float]) -> list[list[float]]:
    roll, pitch, yaw = rpy
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]


def _mat_vec(matrix: list[list[float]], vector: Vector3) -> Vector3:
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    )


def _mat_mul(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [
            sum(left[row][k] * right[k][col] for k in range(3))
            for col in range(3)
        ]
        for row in range(3)
    ]


def _unit(vector: Vector3, *, label: str) -> Vector3:
    length = _length(vector)
    if length <= 1e-12:
        raise ValueError(f"{label} must be non-zero")
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _length(vector: Vector3) -> float:
    return math.sqrt(_dot(vector, vector))


def _dot(left: Vector3, right: Vector3) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _add(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _sub(left: Vector3, right: Vector3) -> Vector3:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _scale(vector: Vector3, factor: float) -> Vector3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


__all__ = [
    "MateFrame",
    "MateFrameCatalog",
    "build_mate_frame_catalog",
    "build_mate_frames",
]
