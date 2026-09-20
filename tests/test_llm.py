from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.llm import (  # noqa: E402
    LLMClient,
    apply_temperature,
    apply_token_limit,
    supports_configurable_temperature,
    uses_max_completion_tokens,
)
from cathy.llm_errors import LLMCallError  # noqa: E402
from cathy.contracts import ModelClient, ModelRequest  # noqa: E402


class _RetryableServerError(Exception):
    status_code = 500


class LLMClientTest(unittest.TestCase):
    def test_uses_max_completion_tokens_for_new_openai_models(self) -> None:
        self.assertTrue(uses_max_completion_tokens("gpt-5.5"))
        self.assertTrue(uses_max_completion_tokens("gpt-5.4-mini"))
        self.assertTrue(uses_max_completion_tokens("o1"))
        self.assertTrue(uses_max_completion_tokens("o3-mini"))
        self.assertTrue(uses_max_completion_tokens("o4-mini-deep-research"))

    def test_uses_max_tokens_for_legacy_models(self) -> None:
        self.assertFalse(uses_max_completion_tokens("gpt-4o"))
        self.assertFalse(uses_max_completion_tokens("gpt-4o-mini"))
        self.assertFalse(uses_max_completion_tokens("deepseek-chat"))
        self.assertFalse(uses_max_completion_tokens("qwen-plus"))

    def test_apply_token_limit_uses_correct_field(self) -> None:
        legacy_kwargs: dict[str, object] = {}
        apply_token_limit(legacy_kwargs, model="gpt-4o-mini", max_tokens=2048)
        self.assertEqual(legacy_kwargs, {"max_tokens": 2048})

        modern_kwargs: dict[str, object] = {}
        apply_token_limit(modern_kwargs, model="gpt-5.5", max_tokens=8192)
        self.assertEqual(modern_kwargs, {"max_completion_tokens": 8192})

    def test_apply_temperature_skips_new_openai_models(self) -> None:
        self.assertFalse(supports_configurable_temperature("gpt-5.5"))
        self.assertTrue(supports_configurable_temperature("gpt-4o-mini"))

        modern_kwargs: dict[str, object] = {}
        apply_temperature(modern_kwargs, model="gpt-5.5", temperature=0.7)
        self.assertEqual(modern_kwargs, {})

        legacy_kwargs: dict[str, object] = {}
        apply_temperature(legacy_kwargs, model="gpt-4o-mini", temperature=0.7)
        self.assertEqual(legacy_kwargs, {"temperature": 0.7})

    @patch("cathy.model_clients.openai_compatible_chat.OpenAI")
    def test_chat_uses_max_completion_tokens_for_gpt5(self, openai_cls: MagicMock) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-5.5",
            temperature=0.7,
            max_tokens=4096,
        )
        completions = openai_cls.return_value.chat.completions
        completions.create.return_value = MagicMock()

        client.chat([{"role": "user", "content": "hi"}])

        kwargs = completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.5")
        self.assertEqual(kwargs["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)

    @patch("cathy.model_clients.openai_compatible_chat.OpenAI")
    def test_chat_uses_max_tokens_for_gpt4o(self, openai_cls: MagicMock) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            temperature=0.7,
            max_tokens=2048,
        )
        completions = openai_cls.return_value.chat.completions
        completions.create.return_value = MagicMock()

        client.chat([{"role": "user", "content": "hi"}])

        kwargs = completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-4o-mini")
        self.assertEqual(kwargs["max_tokens"], 2048)
        self.assertEqual(kwargs["temperature"], 0.7)
        self.assertNotIn("max_completion_tokens", kwargs)

    @patch("cathy.model_clients.openai_compatible_chat.OpenAI")
    def test_chat_retries_retryable_errors(self, openai_cls: MagicMock) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            max_retries=1,
            retry_backoff_initial_sec=0,
        )
        completions = openai_cls.return_value.chat.completions
        expected = MagicMock()
        completions.create.side_effect = [_RetryableServerError("temporary"), expected]

        actual = client.chat([{"role": "user", "content": "hi"}], stage="test")

        self.assertIs(actual, expected)
        self.assertEqual(completions.create.call_count, 2)

    @patch("cathy.model_clients.openai_compatible_chat.OpenAI")
    def test_chat_raises_structured_failure_after_retries(self, openai_cls: MagicMock) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            max_retries=1,
            retry_backoff_initial_sec=0,
        )
        completions = openai_cls.return_value.chat.completions
        completions.create.side_effect = _RetryableServerError("temporary")

        with self.assertRaises(LLMCallError) as ctx:
            client.chat([{"role": "user", "content": "hi"}], stage="test_stage")

        self.assertEqual(ctx.exception.failure.reason, "server_error")
        self.assertTrue(ctx.exception.failure.retryable)
        self.assertEqual(ctx.exception.failure.stage, "test_stage")
        self.assertEqual(ctx.exception.failure.attempts, 2)

    @patch("cathy.model_clients.openai_compatible_chat.OpenAI")
    def test_generate_normalizes_text_and_tool_calls(self, openai_cls: MagicMock) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="qwen-plus",
        )
        self.assertIsInstance(client, ModelClient)

        message = SimpleNamespace(
            content="准备调用工具",
            reasoning_content="内部推理",
            tool_calls=[
                SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(
                        name="echo",
                        arguments='{"msg": "你好"}',
                    ),
                )
            ],
        )
        openai_cls.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message)]
        )

        response = client.generate(
            ModelRequest(
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "echo"}}],
                stage="contract_test",
            )
        )

        self.assertEqual(response.text, "准备调用工具")
        self.assertEqual(response.reasoning_content, "内部推理")
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].id, "call_1")
        self.assertEqual(response.tool_calls[0].name, "echo")
        self.assertEqual(response.tool_calls[0].arguments, {"msg": "你好"})

        kwargs = openai_cls.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen-plus")
        self.assertEqual(kwargs["tool_choice"], "auto")


if __name__ == "__main__":
    unittest.main()
