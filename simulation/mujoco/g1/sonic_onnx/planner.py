"""官方 planner_sonic.onnx 的输入封装、30→50 Hz 重采样和轨迹拼接。"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time

import numpy as np

from .math3d import slerp
from .parameters import DEFAULT_ANGLES, ISAAC_TO_HARDWARE
from .protocol import PlannerCommand


def _direction_change_radians(
    first: tuple[float, float, float], second: tuple[float, float, float]
) -> float:
    first_vector = np.asarray(first[:2], dtype=np.float64)
    second_vector = np.asarray(second[:2], dtype=np.float64)
    first_norm = float(np.linalg.norm(first_vector))
    second_norm = float(np.linalg.norm(second_vector))
    if first_norm < 1e-6 and second_norm < 1e-6:
        return 0.0
    if first_norm < 1e-6 or second_norm < 1e-6:
        return float(np.pi)
    cosine = float(np.dot(first_vector, second_vector) / (first_norm * second_norm))
    return float(np.arccos(np.clip(cosine, -1.0, 1.0)))


def _material_command_change(
    current: PlannerCommand, previous: PlannerCommand | None
) -> bool:
    """忽略路径跟随造成的小幅方向抖动，只响应真正的运动阶段变化。"""
    if previous is None or current.mode != previous.mode:
        return True
    if abs(current.speed - previous.speed) > 0.05:
        return True
    if abs(current.height - previous.height) > 0.05:
        return True
    threshold = np.deg2rad(12.0)
    return (
        _direction_change_radians(current.movement, previous.movement) > threshold
        or _direction_change_radians(current.facing, previous.facing) > threshold
    )


@dataclass
class Motion:
    root_position: np.ndarray       # [N, 3]
    root_quaternion: np.ndarray     # [N, 4], wxyz
    joint_position: np.ndarray      # [N, 29], IsaacLab order
    joint_velocity: np.ndarray      # [N, 29], IsaacLab order

    @property
    def frames(self) -> int:
        return int(self.joint_position.shape[0])

    def clipped_indexes(self, start: int, count: int, step: int) -> np.ndarray:
        return np.minimum(start + np.arange(count) * step, self.frames - 1)


def initial_context(joints_hardware: np.ndarray, height: float = 0.788740) -> np.ndarray:
    context = np.zeros((1, 4, 36), dtype=np.float32)
    context[0, :, 2] = height
    context[0, :, 3] = 1.0
    context[0, :, 7:] = np.asarray(joints_hardware, dtype=np.float32)
    return context


def context_from_motion(motion: Motion, frame: int, lookahead: int = 2) -> np.ndarray:
    """按官方实现，从 50 Hz motion 采样 4 个 30 Hz context frame。"""
    context = np.zeros((1, 4, 36), dtype=np.float32)
    start_time = (frame + lookahead) / 50.0
    for n in range(4):
        f = (start_time + n / 30.0) * 50.0
        f0 = min(int(np.floor(f)), motion.frames - 1)
        f1 = min(f0 + 1, motion.frames - 1)
        alpha = max(0.0, min(1.0, f - f0))
        context[0, n, :3] = (
            (1.0 - alpha) * motion.root_position[f0]
            + alpha * motion.root_position[f1]
        )
        context[0, n, 3:7] = slerp(
            motion.root_quaternion[f0], motion.root_quaternion[f1], alpha
        )
        joints_isaac = (
            (1.0 - alpha) * motion.joint_position[f0]
            + alpha * motion.joint_position[f1]
        )
        # planner qpos 使用硬件/MuJoCo 顺序。
        joints_hardware = np.empty(29, dtype=np.float32)
        joints_hardware[ISAAC_TO_HARDWARE] = joints_isaac
        context[0, n, 7:] = joints_hardware
    return context


def resample_planner_output(qpos: np.ndarray, frames: int) -> Motion:
    if frames < 2 or frames > qpos.shape[1]:
        raise ValueError(f"规划器返回的帧数无效: {frames}")
    source = np.asarray(qpos[0, :frames], dtype=np.float64)
    output_frames = int(np.floor(frames / 30.0 * 50.0))
    positions = np.empty((output_frames, 3), dtype=np.float32)
    quaternions = np.empty((output_frames, 4), dtype=np.float32)
    joints = np.empty((output_frames, 29), dtype=np.float32)
    for out_frame in range(output_frames):
        source_frame = out_frame / 50.0 * 30.0
        f0 = int(np.floor(source_frame))
        f1 = min(f0 + 1, frames - 1)
        alpha = source_frame - f0
        positions[out_frame] = (
            (1.0 - alpha) * source[f0, :3] + alpha * source[f1, :3]
        )
        quaternions[out_frame] = slerp(source[f0, 3:7], source[f1, 3:7], alpha)
        hardware = (1.0 - alpha) * source[f0, 7:] + alpha * source[f1, 7:]
        joints[out_frame] = hardware[ISAAC_TO_HARDWARE]
    velocities = np.zeros_like(joints)
    velocities[:-1] = (joints[1:] - joints[:-1]) * 50.0
    velocities[-1] = velocities[-2]
    return Motion(positions, quaternions, joints, velocities)


def blend_motion(old: Motion, old_frame: int, new: Motion,
                 lookahead: int = 2, blend_frames: int = 8) -> Motion:
    """保留 lookahead 帧，并在 8 帧内淡入新轨迹。"""
    prefix_end = min(old_frame + lookahead, old.frames)
    prefix = slice(old_frame, prefix_end)
    positions = [old.root_position[prefix].copy()]
    quaternions = [old.root_quaternion[prefix].copy()]
    joints = [old.joint_position[prefix].copy()]
    blend_count = min(blend_frames, new.frames)
    new_pos = new.root_position.copy()
    new_quat = new.root_quaternion.copy()
    new_joints = new.joint_position.copy()
    for i in range(blend_count):
        source = min(prefix_end + i, old.frames - 1)
        weight = (i + 1) / blend_count
        new_pos[i] = (1.0 - weight) * old.root_position[source] + weight * new_pos[i]
        new_quat[i] = slerp(old.root_quaternion[source], new_quat[i], weight)
        new_joints[i] = (
            (1.0 - weight) * old.joint_position[source] + weight * new_joints[i]
        )
    positions.append(new_pos)
    quaternions.append(new_quat)
    joints.append(new_joints)
    result_joints = np.concatenate(joints)
    result_vel = np.zeros_like(result_joints)
    result_vel[:-1] = (result_joints[1:] - result_joints[:-1]) * 50.0
    result_vel[-1] = result_vel[-2]
    return Motion(
        np.concatenate(positions), np.concatenate(quaternions),
        result_joints, result_vel,
    )


class PlannerSession:
    def __init__(self, model_path: str, provider: str) -> None:
        import onnxruntime as ort
        self.session = ort.InferenceSession(model_path, providers=[provider])

    def infer(self, context: np.ndarray, command: PlannerCommand,
              random_seed: int = 1234) -> Motion:
        feed = {
            "context_mujoco_qpos": np.asarray(context, dtype=np.float32),
            "target_vel": np.asarray([command.speed], dtype=np.float32),
            "mode": np.asarray([command.mode], dtype=np.int64),
            "movement_direction": np.asarray([command.movement], dtype=np.float32),
            "facing_direction": np.asarray([command.facing], dtype=np.float32),
            "random_seed": np.asarray([random_seed], dtype=np.int64),
            "has_specific_target": np.zeros((1, 1), dtype=np.int64),
            "specific_target_positions": np.zeros((1, 4, 3), dtype=np.float32),
            "specific_target_headings": np.zeros((1, 4), dtype=np.float32),
            "allowed_pred_num_tokens": np.asarray(
                [[1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]], dtype=np.int64
            ),
            "height": np.asarray([command.height], dtype=np.float32),
        }
        output = self.session.run(["mujoco_qpos", "num_pred_frames"], feed)
        return resample_planner_output(output[0], int(output[1].reshape(-1)[0]))


class AsyncPlanner:
    """10 Hz 独立线程；控制环始终消费最近一段可用轨迹。"""
    def __init__(self, session: PlannerSession, *, period: float = 0.1) -> None:
        self.session = session
        self.period = period
        self.motion: Motion | None = None
        self.frame = 0
        self.command = PlannerCommand()
        self.enabled = False
        self.error: Exception | None = None
        self._pending: Motion | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def initialize(self, joints_hardware: np.ndarray) -> None:
        self.motion = self.session.infer(initial_context(joints_hardware), PlannerCommand())
        self.frame = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="sonic-onnx-planner", daemon=True)
        self._thread.start()

    def update_command(self, enabled: bool, command: PlannerCommand) -> None:
        with self._lock:
            self.enabled = enabled
            self.command = command if enabled else PlannerCommand()

    def _run(self) -> None:
        last_command: PlannerCommand | None = None
        last_plan_time = 0.0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                with self._lock:
                    motion = self.motion
                    frame = self.frame
                    command = self.command if self.enabled else PlannerCommand()
                # 官方 planner thread 以 10 Hz 检查输入；慢走/行走默认每 1 s
                # 才执行一次模型，只有 RUN 需要每 0.1 s 重规划。
                interval = 0.1 if command.mode == 3 else 1.0
                elapsed_since_plan = started - last_plan_time
                command_changed = _material_command_change(command, last_command)
                # 慢走轨迹至少持续 0.35 s，避免闭环方向的细微变化不断重置
                # decoder 历史。模式切换立即响应，显著转向受最短间隔保护。
                mode_changed = last_command is None or command.mode != last_command.mode
                should_plan = (
                    last_command is None
                    or elapsed_since_plan >= interval
                    or mode_changed
                    or (command_changed and elapsed_since_plan >= 0.35)
                )
                if motion is not None and should_plan:
                    context = context_from_motion(motion, frame)
                    generated = self.session.infer(context, command)
                    with self._lock:
                        self._pending = generated
                        self.error = None
                    last_command = command
                    last_plan_time = time.monotonic()
            except Exception as exc:
                with self._lock:
                    self.error = exc
            self._stop.wait(max(0.0, self.period - (time.monotonic() - started)))

    def current(self) -> tuple[Motion, int]:
        with self._lock:
            if self.motion is None:
                raise RuntimeError("SONIC planner 尚未初始化")
            if self._pending is not None:
                self.motion = blend_motion(self.motion, self.frame, self._pending)
                self.frame = 0
                self._pending = None
            frame = min(self.frame, self.motion.frames - 1)
            return self.motion, frame

    def advance(self) -> None:
        with self._lock:
            if self.motion is not None:
                self.frame = min(self.frame + 1, self.motion.frames - 1)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


__all__ = [
    "AsyncPlanner", "Motion", "PlannerSession", "blend_motion",
    "context_from_motion", "initial_context", "resample_planner_output",
]
