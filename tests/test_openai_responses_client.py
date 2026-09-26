"""OpenAI Responses 客户端测试。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.artifacts import LocalArtifactStore  # noqa: E402
from cathy.contracts import (  # noqa: E402
    AttachmentRef,
    ImageBlock,
    ModelRequest,
    ModelTool,
    ModelTurnState,
    TextBlock,
)
from cathy.contracts.content import serialize_content_blocks  # noqa: E402
from cathy.llm_errors import LLMCallError  # noqa: E402
from cathy.model_clients.openai_responses import (  # noqa: E402
    OpenAIResponsesClient,
    convert_response_input,
    convert_response_tool,
)


class _RetryableServerError(Exception):
    status_code = 500


class _AsyncStream:
    def __init__(self, events: list[object]) -> None:
        self._events = list(events)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def close(self) -> None:
        self.closed = True


class OpenAIResponsesClientTest(unittest.TestCase):
    @patch("cathy.model_clients.openai_responses.AsyncOpenAI")
    @patch("cathy.model_clients.openai_responses.OpenAI")
    def test_agenerate_uses_native_async_client(
        self,
        _openai_cls: MagicMock,
        async_openai_cls: MagicMock,
    ) -> None:
        async_openai_cls.return_value.responses.create = AsyncMock(
            return_value=SimpleNamespace(
                id="resp_async",
                output_text="异步完成",
                output=[],
            )
        )
        client = OpenAIResponsesClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )

        response = asyncio.run(
            client.agenerate(
                ModelRequest(messages=[{"role": "user", "content": "执行任务"}])
            )
        )

        self.assertEqual(response.text, "异步完成")
        async_openai_cls.return_value.responses.create.assert_awaited_once()

    @patch("cathy.model_clients.openai_responses.AsyncOpenAI")
    @patch("cathy.model_clients.openai_responses.OpenAI")
    def test_astream_normalizes_responses_events(
        self,
        _openai_cls: MagicMock,
        async_openai_cls: MagicMock,
    ) -> None:
        completed_response = SimpleNamespace(
            id="resp_stream",
            output_text="任务完成",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    id="fc_1",
                    name="echo",
                    arguments='{"msg":"hi"}',
                )
            ],
        )
        stream = _AsyncStream(
            [
                SimpleNamespace(type="response.created"),
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta="任务",
                ),
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta="完成",
                ),
                SimpleNamespace(
                    type="response.output_item.added",
                    output_index=1,
                    item=SimpleNamespace(
                        type="function_call",
                        id="fc_1",
                        call_id="call_1",
                        name="echo",
                    ),
                ),
                SimpleNamespace(
                    type="response.function_call_arguments.delta",
                    item_id="fc_1",
                    output_index=1,
                    delta='{"msg":"hi"}',
                ),
                SimpleNamespace(
                    type="response.output_item.done",
                    output_index=1,
                    item=SimpleNamespace(
                        type="function_call",
                        id="fc_1",
                        call_id="call_1",
                        name="echo",
                        arguments='{"msg":"hi"}',
                    ),
                ),
                SimpleNamespace(
                    type="response.completed",
                    response=completed_response,
                ),
            ]
        )
        async_openai_cls.return_value.responses.create = AsyncMock(return_value=stream)
        client = OpenAIResponsesClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )

        async def collect():
            return [
                event
                async for event in client.astream(
                    ModelRequest(messages=[{"role": "user", "content": "执行任务"}])
                )
            ]

        events = asyncio.run(collect())

        self.assertEqual(
            [event.type for event in events],
            [
                "response_started",
                "text_delta",
                "text_delta",
                "tool_call_started",
                "tool_call_delta",
                "tool_call_completed",
                "response_completed",
            ],
        )
        self.assertEqual(
            "".join(event.text for event in events),
            '任务完成{"msg":"hi"}',
        )
        self.assertEqual(events[3].metadata["call_id"], "call_1")
        self.assertEqual(events[4].metadata["item_id"], "fc_1")
        self.assertEqual(events[4].metadata["call_id"], "call_1")
        completed_call = events[5].metadata["tool_call"]
        self.assertEqual(completed_call.id, "call_1")
        self.assertEqual(completed_call.arguments, {"msg": "hi"})
        final = events[-1].response
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final.text, "任务完成")
        self.assertEqual(final.tool_calls[0].arguments, {"msg": "hi"})
        kwargs = async_openai_cls.return_value.responses.create.call_args.kwargs
        self.assertTrue(kwargs["stream"])
        self.assertTrue(stream.closed)

    @patch("cathy.model_clients.openai_responses.OpenAI")
    def test_generate_uses_astra_responses_parameters(self, openai_cls: MagicMock) -> None:
        openai_cls.return_value.responses.create.return_value = SimpleNamespace(
            id="resp_1",
            output_text="完成",
            output=[],
        )
        client = OpenAIResponsesClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-6-astra",
            reasoning_effort="low",
            max_output_tokens=8192,
        )

        response = client.generate(
            ModelRequest(messages=[{"role": "user", "content": "执行任务"}])
        )

        self.assertEqual(response.text, "完成")
        self.assertIsNotNone(response.next_turn_state)
        assert response.next_turn_state is not None
        self.assertEqual(response.next_turn_state.value, "resp_1")
        kwargs = openai_cls.return_value.responses.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-6-astra")
        self.assertEqual(kwargs["reasoning"], {"effort": "low"})
        self.assertEqual(kwargs["max_output_tokens"], 8192)
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("top_p", kwargs)
        self.assertNotIn("max_tokens", kwargs)

    @patch("cathy.model_clients.openai_responses.OpenAI")
    def test_normalizes_function_call_and_flattens_tool_schema(
        self,
        openai_cls: MagicMock,
    ) -> None:
        openai_cls.return_value.responses.create.return_value = SimpleNamespace(
            id="resp_tool",
            output_text="",
            output=[
                SimpleNamespace(
                    type="function_call",
                    id="fc_1",
                    call_id="call_1",
                    name="move_arm",
                    arguments='{"x": 0.2}',
                    async_=True,
                )
            ],
        )
        client = OpenAIResponsesClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )

        response = client.generate(
            ModelRequest(
                messages=[{"role": "user", "content": "移动机械臂"}],
                tools=[
                    ModelTool(
                        name="move_arm",
                        description="移动末端",
                        input_schema={
                            "type": "object",
                            "properties": {"x": {"type": "number"}},
                        },
                        async_hint=True,
                    )
                ],
            )
        )

        self.assertEqual(response.tool_calls[0].id, "call_1")
        self.assertEqual(response.tool_calls[0].name, "move_arm")
        self.assertEqual(response.tool_calls[0].arguments, {"x": 0.2})
        self.assertTrue(response.tool_calls[0].async_execution)
        tool = openai_cls.return_value.responses.create.call_args.kwargs["tools"][0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["name"], "move_arm")
        self.assertTrue(tool["async"])
        self.assertNotIn("function", tool)

    @patch("cathy.model_clients.openai_responses.OpenAI")
    def test_continues_with_function_output(self, openai_cls: MagicMock) -> None:
        openai_cls.return_value.responses.create.return_value = SimpleNamespace(
            id="resp_2",
            output_text="动作完成",
            output=[],
        )
        client = OpenAIResponsesClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )

        client.generate(
            ModelRequest(
                messages=[
                    {"role": "user", "content": "调用工具"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "name": "echo",
                                "arguments": {},
                                "raw_arguments": "{}",
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "content": {"ok": True},
                    }
                ],
                turn_state=ModelTurnState("resp_1"),
                delta_messages=[
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "content": {"ok": True},
                    }
                ],
            )
        )

        kwargs = openai_cls.return_value.responses.create.call_args.kwargs
        self.assertEqual(kwargs["previous_response_id"], "resp_1")
        self.assertEqual(
            kwargs["input"],
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": '{"ok": true}',
                }
            ],
        )

    def test_converts_image_input(self) -> None:
        items = convert_response_input(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "观察桌面"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,abc",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ]
        )

        self.assertEqual(items[0]["content"][0]["type"], "input_text")
        self.assertEqual(items[0]["content"][1]["type"], "input_image")
        self.assertEqual(items[0]["content"][1]["detail"], "high")

    def test_converts_neutral_image_attachment(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            store = LocalArtifactStore(td)
            ref = store.put_bytes(b"image", mime_type="image/png")
            items = convert_response_input(
                [
                    {
                        "role": "user",
                        "content": serialize_content_blocks(
                            (TextBlock("观察"), ImageBlock(ref, detail="low"))
                        ),
                    }
                ],
                attachment_resolver=store,
            )

        image_part = items[0]["content"][1]
        self.assertEqual(image_part["type"], "input_image")
        self.assertTrue(image_part["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(image_part["detail"], "low")

    def test_neutral_image_requires_attachment_resolver(self) -> None:
        ref = AttachmentRef(
            artifact_id="sha256:" + "c" * 64,
            mime_type="image/png",
            sha256="c" * 64,
            size_bytes=1,
        )
        with self.assertRaisesRegex(ValueError, "attachment_resolver"):
            convert_response_input(
                [
                    {
                        "role": "user",
                        "content": serialize_content_blocks((ImageBlock(ref),)),
                    }
                ]
            )

    def test_rejects_invalid_reasoning_effort(self) -> None:
        with self.assertRaisesRegex(ValueError, "reasoning_effort"):
            OpenAIResponsesClient(
                api_key="test-key",
                base_url="https://api.openai.com/v1",
                reasoning_effort="none",
            )

    def test_rejects_tool_without_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少 name"):
            convert_response_tool(ModelTool(name="", description="", input_schema={}))

    @patch("cathy.model_clients.openai_responses.OpenAI")
    def test_retries_retryable_errors(self, openai_cls: MagicMock) -> None:
        expected = SimpleNamespace(id="resp_retry", output_text="ok", output=[])
        openai_cls.return_value.responses.create.side_effect = [
            _RetryableServerError("temporary"),
            expected,
        ]
        client = OpenAIResponsesClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            max_retries=1,
            retry_backoff_initial_sec=0,
        )

        response = client.generate(
            ModelRequest(messages=[{"role": "user", "content": "hi"}], stage="test")
        )

        self.assertEqual(response.text, "ok")
        self.assertEqual(openai_cls.return_value.responses.create.call_count, 2)

    @patch("cathy.model_clients.openai_responses.OpenAI")
    def test_raises_structured_error_after_retries(self, openai_cls: MagicMock) -> None:
        openai_cls.return_value.responses.create.side_effect = _RetryableServerError(
            "temporary"
        )
        client = OpenAIResponsesClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            max_retries=0,
            retry_backoff_initial_sec=0,
        )

        with self.assertRaises(LLMCallError) as ctx:
            client.generate(
                ModelRequest(
                    messages=[{"role": "user", "content": "hi"}],
                    stage="astra_test",
                )
            )

        self.assertEqual(ctx.exception.failure.stage, "astra_test")


if __name__ == "__main__":
    unittest.main()
