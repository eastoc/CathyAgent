"""MCP（Model Context Protocol）客户端集成。

策略：
- 复用官方 `fastmcp.Client`（依赖 `mcp` 官方 SDK），不重复造协议轮子。
- 后台 asyncio 线程持有长连接；同步 `Agent.run` 通过 `run_coroutine_threadsafe` 调用。
- 多 server 走 FastMCP 的 `MCPConfig` 字典（与 Claude Code 的 `mcpServers` 字段同构）。

对外 API：
- `McpHub`            ：连接生命周期 + list/call/shutdown
- `McpToolInfo`       ：单个 MCP 工具的内存表示
- `McpError`          ：MCP 相关异常
- `HAS_FASTMCP`       ：标记 fastmcp 是否安装；未安装时 `McpHub.start` 会抛错
- `McpToolPlugin`     ：把 hub 里的工具适配到 `PluginRegistry`
- `build_mcp_manifest`：基于 hub 当前的工具列表构造 `PluginManifest`
"""

from .client import (
    HAS_FASTMCP,
    McpError,
    McpHub,
    McpToolInfo,
    build_outer_tool_name,
    infer_mcp_client_roots_from_servers,
    normalize_root_uri,
)
from .plugin import McpToolPlugin, build_mcp_manifest

__all__ = [
    "HAS_FASTMCP",
    "McpError",
    "McpHub",
    "McpToolInfo",
    "McpToolPlugin",
    "build_mcp_manifest",
    "build_outer_tool_name",
    "infer_mcp_client_roots_from_servers",
    "normalize_root_uri",
]
