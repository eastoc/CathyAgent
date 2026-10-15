"""G1 地图查看、强制可达性校验和路径准备服务。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
import math

from .config import G1NavigationConfig
from .contracts import MapObservation, NavigationGoal, Pose2D, PreparedNavigation
from .navigation.map import StaticMapBuilder, cell_to_world, world_to_cell
from .navigation.map_view import MapViewService
from .navigation.planner import AStarPlanner
from .navigation.reachability import ReachabilityChecker
from .sonic.contracts import NavigationExecution
from .sonic.executor import NavigationExecutor


class G1NavigationService:
    def __init__(
        self,
        config: G1NavigationConfig,
        *,
        pose_provider: Callable[[], Pose2D] | None = None,
        executor: NavigationExecutor | None = None,
    ) -> None:
        self.config = config
        self.context = StaticMapBuilder(config).build()
        initial = config.initial_pose_xy_yaw
        self._pose_provider = pose_provider or (lambda: Pose2D(*initial))
        self.executor = executor
        self._checker = ReachabilityChecker()
        self._planner = AStarPlanner(config.clearance_cost_margin_m)
        self._view = MapViewService(self.context, scale=config.render_scale)
        self._last_plan = None

    def current_pose(self) -> Pose2D:
        return self._pose_provider()

    def get_map(self) -> MapObservation:
        return self._view.render(self.current_pose(), self._last_plan)

    def prepare(self, goal: NavigationGoal) -> PreparedNavigation:
        start = self.current_pose()
        planning_start = self._project_recoverable_start(start)
        reachability = self._checker.check(self.context, planning_start, goal)
        if not reachability.reachable:
            return PreparedNavigation(reachability, None)
        plan = self._planner.plan(self.context, planning_start, goal)
        if plan is None:
            return PreparedNavigation(
                type(reachability)(
                    False,
                    "NO_PATH",
                    "目标通过点可达性检查，但 A* 未生成有效路径",
                    reachability.start_cell,
                    reachability.goal_cell,
                ),
                None,
            )
        if planning_start != start:
            recovery_distance = math.dist(start.xy, planning_start.xy)
            plan = replace(
                plan,
                start=start,
                # 保留投影起点作为第一个恢复点，避免从安全缓冲区直接切向远处折点。
                waypoints_xy_m=(start.xy, *plan.waypoints_xy_m),
                length_m=plan.length_m + recovery_distance,
            )
        self._last_plan = plan
        return PreparedNavigation(reachability, plan)

    def _project_recoverable_start(self, start: Pose2D) -> Pose2D:
        cell = world_to_cell(self.context, start.xy)
        if cell is None or self.context.traversable[cell]:
            return start
        if self.validate_execution_pose(start) is not None:
            return start
        search_cells = math.ceil(
            self.config.execution_clearance_tolerance_m / self.context.resolution_m
        ) + 1
        maximum_projection_m = (
            self.config.execution_clearance_tolerance_m
            + self.context.resolution_m * math.sqrt(2.0) / 2.0
        )
        best: tuple[float, tuple[int, int]] | None = None
        for dy in range(-search_cells, search_cells + 1):
            for dx in range(-search_cells, search_cells + 1):
                candidate = cell[0] + dy, cell[1] + dx
                if not (
                    0 <= candidate[0] < self.context.shape[0]
                    and 0 <= candidate[1] < self.context.shape[1]
                    and self.context.traversable[candidate]
                ):
                    continue
                xy = cell_to_world(self.context, candidate)
                distance = math.dist(start.xy, xy)
                if distance > maximum_projection_m:
                    continue
                if best is None or distance < best[0]:
                    best = distance, candidate
        if best is None:
            return start
        xy = cell_to_world(self.context, best[1])
        return Pose2D(xy[0], xy[1], start.yaw)

    def validate_execution_pose(self, pose: Pose2D) -> str | None:
        cell = world_to_cell(self.context, pose.xy)
        if cell is None:
            return "机器人根位姿已离开静态地图范围"
        if self.context.occupied[cell]:
            return "机器人根位姿进入了实体障碍区域"
        clearance = float(self.context.clearance_m[cell])
        hard_clearance = (
            self.config.inflation_radius_m
            - self.config.execution_clearance_tolerance_m
        )
        if clearance < hard_clearance:
            return (
                "机器人过度侵入障碍安全缓冲区: "
                f"clearance={clearance:.2f}m < {hard_clearance:.2f}m"
            )
        return None

    def execute(
        self,
        prepared: PreparedNavigation,
        *,
        cancel: threading.Event | None = None,
    ) -> NavigationExecution:
        if not prepared.accepted or prepared.plan is None:
            raise ValueError("只有通过可达性检测且完成规划的路径才能执行")
        if self.executor is None:
            raise RuntimeError("SONIC 执行器未配置")
        return self.executor.execute(prepared.plan, cancel=cancel)

    def close(self) -> None:
        if self.executor is not None:
            self.executor.close()


__all__ = ["G1NavigationService"]
