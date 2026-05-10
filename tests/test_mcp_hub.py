"""Phase 5｜MCP 集成回归。

策略：
- 用 fastmcp 自带的 in-memory FastMCP server 当被测对象（不依赖网络/子进程），
  避免引入 npm / docker 依赖。
- 验收：start → list_tools → call_tool → shutdown 全链路可用，并且能挂到
  PluginRegistry 上当成普通工具被调用。
- 若运行环境未安装 fastmcp，整组用例 skip（与 cli.py 的降级策略一致）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from fastmcp import FastMCP  # noqa: E402

    HAS_FASTMCP = True
except ImportError:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]
    HAS_FASTMCP = False

from cathy.mcp import (  # noqa: E402
    McpError,
    McpHub,
    McpToolPlugin,
    build_mcp_manifest,
    build_outer_tool_name,
    infer_mcp_client_roots_from_servers,
)
from cathy.plugins import PluginError, PluginRegistry  # noqa: E402


class BuildOuterToolNameTest(unittest.TestCase):
    """无需 fastmcp：纯命名规则。"""

    def test_with_server_prefix_emits_double_underscore_form(self) -> None:
        self.assertEqual(
            build_outer_tool_name("fs_read_file", ["fs", "memory"]),
            "mcp__fs__read_file",
        )

    def test_longest_server_prefix_wins(self) -> None:
        # 既有 fs 又有 fs_inner_v2 时，应该按更长前缀拆分
        self.assertEqual(
            build_outer_tool_name("fs_inner_v2_read", ["fs", "fs_inner_v2"]),
            "mcp__fs_inner_v2__read",
        )

    def test_falls_back_to_single_segment_when_no_match(self) -> None:
        self.assertEqual(
            build_outer_tool_name("ping", ["fs"]),
            "mcp__ping",
        )

    def test_no_server_names_keeps_legacy_single_segment(self) -> None:
        self.assertEqual(build_outer_tool_name("add", None), "mcp__add")
        self.assertEqual(build_outer_tool_name("Add-Item!", None), "mcp__add_item_")

    def test_segment_starts_with_letter(self) -> None:
        # tool 名以数字开头时，sanitize 会前缀 't_'
        self.assertEqual(
            build_outer_tool_name("fs_2fa_check", ["fs"]),
            "mcp__fs__t_2fa_check",
        )


class InferMcpClientRootsTest(unittest.TestCase):
    """无需 fastmcp：纯配置推断。"""

    def test_filesystem_args_yield_roots(self) -> None:
        servers = {
            "fs": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp", "/var"],
            },
        }
        self.assertEqual(
            infer_mcp_client_roots_from_servers(servers),
            ["file:///tmp", "file:///var"],
        )

    def test_no_filesystem_returns_empty(self) -> None:
        self.assertEqual(infer_mcp_client_roots_from_servers({}), [])

    def test_existing_file_uri_passthrough(self) -> None:
        servers = {
            "fs": {
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "file:///Users/me/proj",
                ],
            },
        }
        self.assertEqual(
            infer_mcp_client_roots_from_servers(servers),
            ["file:///Users/me/proj"],
        )


def _make_inmemory_server() -> "FastMCP":
    server = FastMCP("CathyTestServer")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers"""
        return a + b

    @server.tool()
    def echo(text: str) -> str:
        """Return text unchanged"""
        return text

    return server


@unittest.skipUnless(HAS_FASTMCP, "fastmcp 未安装，跳过 MCP 集成测试")
class McpHubTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = McpHub(_make_inmemory_server(), connect_timeout=10.0)
        self.hub.start()

    def tearDown(self) -> None:
        self.hub.shutdown(timeout=3.0)

    def test_list_tools_has_prefix_and_schema(self) -> None:
        tools = self.hub.list_tools()
        names = sorted(t.outer_name for t in tools)
        self.assertEqual(names, ["mcp__add", "mcp__echo"])

        add = next(t for t in tools if t.outer_name == "mcp__add")
        self.assertEqual(add.inner_name, "add")
        self.assertIn("a", add.input_schema.get("properties", {}))
        self.assertIn("b", add.input_schema.get("properties", {}))

    def test_call_tool_returns_text(self) -> None:
        out = self.hub.call_tool("mcp__add", {"a": 2, "b": 3})
        self.assertIn("5", out)

        out2 = self.hub.call_tool("mcp__echo", {"text": "你好"})
        self.assertIn("你好", out2)

    def test_call_unknown_tool_raises(self) -> None:
        with self.assertRaises(McpError):
            self.hub.call_tool("mcp__not_exist", {})


@unittest.skipUnless(HAS_FASTMCP, "fastmcp 未安装，跳过 MCP 集成测试")
class McpToolPluginIntegrationTest(unittest.TestCase):
    """通过 PluginRegistry 调用 MCP 工具，验证适配器闭环。"""

    def setUp(self) -> None:
        self.hub = McpHub(_make_inmemory_server(), connect_timeout=10.0)
        self.hub.start()
        self.registry = PluginRegistry(plugins_dirs=[])
        self.manifest = build_mcp_manifest(self.hub, project_root=PROJECT_ROOT)
        self.registry.register_internal_plugin(
            self.manifest,
            McpToolPlugin(self.hub),
            skip_initialize=True,
        )

    def tearDown(self) -> None:
        self.registry.shutdown()  # McpToolPlugin.shutdown 会关 hub

    def test_registry_exposes_mcp_tools_with_openai_schema(self) -> None:
        schemas = self.registry.openai_schemas()
        names = sorted(s["function"]["name"] for s in schemas)
        self.assertIn("mcp__add", names)
        self.assertIn("mcp__echo", names)

    def test_registry_call_routes_to_hub(self) -> None:
        out = self.registry.call("mcp__add", {"a": 7, "b": 8})
        self.assertIn("15", out)

    def test_registry_call_invalid_args_returns_structured_error(self) -> None:
        # b 缺失 → JSON Schema 校验失败，PluginRegistry 会包成 [ToolError]
        out = self.registry.call("mcp__add", {"a": 1})
        self.assertTrue(out.startswith("[ToolError"), out)


@unittest.skipUnless(HAS_FASTMCP, "fastmcp 未安装，跳过 MCP 集成测试")
class BuildManifestEdgeCaseTest(unittest.TestCase):
    def test_empty_hub_raises(self) -> None:
        empty_server = FastMCP("EmptyServer")
        hub = McpHub(empty_server, connect_timeout=10.0)
        hub.start()
        try:
            with self.assertRaises(PluginError):
                build_mcp_manifest(hub, project_root=PROJECT_ROOT)
        finally:
            hub.shutdown(timeout=3.0)


if __name__ == "__main__":
    unittest.main()
