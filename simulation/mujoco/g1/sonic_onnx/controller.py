"""通过 DDS 驱动 MuJoCo G1 的 SONIC ONNX Runtime 控制器。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time

import numpy as np

from .parameters import (
    ACTION_SCALE, DAMPING, DEFAULT_ANGLES, HARDWARE_TO_ISAAC, STIFFNESS,
)
from .planner import AsyncPlanner, PlannerSession
from .policy import ObservationHistory, PolicySessions, RobotState
from .protocol import ZmqCommandReceiver


@dataclass(frozen=True)
class SonicOnnxPaths:
    encoder: Path
    decoder: Path
    planner: Path

    @classmethod
    def from_root(cls, root: Path) -> "SonicOnnxPaths":
        deploy = root / "gear_sonic_deploy"
        return cls(
            deploy / "policy/release/model_encoder.onnx",
            deploy / "policy/release/model_decoder.onnx",
            deploy / "planner/target_vel/V2/planner_sonic.onnx",
        )

    def validate(self) -> None:
        missing = [str(path) for path in (self.encoder, self.decoder, self.planner)
                   if not path.is_file()]
        if missing:
            raise FileNotFoundError("缺少 SONIC ONNX 模型: " + ", ".join(missing))


class SonicOnnxController:
    def __init__(
        self,
        paths: SonicOnnxPaths,
        *,
        provider: str = "CPUExecutionProvider",
        command_endpoint: str = "tcp://127.0.0.1:5556",
        control_hz: float = 50.0,
        planner_hz: float = 10.0,
        startup_seconds: float = 2.0,
        lowstate_timeout: float = 0.5,
    ) -> None:
        paths.validate()
        self.paths = paths
        self.provider = provider
        self.control_period = 1.0 / control_hz
        self.startup_seconds = startup_seconds
        self.lowstate_timeout = lowstate_timeout
        self.receiver = ZmqCommandReceiver(command_endpoint)
        self.policy = PolicySessions(str(paths.encoder), str(paths.decoder), provider)
        self.planner = AsyncPlanner(
            PlannerSession(str(paths.planner), provider), period=1.0 / planner_hz
        )
        self.history = ObservationHistory(10)
        self.last_action = np.zeros(29, dtype=np.float32)
        self._state: RobotState | None = None
        self._state_time = 0.0
        self._state_lock = threading.Lock()
        self._publisher = None
        self._subscriber = None
        self._low_cmd = None
        self._crc = None
        self._running = True

    def _state_callback(self, message: object) -> None:
        state = RobotState(
            quaternion=np.asarray(message.imu_state.quaternion, dtype=np.float64),
            angular_velocity=np.asarray(message.imu_state.gyroscope, dtype=np.float32),
            joint_position_hardware=np.asarray(
                [message.motor_state[i].q for i in range(29)], dtype=np.float32
            ),
            joint_velocity_hardware=np.asarray(
                [message.motor_state[i].dq for i in range(29)], dtype=np.float32
            ),
            mode_machine=int(message.mode_machine),
        )
        with self._state_lock:
            self._state = state
            self._state_time = time.monotonic()

    def _latest_state(self) -> RobotState:
        with self._state_lock:
            state, timestamp = self._state, self._state_time
        if state is None:
            raise RuntimeError("尚未收到 rt/lowstate")
        if time.monotonic() - timestamp > self.lowstate_timeout:
            raise RuntimeError("rt/lowstate 已超时")
        return state

    def setup_dds(self, *, domain_id: int = 0, interface: str | None = None) -> None:
        from unitree_sdk2py.core.channel import (
            ChannelFactoryInitialize, ChannelPublisher, ChannelSubscriber,
        )
        from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_, LowState_
        from unitree_sdk2py.utils.crc import CRC

        if interface:
            ChannelFactoryInitialize(domain_id, interface)
        else:
            ChannelFactoryInitialize(domain_id)
        self._low_cmd = unitree_hg_msg_dds__LowCmd_()
        self._crc = CRC()
        self._publisher = ChannelPublisher("rt/lowcmd", LowCmd_)
        self._publisher.Init()
        self._subscriber = ChannelSubscriber("rt/lowstate", LowState_)
        self._subscriber.Init(self._state_callback, 10)

    def _publish_targets(self, state: RobotState, targets: np.ndarray,
                         *, damping_only: bool = False) -> None:
        command = self._low_cmd
        command.mode_pr = 0
        command.mode_machine = state.mode_machine
        for i in range(29):
            motor = command.motor_cmd[i]
            motor.mode = 1
            motor.tau = 0.0
            motor.q = float(targets[i])
            motor.dq = 0.0
            motor.kp = 0.0 if damping_only else float(STIFFNESS[i])
            motor.kd = 8.0 if damping_only else float(DAMPING[i])
        command.crc = self._crc.Crc(command)
        self._publisher.Write(command)

    def _move_to_default(self) -> RobotState:
        deadline = time.monotonic() + 10.0
        while self._state is None and time.monotonic() < deadline:
            time.sleep(0.01)
        state = self._latest_state()
        initial = state.joint_position_hardware.copy()
        started = time.monotonic()
        while self._running:
            state = self._latest_state()
            ratio = min(1.0, (time.monotonic() - started) / self.startup_seconds)
            target = (1.0 - ratio) * initial + ratio * DEFAULT_ANGLES
            self._publish_targets(state, target)
            if ratio >= 1.0:
                return state
            time.sleep(self.control_period)
        return state

    def run(self, *, domain_id: int = 0, interface: str | None = None) -> None:
        self.receiver.open()
        self.setup_dds(domain_id=domain_id, interface=interface)
        print(f"SONIC ONNX 已加载，provider={self.provider}，等待 LowState...")
        state = self._move_to_default()
        print("G1 默认站姿完成，初始化 10 Hz planner...")
        self.planner.initialize(state.joint_position_hardware)
        self.planner.start()
        print("SONIC ONNX 控制器就绪；等待 navigate 的 start/planner 消息")

        next_tick = time.monotonic()
        try:
            while self._running:
                self.receiver.poll()
                active, command = self.receiver.snapshot()
                self.planner.update_command(active, command)
                state = self._latest_state()
                if active:
                    motion, frame = self.planner.current()
                    self.history.append(state, self.last_action)
                    action = self.policy.infer(motion, frame, state, self.history)
                    self.last_action = action
                    targets = (
                        DEFAULT_ANGLES
                        + action[HARDWARE_TO_ISAAC] * ACTION_SCALE
                    )
                    self._publish_targets(state, targets)
                    self.planner.advance()
                else:
                    self._publish_targets(state, DEFAULT_ANGLES)
                if self.planner.error is not None:
                    raise RuntimeError(f"planner 推理失败: {self.planner.error}")
                next_tick += self.control_period
                delay = next_tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.monotonic()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self) -> None:
        self._running = False
        self.planner.close()
        self.receiver.close()
        try:
            state = self._latest_state()
            self._publish_targets(state, state.joint_position_hardware, damping_only=True)
        except Exception:
            pass


__all__ = ["SonicOnnxController", "SonicOnnxPaths"]
