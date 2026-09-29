import asyncio
import runpy
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = PROJECT_ROOT / "main.py"


class _CapturingModel:
    model = "test-model"

    def __init__(self) -> None:
        self.requests = []

    def generate(self, request):
        from cathy.contracts import ModelResponse

        self.requests.append(request)
        return ModelResponse(text='{"type":"stop","reason":"task_complete"}')


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


class RobotHarnessRuntimeProfileTest(unittest.TestCase):
    @patch("cathy.cli._maybe_register_mcp")
    @patch("cathy.cli.build_model_client")
    @patch("cathy.cli.get_llm")
    def test_robot_harness_sends_no_tools_or_agent_catalogs(
        self,
        get_llm: Mock,
        build_model_client: Mock,
        register_mcp: Mock,
    ) -> None:
        from cathy.cli import build_runtime
        from cathy.session.store import SessionStore

        model = _CapturingModel()
        get_llm.return_value = {"name": "test", "api_key": "test-key"}
        build_model_client.return_value = model
        cfg = {
            "RUNTIME_PROFILE": "robot_harness",
            "AGENT": {"max_steps": 1},
            "SESSION": {"token_budget": 8000},
            # 即使误设为 true，robot harness 也不得启动 MCP。
            "MCP": {"enabled": True, "mcp_servers": {"should_not_start": {}}},
            "HOOKS": {},
        }

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.db")
            try:
                agent, returned_store, _hooks = build_runtime(cfg, store=store)
                session = store.create()
                result = asyncio.run(agent.arun(session, "停止"))

                self.assertIs(returned_store, store)
                self.assertEqual(result.content, '{"type":"stop","reason":"task_complete"}')
                self.assertEqual(agent.tools.model_tools(), [])
                self.assertEqual(agent.tools.list_tools(), [])
                self.assertIsNone(model.requests[0].tools)
                self.assertIn(
                    "具身机器人的高层视觉操作策略",
                    agent.assembler.system_prompt,
                )
                self.assertNotIn("工具能力概览", agent.assembler.system_prompt)
                self.assertNotIn("可用 Skills", agent.assembler.system_prompt)
                self.assertNotIn("planner_executor", agent.assembler.system_prompt)
                self.assertNotIn("search_agent", agent.assembler.system_prompt)
                register_mcp.assert_not_called()
            finally:
                store.close()

    def test_unknown_runtime_profile_is_rejected(self) -> None:
        from cathy.cli import build_runtime

        with self.assertRaisesRegex(ValueError, "未知 RUNTIME_PROFILE"):
            build_runtime({"RUNTIME_PROFILE": "unknown"})


if __name__ == "__main__":
    unittest.main()
