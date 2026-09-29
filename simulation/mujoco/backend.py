"""MuJoCo 原生 model/data 边界；不包含 Agent 或任务逻辑。"""

from __future__ import annotations

import importlib
import math
from contextlib import nullcontext
from typing import Any, Callable, ContextManager, Sequence

import numpy as np

from .config import MujocoEnvironmentConfig
from .errors import MujocoConfigError, MujocoDependencyError, MujocoExecutionError


def require_mujoco() -> Any:
    try:
        return importlib.import_module("mujoco")
    except ImportError as exc:  # pragma: no cover - 取决于可选依赖
        raise MujocoDependencyError(
            "未安装 MuJoCo；请安装 simulation/mujoco/requirements.txt"
        ) from exc


class MujocoBackend:
    def __init__(self, config: MujocoEnvironmentConfig) -> None:
        self.config = config
        self.mujoco = require_mujoco()
        self.model = self.mujoco.MjModel.from_xml_path(str(config.model_path))
        self.data = self.mujoco.MjData(self.model)
        self.arm_joint_names = config.robot.arm_joints
        self.joint_names = (*config.robot.arm_joints, config.robot.gripper_joint)
        self.actuator_names = (
            *config.robot.arm_actuators,
            config.robot.gripper_actuator,
        )
        self.joint_ids = np.asarray(
            [self.model.joint(name).id for name in self.joint_names],
            dtype=int,
        )
        self.actuator_ids = np.asarray(
            [self.model.actuator(name).id for name in self.actuator_names],
            dtype=int,
        )
        self.qpos_addresses = self.model.jnt_qposadr[self.joint_ids].copy()
        self.dof_addresses = self.model.jnt_dofadr[self.joint_ids].copy()
        self.tcp_id = self.model.site(config.robot.tcp_site).id
        self.base_body_id = self.model.body(config.robot.base_body).id
        self._sync_callback: Callable[[], None] | None = None
        self._state_lock: Callable[[], ContextManager[Any]] = nullcontext
        self._validate_contract()
        self.reset()

    @property
    def sim_time(self) -> float:
        return float(self.data.time)

    @property
    def control_hz(self) -> float:
        return self.config.controller.control_hz

    def _validate_contract(self) -> None:
        if len(self.joint_names) != len(self.actuator_names):
            raise MujocoConfigError("joint 与 actuator 数量不一致")
        mapped_joints = self.model.actuator_trnid[self.actuator_ids, 0]
        if not np.array_equal(mapped_joints, self.joint_ids):
            raise MujocoConfigError("配置的 actuator/joint 映射与 MJCF 不一致")
        timestep = float(self.model.opt.timestep)
        if not math.isclose(
            timestep,
            self.config.expected_timestep_seconds,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise MujocoConfigError(
                f"MJCF timestep={timestep}，配置期望 "
                f"{self.config.expected_timestep_seconds}"
            )
        exact_steps = 1.0 / (self.control_hz * timestep)
        if not math.isclose(exact_steps, round(exact_steps), abs_tol=1e-12):
            raise MujocoConfigError("control_hz 与 timestep 必须形成整数步频比")
        for camera in self.config.observation.cameras:
            self.model.camera(camera.name)

    def reset(self) -> None:
        with self._state_lock():
            self.mujoco.mj_resetData(self.model, self.data)
            home = np.asarray(self.config.robot.home, dtype=float)
            self.data.qpos[self.qpos_addresses] = home
            self.data.ctrl[self.actuator_ids] = home
            self.mujoco.mj_forward(self.model, self.data)
            self._validate_state()
        self._sync_viewer()

    def set_state_lock(
        self,
        lock_factory: Callable[[], ContextManager[Any]] | None,
    ) -> None:
        self._state_lock = lock_factory or nullcontext

    def set_sync_callback(self, callback: Callable[[], None] | None) -> None:
        self._sync_callback = callback
        self._sync_viewer()

    def _sync_viewer(self) -> None:
        if self._sync_callback is not None:
            self._sync_callback()

    def robot_qpos(self) -> np.ndarray:
        return self.data.qpos[self.qpos_addresses].copy()

    def robot_qvel(self) -> np.ndarray:
        return self.data.qvel[self.dof_addresses].copy()

    def controls(self) -> np.ndarray:
        return self.data.ctrl[self.actuator_ids].copy()

    def tcp_position(self) -> np.ndarray:
        return self.data.site_xpos[self.tcp_id].copy()

    def tcp_rotation(self) -> np.ndarray:
        return self.data.site_xmat[self.tcp_id].reshape(3, 3).copy()

    def base_pose(self) -> tuple[np.ndarray, np.ndarray]:
        return (
            self.data.xpos[self.base_body_id].copy(),
            self.data.xmat[self.base_body_id].reshape(3, 3).copy(),
        )

    def validate_control(self, values: Sequence[float]) -> np.ndarray:
        control = np.asarray(values, dtype=float)
        if control.shape != (len(self.actuator_ids),):
            raise MujocoExecutionError(
                f"控制向量维度必须为 {len(self.actuator_ids)}"
            )
        if not np.isfinite(control).all():
            raise MujocoExecutionError("控制向量必须全部是有限数")
        for local_index, actuator_id in enumerate(self.actuator_ids):
            if not self.model.actuator_ctrllimited[actuator_id]:
                continue
            low, high = self.model.actuator_ctrlrange[actuator_id]
            if not low <= control[local_index] <= high:
                raise MujocoExecutionError(
                    f"{self.actuator_names[local_index]}={control[local_index]:.6g} "
                    f"超出 ctrlrange [{low:.6g}, {high:.6g}]"
                )
        return control

    def step_control(self, values: Sequence[float]) -> int:
        control = self.validate_control(values)
        timestep = float(self.model.opt.timestep)
        nstep = int(round(1.0 / (self.control_hz * timestep)))
        before = self.sim_time
        with self._state_lock():
            self.data.ctrl[self.actuator_ids] = control
            self.mujoco.mj_step(self.model, self.data, nstep=nstep)
            self.mujoco.mj_forward(self.model, self.data)
            self._validate_state()
            expected = before + nstep * timestep
            if not math.isclose(self.sim_time, expected, rel_tol=0, abs_tol=1e-10):
                raise MujocoExecutionError("仿真时间推进量异常")
        self._sync_viewer()
        return nstep

    def _validate_state(self) -> None:
        if np.any(self.data.warning.number):
            raise MujocoExecutionError("MuJoCo 报告数值警告")
        if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
            raise MujocoExecutionError("MuJoCo 状态包含非有限数")

    def close(self) -> None:
        return None
