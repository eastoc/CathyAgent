"""DH and modified-DH math utilities."""

from __future__ import annotations

import math
from typing import Mapping, Sequence, Tuple

from .model import DHJoint, SerialManipulatorSpec

Matrix4 = Tuple[
    Tuple[float, float, float, float],
    Tuple[float, float, float, float],
    Tuple[float, float, float, float],
    Tuple[float, float, float, float],
]


def identity_matrix() -> Matrix4:
    """Return a 4x4 identity transform."""

    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def matmul(a: Matrix4, b: Matrix4) -> Matrix4:
    """Multiply two 4x4 transforms."""

    rows: list[tuple[float, float, float, float]] = []
    for i in range(4):
        row = []
        for j in range(4):
            row.append(sum(a[i][k] * b[k][j] for k in range(4)))
        rows.append(tuple(row))  # type: ignore[arg-type]
    return tuple(rows)  # type: ignore[return-value]


def standard_dh_transform(*, alpha: float, a: float, d: float, theta: float) -> Matrix4:
    """Return the standard DH transform RotZ(theta) TransZ(d) TransX(a) RotX(alpha)."""

    ca = math.cos(float(alpha))
    sa = math.sin(float(alpha))
    ct = math.cos(float(theta))
    st = math.sin(float(theta))
    return (
        (ct, -st * ca, st * sa, float(a) * ct),
        (st, ct * ca, -ct * sa, float(a) * st),
        (0.0, sa, ca, float(d)),
        (0.0, 0.0, 0.0, 1.0),
    )


def modified_dh_transform(*, alpha: float, a: float, d: float, theta: float) -> Matrix4:
    """Return the Craig-style modified DH transform RotX(alpha) TransX(a) RotZ(theta) TransZ(d)."""

    ca = math.cos(float(alpha))
    sa = math.sin(float(alpha))
    ct = math.cos(float(theta))
    st = math.sin(float(theta))
    return (
        (ct, -st, 0.0, float(a)),
        (st * ca, ct * ca, -sa, -float(d) * sa),
        (st * sa, ct * sa, ca, float(d) * ca),
        (0.0, 0.0, 0.0, 1.0),
    )


def joint_transform(
    joint: DHJoint,
    *,
    representation: str = "dh",
    joint_values: Mapping[str, float] | Sequence[float] | None = None,
    index: int = 0,
) -> Matrix4:
    """Return one joint transform for a DH row."""

    _check_sequence_value_shape(joint, joint_values)
    theta = _resolve_scalar(joint.theta, joint_values=joint_values, index=index)
    d = _resolve_scalar(joint.d, joint_values=joint_values, index=index)
    if representation == "dh":
        return standard_dh_transform(alpha=joint.alpha, a=joint.a, d=d, theta=theta)
    if representation == "modified_dh":
        return modified_dh_transform(alpha=joint.alpha, a=joint.a, d=d, theta=theta)
    raise ValueError(f"unsupported DH representation: {representation!r}")


def forward_kinematics(
    spec: SerialManipulatorSpec,
    joint_values: Mapping[str, float] | Sequence[float] | None = None,
    *,
    upto: int | None = None,
) -> Matrix4:
    """Compute the base-to-tool transform for a serial manipulator spec."""

    if spec.representation not in {"dh", "modified_dh"}:
        raise ValueError(f"forward_kinematics does not support {spec.representation!r} yet")
    count = len(spec.joints) if upto is None else int(upto)
    if count < 0 or count > len(spec.joints):
        raise ValueError("upto must be between 0 and the number of joints")

    transform = identity_matrix()
    for index, joint in enumerate(spec.joints[:count]):
        transform = matmul(
            transform,
            joint_transform(joint, representation=spec.representation, joint_values=joint_values, index=index),
        )
    return transform


def translation_of(transform: Matrix4) -> tuple[float, float, float]:
    """Return the xyz translation from a homogeneous transform."""

    return (transform[0][3], transform[1][3], transform[2][3])


def _resolve_scalar(
    value: float | str,
    *,
    joint_values: Mapping[str, float] | Sequence[float] | None,
    index: int,
) -> float:
    if not isinstance(value, str):
        return float(value)
    if joint_values is None:
        raise ValueError(f"missing joint value for variable {value!r}")
    if isinstance(joint_values, Mapping):
        if value not in joint_values:
            raise ValueError(f"missing joint value for variable {value!r}")
        return float(joint_values[value])
    try:
        return float(joint_values[index])
    except IndexError as exc:
        raise ValueError(f"missing joint value at index {index}") from exc


def _check_sequence_value_shape(
    joint: DHJoint,
    joint_values: Mapping[str, float] | Sequence[float] | None,
) -> None:
    if joint_values is None or isinstance(joint_values, Mapping):
        return
    if isinstance(joint.theta, str) and isinstance(joint.d, str):
        raise ValueError(
            f"joint {joint.name!r} has both theta and d variables; use a mapping of variable names to values"
        )
