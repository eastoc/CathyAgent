"""SONIC 观测和轨迹重采样所需的四元数运算，顺序均为 wxyz。"""

from __future__ import annotations

import numpy as np


def normalize_quat(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=np.float64)
    norm = float(np.linalg.norm(q))
    if norm < 1e-12:
        return np.asarray([1.0, 0.0, 0.0, 0.0])
    return q / norm


def quat_conjugate(q: np.ndarray) -> np.ndarray:
    q = normalize_quat(q)
    return np.asarray([q[0], -q[1], -q[2], -q[3]])


def quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return normalize_quat(np.asarray([
        aw*bw - ax*bx - ay*by - az*bz,
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
    ]))


def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    w, x, y, z = normalize_quat(q)
    return np.asarray([
        [1 - 2*(y*y + z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x + z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x + y*y)],
    ])


def rotate_inverse(q: np.ndarray, vector: np.ndarray) -> np.ndarray:
    return quat_to_matrix(q).T @ np.asarray(vector, dtype=np.float64)


def relative_rotation_6d(base: np.ndarray, reference: np.ndarray) -> np.ndarray:
    relative = quat_multiply(quat_conjugate(base), reference)
    return quat_to_matrix(relative)[:, :2].reshape(-1)


def slerp(a: np.ndarray, b: np.ndarray, fraction: float) -> np.ndarray:
    a = normalize_quat(a)
    b = normalize_quat(b)
    dot = float(np.dot(a, b))
    if dot < 0.0:
        b = -b
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        return normalize_quat(a + fraction * (b - a))
    theta = np.arccos(dot)
    return normalize_quat(
        np.sin((1.0 - fraction) * theta) / np.sin(theta) * a
        + np.sin(fraction * theta) / np.sin(theta) * b
    )


__all__ = [
    "normalize_quat", "quat_conjugate", "quat_multiply", "quat_to_matrix",
    "relative_rotation_6d", "rotate_inverse", "slerp",
]
