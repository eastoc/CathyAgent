"""ToolView 白/黑名单视图测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.plugins.base import ToolPlugin  # noqa: E402
from cathy.plugins.manifest import Execution, PluginManifest, ToolSpec  # noqa: E402
from cathy.plugins.registry import PluginRegistry, ToolView  # noqa: E402


class _EchoPlugin(ToolPlugin):
    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        return f"{tool_name}:{params.get('msg', '')}"


def _make_manifest(plugin_name: str, tool_names: list[str]) -> PluginManifest:
    return PluginManifest(
        name=plugin_name,
        version="0.0.1",
        description="test",
        tools=[
            ToolSpec(
                name=t,
                description=f"echo {t}",
                input_schema={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                    "additionalProperties": False,
                },
            )
            for t in tool_names
        ],
        permissions={},
        execution=Execution(runtime="python", entrypoint="<internal>:_EchoPlugin"),
        metadata={"trust_level": "builtin"},
        source_dir=Path("."),
    )


class ToolViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = PluginRegistry(plugins_dirs=[])
        self.reg.register_internal_plugin(
            _make_manifest("p_a", ["alpha", "beta"]),
            _EchoPlugin(),
        )
        self.reg.register_internal_plugin(
            _make_manifest("p_b", ["gamma"]),
            _EchoPlugin(),
        )

    def test_register_internal_plugin_dedupes_tool_name(self) -> None:
        with self.assertRaises(Exception):
            self.reg.register_internal_plugin(
                _make_manifest("p_dup", ["alpha"]), _EchoPlugin()
            )

    def test_view_allowed_only(self) -> None:
        view = ToolView(self.reg, allowed=["alpha", "gamma"])
        names = sorted(s["function"]["name"] for s in view.openai_schemas())
        self.assertEqual(names, ["alpha", "gamma"])
        self.assertEqual(view.call("alpha", {"msg": "hi"}), "alpha:hi")
        self.assertIn("不在", view.call("beta", {"msg": "x"}))

    def test_view_blocked_overrides_allowed(self) -> None:
        view = ToolView(self.reg, allowed=["alpha", "beta"], blocked=["beta"])
        names = sorted(s["function"]["name"] for s in view.openai_schemas())
        self.assertEqual(names, ["alpha"])
        self.assertIn("不在", view.call("beta", {"msg": "x"}))

    def test_view_default_inherits_all(self) -> None:
        view = ToolView(self.reg)
        names = sorted(s["function"]["name"] for s in view.openai_schemas())
        self.assertEqual(names, ["alpha", "beta", "gamma"])

    def test_view_invalid_args_propagate(self) -> None:
        view = ToolView(self.reg)
        out = view.call("alpha", {})  # 缺 msg → schema 校验失败
        self.assertTrue(out.startswith("[ToolError:alpha]"))


if __name__ == "__main__":
    unittest.main()
