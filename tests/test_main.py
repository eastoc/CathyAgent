import runpy
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = PROJECT_ROOT / "main.py"


class MainEntryTest(unittest.TestCase):
    def test_import_main_module_does_not_invoke_cli(self) -> None:
        calls = {"count": 0}

        fake_cli = types.ModuleType("cathy.cli")

        def fake_main() -> None:
            calls["count"] += 1

        fake_cli.main = fake_main

        with patch.dict("sys.modules", {"cathy.cli": fake_cli}, clear=False):
            runpy.run_path(str(MAIN_PATH), run_name="not_main")

        self.assertEqual(calls["count"], 0)

    def test_run_as_script_invokes_cli_once(self) -> None:
        calls = {"count": 0}

        fake_cli = types.ModuleType("cathy.cli")

        def fake_main() -> None:
            calls["count"] += 1

        fake_cli.main = fake_main

        with patch.dict("sys.modules", {"cathy.cli": fake_cli}, clear=False):
            runpy.run_path(str(MAIN_PATH), run_name="__main__")

        self.assertEqual(calls["count"], 1)


if __name__ == "__main__":
    unittest.main()
