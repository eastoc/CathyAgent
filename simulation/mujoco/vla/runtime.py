"""单线程拥有双臂仿真、渲染和 Viewer；网络等待期间不推进仿真。"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from contextlib import nullcontext
from typing import Any

import numpy as np

from ..config import ViewerConfig
from ..observation import encode_png
from ..viewer import MujocoViewer


def quaternion_to_axis_angle(quaternion) -> np.ndarray:
    q = np.asarray(quaternion, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or np.linalg.norm(q) < 1e-8:
        raise ValueError("末端 quaternion_xyzw 无效")
    q = q / np.linalg.norm(q)
    # 与 openpi LIBERO 示例一致，不额外规范化四元数符号。
    scale = np.linalg.norm(q[:3])
    if scale < 1e-8:
        return np.zeros(3)
    return q[:3] * (2 * np.arctan2(scale, q[3]) / scale)


def libero_state(obs: dict, index: int) -> np.ndarray:
    prefix = f"robot{index}_"
    result = np.concatenate((obs[prefix + "eef_pos"],
                             quaternion_to_axis_angle(obs[prefix + "eef_quat"]),
                             obs[prefix + "gripper_qpos"]))
    if result.shape != (8,) or not np.isfinite(result).all():
        raise ValueError("Panda 观测不符合 LIBERO 8维状态契约")
    return result


class PandaRuntime:
    @staticmethod
    def check_dependencies():
        import importlib.metadata
        try:
            version = importlib.metadata.version("robosuite")
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                "缺少 robosuite；请在独立 Python 3.10 环境安装 "
                "simulation/mujoco/vla/requirements.txt"
            ) from exc
        if version != "1.4.1":
            raise RuntimeError(f"此后端要求 robosuite==1.4.1，当前为 {version}")
        import mujoco
        if tuple(int(part) for part in mujoco.__version__.split(".")[:2]) >= (3, 10):
            raise RuntimeError(
                f"MuJoCo {mujoco.__version__} 的 mj_fullM 新接口不兼容 robosuite 1.4.1；"
                "请在 VLA 环境使用项目固定版本：python -m pip install 'mujoco==3.3.0'。"
                "若当前环境还运行其他项目，请先建立独立环境。"
            )

    def __init__(self, config: dict, *, viewer_enabled: bool | None = None):
        self.check_dependencies()
        self.config = config
        self._closed = False
        self._viewer = None
        self._renderer = None
        self.env = None
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="panda")
        self._stop = threading.Event()
        try:
            self._pool.submit(self._initialize, viewer_enabled).result(timeout=90)
        except BaseException:
            self.close()
            raise

    def _initialize(self, viewer_enabled):
        import mujoco
        from .scene import make_environment

        self.env = make_environment(self.config)
        self._obs = self.env.reset()
        if self.env.action_dim != 14:
            raise ValueError("双 Panda 环境必须具有14维 OSC_POSE 动作")
        # robosuite 的 MjSim 包装底下仍然是原生 MuJoCo model/data。
        self.model = self.env.sim.model._model
        self.data = self.env.sim.data._data
        size = self.config["observation"]["render_size"]
        # 原生 Renderer 在 macOS 使用 CGL，不创建 NSWindow。
        # 创建、采图和释放都由同一个仿真工作线程负责。
        self._renderer = mujoco.Renderer(self.model, height=size, width=size)
        self._render_options = mujoco.MjvOption()
        self._render_options.geomgroup[0] = 0  # 与 robosuite 默认一致：隐藏碰撞网格。
        self._render_options.geomgroup[1] = 1
        enabled = self.config["runtime"]["viewer"] if viewer_enabled is None else viewer_enabled
        self._viewer = MujocoViewer(self, ViewerConfig(enabled, True, True))
        self._viewer.open()
        self._gripper_commands = [-1.0, -1.0]
        self._steps = 0
        self._snapshot = 0
        for _ in range(self.config["runtime"]["settle_steps"]):
            self._step(np.zeros(6).tolist() + [-1], "left", self._stop)
        self._steps = 0

    def describe(self):
        return {
            "environment_id": self.config["environment_id"], "robots": ["Panda", "Panda"],
            "grippers": "每臂一个双指夹爪", "control_hz": 20,
            "cameras": ["agentview", "robot0_eye_in_hand", "robot1_eye_in_hand"],
            "policy": "pi05_libero", "policy_state_dim": 8, "policy_action_dim": 7,
            "allowed_arms": self.config["policy"]["allowed_arms"],
            "coordinated_bimanual_supported": False,
            "custom_scene_out_of_distribution": True,
            "skill_completion": "执行步数结束仅返回检查点，任务成功由 Agent 看图验证",
        }

    def _render_camera(self, camera: str):
        with self._viewer.lock() if self._viewer else nullcontext():
            self._renderer.update_scene(self.data, camera=camera,
                                        scene_option=self._render_options)
            rgb = self._renderer.render()
        # 原生 Renderer 已上下翻转成 top-down；先还原 robosuite 默认的
        # OpenGL 原始方向，再由策略适配器统一执行 LIBERO 的 180 度旋转。
        return np.ascontiguousarray(rgb[::-1])

    def _observation(self, arm: str, *, images: bool = True):
        index = {"left": 0, "right": 1}[arm]
        obs = self._obs
        return {"state": libero_state(obs, index),
                "image": self._render_camera("agentview") if images else None,
                "wrist_image": self._render_camera(f"robot{index}_eye_in_hand") if images else None,
                "snapshot_id": self._snapshot}

    def _snapshot_result(self):
        state = {"snapshot_id": self._snapshot, "sim_time": float(self.data.time),
                 "episode_steps": self._steps,
                 "arms": {arm: {"eef_and_gripper_state": libero_state(self._obs, i).tolist()}
                          for i, arm in enumerate(("left", "right"))}}
        images = []
        for camera in ("agentview", "robot0_eye_in_hand", "robot1_eye_in_hand"):
            rgb = self._render_camera(camera)
            if self.config["observation"]["policy_rotate_180"]:
                rgb = rgb[::-1, ::-1]
            images.append((camera, encode_png(np.ascontiguousarray(rgb))))
        return state, images

    def _step(self, action, arm, cancel):
        if cancel.is_set() or self._stop.is_set():
            raise RuntimeError("动作已取消")
        if self._steps >= self.config["runtime"]["max_episode_steps"]:
            raise RuntimeError("episode 步数预算耗尽，请重启场景")
        value = np.asarray(action, dtype=float)
        if value.shape != (7,) or not np.isfinite(value).all() or np.abs(value).max() > 1.000001:
            raise ValueError("执行器只接受范围 [-1,1] 的单臂7维动作")
        index = {"left": 0, "right": 1}[arm]
        combined = np.zeros(14)
        combined[6], combined[13] = self._gripper_commands
        combined[index * 7:(index + 1) * 7] = value
        self._gripper_commands[index] = float(value[-1])
        with self._viewer.lock() if self._viewer else nullcontext():
            self._obs, _, _, _ = self.env.step(combined)
            if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
                self._stop.set()
                raise RuntimeError("仿真数值无效，已停止")
            self._steps += 1
            self._snapshot += 1
        if self._viewer:
            self._viewer.sync()

    async def _run(self, fn, *args, cancel=None):
        if self._closed:
            raise RuntimeError("Panda runtime 已关闭")
        future = asyncio.wrap_future(self._pool.submit(fn, *args))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            if cancel is not None:
                cancel.set()
            # 每次只在工作线程投递一步；等该步结束后再交还控制权。
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
            try:
                if self._renderer is not None:
                    self._renderer.close()
                    self._renderer = None
            finally:
                if self.env:
                    self.env.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            self._pool.submit(self._cleanup).result(timeout=30)
        finally:
            self._pool.shutdown(wait=True, cancel_futures=True)
