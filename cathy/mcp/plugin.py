"""把 `McpHub` 适配成 `ToolPlugin`，让 MCP 工具走和内置工具同一条路。

设计原则：
- MCP 服务的发现已经在 hub.start() 完成；这里只负责把 hub 当下持有的工具
  转成 PluginManifest，并在 execute() 时转发到 `hub.call_tool`。
- 我们用 `register_internal_plugin(skip_initialize=False)` 注册时，本插件的
  `initialize` 不需要再做事，留作未来 reload / 探活的钩子。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cathy.plugins import PluginError, ToolPlugin
from cathy.plugins.manifest import Execution, PluginManifest, ToolSpec

from .client import McpError, McpHub


class McpToolPlugin(ToolPlugin):
    """把所有 MCP 工具聚合在一个 plugin 实例下。"""

    def __init__(self, hub: McpHub) -> None:
        self._hub = hub

    def initialize(self, config: dict[str, Any]) -> None:
        # 真正的连接生命周期由 McpHub 自己持有；插件这层不重复 init。
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        try:
            return self._hub.call_tool(tool_name, params or {})
        except McpError as exc:
            raise PluginError(str(exc)) from exc

    def shutdown(self) -> None:
        self._hub.shutdown()


def build_mcp_manifest(
    hub: McpHub,
    *,
    project_root: Path,
    plugin_name: str = "mcp",
    version: str = "0.1.0",
) -> PluginManifest:
    """基于 hub 当前的工具列表构造 manifest。

    Raises:
        PluginError: hub 当前没有任何 MCP 工具时抛出（避免空 manifest 触发 schema 校验失败）。
    """
    tools: list[ToolSpec] = []
    seen: set[str] = set()
    for info in hub.list_tools():
        if info.outer_name in seen:
            continue
        seen.add(info.outer_name)
        tools.append(
            ToolSpec(
                name=info.outer_name,
                description=info.description,
                input_schema=info.input_schema,
            )
        )
    if not tools:
        raise PluginError("MCP hub 当前没有可注册的工具")

    return PluginManifest(
        name=plugin_name,
        version=version,
        description="MCP servers (FastMCP client)",
        tools=tools,
        permissions={},
        execution=Execution(runtime="python", entrypoint=":inline"),
        metadata={"trust_level": "verified"},
        source_dir=project_root,
    )
