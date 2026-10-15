"""SONIC 编码器/解码器观测构造与 ONNX Runtime 执行。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .math3d import relative_rotation_6d, rotate_inverse
from .parameters import DEFAULT_ANGLES, ISAAC_TO_HARDWARE
from .planner import Motion


@dataclass(frozen=True)
class RobotState:
    quaternion: np.ndarray
    angular_velocity: np.ndarray
    joint_position_hardware: np.ndarray
    joint_velocity_hardware: np.ndarray
    mode_machine: int = 0


@dataclass(frozen=True)
class HistoryEntry:
    angular_velocity: np.ndarray
    joint_position: np.ndarray
    joint_velocity: np.ndarray
    last_action: np.ndarray
    gravity: np.ndarray


class ObservationHistory:
    def __init__(self, length: int = 10) -> None:
        self.length = length
        self.entries: deque[HistoryEntry] = deque(maxlen=length)

    def append(self, state: RobotState, last_action: np.ndarray) -> None:
        joint_position = (
            state.joint_position_hardware[ISAAC_TO_HARDWARE]
            - DEFAULT_ANGLES[ISAAC_TO_HARDWARE]
        )
        joint_velocity = state.joint_velocity_hardware[ISAAC_TO_HARDWARE]
        entry = HistoryEntry(
            np.asarray(state.angular_velocity, dtype=np.float32).copy(),
            np.asarray(joint_position, dtype=np.float32).copy(),
            np.asarray(joint_velocity, dtype=np.float32).copy(),
            np.asarray(last_action, dtype=np.float32).copy(),
            np.asarray(
                rotate_inverse(state.quaternion, np.asarray([0.0, 0.0, -1.0])),
                dtype=np.float32,
            ),
        )
        if not self.entries:
            for _ in range(self.length - 1):
                self.entries.append(entry)
        self.entries.append(entry)

    def policy_tail(self) -> np.ndarray:
        if len(self.entries) != self.length:
            raise RuntimeError("SONIC 观测历史尚未就绪")
        entries = list(self.entries)  # oldest -> newest
        return np.concatenate([
            np.concatenate([entry.angular_velocity for entry in entries]),
            np.concatenate([entry.joint_position for entry in entries]),
            np.concatenate([entry.joint_velocity for entry in entries]),
            np.concatenate([entry.last_action for entry in entries]),
            np.concatenate([entry.gravity for entry in entries]),
        ]).astype(np.float32, copy=False)


def encoder_observation(motion: Motion, frame: int, base_quaternion: np.ndarray) -> np.ndarray:
    """构造 release encoder 的 1762 维输入，仅填 g1 mode 所需字段。"""
    obs = np.zeros((1, 1762), dtype=np.float32)
    obs[0, 0] = 0.0  # encoder mode: g1
    indexes = motion.clipped_indexes(frame, 10, 5)
    obs[0, 4:294] = motion.joint_position[indexes].reshape(-1)
    obs[0, 294:584] = motion.joint_velocity[indexes].reshape(-1)
    orientations = [
        relative_rotation_6d(base_quaternion, motion.root_quaternion[index])
        for index in indexes
    ]
    obs[0, 601:661] = np.asarray(orientations, dtype=np.float32).reshape(-1)
    return obs


class PolicySessions:
    def __init__(self, encoder_path: str, decoder_path: str, provider: str) -> None:
        import onnxruntime as ort
        self.encoder = ort.InferenceSession(encoder_path, providers=[provider])
        self.decoder = ort.InferenceSession(decoder_path, providers=[provider])
        self._validate()

    def _validate(self) -> None:
        encoder_input = self.encoder.get_inputs()[0]
        decoder_input = self.decoder.get_inputs()[0]
        if encoder_input.name != "obs_dict" or encoder_input.shape[-1] != 1762:
            raise RuntimeError(f"encoder 输入不兼容: {encoder_input.name} {encoder_input.shape}")
        if decoder_input.name != "obs_dict" or decoder_input.shape[-1] != 994:
            raise RuntimeError(f"decoder 输入不兼容: {decoder_input.name} {decoder_input.shape}")

    def infer(self, motion: Motion, frame: int, state: RobotState,
              history: ObservationHistory) -> np.ndarray:
        encoder_input = encoder_observation(motion, frame, state.quaternion)
        token = self.encoder.run(None, {"obs_dict": encoder_input})[0]
        observation = np.concatenate([token.reshape(-1), history.policy_tail()])
        if observation.size != 994:
            raise RuntimeError(f"decoder observation 应为 994 维，实际 {observation.size}")
        action = self.decoder.run(
            None, {"obs_dict": observation.reshape(1, -1).astype(np.float32)}
        )[0]
        return np.asarray(action, dtype=np.float32).reshape(29)


__all__ = [
    "HistoryEntry", "ObservationHistory", "PolicySessions", "RobotState",
    "encoder_observation",
]
