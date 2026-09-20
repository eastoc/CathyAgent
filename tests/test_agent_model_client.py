"""Agent 与统一 ModelClient 契约的集成测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.agent import Agent, AgentConfig  # noqa: E402
from cathy.context import ContextAssembler  # noqa: E402
from cathy.contracts import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelTurnState,
)
from cathy.plugins.base import ToolPlugin  # noqa: E402
from cathy.plugins.manifest import Execution, PluginManifest, ToolSpec  # noqa: E402
from cathy.plugins.registry import PluginRegistry  # noqa: E402
from cathy.session.store import SessionStore  # noqa: E402


class _EchoPlugin(ToolPlugin):
    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        return f"echo:{params.get('msg', '')}"


class _ScriptedModelClient:
    model = "gpt-6-astra"

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("模型调用次数超出脚本预设")
        return self.responses.pop(0)


def _registry() -> PluginRegistry:
    registry = PluginRegistry(plugins_dirs=[])
    registry.register_internal_plugin(
        PluginManifest(
            name="echo",
            version="0.0.1",
            description="echo",
            tools=[
                ToolSpec(
                    name="echo",
                    description="echo",
                    input_schema={
                        "type": "object",
                        "properties": {"msg": {"type": "string"}},
                        "required": ["msg"],
                    },
                )
            ],
            permissions={},
            execution=Execution(runtime="python", entrypoint="<internal>:Echo"),
            metadata={"trust_level": "builtin"},
            source_dir=Path(__file__).parent,
        ),
        _EchoPlugin(),
    )
    return registry


class AgentModelClientTest(unittest.TestCase):
    def test_responses_tool_loop_uses_continuation_delta(self) -> None:
        model = _ScriptedModelClient(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            id="call_1",
                            name="echo",
                            arguments={"msg": "ping"},
                            raw_arguments='{"msg":"ping"}',
                        ),
                    ),
                    next_turn_state=ModelTurnState("resp_1"),
                ),
                ModelResponse(
                    text="最终答案",
                    next_turn_state=ModelTurnState("resp_2"),
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(Path(td) / "session.db")
            session = store.create()
            agent = Agent(
                llm=model,
                tools=_registry(),
                assembler=ContextAssembler(token_budget=4096),
                store=store,
                config=AgentConfig(max_steps=3),
            )

            answer, _trace = agent.run(session, "调用 echo")
            store.close()

        self.assertEqual(answer, "最终答案")
        self.assertEqual(len(model.requests), 2)
        second = model.requests[1]
        self.assertIsNotNone(second.turn_state)
        assert second.turn_state is not None
        self.assertEqual(second.turn_state.value, "resp_1")
        self.assertIsNotNone(second.delta_messages)
        assert second.delta_messages is not None
        self.assertEqual(len(second.delta_messages), 1)
        self.assertEqual(second.delta_messages[0]["role"], "tool")
        self.assertEqual(
            second.delta_messages[0]["tool_call_id"],
            "call_1",
        )
        self.assertEqual(second.delta_messages[0]["content"], "echo:ping")


if __name__ == "__main__":
    unittest.main()
