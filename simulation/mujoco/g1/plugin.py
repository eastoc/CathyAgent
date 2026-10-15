"""把 G1 地图与导航服务暴露为 CathyAgent 工具。"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from cathy.artifacts import LocalArtifactStore
from cathy.contracts import ImageBlock, JsonBlock, ToolResult
from cathy.plugins import PluginError, ToolPlugin

from .config import DEFAULT_G1_ENVIRONMENT, load_g1_navigation_config
from .contracts import NavigationGoal
from .service import G1NavigationService
from .sonic.contracts import NavigationExecution, TimedPose2D
from .sonic.executor import NavigationExecutor
from .sonic.pose import ZmqNavigationPoseProvider
from .sonic.transport import ZmqSonicTransport


class G1NavigationToolPlugin(ToolPlugin):
    def __init__(self) -> None:
        self.service: G1NavigationService | None = None
        self.artifact_store: LocalArtifactStore | None = None
        self._pose_resource = None

    def initialize(self, config: dict[str, Any]) -> None:
        environment = config.get("environment") or DEFAULT_G1_ENVIRONMENT
        navigation_config = load_g1_navigation_config(environment)
        store = config.get("artifact_store")
        if store is not None and not isinstance(store, LocalArtifactStore):
            raise TypeError("artifact_store 必须是 LocalArtifactStore")
        if store is None:
            store = LocalArtifactStore(Path(config.get("artifact_store_path") or "outputs/g1_artifacts"))
        self.artifact_store = store
        raw_pose_provider = config.get("pose_provider")
        executor = config.get("navigation_executor")
        pose_provider = None
        if executor is not None and not isinstance(executor, NavigationExecutor):
            raise TypeError("navigation_executor 必须是 NavigationExecutor")
        if (
            raw_pose_provider is None
            and executor is None
            and navigation_config.execution_enabled
        ):
            self._pose_resource = ZmqNavigationPoseProvider(
                navigation_config.sonic.pose_endpoint,
                topic=navigation_config.sonic.pose_topic,
            )
            raw_pose_provider = self._pose_resource
        if raw_pose_provider is not None:
            if not callable(raw_pose_provider):
                raise TypeError("pose_provider 必须可调用")

            def read_timed_pose() -> TimedPose2D:
                value = raw_pose_provider()
                if isinstance(value, TimedPose2D):
                    return value
                return TimedPose2D(value, time.monotonic())

            pose_provider = lambda: read_timed_pose().pose
            if executor is None and navigation_config.execution_enabled:
                transport = config.get("sonic_transport") or ZmqSonicTransport(
                    navigation_config.sonic.publish_endpoint,
                    bind=navigation_config.sonic.bind,
                )
                executor = NavigationExecutor(
                    navigation_config.sonic,
                    pose_provider=read_timed_pose,
                    transport=transport,
                )
        self.service = G1NavigationService(
            navigation_config,
            pose_provider=pose_provider,
            executor=executor,
        )
        if executor is not None:
            executor.pose_validator = self.service.validate_execution_pose

    def _require_service(self) -> G1NavigationService:
        if self.service is None:
            raise PluginError("G1 导航服务尚未初始化")
        return self.service

    @staticmethod
    def _failure(tool_name: str, payload: dict[str, Any]) -> ToolResult:
        return ToolResult(
            call_id="",
            tool_name=tool_name,
            status="failed",
            content=(JsonBlock(payload, label="navigation_result"),),
            metadata={"motion_started": bool(payload.get("motion_started", False))},
            error_code=str(payload.get("error_code") or "navigation_failed"),
            error_message=str(payload.get("message") or "导航失败"),
        )

    def execute(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        service = self._require_service()
        if tool_name == "get_map":
            try:
                observation = service.get_map()
            except RuntimeError as exc:
                return self._failure(
                    tool_name,
                    {
                        "status": "failed",
                        "error_code": "POSE_UNAVAILABLE",
                        "message": f"无法生成当前地图视图: {exc}",
                        "motion_started": False,
                    },
                )
            assert self.artifact_store is not None
            reference = self.artifact_store.put_bytes(
                observation.png,
                mime_type="image/png",
                filename=f"{service.config.environment_id}-{service.context.map_version}-map.png",
                width=observation.width,
                height=observation.height,
            )
            image = ImageBlock(
                reference,
                detail="high",
                source="g1_navigation_map",
                frame_id=service.context.frame,
            )
            return ToolResult.succeeded(
                call_id="",
                tool_name=tool_name,
                content=(JsonBlock(observation.metadata, label="navigation_map"), image),
                artifacts=(reference.artifact_id,),
                metadata={
                    "map_version": service.context.map_version,
                    "robot_profile_version": service.context.robot_profile_version,
                },
            )
        if tool_name != "navigate":
            raise PluginError(f"未知 G1 工具: {tool_name}")

        return self._navigate(params, cancel=None)

    def _navigate(
        self,
        params: dict[str, Any],
        *,
        cancel: threading.Event | None,
    ) -> ToolResult:
        service = self._require_service()
        xy = params["goal_xy_m"]
        goal = NavigationGoal(float(xy[0]), float(xy[1]), float(params["goal_yaw_rad"]))
        prepared = service.prepare(goal)
        if not prepared.accepted:
            payload = {
                "status": "failed",
                "motion_started": False,
                "requested_goal": {"xy_m": list(goal.xy), "yaw_rad": goal.yaw},
                **prepared.reachability.to_dict(),
            }
            return self._failure("navigate", payload)

        assert prepared.plan is not None
        if not service.config.execution_enabled:
            payload = {
                "status": "failed",
                "error_code": "EXECUTION_DISABLED",
                "message": "目标可达且 A* 已生成路径，但 SONIC 执行后端尚未启用",
                "motion_started": False,
                "requested_goal": {"xy_m": list(goal.xy), "yaw_rad": goal.yaw},
                "plan": prepared.plan.to_dict(),
            }
            return self._failure("navigate", payload)
        if service.executor is None:
            return self._failure(
                "navigate",
                {
                    "status": "failed",
                    "error_code": "SONIC_NOT_CONNECTED",
                    "message": "SONIC 执行已启用，但未注入 MuJoCo 根位姿反馈或执行器",
                    "motion_started": False,
                    "plan": prepared.plan.to_dict(),
                },
            )
        execution = service.execute(prepared, cancel=cancel)
        return self._execution_result(prepared.plan.to_dict(), execution)

    @staticmethod
    def _execution_result(
        plan: dict[str, Any], execution: NavigationExecution
    ) -> ToolResult:
        payload = {**execution.to_dict(), "plan": plan}
        metadata = {
            "motion_started": execution.motion_started,
            "commands_sent": execution.commands_sent,
        }
        if execution.status == "succeeded":
            return ToolResult.succeeded(
                call_id="",
                tool_name="navigate",
                content=(JsonBlock(payload, label="navigation_result"),),
                metadata=metadata,
            )
        if execution.status == "cancelled":
            return ToolResult(
                call_id="",
                tool_name="navigate",
                status="cancelled",
                content=(JsonBlock(payload, label="navigation_result"),),
                metadata=metadata,
                error_code=execution.error_code,
                error_message=execution.message,
            )
        return ToolResult(
            call_id="",
            tool_name="navigate",
            status="failed",
            content=(JsonBlock(payload, label="navigation_result"),),
            metadata=metadata,
            error_code=execution.error_code,
            error_message=execution.message,
        )

    async def aexecute(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        if tool_name != "navigate":
            return self.execute(tool_name, params)
        service = self._require_service()
        if not service.config.execution_enabled or service.executor is None:
            return self.execute(tool_name, params)
        cancel = threading.Event()
        try:
            return await asyncio.to_thread(self._navigate, params, cancel=cancel)
        except asyncio.CancelledError:
            cancel.set()
            raise

    def health_check(self) -> bool:
        return self.service is not None

    def shutdown(self) -> None:
        if self.service is not None:
            self.service.close()
        if self._pose_resource is not None:
            self._pose_resource.close()
            self._pose_resource = None
        self.service = None


__all__ = ["G1NavigationToolPlugin"]
