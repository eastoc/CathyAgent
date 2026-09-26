"""Agent 与统一 ModelClient 契约的集成测试。"""

from __future__ import annotations

import asyncio
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
    AgentRequest,
    AttachmentRef,
    ImageBlock,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelTurnState,
    TextBlock,
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


class _ConcurrentPlugin(ToolPlugin):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        raise AssertionError("Agent 应使用原生异步工具入口")

    async def aexecute(self, tool_name: str, params: dict[str, Any]) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.03)
            return f"{tool_name}:{params['value']}"
        finally:
            self.active -= 1


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


def _concurrent_registry(plugin: _ConcurrentPlugin) -> PluginRegistry:
    registry = PluginRegistry(plugins_dirs=[])
    registry.register_internal_plugin(
        PluginManifest(
            name="concurrent",
            version="0.0.1",
            description="concurrent tools",
            tools=[
                ToolSpec(
                    name=name,
                    description=name,
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                )
                for name in ("first", "second")
            ],
            permissions={},
            execution=Execution(runtime="python", entrypoint="<internal>:Concurrent"),
            metadata={"trust_level": "builtin"},
            source_dir=Path(__file__).parent,
        ),
        plugin,
    )
    return registry


class AgentModelClientTest(unittest.TestCase):
    def test_multiple_tool_calls_run_concurrently_and_preserve_message_order(self) -> None:
        plugin = _ConcurrentPlugin()
        model = _ScriptedModelClient(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            id="call_1",
                            name="first",
                            arguments={"value": "one"},
                        ),
                        ModelToolCall(
                            id="call_2",
                            name="second",
                            arguments={"value": "two"},
                        ),
                    )
                ),
                ModelResponse(text="完成"),
            ]
        )

        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(Path(td) / "session.db")
            session = store.create()
            agent = Agent(
                llm=model,
                tools=_concurrent_registry(plugin),
                assembler=ContextAssembler(token_budget=4096),
                store=store,
            )

            answer, _trace = agent.run(session, "并行执行")
            store.close()

        self.assertEqual(answer, "完成")
        self.assertEqual(plugin.max_active, 2)
        assert model.requests[1].delta_messages is not None
        self.assertEqual(
            [message["tool_call_id"] for message in model.requests[1].delta_messages],
            ["call_1", "call_2"],
        )

    def test_run_request_passes_neutral_multimodal_content(self) -> None:
        model = _ScriptedModelClient([ModelResponse(text="看到了")])
        ref = AttachmentRef(
            artifact_id="sha256:" + "b" * 64,
            mime_type="image/jpeg",
            sha256="b" * 64,
            size_bytes=10,
        )

        with tempfile.TemporaryDirectory() as td:
            store = SessionStore(Path(td) / "session.db")
            session = store.create()
            agent = Agent(
                llm=model,
                tools=_registry(),
                assembler=ContextAssembler(token_budget=4096),
                store=store,
            )

            answer, _trace = agent.run_request(
                session,
                AgentRequest(
                    content=(TextBlock("看图"), ImageBlock(ref)),
                    metadata={"task_id": "pick-1"},
                ),
            )
            loaded = store.load(session.id)
            store.close()

        self.assertEqual(answer, "看到了")
        content = model.requests[0].messages[-1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[1]["attachment"]["artifact_id"], ref.artifact_id)
        assert loaded is not None
        self.assertEqual(loaded.messages[0].content[1], ImageBlock(ref))
        self.assertEqual(loaded.messages[0].metadata["task_id"], "pick-1")

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
        self.assertEqual(
            second.delta_messages[0]["content"][0]["text"],
            "echo:ping",
        )


if __name__ == "__main__":
    unittest.main()
