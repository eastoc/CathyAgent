"""G1 导航各层之间的不可变数据契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    yaw: float

    @property
    def xy(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True)
class NavigationGoal:
    x: float
    y: float
    yaw: float

    @property
    def xy(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True)
class Landmark:
    name: str
    label: str
    geom: str
    center_xy_m: tuple[float, float]
    bounds_xy_m: tuple[tuple[float, float], tuple[float, float]]
    navigable_surface: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MapContext:
    map_version: str
    robot_profile_version: str
    frame: str
    resolution_m: float
    origin_xy_m: tuple[float, float]
    bounds_min_xy_m: tuple[float, float]
    bounds_max_xy_m: tuple[float, float]
    inflation_radius_m: float
    ground_mask: np.ndarray
    occupied: np.ndarray
    traversable: np.ndarray
    clearance_m: np.ndarray
    component_ids: np.ndarray
    landmarks: tuple[Landmark, ...]
    obstacle_name_by_cell: np.ndarray

    @property
    def shape(self) -> tuple[int, int]:
        return self.occupied.shape


@dataclass(frozen=True)
class ReachabilityResult:
    reachable: bool
    error_code: str | None
    message: str
    start_cell: tuple[int, int] | None = None
    goal_cell: tuple[int, int] | None = None
    blocking_object: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NavigationPlan:
    map_version: str
    robot_profile_version: str
    start: Pose2D
    goal: NavigationGoal
    cells: tuple[tuple[int, int], ...]
    waypoints_xy_m: tuple[tuple[float, float], ...]
    length_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_version": self.map_version,
            "robot_profile_version": self.robot_profile_version,
            "start": asdict(self.start),
            "goal": asdict(self.goal),
            "waypoints_xy_m": [list(point) for point in self.waypoints_xy_m],
            "length_m": self.length_m,
        }


@dataclass(frozen=True)
class MapObservation:
    metadata: dict[str, Any]
    png: bytes
    width: int
    height: int


@dataclass(frozen=True)
class PreparedNavigation:
    reachability: ReachabilityResult
    plan: NavigationPlan | None

    @property
    def accepted(self) -> bool:
        return self.reachability.reachable and self.plan is not None
