"""Kinematic model abstractions for robot_sdk.

This layer describes how a robot moves. It intentionally does not contain
visual geometry, collision geometry, or simulator-specific export details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Tuple, Union

ScalarOrVariable = Union[float, str]
Vec3 = Tuple[float, float, float]
JointKind = Literal["revolute", "continuous", "prismatic", "fixed"]
KinematicRepresentation = Literal["dh", "modified_dh"]


def _clean_name(value: str, *, field_name: str) -> str:
    name = str(value).strip()
    if not name:
        raise ValueError(f"{field_name} is required")
    return name


def _vec3(values: tuple[float, float, float], *, name: str) -> Vec3:
    if len(values) != 3:
        raise ValueError(f"{name} must have 3 values")
    return (float(values[0]), float(values[1]), float(values[2]))


def _range_pair(values: tuple[float, float], *, name: str) -> tuple[float, float]:
    if len(values) != 2:
        raise ValueError(f"{name} must have 2 values")
    lo, hi = float(values[0]), float(values[1])
    if lo > hi:
        raise ValueError(f"{name} lower value cannot exceed upper value")
    return (lo, hi)


@dataclass(frozen=True)
class DHJoint:
    """One row in a DH-style serial-chain kinematic table.

    ``theta`` and ``d`` may be numeric constants or variable names. Revolute
    joints normally vary ``theta``; prismatic joints normally vary ``d``.
    Lengths are meters and angles are radians.
    """

    name: str
    alpha: float
    a: float
    d: ScalarOrVariable
    theta: ScalarOrVariable
    joint_type: JointKind = "revolute"
    limit: tuple[float, float] | None = None
    role: str | None = None
    axis_hint: Vec3 | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_name(self.name, field_name="dh_joint.name"))
        object.__setattr__(self, "alpha", float(self.alpha))
        object.__setattr__(self, "a", float(self.a))
        if not isinstance(self.d, str):
            object.__setattr__(self, "d", float(self.d))
        elif not self.d.strip():
            raise ValueError("dh_joint.d variable name cannot be empty")
        if not isinstance(self.theta, str):
            object.__setattr__(self, "theta", float(self.theta))
        elif not self.theta.strip():
            raise ValueError("dh_joint.theta variable name cannot be empty")
        if self.joint_type not in {"revolute", "continuous", "prismatic", "fixed"}:
            raise ValueError(f"unsupported dh_joint.joint_type: {self.joint_type!r}")
        if self.limit is not None:
            object.__setattr__(self, "limit", _range_pair(self.limit, name="dh_joint.limit"))
        if self.role is not None:
            role = str(self.role).strip()
            if not role:
                raise ValueError("dh_joint.role cannot be empty when provided")
            object.__setattr__(self, "role", role)
        if self.axis_hint is not None:
            object.__setattr__(self, "axis_hint", _vec3(self.axis_hint, name="dh_joint.axis_hint"))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class SerialManipulatorSpec:
    """Kinematic specification for a serial manipulator."""

    name: str
    dof: int
    representation: KinematicRepresentation
    joints: list[DHJoint]
    base_frame: str = "base"
    tool_frame: str = "tool0"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_name(self.name, field_name="serial_spec.name"))
        object.__setattr__(self, "dof", int(self.dof))
        if self.dof < 0:
            raise ValueError("serial_spec.dof cannot be negative")
        if self.representation not in {"dh", "modified_dh"}:
            raise ValueError(f"unsupported serial_spec.representation: {self.representation!r}")
        object.__setattr__(self, "joints", list(self.joints))
        object.__setattr__(self, "base_frame", _clean_name(self.base_frame, field_name="serial_spec.base_frame"))
        object.__setattr__(self, "tool_frame", _clean_name(self.tool_frame, field_name="serial_spec.tool_frame"))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class LegChainSpec:
    """Kinematic specification for one articulated leg."""

    name: str
    mount_xyz: Vec3
    joints: list[DHJoint]
    segment_lengths: tuple[float, ...]
    mirror_of: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_name(self.name, field_name="leg_chain.name"))
        object.__setattr__(self, "mount_xyz", _vec3(self.mount_xyz, name="leg_chain.mount_xyz"))
        object.__setattr__(self, "joints", list(self.joints))
        lengths = tuple(float(value) for value in self.segment_lengths)
        if any(value <= 0.0 for value in lengths):
            raise ValueError("leg_chain.segment_lengths values must be positive")
        object.__setattr__(self, "segment_lengths", lengths)
        if self.mirror_of is not None:
            object.__setattr__(self, "mirror_of", _clean_name(self.mirror_of, field_name="leg_chain.mirror_of"))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class QuadrupedSpec:
    """Kinematic specification for a four-legged robot."""

    name: str
    body_size: Vec3
    legs: list[LegChainSpec]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _clean_name(self.name, field_name="quadruped_spec.name"))
        body_size = _vec3(self.body_size, name="quadruped_spec.body_size")
        if any(value <= 0.0 for value in body_size):
            raise ValueError("quadruped_spec.body_size values must be positive")
        object.__setattr__(self, "body_size", body_size)
        object.__setattr__(self, "legs", list(self.legs))
        object.__setattr__(self, "metadata", dict(self.metadata))
