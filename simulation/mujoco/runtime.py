"""独占线程持有 MuJoCo 状态的配置化 Runtime。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from cathy.artifacts import LocalArtifactStore

from simulation.contracts import ExecutionResult, Pose

from .backend import MujocoBackend
from .config import MujocoEnvironmentConfig
from .controller import Z1Controller, _matrix_to_quaternion
from .errors import MujocoExecutionError
from .observation import ObservationResult, Z1ObservationProvider
from .viewer import MujocoViewer


@dataclass
class _RuntimeCommand:
    operation: str
    params: dict[str, Any]
    future: concurrent.futures.Future[Any]
    cancel: threading.Event = field(default_factory=threading.Event)


class MujocoRuntime:
    def __init__(
        self,
        config: MujocoEnvironmentConfig,
        *,
        artifact_store: LocalArtifactStore | None = None,
        viewer_enabled: bool | None = None,
    ) -> None:
        self.config = config
        self.viewer_enabled = viewer_enabled
        self.artifact_store = artifact_store or LocalArtifactStore(
            Path(__file__).resolve().parent / "outputs" / "artifacts"
        )
        self._queue: queue.Queue[_RuntimeCommand | None] = queue.Queue()
        self._ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._closed = False
        self._thread = threading.Thread(
            target=self._worker,
            name=f"mujoco-{config.environment_id}",
            daemon=True,
        )
        self._thread.start()
        self._ready.result(timeout=config.runtime.command_timeout_seconds)

    def _worker(self) -> None:
        try:
            self._backend = MujocoBackend(self.config)
            viewer_config = self.config.runtime.viewer
            if self.viewer_enabled is not None:
                viewer_config = type(viewer_config)(
                    enabled=self.viewer_enabled,
                    show_left_ui=viewer_config.show_left_ui,
                    show_right_ui=viewer_config.show_right_ui,
                )
            self._viewer = MujocoViewer(self._backend, viewer_config)
            self._viewer.open()
            self._backend.set_state_lock(self._viewer.lock)
            self._backend.set_sync_callback(self._viewer.sync)
            self._controller = Z1Controller(self._backend, self.config.controller)
            self._observation = Z1ObservationProvider(
                self._backend,
                self.config.observation,
                self.artifact_store,
            )
            self._snapshot_id = 0
            self._action_count = 0
            self._controller.hold(self.config.runtime.settle_seconds)
            self._ready.set_result(None)
        except BaseException as exc:
            backend = getattr(self, "_backend", None)
            if backend is not None:
                backend.set_sync_callback(None)
                backend.set_state_lock(None)
            observation = getattr(self, "_observation", None)
            if observation is not None:
                observation.close()
            viewer = getattr(self, "_viewer", None)
            if viewer is not None:
                viewer.close()
            if backend is not None:
                backend.close()
            self._ready.set_exception(exc)
            return

        while True:
            command = self._queue.get()
            if command is None:
                break
            if command.future.cancelled():
                continue
            try:
                result = self._dispatch(
                    command.operation,
                    command.params,
                    cancel=command.cancel,
                )
            except BaseException as exc:
                if not command.future.cancelled():
                    command.future.set_exception(exc)
            else:
                if not command.future.cancelled():
                    command.future.set_result(result)
        self._backend.set_sync_callback(None)
        self._backend.set_state_lock(None)
        self._observation.close()
        self._viewer.close()
        self._backend.close()

    def _check_episode_budget(self) -> None:
        limits = self.config.runtime
        if self._action_count >= limits.max_agent_actions:
            raise MujocoExecutionError("episode 动作预算已耗尽")
        if self._backend.sim_time >= limits.max_sim_seconds:
            raise MujocoExecutionError("episode 仿真时间预算已耗尽")

    def _current_pose(self) -> Pose:
        return Pose(
            frame=self.config.robot.world_frame,
            position=tuple(float(value) for value in self._backend.tcp_position()),
            quaternion_wxyz=_matrix_to_quaternion(self._backend.tcp_rotation()),
        )

    def _state_change_result(self, message: str) -> ExecutionResult:
        return ExecutionResult(
            status="succeeded",
            reached=True,
            sim_time=self._backend.sim_time,
            snapshot_id=self._snapshot_id,
            actual_pose=self._current_pose(),
            message=message,
        )

    def _dispatch(
        self,
        operation: str,
        params: Mapping[str, Any],
        *,
        cancel: threading.Event,
    ) -> Any:
        if operation == "describe":
            return self._describe()
        if operation == "observe":
            return self._observation.capture(
                snapshot_id=self._snapshot_id,
                cameras=params.get("cameras"),
                render=bool(params.get("render", True)),
            )
        if operation == "reset":
            self._backend.reset()
            self._controller.hold(self.config.runtime.settle_seconds, cancel=cancel)
            self._snapshot_id += 1
            self._action_count = 0
            return {
                "status": "succeeded",
                "snapshot_id": self._snapshot_id,
                "sim_time": self._backend.sim_time,
            }

        self._check_episode_budget()
        self._action_count += 1
        self._snapshot_id += 1
        if operation == "move_ee":
            return self._controller.move_ee(
                frame=str(params["frame"]),
                position=params["position"],
                quaternion_wxyz=params.get("quaternion_wxyz"),
                duration_seconds=float(params.get("duration_seconds", 1.5)),
                snapshot_id=self._snapshot_id,
                cancel=cancel,
            )
        if operation == "set_gripper":
            self._controller.set_gripper(float(params["opening"]), cancel=cancel)
            return self._state_change_result("夹爪命令已执行")
        if operation == "wait":
            self._controller.hold(float(params["sim_seconds"]), cancel=cancel)
            return self._state_change_result("仿真时间已推进")
        raise KeyError(f"未知 Runtime operation: {operation}")

    def _describe(self) -> dict[str, Any]:
        robot = self.config.robot
        cameras = self.config.observation.cameras
        return {
            "environment_id": self.config.environment_id,
            "robot_id": robot.robot_id,
            "runtime_mode": self.config.runtime.mode,
            "viewer_enabled": self._viewer.enabled,
            "viewer_running": self._viewer.is_running,
            "base_frame": robot.base_body,
            "world_frame": robot.world_frame,
            "tcp_frame": robot.tcp_site,
            "length_unit": "m",
            "angle_unit": "rad",
            "orientation_convention": "quaternion_wxyz",
            "control_hz": self.config.controller.control_hz,
            "physics_hz": 1.0 / float(self._backend.model.opt.timestep),
            "joint_names": list(self._backend.joint_names),
            "actuator_names": list(self._backend.actuator_names),
            "cameras": [camera.name for camera in cameras],
            "primary_camera": next(camera.name for camera in cameras if camera.primary),
            "workspace_bounds": {
                "frame": robot.world_frame,
                "min": list(robot.workspace_min),
                "max": list(robot.workspace_max),
            },
            "gripper_convention": {
                "opening_range": [0.0, 1.0],
                "closed": 0.0,
                "open": 1.0,
            },
            "capabilities": [
                "observe_rgb",
                "move_ee",
                "set_gripper",
                "wait_sim_time",
            ],
            "privileged_object_state_exposed": False,
        }

    def _enqueue(self, operation: str, params: Mapping[str, Any]) -> _RuntimeCommand:
        if self._closed:
            raise RuntimeError("MujocoRuntime 已关闭")
        command = _RuntimeCommand(
            operation=operation,
            params=dict(params),
            future=concurrent.futures.Future(),
        )
        self._queue.put(command)
        return command

    async def _asubmit(self, operation: str, params: Mapping[str, Any]) -> Any:
        command = self._enqueue(operation, params)
        wrapped = asyncio.wrap_future(command.future)
        try:
            return await asyncio.wait_for(
                wrapped,
                timeout=self.config.runtime.command_timeout_seconds,
            )
        except (asyncio.CancelledError, TimeoutError):
            command.cancel.set()
            raise

    async def execute(self, operation: str, params: Mapping[str, Any]) -> Any:
        """异步执行一个已注册的 Runtime 操作。

        Plugin 只依赖这个公开入口，不接触线程、队列等实现细节。
        """
        return await self._asubmit(operation, params)

    def call(self, operation: str, params: Mapping[str, Any]) -> Any:
        command = self._enqueue(operation, params)
        try:
            return command.future.result(
                timeout=self.config.runtime.command_timeout_seconds
            )
        except concurrent.futures.TimeoutError:
            command.cancel.set()
            raise TimeoutError(f"MuJoCo operation={operation} 超时") from None

    async def describe(self) -> Mapping[str, Any]:
        return await self._asubmit("describe", {})

    async def observe(
        self,
        *,
        cameras: Sequence[str] | None = None,
        render: bool = True,
    ) -> ObservationResult:
        return await self._asubmit(
            "observe",
            {"cameras": list(cameras) if cameras is not None else None, "render": render},
        )

    async def move_ee(
        self,
        *,
        frame: str,
        position: Sequence[float],
        quaternion_wxyz: Sequence[float] | None = None,
        duration_seconds: float = 1.5,
    ) -> ExecutionResult:
        return await self._asubmit(
            "move_ee",
            {
                "frame": frame,
                "position": list(position),
                "quaternion_wxyz": (
                    list(quaternion_wxyz) if quaternion_wxyz is not None else None
                ),
                "duration_seconds": duration_seconds,
            },
        )

    async def set_gripper(self, *, opening: float) -> ExecutionResult:
        return await self._asubmit("set_gripper", {"opening": opening})

    async def wait(self, *, sim_seconds: float) -> ExecutionResult:
        return await self._asubmit("wait", {"sim_seconds": sim_seconds})

    async def reset(self) -> Mapping[str, Any]:
        return await self._asubmit("reset", {})

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout=self.config.runtime.command_timeout_seconds)
        if self._thread.is_alive():
            raise RuntimeError("MujocoRuntime 工作线程未能停止")
