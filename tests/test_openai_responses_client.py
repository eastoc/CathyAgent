"""OpenAI Responses 客户端测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.contracts import ModelRequest  # noqa: E402
from cathy.llm_errors import LLMCallError  # noqa: E402
from cathy.model_clients.openai_responses import (  # noqa: E402
    OpenAIResponsesClient,
    convert_response_input,
    convert_response_tool,
)


class _RetryableServerError(Exception):
    status_code = 500


class OpenAIResponsesClientTest(unittest.TestCase):
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
        self.assertEqual(response.continuation_id, "resp_1")
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
                    {
                        "type": "function",
                        "function": {
                            "name": "move_arm",
                            "description": "移动末端",
                            "parameters": {
                                "type": "object",
                                "properties": {"x": {"type": "number"}},
                            },
                        },
                    }
                ],
            )
        )

        self.assertEqual(response.tool_calls[0].id, "call_1")
        self.assertEqual(response.tool_calls[0].name, "move_arm")
        self.assertEqual(response.tool_calls[0].arguments, {"x": 0.2})
        tool = openai_cls.return_value.responses.create.call_args.kwargs["tools"][0]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(tool["name"], "move_arm")
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
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_1",
                        "content": {"ok": True},
                    }
                ],
                continuation_id="resp_1",
                continuation_messages=[
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

    def test_rejects_invalid_reasoning_effort(self) -> None:
        with self.assertRaisesRegex(ValueError, "reasoning_effort"):
            OpenAIResponsesClient(
                api_key="test-key",
                base_url="https://api.openai.com/v1",
                reasoning_effort="none",
            )

    def test_rejects_tool_without_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少 name"):
            convert_response_tool({"type": "function", "function": {}})

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
