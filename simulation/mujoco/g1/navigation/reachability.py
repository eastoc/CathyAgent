"""导航执行前的强制地面可达性检测。"""

from __future__ import annotations

import math

import numpy as np

from ..contracts import MapContext, NavigationGoal, Pose2D, ReachabilityResult
from .map import world_to_cell


class ReachabilityChecker:
    def check(
        self,
        context: MapContext,
        start: Pose2D,
        goal: NavigationGoal,
    ) -> ReachabilityResult:
        values = (start.x, start.y, start.yaw, goal.x, goal.y, goal.yaw)
        if not all(math.isfinite(value) for value in values):
            return ReachabilityResult(False, "INVALID_ARGUMENT", "坐标与朝向必须是有限数")
        start_cell = world_to_cell(context, start.xy)
        goal_cell = world_to_cell(context, goal.xy)
        if start_cell is None:
            return ReachabilityResult(False, "START_INVALID", "机器人当前位置不在地图范围内")
        if goal_cell is None:
            return ReachabilityResult(
                False,
                "OUT_OF_BOUNDS",
                "目标坐标不在地图范围内",
                start_cell=start_cell,
            )
        if not context.ground_mask[start_cell] or not context.traversable[start_cell]:
            return ReachabilityResult(
                False,
                "START_INVALID",
                "机器人当前位置不能建立合法的地面导航起点",
                start_cell=start_cell,
                goal_cell=goal_cell,
            )
        if not context.ground_mask[goal_cell]:
            return ReachabilityResult(
                False,
                "NOT_WALKABLE",
                "目标位置没有可行走地面支撑",
                start_cell=start_cell,
                goal_cell=goal_cell,
            )
        if context.occupied[goal_cell]:
            blocking = str(context.obstacle_name_by_cell[goal_cell]) or None
            return ReachabilityResult(
                False,
                "GOAL_OCCUPIED",
                "目标位于实体障碍占据区域，请选择相邻的可达地面位置",
                start_cell=start_cell,
                goal_cell=goal_cell,
                blocking_object=blocking,
            )
        if not context.traversable[goal_cell]:
            return ReachabilityResult(
                False,
                "INSUFFICIENT_CLEARANCE",
                "目标点本身空闲，但不满足 G1 膨胀半径要求",
                start_cell=start_cell,
                goal_cell=goal_cell,
                blocking_object=self._nearest_obstacle(context, goal_cell),
            )
        start_component = int(context.component_ids[start_cell])
        goal_component = int(context.component_ids[goal_cell])
        if start_component < 0 or goal_component != start_component:
            return ReachabilityResult(
                False,
                "GOAL_DISCONNECTED",
                "目标地面与机器人当前位置不在同一可通行区域",
                start_cell=start_cell,
                goal_cell=goal_cell,
            )
        return ReachabilityResult(
            True,
            None,
            "目标位于与机器人连通的可通行地面",
            start_cell=start_cell,
            goal_cell=goal_cell,
        )

    @staticmethod
    def _nearest_obstacle(context: MapContext, cell: tuple[int, int]) -> str | None:
        occupied = np.argwhere(context.occupied)
        if not len(occupied):
            return None
        distances = np.square(occupied - np.asarray(cell)).sum(axis=1)
        nearest = tuple(int(value) for value in occupied[int(np.argmin(distances))])
        return str(context.obstacle_name_by_cell[nearest]) or None


__all__ = ["ReachabilityChecker"]
