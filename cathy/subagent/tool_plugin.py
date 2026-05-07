"""把任意 Subagent 实例包装成对父 agent 透明的 ToolPlugin。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..plugins.base import PluginError, ToolPlugin
from ..plugins.manifest import Execution, PluginManifest, ToolSpec
from .base import Subagent


def build_subagent_tool_manifest(subagent: Subagent) -> PluginManifest:
    return PluginManifest(
        name=subagent.name,
        version="0.1.0",
        description=subagent.description,
        tools=[
            ToolSpec(
                name=subagent.name,
                description=subagent.description,
                input_schema=subagent.input_schema,
            )
        ],
        permissions={"network": False, "filesystem": False},
        execution=Execution(
            runtime="python",
            entrypoint="<internal>:SubagentToolPlugin",
        ),
        metadata={"trust_level": "builtin", "kind": "subagent"},
        source_dir=Path(__file__).parent,
    )


class SubagentToolPlugin(ToolPlugin):
    """ToolPlugin 适配器：把 Subagent.run() 暴露成 OpenAI function tool。"""

    def __init__(self, subagent: Subagent) -> None:
        self._subagent = subagent

    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != self._subagent.name:
            raise PluginError(
                f"{self._subagent.name} 插件不支持的工具: {tool_name}"
            )
        result = self._subagent.run(params)
        return result.final_answer or f"[subagent:{self._subagent.name}] 子任务无输出"

    @property
    def subagent(self) -> Subagent:
        return self._subagent
