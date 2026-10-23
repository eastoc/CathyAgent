from __future__ import annotations

import sys
import os
import platform
import shlex
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.sandbox import SandboxError, SandboxLimits, SandboxPolicy, create_executor  # noqa: E402
from cathy.sandbox.local_restricted import LocalRestrictedExecutor  # noqa: E402
from cathy.sandbox.seatbelt import SeatbeltExecutor  # noqa: E402


class SandboxFactoryTest(unittest.TestCase):
    def test_local_restricted(self) -> None:
        ex = create_executor("local_restricted")
        self.assertIsInstance(ex, LocalRestrictedExecutor)

    def test_seatbelt(self) -> None:
        ex = create_executor("seatbelt")
        self.assertIsInstance(ex, SeatbeltExecutor)

    def test_unknown_backend(self) -> None:
        with self.assertRaises(SandboxError):
            create_executor("foobar")


@unittest.skipUnless(platform.system() == "Darwin", "仅 macOS 支持 Seatbelt")
class SeatbeltCliTest(unittest.TestCase):
    def test_process_is_confined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            workspace.mkdir()
            policy = SandboxPolicy(workspace_root=workspace)
            executor = create_executor("seatbelt")
            common = dict(cwd=workspace, env=dict(os.environ),
                          limits=SandboxLimits(timeout_sec=5), policy=policy)

            inside = executor.run(command="printf allowed > inside.txt", **common)
            self.assertEqual(inside.exit_code, 0, inside.stderr)
            self.assertEqual((workspace / "inside.txt").read_text(), "allowed")

            outside = executor.run(command="printf blocked > ../outside.txt", **common)
            self.assertNotEqual(outside.exit_code, 0)
            self.assertFalse((base / "outside.txt").exists())

            network = executor.run(command=(
                f"{shlex.quote(sys.executable)} -c "
                "'import socket; socket.socket().connect((\"127.0.0.1\", 9))'"
            ), **common)
            self.assertNotEqual(network.exit_code, 0)
            self.assertIn("PermissionError", network.stderr)

    def test_explicit_denials_and_domain_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            secret = workspace / "secret.txt"
            protected = workspace / "protected.txt"
            secret.write_text("secret")
            protected.write_text("original")
            policy = SandboxPolicy(workspace_root=workspace,
                                   deny_read=[str(secret)], deny_write=[str(protected)])
            executor = create_executor("seatbelt")
            common = dict(cwd=workspace, env=dict(os.environ),
                          limits=SandboxLimits(timeout_sec=5), policy=policy)
            read = executor.run(command="/bin/cat secret.txt", **common)
            write = executor.run(command="printf changed > protected.txt", **common)
            self.assertNotEqual(read.exit_code, 0)
            self.assertNotIn("secret", read.stdout)
            self.assertNotEqual(write.exit_code, 0)
            self.assertEqual(protected.read_text(), "original")

            with self.assertRaisesRegex(SandboxError, "域名网络白名单"):
                executor.run(
                    command="true", cwd=workspace, env=dict(os.environ),
                    limits=SandboxLimits(),
                    policy=SandboxPolicy(workspace_root=workspace,
                                         allow_network=True,
                                         allowed_domains=["example.com"]),
                )


if __name__ == "__main__":
    unittest.main()

