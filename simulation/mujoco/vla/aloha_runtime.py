"""原生 MuJoCo ALOHA；物理、相机均由专属工作线程拥有，不依赖 robosuite。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import threading

import numpy as np

from ..config import ViewerConfig
from ..observation import encode_png
from ..viewer import MujocoViewer
from .aloha_contract import (ACTION_REPRESENTATION, CAMERAS, CONTRACT_VERSION,
                             GRIPPER_INDICES, pack_state, unpack_action, validate_actions)
from .aloha_scene import CAMERA_MAP, JOINT_NAMES, make_aloha_model


class AlohaRuntime:
    @staticmethod
    def check_dependencies():
        import mujoco
        if tuple(int(part) for part in mujoco.__version__.split(".")[:2]) < (3, 3):
            raise RuntimeError("原生 ALOHA 后端需要 MuJoCo>=3.3；不需要 robosuite")

    def __init__(self, config, *, viewer_enabled=None):
        self.check_dependencies()
        self.config = config
        self._closed = False
        self._stop = threading.Event()
        self._renderer = self._viewer = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aloha")
        try:
            self._pool.submit(self._initialize, viewer_enabled).result(timeout=90)
        except BaseException:
            self.close()
            raise

    def _initialize(self, viewer_enabled):
        import mujoco
        self.model = make_aloha_model(self.config)
        self.data = mujoco.MjData(self.model)
        joint_ids = np.array([self.model.joint(name).id for name in JOINT_NAMES])
        self._qpos_indices = self.model.jnt_qposadr[joint_ids]
        self._actuator_indices = np.array([self._actuator_for_joint(j) for j in joint_ids])
        self._ctrl_limits = self.model.actuator_ctrlrange[self._actuator_indices].copy()
        if self.model.nu != 16:
            raise ValueError("ALOHA 资产应有16个位置执行器：双臂各6关节+2手指")
        ratio = 1 / (self.config["runtime"]["control_hz"] * self.model.opt.timestep)
        self._substeps = round(ratio)
        if self._substeps < 1 or not np.isclose(ratio, self._substeps):
            raise ValueError("控制周期必须是物理步长的整数倍")
        arm = self.config["scene"]["initial_arm_qpos"] + [self.config["scene"]["initial_gripper"]]
        initial = unpack_action(arm * 2)
        if ((initial < self._ctrl_limits[:, 0]) | (initial > self._ctrl_limits[:, 1])).any():
            raise ValueError("初始 ALOHA 姿态超出物理关节限位")
        self.data.qpos[self._qpos_indices] = initial
        self.data.ctrl[self._actuator_indices] = initial
        self._last_target = np.asarray(arm * 2, dtype=float)
        mujoco.mj_forward(self.model, self.data)
        self._steps = self._snapshot = self._limited_steps = 0
        obs = self.config["observation"]
        self._renderer = mujoco.Renderer(self.model, height=obs["render_height"], width=obs["render_width"])
        self._render_options = mujoco.MjvOption()
        enabled = self.config["runtime"]["viewer"] if viewer_enabled is None else viewer_enabled
        self._viewer = MujocoViewer(self, ViewerConfig(enabled, True, True))
        self._viewer.open()
        for _ in range(self.config["runtime"]["settle_steps"]):
            self._step(self._last_target, "both", self._stop)
        self._steps = self._limited_steps = 0

    def _actuator_for_joint(self, joint_id):
        indices = np.flatnonzero(self.model.actuator_trnid[:, 0] == joint_id)
        if len(indices) != 1:
            raise ValueError("ALOHA 关节必须一一对应位置执行器")
        return indices[0]

    def describe(self):
        return {"environment_id": self.config["environment_id"], "robots": ["ViperX 300s", "ViperX 300s"],
                "robot_platform": "ALOHA", "backend": "native_mujoco", "robosuite_required": False,
                "control_hz": 50, "cameras": list(CAMERAS), "policy": "pi05_aloha",
                "checkpoint": "pi05_base", "policy_state_dim": 14, "policy_action_dim": 14,
                "contract_version": CONTRACT_VERSION, "action_representation": ACTION_REPRESENTATION,
                "allowed_arms": ["both"], "bimanual_action_interface": True,
                "task_performance_validated": False, "custom_scene_out_of_distribution": True,
                "skill_completion": "执行预算结束仅为检查点，任务成功必须看图验证"}

    def _state(self):
        return pack_state(self.data.qpos[self._qpos_indices])

    def _render_camera(self, camera):
        with self._viewer.lock() if self._viewer else nullcontext():
            self._renderer.update_scene(self.data, camera=CAMERA_MAP[camera], scene_option=self._render_options)
            # 原生 Renderer 返回 top-down RGB；ALOHA 不做 LIBERO 翻转。
            return self._renderer.render().copy()

    def _observation(self, arm):
        if arm != "both":
            raise ValueError("ALOHA 策略输入输出为完整双臂，arm 必须是 both")
        return {"state": self._state(), "images": {name: self._render_camera(name) for name in CAMERAS},
                "snapshot_id": self._snapshot}

    def _snapshot_result(self):
        return ({"snapshot_id": self._snapshot, "sim_time": float(self.data.time),
                 "episode_steps": self._steps, "joint_state": self._state().tolist(),
                 "action_limited_steps": self._limited_steps,
                 "state_layout": "left_joints_rad[6], left_gripper[1], right_joints_rad[6], right_gripper[1]"},
                [(name, encode_png(self._render_camera(name))) for name in CAMERAS])

    def _step(self, action, arm, cancel):
        import mujoco
        if cancel.is_set() or self._stop.is_set():
            raise RuntimeError("动作已取消")
        if arm != "both":
            raise ValueError("ALOHA 只能执行14维双臂预测，arm 必须是 both")
        cfg = self.config["runtime"]
        if self._steps >= cfg["max_episode_steps"]:
            raise RuntimeError("episode 步数预算耗尽，请重启场景")
        requested = validate_actions([action])[0]
        target = requested.copy()
        # 关节目标限速，防止基础模型输出令仿真产生瞬时大跳变。
        limits = np.full(14, cfg["max_joint_velocity"] / cfg["control_hz"])
        limits[GRIPPER_INDICES] = cfg["max_gripper_velocity"] / cfg["control_hz"]
        target = self._last_target + np.clip(target - self._last_target, -limits, limits)
        raw = unpack_action(target)
        raw = np.clip(raw, self._ctrl_limits[:, 0], self._ctrl_limits[:, 1])
        actual_target = pack_state(raw).astype(float)
        if not np.allclose(requested, actual_target, atol=1e-6):
            self._limited_steps += 1
        self._last_target = actual_target
        with self._viewer.lock() if self._viewer else nullcontext():
            self.data.ctrl[self._actuator_indices] = raw
            mujoco.mj_step(self.model, self.data, nstep=self._substeps)
            # 保证刚推进后的相机位姿和关节状态属于同一帧。
            mujoco.mj_forward(self.model, self.data)
            if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
                self._stop.set()
                raise RuntimeError("仿真数值无效，已停止")
            self._steps += 1
            self._snapshot += 1
        if self._viewer:
            self._viewer.sync()

    async def _run(self, fn, *args, cancel=None):
        if self._closed:
            raise RuntimeError("ALOHA runtime 已关闭")
        future = asyncio.wrap_future(self._pool.submit(fn, *args))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if cancel is not None:
                cancel.set()
            try:
                await asyncio.shield(future)
            except Exception:
                pass
            raise

    async def observe_policy(self, arm):
        return await self._run(self._observation, arm)

    async def snapshot(self):
        return await self._run(self._snapshot_result)

    async def step(self, action, arm, cancel):
        return await self._run(self._step, action, arm, cancel, cancel=cancel)

    def _cleanup(self):
        try:
            if self._viewer:
                self._viewer.close()
        finally:
            if self._renderer is not None:
                self._renderer.close()
                self._renderer = None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            self._pool.submit(self._cleanup).result(timeout=30)
        finally:
            self._pool.shutdown(wait=True, cancel_futures=True)
