"""SONIC 适配层内部数据契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..contracts import Pose2D


@dataclass(frozen=True)
class TimedPose2D:
    pose: Pose2D
    monotonic_time: float
    root_qpos: tuple[float, ...] | None = None
    joint_position_hardware: tuple[float, ...] | None = None
    motion_active: bool | None = None


@dataclass(frozen=True)
class SonicCommand:
    mode: int
    movement: tuple[float, float, float]
    facing: tuple[float, float, float]
    speed: float
    phase: str


@dataclass(frozen=True)
class NavigationExecution:
    status: str
    error_code: str | None
    message: str
    motion_started: bool
    final_pose: Pose2D
    elapsed_seconds: float
    commands_sent: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["NavigationExecution", "SonicCommand", "TimedPose2D"]
