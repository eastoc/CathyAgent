from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.plugins.base import PluginError  # noqa: E402
from plugins.builtin.shell_exec.main import ShellExecPlugin  # noqa: E402


class ShellExecPluginTest(unittest.TestCase):
    def _new_plugin(self, workspace: Path) -> ShellExecPlugin:
        p = ShellExecPlugin()
        p.initialize(
            {
                "backend": "local_restricted",
                "workspace_root": str(workspace),
                "default_timeout_sec": 5,
                "max_timeout_sec": 10,
                "max_output_bytes": 4096,
                "allow_network": False,
                "allowed_domains": [],
            }
        )
        return p

    def test_exec_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = self._new_plugin(Path(td))
            plugin.attach_session("s1")
            raw = plugin.execute(
                "shell_exec", {"command": "python -c \"print('ok')\"", "cwd": "."}
            )
            out = json.loads(raw)
            self.assertEqual(out["exit_code"], 0)
            self.assertIn("ok", out["stdout"])
            self.assertFalse(out["timed_out"])
            self.assertEqual(out["backend"], "local_restricted")

    def test_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = self._new_plugin(Path(td))
            plugin.attach_session("s1")
            raw = plugin.execute(
                "shell_exec",
                {"command": "python -c \"import time; time.sleep(1.2)\"", "timeout_sec": 0.2},
            )
            out = json.loads(raw)
            self.assertTrue(out["timed_out"])
            self.assertEqual(out["exit_code"], 124)

    def test_command_newline_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = self._new_plugin(Path(td))
            plugin.attach_session("s1")
            with self.assertRaises(PluginError):
                plugin.execute("shell_exec", {"command": "echo a\necho b"})

    def test_command_dangerous_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = self._new_plugin(Path(td))
            plugin.attach_session("s1")
            with self.assertRaises(PluginError):
                plugin.execute("shell_exec", {"command": "sudo ls"})

    def test_cwd_escape_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = self._new_plugin(Path(td))
            plugin.attach_session("s1")
            with self.assertRaises(PluginError):
                plugin.execute("shell_exec", {"command": "pwd", "cwd": "../"})

    def test_session_workspace_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            plugin = self._new_plugin(Path(td))
            plugin.attach_session("alpha")
            out1 = json.loads(plugin.execute("shell_exec", {"command": "pwd"}))
            self.assertIn("/alpha", out1["stdout"])

            plugin.attach_session("beta")
            out2 = json.loads(plugin.execute("shell_exec", {"command": "pwd"}))
            self.assertIn("/beta", out2["stdout"])
            self.assertNotIn("/alpha", out2["stdout"])


if __name__ == "__main__":
    unittest.main()

