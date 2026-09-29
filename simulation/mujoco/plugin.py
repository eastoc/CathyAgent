"""把配置化 MuJoCo Runtime 暴露为 CathyAgent 工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cathy.artifacts import LocalArtifactStore
from cathy.contracts import JsonBlock, ToolResult
from cathy.plugins import PluginError, ToolPlugin

from simulation.contracts import ExecutionResult
from simulation.mujoco.config import DEFAULT_ENVIRONMENT
from simulation.mujoco.errors import MujocoSimulationError
from simulation.mujoco.factory import create_runtime
from simulation.mujoco.observation import ObservationResult


class MujocoToolPlugin(ToolPlugin):
    def __init__(self) -> None:
        self.runtime = None

    def initialize(self, config: dict[str, Any]) -> None:
        environment = config.get("environment") or DEFAULT_ENVIRONMENT
        artifact_store = config.get("artifact_store")
        if artifact_store is not None and not isinstance(
            artifact_store, LocalArtifactStore
        ):
            raise TypeError("artifact_store 必须是 LocalArtifactStore")
        if artifact_store is None and config.get("artifact_store_path"):
            artifact_store = LocalArtifactStore(Path(config["artifact_store_path"]))
        self.runtime = create_runtime(
            environment,
            artifact_store=artifact_store,
            viewer_enabled=config.get("viewer_enabled"),
        )

    def _require_runtime(self):
        if self.runtime is None:
            raise PluginError("MuJoCo Runtime 尚未初始化")
        return self.runtime

    @staticmethod
    def _operation(tool_name: str) -> str:
        operations = {
            "robot_describe": "describe",
            "robot_observe": "observe",
            "robot_move_ee": "move_ee",
            "robot_set_gripper": "set_gripper",
            "robot_wait": "wait",
        }
        try:
            return operations[tool_name]
        except KeyError as exc:
            raise PluginError(f"未知工具: {tool_name}") from exc

    @staticmethod
    def _result(tool_name: str, value: Any) -> ToolResult:
        if isinstance(value, ObservationResult):
            return ToolResult.succeeded(
                call_id="",
                tool_name=tool_name,
                content=(JsonBlock(value.state, label="robot_observation"), *value.images),
                artifacts=tuple(image.attachment.artifact_id for image in value.images),
                metadata={"snapshot_id": value.state["snapshot_id"]},
            )
        if isinstance(value, ExecutionResult):
            payload = value.to_dict()
            if value.status == "succeeded":
                return ToolResult.succeeded(
                    call_id="",
                    tool_name=tool_name,
                    content=(JsonBlock(payload, label="execution_result"),),
                    metadata={"snapshot_id": value.snapshot_id},
                )
            return ToolResult(
                call_id="",
                tool_name=tool_name,
                status="failed",
                content=(JsonBlock(payload, label="execution_result"),),
                metadata={"snapshot_id": value.snapshot_id},
                error_code=value.error_code or "execution_failed",
                error_message=value.message,
            )
        if isinstance(value, Mapping):
            return ToolResult.succeeded(
                call_id="",
                tool_name=tool_name,
                content=(JsonBlock(dict(value)),),
            )
        return ToolResult.succeeded(
            call_id="",
            tool_name=tool_name,
            content=str(value),
        )

    def execute(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        runtime = self._require_runtime()
        try:
            value = runtime.call(self._operation(tool_name), params)
        except MujocoSimulationError as exc:
            return ToolResult.failed(
                call_id="",
                tool_name=tool_name,
                message=str(exc),
                error_code=exc.error_code,
            )
        return self._result(tool_name, value)

    async def aexecute(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> ToolResult:
        runtime = self._require_runtime()
        operation = self._operation(tool_name)
        try:
            value = await runtime.execute(operation, params)
        except MujocoSimulationError as exc:
            return ToolResult.failed(
                call_id="",
                tool_name=tool_name,
                message=str(exc),
                error_code=exc.error_code,
            )
        return self._result(tool_name, value)

    def health_check(self) -> bool:
        return self.runtime is not None and not self.runtime._closed

    def shutdown(self) -> None:
        if self.runtime is not None:
            self.runtime.close()
            self.runtime = None
