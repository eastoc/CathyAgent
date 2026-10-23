"""厨房桌面任务与导航到操作的交接契约。"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import yaml


NAVIGATION_METADATA = Path(__file__).resolve().parents[2] / "assets/g1_open_kitchen/navigation.yaml"


@dataclass(frozen=True)
class ManipulationStation:
    name: str
    position_xy_m: tuple[float, float]
    yaw_rad: float
    position_tolerance_m: float
    yaw_tolerance_rad: float


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    instruction: str
    object_body: str
    object_joint: str
    target_body: str
    target_site: str
    object_spawn_pos_m: tuple[float, float, float]
    object_park_pos_m: tuple[float, float, float]
    target_pos_m: tuple[float, float, float]
    target_park_pos_m: tuple[float, float, float]
    position_tolerance_m: tuple[float, float, float]
    upright_cosine_min: float


def load_station(name: str = "island_south") -> ManipulationStation:
    metadata = yaml.safe_load(NAVIGATION_METADATA.read_text(encoding="utf-8")) or {}
    for raw in metadata.get("manipulation_stations", ()):
        if raw.get("name") == name:
            return ManipulationStation(
                name=name,
                position_xy_m=tuple(float(v) for v in raw["position_xy_m"]),
                yaw_rad=float(raw["yaw_rad"]),
                position_tolerance_m=float(raw["position_tolerance_m"]),
                yaw_tolerance_rad=float(raw["yaw_tolerance_rad"]),
            )
    raise ValueError(f"navigation.yaml 缺少 manipulation station: {name}")


def angle_error(a: float, b: float) -> float:
    return abs(math.atan2(math.sin(a - b), math.cos(a - b)))


def validate_handoff(x: float, y: float, yaw: float, station: ManipulationStation | None = None) -> None:
    station = station or load_station()
    distance = math.hypot(x - station.position_xy_m[0], y - station.position_xy_m[1])
    heading = angle_error(yaw, station.yaw_rad)
    if distance > station.position_tolerance_m or heading > station.yaw_tolerance_rad:
        raise ValueError(
            f"导航位姿尚未满足操作交接条件: position_error={distance:.3f}m, "
            f"yaw_error={heading:.3f}rad"
        )


# 目标 checkpoint 只在 Arena 的静态苹果到盘子任务上 post-train；不把其他物体
# 暴露成“支持任务”，避免将任务外分布行为误报为模型能力。
TASKS = {
    "apple_to_plate": TaskSpec(
        task_id="apple_to_plate",
        instruction="move the apple to the plate",
        object_body="task_apple",
        object_joint="task_apple_free",
        target_body="target_plate_body",
        target_site="target_plate",
        object_spawn_pos_m=(3.18, -0.525, 0.985),
        object_park_pos_m=(-10.0, -10.0, -2.0),
        target_pos_m=(3.54, -0.50, 0.925),
        target_park_pos_m=(-11.0, -10.0, -2.0),
        position_tolerance_m=(0.085, 0.085, 0.045),
        upright_cosine_min=-1.0,
    ),
}

DEFAULT_TASK = "apple_to_plate"
