"""Basic DH kinematics for serial-chain robot MVPs.

This module stays strictly in the kinematic layer. The returned transforms are
not CAD mate transforms and must pass through the mechanical layout layer before
CAD skeleton generation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
import numpy.typing as npt

from robot_sdk.types import DHParam, KinematicConvention


Matrix4 = npt.NDArray[np.float64]
JointValues = Mapping[str, float] | Sequence[float]


def identity_matrix() -> Matrix4:
    """Return a 4x4 identity transform."""

    return np.eye(4, dtype=float)


def matrix_multiply(left: Matrix4, right: Matrix4) -> Matrix4:
    """Multiply two 4x4 transforms."""

    return np.asarray(left, dtype=float) @ np.asarray(right, dtype=float)


def dh_transform(
    param: DHParam,
    *,
    convention: KinematicConvention = "dh",
    joint_value: float | None = None,
) -> Matrix4:
    """Return the transform for one standard or modified DH row.

    For revolute joints, `joint_value` replaces theta. For prismatic joints, it
    replaces d. Values follow the units declared on `param`.
    """

    if convention == "poe":
        raise ValueError("dh_transform does not support POE convention")

    a = param.a
    alpha = _angle_to_rad(param.alpha, param.angle_unit)
    d = param.d
    theta = _angle_to_rad(param.theta, param.angle_unit)

    if joint_value is not None:
        if param.joint_type == "revolute":
            theta = _angle_to_rad(joint_value, param.angle_unit)
        elif param.joint_type == "prismatic":
            d = joint_value
        elif param.joint_type == "fixed":
            raise ValueError("Fixed DHParam cannot receive a joint_value")

    if convention == "dh":
        return _standard_dh_matrix(a=a, alpha=alpha, d=d, theta=theta)
    if convention == "modified_dh":
        return _modified_dh_matrix(a=a, alpha=alpha, d=d, theta=theta)
    raise ValueError(f"Unsupported DH convention: {convention}")


def forward_kinematics_chain(
    dh_params: Sequence[DHParam],
    *,
    joint_values: JointValues | None = None,
    convention: KinematicConvention = "dh",
    include_base: bool = True,
) -> list[Matrix4]:
    """Return cumulative transforms for each DH row in a serial chain."""

    if not dh_params:
        raise ValueError("dh_params cannot be empty")

    current = identity_matrix()
    transforms: list[Matrix4] = [current] if include_base else []
    for index, param in enumerate(dh_params):
        value = _joint_value_at(param, index, joint_values)
        current = matrix_multiply(
            current,
            dh_transform(param, convention=convention, joint_value=value),
        )
        transforms.append(current)
    return transforms


def forward_kinematics(
    dh_params: Sequence[DHParam],
    *,
    joint_values: JointValues | None = None,
    convention: KinematicConvention = "dh",
) -> Matrix4:
    """Return the end-effector kinematic transform for a serial DH chain."""

    return forward_kinematics_chain(
        dh_params,
        joint_values=joint_values,
        convention=convention,
        include_base=False,
    )[-1]


def position_from_matrix(matrix: Matrix4) -> tuple[float, float, float]:
    """Extract xyz translation from a homogeneous transform."""

    transform = np.asarray(matrix, dtype=float)
    return (
        float(transform[0, 3]),
        float(transform[1, 3]),
        float(transform[2, 3]),
    )


def estimate_reach(dh_params: Sequence[DHParam]) -> float:
    """Estimate a conservative maximum reach from DH length offsets.

    This is an early sizing heuristic, not a workspace proof. It sums the
    per-link geometric contribution sqrt(a^2 + d^2).
    """

    if not dh_params:
        raise ValueError("dh_params cannot be empty")
    return sum(math.hypot(param.a, param.d) for param in dh_params)


def _standard_dh_matrix(*, a: float, alpha: float, d: float, theta: float) -> Matrix4:
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    cos_alpha = math.cos(alpha)
    sin_alpha = math.sin(alpha)
    return np.array(
        [
            [
                cos_theta,
                -sin_theta * cos_alpha,
                sin_theta * sin_alpha,
                a * cos_theta,
            ],
            [
                sin_theta,
                cos_theta * cos_alpha,
                -cos_theta * sin_alpha,
                a * sin_theta,
            ],
            [0.0, sin_alpha, cos_alpha, d],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=float,
    )


def _modified_dh_matrix(*, a: float, alpha: float, d: float, theta: float) -> Matrix4:
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    cos_alpha = math.cos(alpha)
    sin_alpha = math.sin(alpha)
    return np.array(
        [
            [cos_theta, -sin_theta, 0.0, a],
            [
                sin_theta * cos_alpha,
                cos_theta * cos_alpha,
                -sin_alpha,
                -d * sin_alpha,
            ],
            [
                sin_theta * sin_alpha,
                cos_theta * sin_alpha,
                cos_alpha,
                d * cos_alpha,
            ],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=float,
    )


def _joint_value_at(
    param: DHParam,
    index: int,
    joint_values: JointValues | None,
) -> float | None:
    if joint_values is None:
        return None
    if isinstance(joint_values, Mapping):
        if param.variable and param.variable in joint_values:
            return joint_values[param.variable]
        return joint_values.get(param.joint_id)
    if index >= len(joint_values):
        raise ValueError("joint_values sequence is shorter than dh_params")
    return joint_values[index]


def _angle_to_rad(value: float, unit: str) -> float:
    if unit == "rad":
        return value
    if unit == "deg":
        return math.radians(value)
    raise ValueError(f"Unsupported angle unit: {unit}")
