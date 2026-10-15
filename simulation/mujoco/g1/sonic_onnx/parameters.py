"""与 NVIDIA G1 部署代码一致的关节映射和电机参数。"""

from __future__ import annotations

import math
import numpy as np

NUM_JOINTS = 29

# IsaacLab index -> Unitree/MuJoCo hardware index.
ISAAC_TO_HARDWARE = np.asarray(
    [0, 6, 12, 1, 7, 13, 2, 8, 14, 3, 9, 15, 22, 4, 10,
     16, 23, 5, 11, 17, 24, 18, 25, 19, 26, 20, 27, 21, 28],
    dtype=np.int64,
)
# Unitree/MuJoCo hardware index -> IsaacLab index.
HARDWARE_TO_ISAAC = np.asarray(
    [0, 3, 6, 9, 13, 17, 1, 4, 7, 10, 14, 18, 2, 5, 8,
     11, 15, 19, 21, 23, 25, 27, 12, 16, 20, 22, 24, 26, 28],
    dtype=np.int64,
)

DEFAULT_ANGLES = np.asarray(
    [-0.312, 0.0, 0.0, 0.669, -0.363, 0.0,
     -0.312, 0.0, 0.0, 0.669, -0.363, 0.0,
     0.0, 0.0, 0.0,
     0.2, 0.2, 0.0, 0.6, 0.0, 0.0, 0.0,
     0.2, -0.2, 0.0, 0.6, 0.0, 0.0, 0.0],
    dtype=np.float32,
)

_ARMATURE = {"5020": 0.003609725, "7520_14": 0.010177520,
             "7520_22": 0.025101925, "4010": 0.00425}
_EFFORT = {"5020": 25.0, "7520_14": 88.0,
           "7520_22": 139.0, "4010": 5.0}
_TYPES = [
    "7520_22", "7520_22", "7520_14", "7520_22", "5020", "5020",
    "7520_22", "7520_22", "7520_14", "7520_22", "5020", "5020",
    "7520_14", "5020", "5020",
    "5020", "5020", "5020", "5020", "5020", "4010", "4010",
    "5020", "5020", "5020", "5020", "5020", "4010", "4010",
]
_DOUBLE_GAIN = {4, 5, 10, 11, 13, 14}
_OMEGA = 10.0 * 2.0 * math.pi

STIFFNESS = np.asarray(
    [_ARMATURE[kind] * _OMEGA**2 * (2.0 if i in _DOUBLE_GAIN else 1.0)
     for i, kind in enumerate(_TYPES)], dtype=np.float32,
)
DAMPING = np.asarray(
    [2.0 * 2.0 * _ARMATURE[kind] * _OMEGA *
     (2.0 if i in _DOUBLE_GAIN else 1.0)
     for i, kind in enumerate(_TYPES)], dtype=np.float32,
)
# 官方 action scale 不包含 ankle/waist 的双倍 PD 系数。
ACTION_SCALE = np.asarray(
    [0.25 * _EFFORT[kind] / (_ARMATURE[kind] * _OMEGA**2)
     for kind in _TYPES], dtype=np.float32,
)

# 官方 G1 29-DoF MuJoCo 配置中的低层力矩上限（hardware order）。
EFFORT_LIMITS = np.asarray(
    [88, 88, 88, 139, 50, 50, 88, 88, 88, 139, 50, 50,
     88, 50, 50, 25, 25, 25, 25, 25, 5, 5,
     25, 25, 25, 25, 25, 5, 5],
    dtype=np.float64,
)

__all__ = [
    "ACTION_SCALE", "DAMPING", "DEFAULT_ANGLES", "EFFORT_LIMITS", "HARDWARE_TO_ISAAC",
    "ISAAC_TO_HARDWARE", "NUM_JOINTS", "STIFFNESS",
]
