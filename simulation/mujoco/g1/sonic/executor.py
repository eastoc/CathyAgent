"""闭环路径执行器：读取 MuJoCo 根位姿并周期下发 SONIC 命令。"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable

from ..config import SonicConfig
from ..contracts import NavigationPlan, Pose2D
from .contracts import NavigationExecution, TimedPose2D
from .follower import PathFollower
from .transport import SonicTransport


class NavigationExecutor:
    def __init__(
        self,
        config: SonicConfig,
        *,
        pose_provider: Callable[[], TimedPose2D],
        transport: SonicTransport,
        pose_validator: Callable[[Pose2D], str | None] | None = None,
    ) -> None:
        self.config = config
        self.pose_provider = pose_provider
        self.transport = transport
        self.pose_validator = pose_validator

    def execute(
        self,
        plan: NavigationPlan,
        *,
        cancel: threading.Event | None = None,
    ) -> NavigationExecution:
        cancel = cancel or threading.Event()
        start_time = time.monotonic()
        commands_sent = 0
        motion_started = False
        last_pose = plan.start
        facing = (math.cos(last_pose.yaw), math.sin(last_pose.yaw), 0.0)
        follower = PathFollower(
            plan,
            speed_mps=self.config.slow_walk_speed_mps,
            waypoint_tolerance_m=self.config.waypoint_tolerance_m,
            goal_position_tolerance_m=self.config.goal_position_tolerance_m,
            goal_yaw_tolerance_rad=self.config.goal_yaw_tolerance_rad,
        )
        period = 1.0 / self.config.command_hz

        try:
            self.transport.open()
            # 重发启动帧，覆盖 PUB/SUB 建链初期可能丢失的首帧。
            for _ in range(3):
                if cancel.wait(0.1):
                    return self._result(
                        "cancelled", "CANCELLED", "导航已取消", False,
                        last_pose, start_time, commands_sent,
                    )
                self.transport.start_planner()
                self.transport.idle(facing)

            while True:
                now = time.monotonic()
                if cancel.is_set():
                    return self._result(
                        "cancelled", "CANCELLED", "导航已取消", motion_started,
                        last_pose, start_time, commands_sent,
                    )
                if now - start_time > self.config.execution_timeout_seconds:
                    return self._result(
                        "failed", "EXECUTION_TIMEOUT", "导航执行超时", motion_started,
                        last_pose, start_time, commands_sent,
                    )

                try:
                    sample = self.pose_provider()
                except Exception as exc:
                    return self._result(
                        "failed", "POSE_UNAVAILABLE", f"无法读取 MuJoCo 根位姿: {exc}",
                        motion_started, last_pose, start_time, commands_sent,
                    )
                last_pose = sample.pose
                if now - sample.monotonic_time > self.config.pose_timeout_seconds:
                    return self._result(
                        "failed", "POSE_STALE", "MuJoCo 根位姿反馈已过期", motion_started,
                        last_pose, start_time, commands_sent,
                    )
                if self.pose_validator is not None:
                    invalid_reason = self.pose_validator(last_pose)
                    if invalid_reason:
                        return self._result(
                            "failed", "ROBOT_POSE_INVALID", invalid_reason, motion_started,
                            last_pose, start_time, commands_sent,
                        )


                command = follower.command(last_pose)
                facing = command.facing
                if command.phase == "arrived":
                    return self._result(
                        "succeeded", None, "G1 已到达目标并完成朝向对齐", motion_started,
                        last_pose, start_time, commands_sent,
                    )
                self.transport.send(command)
                commands_sent += 1
                motion_started = True
                cancel.wait(period)
        except Exception as exc:
            return self._result(
                "failed", "SONIC_TRANSPORT_ERROR", f"SONIC 执行失败: {exc}",
                motion_started, last_pose, start_time, commands_sent,
            )
        finally:
            # 连续发三帧，降低 PUB/SUB 丢失单个停止帧后继续沿旧方向走的风险。
            for _ in range(3):
                try:
                    self.transport.idle(facing)
                except Exception:
                    break
                time.sleep(0.01)

    @staticmethod
    def _result(
        status: str,
        error_code: str | None,
        message: str,
        motion_started: bool,
        final_pose: Pose2D,
        start_time: float,
        commands_sent: int,
    ) -> NavigationExecution:
        return NavigationExecution(
            status=status,
            error_code=error_code,
            message=message,
            motion_started=motion_started,
            final_pose=final_pose,
            elapsed_seconds=time.monotonic() - start_time,
            commands_sent=commands_sent,
        )

    def close(self) -> None:
        self.transport.close()


__all__ = ["NavigationExecutor"]
