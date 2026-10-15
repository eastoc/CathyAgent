"""把 A* 折线路径转换为 SONIC 世界系移动与朝向命令。"""

from __future__ import annotations

import math

from ..contracts import NavigationPlan, Pose2D
from .contracts import SonicCommand

IDLE = 0
SLOW_WALK = 1


def angle_error(target: float, actual: float) -> float:
    return math.atan2(math.sin(target - actual), math.cos(target - actual))


class PathFollower:
    def __init__(
        self,
        plan: NavigationPlan,
        *,
        speed_mps: float,
        waypoint_tolerance_m: float,
        goal_position_tolerance_m: float,
        goal_yaw_tolerance_rad: float,
    ) -> None:
        if not plan.waypoints_xy_m:
            raise ValueError("路径至少需要一个路点")
        self.plan = plan
        self.speed_mps = speed_mps
        self.waypoint_tolerance_m = waypoint_tolerance_m
        self.goal_position_tolerance_m = goal_position_tolerance_m
        self.goal_yaw_tolerance_rad = goal_yaw_tolerance_rad
        self._index = min(1, len(plan.waypoints_xy_m) - 1)

    def _advance(self, pose: Pose2D) -> tuple[float, float]:
        points = self.plan.waypoints_xy_m
        while self._index < len(points) - 1:
            point = points[self._index]
            if math.hypot(point[0] - pose.x, point[1] - pose.y) > self.waypoint_tolerance_m:
                break
            self._index += 1
        return points[self._index]

    def command(self, pose: Pose2D) -> SonicCommand:
        target = self._advance(pose)
        dx, dy = target[0] - pose.x, target[1] - pose.y
        distance = math.hypot(dx, dy)
        final_segment = self._index == len(self.plan.waypoints_xy_m) - 1

        if final_segment and distance <= self.goal_position_tolerance_m:
            final_yaw = self.plan.goal.yaw
            facing = (math.cos(final_yaw), math.sin(final_yaw), 0.0)
            if abs(angle_error(final_yaw, pose.yaw)) <= self.goal_yaw_tolerance_rad:
                return SonicCommand(IDLE, (0.0, 0.0, 0.0), facing, -1.0, "arrived")
            # movement 为零时，SONIC 使用 facing 执行原地转向。
            return SonicCommand(SLOW_WALK, (0.0, 0.0, 0.0), facing, 0.0, "align")

        if distance <= 1e-9:
            direction = (math.cos(pose.yaw), math.sin(pose.yaw), 0.0)
        else:
            direction = (dx / distance, dy / distance, 0.0)
        return SonicCommand(SLOW_WALK, direction, direction, self.speed_mps, "walk")


__all__ = ["IDLE", "SLOW_WALK", "PathFollower", "angle_error"]
