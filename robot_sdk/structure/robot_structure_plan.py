"""Robot structure planning contracts.

This module describes the intended robot topology before it is converted into
MechanicalLayout frames and CAD bodies.  It is intentionally deterministic and
LLM-free: agents may decide which plan to use, while SDK validation can inspect
the resulting structure intent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from robot_sdk.types import LengthUnit, SerializableMixin


StationRole = Literal[
    "base",
    "shoulder",
    "elbow",
    "wrist1",
    "wrist2",
    "wrist3",
    "tool",
]
JointAxisRole = Literal[
    "base_yaw",
    "shoulder_pitch",
    "elbow_pitch",
    "wrist_roll",
    "wrist_pitch",
    "tool_roll",
    "generic",
]
LinkRouteType = Literal[
    "straight",
    "offset",
    "elbow",
    "wrist_spacer",
    "tool_stub",
]
DatumSemantic = Literal[
    "mount_plane",
    "joint_axis",
    "interface_plane",
    "tool_plane",
]
StructureSource = Literal[
    "layout_agent",
    "generic_6axis_cobot_profile",
    "profile_structure",
    "manual",
    "unknown",
]


@dataclass
class StructureStation(SerializableMixin):
    """A named mechanical station such as base, shoulder, elbow, or wrist."""

    id: str
    role: StationRole
    origin: tuple[float, float, float]
    parent_id: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.id, "StructureStation.id")
        _validate_vec3(self.origin, "StructureStation.origin")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StructureStation":
        return cls(
            id=str(data.get("id") or "").strip(),
            role=str(data.get("role") or "base"),
            origin=_vec3(data.get("origin")),
            parent_id=_none_if_blank(data.get("parent_id")),
            description=str(data.get("description") or ""),
        )


@dataclass
class JointAxisPlan(SerializableMixin):
    """Intended joint axis in structure-planning coordinates."""

    joint_id: str
    station_id: str
    role: JointAxisRole
    direction: tuple[float, float, float]
    origin: tuple[float, float, float] | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        _require_text(self.joint_id, "JointAxisPlan.joint_id")
        _require_text(self.station_id, "JointAxisPlan.station_id")
        _validate_vec3(self.direction, "JointAxisPlan.direction")
        if _norm(self.direction) <= 1e-9:
            raise ValueError("JointAxisPlan.direction cannot be zero")
        if self.origin is not None:
            _validate_vec3(self.origin, "JointAxisPlan.origin")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JointAxisPlan":
        origin = data.get("origin")
        return cls(
            joint_id=str(data.get("joint_id") or "").strip(),
            station_id=str(data.get("station_id") or "").strip(),
            role=str(data.get("role") or "generic"),
            direction=_vec3(data.get("direction")),
            origin=_vec3(origin) if origin is not None else None,
            rationale=str(data.get("rationale") or ""),
        )


@dataclass
class LinkRoutePlan(SerializableMixin):
    """How a link should route between two mechanical stations."""

    link_id: str
    from_station_id: str
    to_station_id: str
    route_type: LinkRouteType
    waypoints: list[tuple[float, float, float]] = field(default_factory=list)
    rationale: str = ""

    def __post_init__(self) -> None:
        _require_text(self.link_id, "LinkRoutePlan.link_id")
        _require_text(self.from_station_id, "LinkRoutePlan.from_station_id")
        _require_text(self.to_station_id, "LinkRoutePlan.to_station_id")
        if self.from_station_id == self.to_station_id:
            raise ValueError("LinkRoutePlan endpoints must differ")
        for index, waypoint in enumerate(self.waypoints):
            _validate_vec3(waypoint, f"LinkRoutePlan.waypoints[{index}]")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LinkRoutePlan":
        return cls(
            link_id=str(data.get("link_id") or "").strip(),
            from_station_id=str(data.get("from_station_id") or "").strip(),
            to_station_id=str(data.get("to_station_id") or "").strip(),
            route_type=str(data.get("route_type") or "straight"),
            waypoints=[
                _vec3(item)
                for item in data.get("waypoints") or []
            ],
            rationale=str(data.get("rationale") or ""),
        )


@dataclass
class DatumPlan(SerializableMixin):
    """A datum that later adapters can convert into part features and mates."""

    id: str
    owner_id: str
    semantic: DatumSemantic
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]
    tangent: tuple[float, float, float] = (1.0, 0.0, 0.0)
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.id, "DatumPlan.id")
        _require_text(self.owner_id, "DatumPlan.owner_id")
        _validate_vec3(self.origin, "DatumPlan.origin")
        _validate_vec3(self.normal, "DatumPlan.normal")
        _validate_vec3(self.tangent, "DatumPlan.tangent")
        if _norm(self.normal) <= 1e-9:
            raise ValueError("DatumPlan.normal cannot be zero")
        if _norm(self.tangent) <= 1e-9:
            raise ValueError("DatumPlan.tangent cannot be zero")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatumPlan":
        return cls(
            id=str(data.get("id") or "").strip(),
            owner_id=str(data.get("owner_id") or "").strip(),
            semantic=str(data.get("semantic") or "interface_plane"),
            origin=_vec3(data.get("origin")),
            normal=_vec3(data.get("normal")),
            tangent=_vec3(data.get("tangent"), default=(1.0, 0.0, 0.0)),
            description=str(data.get("description") or ""),
        )


@dataclass
class StructureSubassemblyPlan(SerializableMixin):
    """Expected CAD package grouping before concrete CadQuery parts exist."""

    name: str
    role: str
    part_ids: list[str]
    anchor_part_id: str
    rationale: str = ""

    def __post_init__(self) -> None:
        _require_text(self.name, "StructureSubassemblyPlan.name")
        _require_text(self.role, "StructureSubassemblyPlan.role")
        if not self.part_ids:
            raise ValueError("StructureSubassemblyPlan.part_ids cannot be empty")
        if self.anchor_part_id not in self.part_ids:
            raise ValueError("StructureSubassemblyPlan.anchor_part_id must be in part_ids")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StructureSubassemblyPlan":
        return cls(
            name=str(data.get("name") or "").strip(),
            role=str(data.get("role") or "local_subassembly").strip(),
            part_ids=[
                str(item).strip()
                for item in data.get("part_ids") or []
                if str(item).strip()
            ],
            anchor_part_id=str(data.get("anchor_part_id") or "").strip(),
            rationale=str(data.get("rationale") or ""),
        )


@dataclass
class RobotStructurePlan(SerializableMixin):
    """Structure-first robot design intent consumed before MechanicalLayout."""

    name: str
    family: str
    source: StructureSource
    units: LengthUnit
    stations: list[StructureStation]
    joint_axes: list[JointAxisPlan]
    link_routes: list[LinkRoutePlan]
    datums: list[DatumPlan] = field(default_factory=list)
    subassemblies: list[StructureSubassemblyPlan] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.name, "RobotStructurePlan.name")
        _require_text(self.family, "RobotStructurePlan.family")
        if self.units not in {"mm", "m"}:
            raise ValueError("RobotStructurePlan.units must be mm or m")
        if not self.stations:
            raise ValueError("RobotStructurePlan.stations cannot be empty")
        if not self.joint_axes:
            raise ValueError("RobotStructurePlan.joint_axes cannot be empty")
        _reject_duplicates([station.id for station in self.stations], "station")
        _reject_duplicates([axis.joint_id for axis in self.joint_axes], "joint axis")
        _reject_duplicates([route.link_id for route in self.link_routes], "link route")
        _reject_duplicates([datum.id for datum in self.datums], "datum")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RobotStructurePlan":
        return cls(
            name=str(data.get("name") or "").strip(),
            family=str(data.get("family") or "unknown").strip(),
            source=str(data.get("source") or "unknown"),
            units=str(data.get("units") or "mm"),
            stations=[
                StructureStation.from_dict(item)
                for item in data.get("stations") or []
                if isinstance(item, dict)
            ],
            joint_axes=[
                JointAxisPlan.from_dict(item)
                for item in data.get("joint_axes") or []
                if isinstance(item, dict)
            ],
            link_routes=[
                LinkRoutePlan.from_dict(item)
                for item in data.get("link_routes") or []
                if isinstance(item, dict)
            ],
            datums=[
                DatumPlan.from_dict(item)
                for item in data.get("datums") or []
                if isinstance(item, dict)
            ],
            subassemblies=[
                StructureSubassemblyPlan.from_dict(item)
                for item in data.get("subassemblies") or []
                if isinstance(item, dict)
            ],
            assumptions=_string_list(data.get("assumptions")),
            warnings=_string_list(data.get("warnings")),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )


def build_generic_6axis_cobot_structure_plan(
    *,
    reach_mm: float = 500.0,
    name: str = "generic_6axis_cobot",
) -> RobotStructurePlan:
    """Build a conservative non-planar 6-axis cobot structure intent.

    This is not a manufacturer DH profile.  It gives layout/CAD agents a
    source-first alternative to the old planar 6-DOF template: vertical base,
    shoulder/elbow pitch axes, and a compact three-axis wrist.
    """

    if reach_mm <= 0:
        raise ValueError("reach_mm must be positive")

    r = float(reach_mm)
    z_base = 0.18 * r
    z_arm = 0.30 * r
    z_wrist = 0.24 * r
    stations = [
        StructureStation("base", "base", (0.0, 0.0, 0.0), description="Fixed base plate."),
        StructureStation("shoulder", "shoulder", (0.0, 0.0, z_base), "base"),
        StructureStation("elbow", "elbow", (0.42 * r, 0.0, z_arm), "shoulder"),
        StructureStation("wrist1", "wrist1", (0.76 * r, 0.0, z_wrist), "elbow"),
        StructureStation("wrist2", "wrist2", (0.84 * r, 0.0, z_wrist), "wrist1"),
        StructureStation("wrist3", "wrist3", (0.92 * r, 0.0, z_wrist), "wrist2"),
        StructureStation("tool", "tool", (r, 0.0, z_wrist), "wrist3"),
    ]
    station_origin = {station.id: station.origin for station in stations}
    joint_axes = [
        JointAxisPlan("J1", "base", "base_yaw", (0.0, 0.0, 1.0), station_origin["base"]),
        JointAxisPlan("J2", "shoulder", "shoulder_pitch", (0.0, 1.0, 0.0), station_origin["shoulder"]),
        JointAxisPlan("J3", "elbow", "elbow_pitch", (0.0, 1.0, 0.0), station_origin["elbow"]),
        JointAxisPlan("J4", "wrist1", "wrist_roll", (1.0, 0.0, 0.0), station_origin["wrist1"]),
        JointAxisPlan("J5", "wrist2", "wrist_pitch", (0.0, 1.0, 0.0), station_origin["wrist2"]),
        JointAxisPlan("J6", "wrist3", "tool_roll", (1.0, 0.0, 0.0), station_origin["wrist3"]),
    ]
    link_routes = [
        LinkRoutePlan("L1", "base", "shoulder", "offset", rationale="Base column raises shoulder above mount."),
        LinkRoutePlan("L2", "shoulder", "elbow", "straight", rationale="Upper arm span."),
        LinkRoutePlan("L3", "elbow", "wrist1", "elbow", rationale="Forearm drops into wrist offset instead of a flat box."),
        LinkRoutePlan("L4", "wrist1", "wrist2", "wrist_spacer", rationale="Compact wrist offset housing."),
        LinkRoutePlan("L5", "wrist2", "wrist3", "elbow", rationale="Cross-axis wrist connector."),
        LinkRoutePlan("L6", "wrist3", "tool", "tool_stub", rationale="Tool flange stub."),
    ]
    datums = [
        DatumPlan("base_mount_plane", "base", "mount_plane", station_origin["base"], (0.0, 0.0, 1.0)),
        *[
            DatumPlan(
                f"{axis.joint_id}_axis_datum",
                axis.joint_id,
                "joint_axis",
                axis.origin or station_origin[axis.station_id],
                axis.direction,
            )
            for axis in joint_axes
        ],
        DatumPlan("tool_mount_plane", "tool", "tool_plane", station_origin["tool"], (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ]
    subassemblies = [
        StructureSubassemblyPlan("base", "base_mount", ["base", "J1"], "base"),
        StructureSubassemblyPlan("shoulder", "joint_link_joint", ["J1", "L1", "J2"], "J1"),
        StructureSubassemblyPlan("upper_arm", "joint_link_joint", ["J2", "L2", "J3"], "J2"),
        StructureSubassemblyPlan("forearm", "joint_link_joint", ["J3", "L3", "J4"], "J3"),
        StructureSubassemblyPlan("wrist", "wrist_group", ["J4", "L4", "J5", "L5", "J6"], "J4"),
        StructureSubassemblyPlan("tool", "terminal_tool", ["J6", "L6", "end_effector"], "J6"),
    ]
    return RobotStructurePlan(
        name=name,
        family="generic_6axis_cobot",
        source="generic_6axis_cobot_profile",
        units="mm",
        stations=stations,
        joint_axes=joint_axes,
        link_routes=link_routes,
        datums=datums,
        subassemblies=subassemblies,
        assumptions=[
            "Generic 6-axis cobot structure is a topology template, not official manufacturer geometry.",
            "MechanicalLayout adapter must convert these stations into explicit interface frames before CAD.",
        ],
        warnings=[
            "This structure plan is suitable for concept blocking only; detailed actuator and casting geometry are unresolved."
        ],
        metadata={"reach_mm": r},
    )


def normalized_axis(direction: tuple[float, float, float]) -> tuple[float, float, float]:
    """Return a unit axis vector."""

    length = _norm(direction)
    if length <= 1e-9:
        raise ValueError("direction cannot be zero")
    return tuple(component / length for component in direction)  # type: ignore[return-value]


def axes_are_parallel(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    *,
    tolerance: float = 0.98,
) -> bool:
    """Return true when two axes are nearly parallel or antiparallel."""

    a = normalized_axis(first)
    b = normalized_axis(second)
    return abs(sum(a_i * b_i for a_i, b_i in zip(a, b))) >= tolerance


def _norm(vec: tuple[float, float, float]) -> float:
    return math.sqrt(sum(component * component for component in vec))


def _require_text(value: str, field_name: str) -> None:
    if not str(value or "").strip():
        raise ValueError(f"{field_name} is required")


def _validate_vec3(value: tuple[float, float, float], field_name: str) -> None:
    if len(value) != 3:
        raise ValueError(f"{field_name} must contain 3 values")


def _vec3(
    value: object,
    *,
    default: tuple[float, float, float] | None = None,
) -> tuple[float, float, float]:
    if value is None:
        if default is None:
            raise ValueError("Expected vec3 value")
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("Expected vec3 value")
    return (float(value[0]), float(value[1]), float(value[2]))


def _none_if_blank(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _reject_duplicates(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"Duplicate {label} ids: {sorted(set(duplicates))}")


__all__ = [
    "DatumPlan",
    "JointAxisPlan",
    "LinkRoutePlan",
    "RobotStructurePlan",
    "StructureStation",
    "StructureSubassemblyPlan",
    "axes_are_parallel",
    "build_generic_6axis_cobot_structure_plan",
    "normalized_axis",
]
