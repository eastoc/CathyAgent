from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.sandbox import SandboxError, create_executor  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()

