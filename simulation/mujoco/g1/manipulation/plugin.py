"""将 G1 GN1x 高层 manipulation 技能暴露为 CathyAgent 工具。"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any, Mapping

from PIL import Image

from cathy.artifacts import LocalArtifactStore
from cathy.contracts import ImageBlock, JsonBlock, ToolResult
from cathy.plugins import PluginError, ToolPlugin

from .client import Gr00tClientError
from .config import G1ManipulationConfig
from .service import G1ManipulationTools


class G1ManipulationToolPlugin(ToolPlugin):
    def __init__(self) -> None:
        self.tools: G1ManipulationTools | None = None
        self.artifact_store: LocalArtifactStore | None = None

    def initialize(self, config: dict[str, Any]) -> None:
        store = config.get("artifact_store")
        if store is not None and not isinstance(store, LocalArtifactStore):
            raise TypeError("artifact_store 必须是 LocalArtifactStore")
        if store is None:
            store = LocalArtifactStore(
                Path(config.get("artifact_store_path") or "outputs/g1_manipulation_artifacts")
            )
        self.artifact_store = store
        resolved = G1ManipulationConfig.from_mapping(config.get("manipulation"))
        self.tools = G1ManipulationTools(
            resolved,
            navigation_pose_provider=config.get("navigation_pose_provider"),
            viewer_enabled=bool(config.get("viewer_enabled", False)),
        )

    def _require_tools(self) -> G1ManipulationTools:
        if self.tools is None:
            raise PluginError("G1 manipulation 尚未初始化")
        return self.tools

    def _observe_result(self, tool_name: str, value) -> ToolResult:
        payload, images = value
        assert self.artifact_store is not None
        blocks = [JsonBlock(payload, label="g1_observation")]
        artifact_ids = []
        for camera, rgb in images.items():
            buffer = io.BytesIO()
            Image.fromarray(rgb).save(buffer, format="PNG")
            reference = self.artifact_store.put_bytes(
                buffer.getvalue(), mime_type="image/png",
                filename=f"g1-{camera}.png", width=rgb.shape[1], height=rgb.shape[0],
            )
            blocks.append(ImageBlock(reference, detail="high", source=camera, frame_id=camera))
            artifact_ids.append(reference.artifact_id)
        return ToolResult.succeeded(
            call_id="", tool_name=tool_name, content=tuple(blocks),
            artifacts=tuple(artifact_ids),
        )

    @staticmethod
    def _result(tool_name: str, value: Any) -> ToolResult:
        payload = dict(value) if isinstance(value, Mapping) else {"value": value}
        status = str(payload.get("status") or "succeeded")
        if status == "failed":
            return ToolResult(
                call_id="", tool_name=tool_name, status="failed",
                content=(JsonBlock(payload, label="g1_skill_result"),),
                error_code=str(payload.get("error_code") or "g1_skill_failed"),
                error_message=str(payload.get("message") or "G1 技能执行失败"),
            )
        if status == "cancelled":
            return ToolResult(
                call_id="", tool_name=tool_name, status="cancelled",
                content=(JsonBlock(payload, label="g1_skill_result"),),
                error_code="operator_cancelled", error_message="G1 技能已取消",
            )
        return ToolResult.succeeded(
            call_id="", tool_name=tool_name,
            content=(JsonBlock(payload, label="g1_skill_result"),),
        )

    async def aexecute(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        tools = self._require_tools()
        try:
            value = await tools.execute(tool_name, params)
        except (Gr00tClientError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            return ToolResult.failed(
                call_id="", tool_name=tool_name, message=str(exc),
                error_code="g1_manipulation_rejected",
            )
        if tool_name == "g1_observe":
            return self._observe_result(tool_name, value)
        return self._result(tool_name, value)

    def execute(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        if tool_name == "g1_stop":
            return self._result(tool_name, self._require_tools().request_stop(params.get("request_id")))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aexecute(tool_name, params))
        raise PluginError(f"{tool_name} 在异步上下文中必须通过 aexecute 调用")

    def health_check(self) -> bool:
        return self.tools is not None

    def shutdown(self) -> None:
        if self.tools is not None:
            self.tools.close()
            self.tools = None


__all__ = ["G1ManipulationToolPlugin"]
