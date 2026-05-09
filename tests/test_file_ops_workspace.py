from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.plugins.base import PluginError  # noqa: E402
from plugins.builtin.file_ops.main import FileOpsPlugin  # noqa: E402


class FileOpsWorkspaceIsolationTest(unittest.TestCase):
    def _new_plugin(self, workspace_parent: Path) -> FileOpsPlugin:
        p = FileOpsPlugin()
        p.initialize({"workspace_root": str(workspace_parent)})
        return p

    def test_per_session_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            plugin = self._new_plugin(parent)

            plugin.attach_session("s1")
            plugin.execute("write_file", {"path": "a.txt", "content": "hello"})
            self.assertIn("a.txt", plugin.execute("list_dir", {"path": "."}))

            plugin.attach_session("s2")
            # s2 看不到 s1 内容
            out = plugin.execute("list_dir", {"path": "."})
            self.assertIn("(空目录)", out)
            # 也不能通过相对路径回跳到 s1
            with self.assertRaises(PluginError):
                plugin.execute("read_file", {"path": "../s1/a.txt"})

    def test_attach_session_requires_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = self._new_plugin(Path(td))
            with self.assertRaises(PluginError):
                plugin.attach_session("")


if __name__ == "__main__":
    unittest.main()

