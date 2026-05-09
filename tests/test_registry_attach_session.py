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
from cathy.plugins.registry import PluginRegistry  # noqa: E402


class _SessionAwarePlugin(ToolPlugin):
    def __init__(self) -> None:
        self.session_ids: list[str] = []

    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        return "ok"

    def attach_session(self, session_id: str) -> None:
        self.session_ids.append(session_id)


def _manifest(name: str, tool: str) -> PluginManifest:
    return PluginManifest(
        name=name,
        version="0.0.1",
        description="test",
        tools=[
            ToolSpec(
                name=tool,
                description="t",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        ],
        permissions={},
        execution=Execution(runtime="python", entrypoint="<internal>"),
        metadata={"trust_level": "builtin"},
        source_dir=Path("."),
    )


class RegistryAttachSessionTest(unittest.TestCase):
    def test_attach_session_broadcast(self) -> None:
        reg = PluginRegistry(plugins_dirs=[])
        p1 = _SessionAwarePlugin()
        p2 = _SessionAwarePlugin()
        reg.register_internal_plugin(_manifest("p1", "t1"), p1)
        reg.register_internal_plugin(_manifest("p2", "t2"), p2)

        reg.attach_session("abc123")
        self.assertEqual(p1.session_ids, ["abc123"])
        self.assertEqual(p2.session_ids, ["abc123"])


if __name__ == "__main__":
    unittest.main()

