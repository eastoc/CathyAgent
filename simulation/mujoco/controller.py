"""Z1 末端 IK、关节插值和夹爪控制。"""

from __future__ import annotations

import math
import threading
from typing import Sequence

import numpy as np

from simulation.contracts import ExecutionResult, Pose

from .backend import MujocoBackend
from .config import ControllerConfig
from .errors import MujocoCancelledError, MujocoExecutionError, MujocoIKError


def _quaternion_to_matrix(values: Sequence[float]) -> np.ndarray:
    quaternion = np.asarray(values, dtype=float)
    if quaternion.shape != (4,) or not np.isfinite(quaternion).all():
        raise MujocoExecutionError("quaternion_wxyz 必须包含 4 个有限数")
    norm = float(np.linalg.norm(quaternion))
    if norm < 1e-12:
        raise MujocoExecutionError("quaternion_wxyz 不能为零四元数")
    w, x, y, z = quaternion / norm
    return np.asarray(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=float,
    )


def _matrix_to_quaternion(matrix: np.ndarray) -> tuple[float, float, float, float]:
    trace = float(np.trace(matrix))
    if trace > 0:
        scale = math.sqrt(trace + 1.0) * 2
        w = 0.25 * scale
        x = (matrix[2, 1] - matrix[1, 2]) / scale
        y = (matrix[0, 2] - matrix[2, 0]) / scale
        z = (matrix[1, 0] - matrix[0, 1]) / scale
    else:
        index = int(np.argmax(np.diag(matrix)))
        if index == 0:
            scale = math.sqrt(1 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2]) * 2
            w = (matrix[2, 1] - matrix[1, 2]) / scale
            x = 0.25 * scale
            y = (matrix[0, 1] + matrix[1, 0]) / scale
            z = (matrix[0, 2] + matrix[2, 0]) / scale
        elif index == 1:
            scale = math.sqrt(1 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2]) * 2
            w = (matrix[0, 2] - matrix[2, 0]) / scale
            x = (matrix[0, 1] + matrix[1, 0]) / scale
            y = 0.25 * scale
            z = (matrix[1, 2] + matrix[2, 1]) / scale
        else:
            scale = math.sqrt(1 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1]) * 2
            w = (matrix[1, 0] - matrix[0, 1]) / scale
            x = (matrix[0, 2] + matrix[2, 0]) / scale
            y = (matrix[1, 2] + matrix[2, 1]) / scale
            z = 0.25 * scale
    quaternion = np.asarray([w, x, y, z], dtype=float)
    quaternion /= np.linalg.norm(quaternion)
    if quaternion[0] < 0:
        quaternion = -quaternion
    return tuple(float(value) for value in quaternion)  # type: ignore[return-value]


def _orientation_error_rad(current: np.ndarray, target: np.ndarray) -> float:
    cosine = float(np.clip((np.trace(current.T @ target) - 1) / 2, -1, 1))
    return math.acos(cosine)


class Z1Controller:
    def __init__(self, backend: MujocoBackend, config: ControllerConfig) -> None:
        self.backend = backend
        self.config = config

    def _check_cancelled(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise MujocoCancelledError("动作已取消")

    def _ticks(self, seconds: float) -> int:
        if (
            not math.isfinite(seconds)
            or seconds <= 0
            or seconds > self.config.max_duration_seconds
        ):
            raise MujocoExecutionError(
                f"duration_seconds 必须在 (0, {self.config.max_duration_seconds}]"
            )
        return max(1, math.ceil(seconds * self.config.control_hz))

    def hold(self, seconds: float, *, cancel: threading.Event | None = None) -> None:
        for _ in range(self._ticks(seconds)):
            self._check_cancelled(cancel)
            self.backend.step_control(self.backend.controls())

    def move_joints(
        self,
        target: Sequence[float],
        *,
        seconds: float,
        cancel: threading.Event | None = None,
    ) -> None:
        target_control = self.backend.validate_control(target)
        initial = self.backend.controls()
        ticks = self._ticks(seconds)
        for index in range(1, ticks + 1):
            self._check_cancelled(cancel)
            ratio = index / ticks
            blend = 10 * ratio**3 - 15 * ratio**4 + 6 * ratio**5
            self.backend.step_control(initial + blend * (target_control - initial))

    def set_gripper(
        self,
        opening: float,
        *,
        cancel: threading.Event | None = None,
    ) -> None:
        if not math.isfinite(opening) or not 0 <= opening <= 1:
            raise MujocoExecutionError("gripper opening 必须在 [0, 1]")
        robot = self.backend.config.robot
        target = self.backend.controls()
        target[-1] = (
            robot.gripper_close_control
            + opening * (robot.gripper_open_control - robot.gripper_close_control)
        )
        self.move_joints(target, seconds=0.7, cancel=cancel)
        self.hold(0.3, cancel=cancel)

    def _target_world(
        self,
        *,
        frame: str,
        position: Sequence[float],
        quaternion_wxyz: Sequence[float] | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        point = np.asarray(position, dtype=float)
        if point.shape != (3,) or not np.isfinite(point).all():
            raise MujocoExecutionError("position 必须包含 3 个有限数")
        robot = self.backend.config.robot
        if frame == robot.world_frame:
            world_position = point
            world_rotation = (
                np.asarray(self.config.default_down_rotation).reshape(3, 3)
                if quaternion_wxyz is None
                else _quaternion_to_matrix(quaternion_wxyz)
            )
        elif frame == robot.base_body:
            base_position, base_rotation = self.backend.base_pose()
            world_position = base_position + base_rotation @ point
            world_rotation = (
                base_rotation
                @ np.asarray(self.config.default_down_rotation).reshape(3, 3)
                if quaternion_wxyz is None
                else base_rotation @ _quaternion_to_matrix(quaternion_wxyz)
            )
        else:
            raise MujocoExecutionError(
                f"不支持 frame={frame!r}；允许 {robot.world_frame!r}/{robot.base_body!r}"
            )
        low = np.asarray(robot.workspace_min)
        high = np.asarray(robot.workspace_max)
        if np.any(world_position < low) or np.any(world_position > high):
            raise MujocoExecutionError(
                f"目标 {world_position.tolist()} 超出 world workspace"
            )
        return world_position, world_rotation

    def solve_ik(self, position: np.ndarray, rotation: np.ndarray) -> np.ndarray:
        backend = self.backend
        model, data, mujoco = backend.model, backend.data, backend.mujoco
        scratch = mujoco.MjData(model)
        scratch.qpos[:] = data.qpos
        arm_ids = backend.joint_ids[:-1]
        arm_qadr = backend.qpos_addresses[:-1]
        arm_vadr = backend.dof_addresses[:-1]
        lower, upper = model.jnt_range[arm_ids].T
        base_position, _ = backend.base_pose()
        yaw = math.atan2(
            position[1] - base_position[1],
            position[0] - base_position[0],
        )
        seeds = [backend.robot_qpos()[:-1]]
        for configured in self.config.ik_seeds:
            seed = np.asarray(configured, dtype=float).copy()
            seed[0] = yaw
            seeds.append(seed)
        jacp = np.zeros((3, model.nv))
        jacr = np.zeros((3, model.nv))
        best_error = float("inf")
        for seed in seeds:
            qpos = np.clip(seed.copy(), lower + 0.005, upper - 0.005)
            for _ in range(self.config.max_iterations):
                scratch.qpos[arm_qadr] = qpos
                mujoco.mj_forward(model, scratch)
                current_rotation = scratch.site_xmat[backend.tcp_id].reshape(3, 3)
                position_delta = position - scratch.site_xpos[backend.tcp_id]
                rotation_delta = sum(
                    np.cross(current_rotation[:, index], rotation[:, index])
                    for index in range(3)
                ) / 2
                best_error = min(best_error, float(np.linalg.norm(position_delta)))
                if (
                    np.linalg.norm(position_delta) < self.config.position_tolerance_m
                    and _orientation_error_rad(current_rotation, rotation)
                    < self.config.orientation_tolerance_rad
                ):
                    return qpos
                weight = self.config.rotation_weight
                error = np.r_[position_delta, weight * rotation_delta]
                mujoco.mj_jacSite(model, scratch, jacp, jacr, backend.tcp_id)
                jacobian = np.vstack(
                    [jacp[:, arm_vadr], weight * jacr[:, arm_vadr]]
                )
                damping = self.config.damping
                delta = jacobian.T @ np.linalg.solve(
                    jacobian @ jacobian.T + damping * np.eye(6),
                    error,
                )
                scale = min(
                    1.0,
                    self.config.interpolation_step_limit
                    / (float(np.linalg.norm(delta)) + 1e-12),
                )
                qpos = np.clip(qpos + scale * delta, lower + 0.005, upper - 0.005)
        raise MujocoIKError(
            f"TCP 目标不可达: {position.tolist()}，最佳位置误差 {best_error:.4f} m"
        )

    def move_ee(
        self,
        *,
        frame: str,
        position: Sequence[float],
        quaternion_wxyz: Sequence[float] | None,
        duration_seconds: float,
        snapshot_id: int,
        cancel: threading.Event | None = None,
    ) -> ExecutionResult:
        world_position, world_rotation = self._target_world(
            frame=frame,
            position=position,
            quaternion_wxyz=quaternion_wxyz,
        )
        target_control = self.backend.controls()
        solution = self.solve_ik(world_position, world_rotation)
        current_rotation = self.backend.tcp_rotation()
        if np.linalg.norm(current_rotation - world_rotation) > 0.08:
            target_control[:-1] = solution
            self.move_joints(target_control, seconds=duration_seconds, cancel=cancel)
        else:
            initial = self.backend.tcp_position()
            ticks = self._ticks(duration_seconds)
            for index in range(1, ticks + 1):
                self._check_cancelled(cancel)
                ratio = index / ticks
                blend = 10 * ratio**3 - 15 * ratio**4 + 6 * ratio**5
                waypoint = initial + blend * (world_position - initial)
                target_control[:-1] = self.solve_ik(waypoint, world_rotation)
                self.backend.step_control(target_control)
        self.hold(0.25, cancel=cancel)
        actual_position = self.backend.tcp_position()
        actual_rotation = self.backend.tcp_rotation()
        position_error = float(np.linalg.norm(actual_position - world_position))
        orientation_error = _orientation_error_rad(actual_rotation, world_rotation)
        reached = (
            position_error <= self.config.execution_position_tolerance_m
            and orientation_error <= self.config.execution_orientation_tolerance_rad
        )
        actual_pose = Pose(
            frame=self.backend.config.robot.world_frame,
            position=tuple(float(value) for value in actual_position),
            quaternion_wxyz=_matrix_to_quaternion(actual_rotation),
        )
        return ExecutionResult(
            status="succeeded" if reached else "failed",
            reached=reached,
            sim_time=self.backend.sim_time,
            snapshot_id=snapshot_id,
            actual_pose=actual_pose,
            position_error_m=position_error,
            orientation_error_rad=orientation_error,
            error_code=None if reached else "target_not_reached",
            message="目标已到达" if reached else "执行结束但未达到目标容差",
        )
