"""Lightweight source-level assembly helpers backed by build123d joints.

This module is the phase-10 assembly path. Parts own named local joints, and
assembly relationships are resolved by build123d's native ``connect_to`` API.
The helper records source mate metadata for documentation and validation, but
does not compile a global pose graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from robot_sdk.cad.bbox import bounding_box_from_cad_object


@dataclass(frozen=True)
class SourceMateTarget:
    """A named build123d joint on a part-like object."""

    part: Any
    frame: str


@dataclass(frozen=True)
class SourceMateRelation:
    """A source-level mate relation resolved through native build123d joints."""

    label: str
    relation: str
    fixed: str
    moving: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    fixed_endpoint: Mapping[str, Any] = field(default_factory=dict)
    moving_endpoint: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "label": self.label,
            "relation": self.relation,
            "fixed": self.fixed,
            "moving": self.moving,
            "parameters": _json_safe(self.parameters),
        }
        fixed_endpoint = _json_safe(self.fixed_endpoint)
        moving_endpoint = _json_safe(self.moving_endpoint)
        if fixed_endpoint:
            payload["fixed_endpoint"] = fixed_endpoint
        if moving_endpoint:
            payload["moving_endpoint"] = moving_endpoint
        return payload


@dataclass(frozen=True)
class SourceStepExportResult:
    """Result of exporting a build123d source-level assembly to STEP."""

    path: Path
    exists: bool
    size_bytes: int | None = None
    export_type: str = "STEP"
    bbox: Any | None = None


class SourceAssemblyHelper:
    """Small wrapper around build123d part-local joints.

    The important rule is that ``connect`` delegates positioning to native
    build123d joints. CathyAgent records the relationship; it does not solve it.
    """

    def __init__(self, name: str) -> None:
        self.label = label_text(name)
        self.children: list[Any] = []
        self.relations: list[SourceMateRelation] = []

    def add(
        self,
        shape: Any,
        name: str,
        *,
        color: Any | None = None,
    ) -> Any:
        label_shape(shape, name, color=color)
        self.children.append(shape)
        return shape

    def rigid_frame(self, part: Any, name: str, location: Any) -> SourceMateTarget:
        return add_rigid_frame(part, name, location)

    def revolute_frame(
        self,
        part: Any,
        name: str,
        axis: Any,
        **joint_options: Any,
    ) -> SourceMateTarget:
        return add_axis_frame(
            part,
            name,
            axis,
            joint_type="RevoluteJoint",
            **joint_options,
        )

    def connect(
        self,
        fixed: SourceMateTarget | tuple[Any, str],
        moving: SourceMateTarget | tuple[Any, str],
        *,
        relation: str = "rigid",
        label: str | None = None,
        **connect_options: Any,
    ) -> SourceMateRelation:
        fixed_target = _normalize_target(fixed)
        moving_target = _normalize_target(moving)
        fixed_label, fixed_joint = _joint_for_target(fixed_target)
        moving_label, moving_joint = _joint_for_target(moving_target)
        options = {
            key: value
            for key, value in connect_options.items()
            if value is not None
        }
        fixed_joint.connect_to(moving_joint, **options)
        relation_record = SourceMateRelation(
            label=label_text(label)
            if label is not None
            else label_text(relation, fixed_label, moving_label),
            relation=label_text(relation),
            fixed=fixed_label,
            moving=moving_label,
            parameters=options,
            fixed_endpoint=_mate_endpoint_payload(fixed_target, fixed_label, fixed_joint),
            moving_endpoint=_mate_endpoint_payload(moving_target, moving_label, moving_joint),
        )
        self.relations.append(relation_record)
        return relation_record

    def face_to_face(
        self,
        fixed: SourceMateTarget | tuple[Any, str],
        moving: SourceMateTarget | tuple[Any, str],
        *,
        offset: float | Sequence[float] | Any | None = None,
        label: str | None = None,
    ) -> SourceMateRelation:
        fixed_target = _normalize_target(fixed)
        if offset is not None:
            fixed_target = offset_target(fixed_target, offset, label=label)
        return self.connect(
            fixed_target,
            moving,
            relation="face_to_face",
            label=label,
        )

    def coaxial(
        self,
        fixed: SourceMateTarget | tuple[Any, str],
        moving: SourceMateTarget | tuple[Any, str],
        *,
        offset: float | Sequence[float] | Any | None = None,
        label: str | None = None,
    ) -> SourceMateRelation:
        fixed_target = _normalize_target(fixed)
        if offset is not None:
            fixed_target = offset_target(fixed_target, offset, label=label)
        return self.connect(
            fixed_target,
            moving,
            relation="coaxial",
            label=label,
        )

    def build(self) -> Any:
        build123d = _import_build123d()
        compound = build123d.Compound(label=self.label, children=list(self.children))
        mates = source_mate_payload(self.relations)
        if mates:
            compound.assembly_mates = mates
        return compound


def label_text(name: object, *details: object) -> str:
    tokens = [_normalize_label_token(name, field_name="name")]
    tokens.extend(_normalize_label_token(detail, field_name="detail") for detail in details)
    return ":".join(tokens)


def label_shape(shape: Any, name: str, *, color: Any | None = None) -> Any:
    shape.label = label_text(name)
    if color is not None:
        shape.color = color
    return shape


def target(part: Any, frame: str) -> SourceMateTarget:
    return SourceMateTarget(part=part, frame=label_text(frame))


def add_rigid_frame(part: Any, name: str, location: Any) -> SourceMateTarget:
    build123d = _import_build123d()
    label = label_text(name)
    build123d.RigidJoint(label=label, to_part=part, joint_location=location)
    return SourceMateTarget(part=part, frame=label)


def add_axis_frame(
    part: Any,
    name: str,
    axis: Any,
    *,
    joint_type: str,
    **joint_options: Any,
) -> SourceMateTarget:
    build123d = _import_build123d()
    joint_cls = getattr(build123d, joint_type)
    label = label_text(name)
    joint_cls(
        label=label,
        to_part=part,
        axis=axis,
        **{key: value for key, value in joint_options.items() if value is not None},
    )
    return SourceMateTarget(part=part, frame=label)


def offset_target(
    fixed: SourceMateTarget | tuple[Any, str],
    offset: float | Sequence[float] | Any,
    *,
    label: str | None = None,
) -> SourceMateTarget:
    fixed_target = _normalize_target(fixed)
    fixed_label, fixed_joint = _joint_for_target(fixed_target)
    location = _joint_location(fixed_joint)
    if location is None:
        raise ValueError(f"Joint {fixed_label!r} does not expose a location")
    build123d = _import_build123d()
    target_location = location * _offset_location(offset)
    target_label = label_text(label or fixed_label, "offset")
    build123d.RigidJoint(
        label=target_label,
        to_part=fixed_target.part,
        joint_location=target_location,
    )
    return SourceMateTarget(part=fixed_target.part, frame=target_label)


def source_mate_payload(relations: Sequence[SourceMateRelation]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"m{index}",
            **relation.to_dict(),
        }
        for index, relation in enumerate(relations, start=1)
    ]


def export_source_step(assembly: Any, path: str | Path) -> SourceStepExportResult:
    build123d = _import_build123d()
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bbox = bounding_box_from_cad_object(assembly)
    build123d.export_step(assembly, output_path)
    exists = output_path.exists()
    return SourceStepExportResult(
        path=output_path,
        exists=exists,
        size_bytes=output_path.stat().st_size if exists else None,
        bbox=bbox,
    )


def _normalize_target(value: SourceMateTarget | tuple[Any, str]) -> SourceMateTarget:
    if isinstance(value, SourceMateTarget):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        return SourceMateTarget(part=value[0], frame=label_text(value[1]))
    raise TypeError("Mate target must be SourceMateTarget or (part, frame_name)")


def _joint_for_target(target_value: SourceMateTarget) -> tuple[str, Any]:
    joints = getattr(target_value.part, "joints", None)
    if not isinstance(joints, Mapping):
        raise ValueError("Mate target part does not expose a build123d joints mapping")
    joint = joints.get(target_value.frame)
    if joint is None:
        raise KeyError(f"Part does not define mate frame {target_value.frame!r}")
    return target_value.frame, joint


def _mate_endpoint_payload(
    target_value: SourceMateTarget,
    frame: str,
    joint: Any,
) -> dict[str, Any]:
    endpoint: dict[str, Any] = {
        "part": str(getattr(target_value.part, "label", "") or type(target_value.part).__name__),
        "frame": frame,
    }
    location_payload = _location_payload(_joint_location(joint))
    if location_payload:
        endpoint["location"] = location_payload
    return endpoint


def _joint_location(joint: Any) -> Any | None:
    return getattr(joint, "location", None) or getattr(joint, "joint_location", None)


def _location_payload(location: Any) -> dict[str, Any]:
    if location is None:
        return {}
    payload: dict[str, Any] = {}
    position = _vector_payload(getattr(location, "position", None))
    orientation = _vector_payload(getattr(location, "orientation", None))
    if position is not None:
        payload["position"] = position
    if orientation is not None:
        payload["orientation"] = orientation
    return payload


def _vector_payload(value: Any) -> list[float] | None:
    if value is None:
        return None
    components = []
    for attr in ("X", "Y", "Z"):
        if hasattr(value, attr):
            components.append(getattr(value, attr))
    if len(components) != 3:
        for attr in ("x", "y", "z"):
            if hasattr(value, attr):
                components.append(getattr(value, attr))
        if len(components) > 3:
            components = components[-3:]
    if len(components) != 3:
        try:
            components = list(value)
        except TypeError:
            return None
    if len(components) < 3:
        return None
    try:
        return [float(components[0]), float(components[1]), float(components[2])]
    except (TypeError, ValueError):
        return None


def _offset_location(offset: float | Sequence[float] | Any) -> Any:
    build123d = _import_build123d()
    if hasattr(offset, "wrapped"):
        return offset
    if isinstance(offset, (int, float)):
        return build123d.Location((0.0, 0.0, float(offset)))
    if isinstance(offset, Sequence) and not isinstance(offset, (str, bytes, bytearray)):
        return build123d.Location(tuple(float(value) for value in offset))
    return offset


def _normalize_label_token(value: object, *, field_name: str) -> str:
    token = str(value).strip()
    if not token:
        raise ValueError(f"Source assembly label {field_name} must be non-empty")
    return "_".join(token.replace(":", "_").split())


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(child) for child in value]
    return str(value)


def _import_build123d() -> Any:
    try:
        import build123d
    except ModuleNotFoundError as exc:
        raise RuntimeError("source_assembly requires build123d at runtime") from exc
    return build123d


__all__ = [
    "SourceAssemblyHelper",
    "SourceMateRelation",
    "SourceMateTarget",
    "SourceStepExportResult",
    "add_axis_frame",
    "add_rigid_frame",
    "export_source_step",
    "label_shape",
    "label_text",
    "offset_target",
    "source_mate_payload",
    "target",
]
