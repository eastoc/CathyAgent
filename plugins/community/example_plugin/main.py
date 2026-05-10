"""社区插件示例实现。"""

from __future__ import annotations

from typing import Any

from cathy.plugins import PluginError, ToolPlugin


class ExamplePlugin(ToolPlugin):
    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != "example_ping":
            raise PluginError(f"未知工具: {tool_name}")
        return "example_plugin: pong"
