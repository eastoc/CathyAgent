"""MuJoCo 与 SONIC ONNX 的同进程内存适配器。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .controller import SonicOnnxPaths
from .parameters import (
    ACTION_SCALE, DAMPING, DEFAULT_ANGLES, HARDWARE_TO_ISAAC, STIFFNESS,
)
from .planner import AsyncPlanner, PlannerSession
from .policy import ObservationHistory, PolicySessions, RobotState
from .protocol import PlannerCommand, ZmqCommandReceiver


@dataclass
class InMemoryMotorCommand:
    mode: int = 1
    tau: float = 0.0
    q: float = 0.0
    dq: float = 0.0
    kp: float = 0.0
    kd: float = 0.0


@dataclass
class InMemoryLowCommand:
    motor_cmd: list[InMemoryMotorCommand] = field(
        default_factory=lambda: [InMemoryMotorCommand() for _ in range(29)]
    )


class InMemoryUnitreeBridge:
    """满足官方 DefaultEnv 所需接口，不创建任何 DDS channel。"""

    def __init__(self) -> None:
        self.num_body_motor = 29
        self.num_hand_motor = 0
        self.use_sensor = False
        self.low_cmd = InMemoryLowCommand()
        self.joystick = None

    def PublishLowState(self, _observation: dict[str, object]) -> None:
        return


class InProcessSonicController:
    """由 MuJoCo 主循环每 4 个仿真步调用一次的 50 Hz SONIC 控制器。"""

    def __init__(
        self,
        paths: SonicOnnxPaths,
        bridge: InMemoryUnitreeBridge,
        *,
        provider: str = "CPUExecutionProvider",
        command_endpoint: str | None = "tcp://127.0.0.1:5556",
        control_hz: float = 50.0,
        planner_hz: float = 10.0,
        startup_seconds: float = 2.0,
        forced_command: PlannerCommand | None = None,
    ) -> None:
        paths.validate()
        self.bridge = bridge
        self.receiver = (
            ZmqCommandReceiver(command_endpoint) if command_endpoint is not None else None
        )
        self.policy = PolicySessions(str(paths.encoder), str(paths.decoder), provider)
        self.planner = AsyncPlanner(
            PlannerSession(str(paths.planner), provider), period=1.0 / planner_hz
        )
        self.history = ObservationHistory(10)
        self.last_action = np.zeros(29, dtype=np.float32)
        self.startup_ticks = max(1, round(startup_seconds * control_hz))
        self.tick_count = 0
        self.initial_joint_position: np.ndarray | None = None
        self.ready = False
        self.provider = provider
        self.forced_command = forced_command
        self.policy_ticks = 0
        self.max_abs_action = 0.0
        self.active = False
        self.motion_active = False

    @staticmethod
    def read_state(env: object) -> RobotState:
        data = env.mj_data
        joint_position = np.asarray(
            data.qpos[env.body_joint_index + env.qpos_offset - 1], dtype=np.float32
        ).copy()
        joint_velocity = np.asarray(
            data.qvel[env.body_joint_index + env.qvel_offset - 1], dtype=np.float32
        ).copy()
        return RobotState(
            quaternion=np.asarray(data.qpos[3:7], dtype=np.float64).copy(),
            angular_velocity=np.asarray(data.qvel[3:6], dtype=np.float32).copy(),
            joint_position_hardware=joint_position,
            joint_velocity_hardware=joint_velocity,
        )

    def open(self, env: object) -> None:
        if self.receiver is not None:
            self.receiver.open()
        state = self.read_state(env)
        self.initial_joint_position = state.joint_position_hardware.copy()
        self._write_targets(self.initial_joint_position)
        print(f"SONIC ONNX 同进程后端已加载，provider={self.provider}")

    def _write_targets(self, targets: np.ndarray, *, damping_only: bool = False) -> None:
        for i, motor in enumerate(self.bridge.low_cmd.motor_cmd):
            motor.mode = 1
            motor.tau = 0.0
            motor.q = float(targets[i])
            motor.dq = 0.0
            motor.kp = 0.0 if damping_only else float(STIFFNESS[i])
            motor.kd = 8.0 if damping_only else float(DAMPING[i])

    def step(self, env: object) -> None:
        """执行一次 50 Hz 控制更新；MuJoCo 物理步本身仍在主线程运行。"""
        if self.receiver is not None:
            self.receiver.poll()
        state = self.read_state(env)

        if not self.ready:
            if self.initial_joint_position is None:
                self.initial_joint_position = state.joint_position_hardware.copy()
            ratio = min(1.0, (self.tick_count + 1) / self.startup_ticks)
            targets = (
                (1.0 - ratio) * self.initial_joint_position
                + ratio * DEFAULT_ANGLES
            )
            self._write_targets(targets)
            self.tick_count += 1
            if ratio >= 1.0:
                print("G1 默认站姿完成，初始化 10 Hz planner...")
                self.planner.initialize(state.joint_position_hardware)
                self.planner.start()
                self.ready = True
                print("SONIC ONNX 同进程控制器就绪；等待 navigate 消息")
            return

        if self.forced_command is not None:
            receiver_active, command = True, self.forced_command
        elif self.receiver is None:
            receiver_active, command = False, None
        else:
            receiver_active, command = self.receiver.snapshot()
        if command is None:
            command = PlannerCommand()
        # start_planner 会让 ZMQ receiver 保持 active，停止导航时 follower 只发送
        # mode=0。mode=0 必须退出策略推理并恢复站立保护，否则 ONNX 会继续阻塞
        # MuJoCo 主循环，根位姿发布也会随之停止。
        control_active = bool(receiver_active and command.mode != 0)
        if control_active != self.active:
            print(
                "SONIC 导航命令状态: "
                + ("active" if control_active else "idle"),
                flush=True,
            )
        self.active = control_active
        self.motion_active = control_active
        self.planner.update_command(control_active, command)
        if control_active:
            motion, frame = self.planner.current()
            self.history.append(state, self.last_action)
            action = self.policy.infer(motion, frame, state, self.history)
            self.last_action = action
            self.policy_ticks += 1
            self.max_abs_action = max(
                self.max_abs_action, float(np.max(np.abs(action)))
            )
            targets = DEFAULT_ANGLES + action[HARDWARE_TO_ISAAC] * ACTION_SCALE
            self._write_targets(targets)
            self.planner.advance()
        else:
            self._write_targets(DEFAULT_ANGLES)

        if self.planner.error is not None:
            raise RuntimeError(f"planner 推理失败: {self.planner.error}")

    def close(self, env: object | None = None) -> None:
        self.planner.close()
        if self.receiver is not None:
            self.receiver.close()
        if env is not None:
            try:
                self._write_targets(
                    self.read_state(env).joint_position_hardware, damping_only=True
                )
            except Exception:
                pass


__all__ = [
    "InMemoryLowCommand", "InMemoryMotorCommand", "InMemoryUnitreeBridge",
    "InProcessSonicController",
]
