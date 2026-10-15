"""G1 静态地图导航配置。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from simulation.mujoco.config import MODULE_ROOT, resolve_resource


DEFAULT_G1_ENVIRONMENT = "configs/environments/g1_open_kitchen.yaml"


@dataclass(frozen=True)
class SonicConfig:
    publish_endpoint: str
    bind: bool
    pose_endpoint: str
    pose_topic: str
    command_hz: float
    slow_walk_speed_mps: float
    waypoint_tolerance_m: float
    goal_position_tolerance_m: float
    goal_yaw_tolerance_rad: float
    pose_timeout_seconds: float
    execution_timeout_seconds: float


@dataclass(frozen=True)
class G1NavigationConfig:
    environment_id: str
    scene_path: Path
    navigation_path: Path
    map_version: str
    robot_profile_version: str
    inflation_radius_m: float
    clearance_cost_margin_m: float
    execution_clearance_tolerance_m: float
    render_scale: int
    initial_pose_xy_yaw: tuple[float, float, float]
    execution_enabled: bool
    sonic: SonicConfig


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是 YAML 映射")
    return dict(value)


def load_g1_navigation_config(
    path: str | Path = DEFAULT_G1_ENVIRONMENT,
) -> G1NavigationConfig:
    resolved = resolve_resource(path)
    raw = _mapping(yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}, str(resolved))
    navigation = _mapping(raw.get("navigation"), "navigation")
    robot = _mapping(raw.get("robot"), "robot")
    rendering = _mapping(raw.get("rendering") or {}, "rendering")
    sonic = _mapping(raw.get("sonic") or {}, "sonic")
    initial = raw.get("initial_pose_xy_yaw")
    if not isinstance(initial, list) or len(initial) != 3:
        raise ValueError("initial_pose_xy_yaw 必须包含 x、y、yaw")
    pose = tuple(float(item) for item in initial)
    radius = float(robot.get("inflation_radius_m"))
    margin = float(robot.get("clearance_cost_margin_m", 0.2))
    execution_tolerance = float(
        robot.get("execution_clearance_tolerance_m", min(0.10, radius * 0.25))
    )
    scale = int(rendering.get("scale", 5))
    if not all(
        math.isfinite(item) for item in (*pose, radius, margin, execution_tolerance)
    ):
        raise ValueError("G1 导航数值配置必须为有限数")
    if (
        radius <= 0
        or margin < 0
        or not 0 <= execution_tolerance < radius
        or scale < 1
    ):
        raise ValueError(
            "膨胀半径必须为正数，代价余量非负，执行容差需小于膨胀半径，"
            "渲染倍数至少为 1"
        )
    sonic_config = SonicConfig(
        publish_endpoint=str(sonic.get("publish_endpoint") or "tcp://*:5556"),
        bind=bool(sonic.get("bind", True)),
        pose_endpoint=str(sonic.get("pose_endpoint") or "tcp://localhost:5558"),
        pose_topic=str(sonic.get("pose_topic") or "g1_nav_pose"),
        command_hz=float(sonic.get("command_hz", 20.0)),
        slow_walk_speed_mps=float(sonic.get("slow_walk_speed_mps", 0.35)),
        waypoint_tolerance_m=float(sonic.get("waypoint_tolerance_m", 0.20)),
        goal_position_tolerance_m=float(sonic.get("goal_position_tolerance_m", 0.12)),
        goal_yaw_tolerance_rad=float(sonic.get("goal_yaw_tolerance_rad", 0.15)),
        pose_timeout_seconds=float(sonic.get("pose_timeout_seconds", 0.5)),
        execution_timeout_seconds=float(sonic.get("execution_timeout_seconds", 120.0)),
    )
    positive_sonic = (
        sonic_config.command_hz,
        sonic_config.slow_walk_speed_mps,
        sonic_config.waypoint_tolerance_m,
        sonic_config.goal_position_tolerance_m,
        sonic_config.goal_yaw_tolerance_rad,
        sonic_config.pose_timeout_seconds,
        sonic_config.execution_timeout_seconds,
    )
    if not sonic_config.publish_endpoint.startswith("tcp://"):
        raise ValueError("sonic.publish_endpoint 必须是 tcp:// 地址")
    if not sonic_config.pose_endpoint.startswith("tcp://") or not sonic_config.pose_topic:
        raise ValueError("SONIC 位姿反馈地址或 topic 无效")
    if not all(math.isfinite(value) and value > 0 for value in positive_sonic):
        raise ValueError("SONIC 频率、速度、容差与超时必须是正有限数")
    return G1NavigationConfig(
        environment_id=str(raw.get("environment_id") or "g1_open_kitchen"),
        scene_path=resolve_resource(str(raw.get("model_path"))),
        navigation_path=resolve_resource(str(navigation.get("metadata_path"))),
        map_version=str(navigation.get("map_version") or "map-v1"),
        robot_profile_version=str(robot.get("profile_version") or "g1-walk-v1"),
        inflation_radius_m=radius,
        clearance_cost_margin_m=margin,
        execution_clearance_tolerance_m=execution_tolerance,
        render_scale=scale,
        initial_pose_xy_yaw=pose,  # type: ignore[arg-type]
        execution_enabled=bool(raw.get("execution_enabled", False)),
        sonic=sonic_config,
    )


def load_navigation_metadata(config: G1NavigationConfig) -> dict[str, Any]:
    value = yaml.safe_load(config.navigation_path.read_text(encoding="utf-8")) or {}
    return _mapping(value, str(config.navigation_path))


__all__ = [
    "DEFAULT_G1_ENVIRONMENT",
    "G1NavigationConfig",
    "SonicConfig",
    "load_g1_navigation_config",
    "load_navigation_metadata",
]
