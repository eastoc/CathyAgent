from __future__ import annotations

import sys
import unittest
from pathlib import Path
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

    @patch("cathy.llm.OpenAI")
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

        self.assertEqual(openai_cls.call_args.kwargs["max_retries"], 0)
        kwargs = completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.5")
        self.assertEqual(kwargs["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)

    @patch("cathy.llm.OpenAI")
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

    @patch("cathy.llm.OpenAI")
    def test_chat_retries_retryable_failures(self, openai_cls: MagicMock) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            max_retries=2,
            retry_backoff_initial_sec=0,
        )
        completions = openai_cls.return_value.chat.completions
        expected = MagicMock()
        completions.create.side_effect = [
            _RetryableServerError("temporary server failure"),
            expected,
        ]

        actual = client.chat([{"role": "user", "content": "hi"}], stage="unit_test")

        self.assertIs(actual, expected)
        self.assertEqual(completions.create.call_count, 2)

    @patch("cathy.llm.OpenAI")
    def test_chat_raises_structured_failure_after_non_retryable_error(
        self,
        openai_cls: MagicMock,
    ) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            max_retries=2,
            retry_backoff_initial_sec=0,
        )
        completions = openai_cls.return_value.chat.completions
        completions.create.side_effect = ValueError("bad local input")

        with self.assertRaises(LLMCallError) as context:
            client.chat([{"role": "user", "content": "hi"}], stage="unit_test")

        failure = context.exception.failure
        self.assertEqual(failure.stage, "unit_test")
        self.assertEqual(failure.reason, "unknown")
        self.assertFalse(failure.retryable)
        self.assertEqual(failure.attempts, 1)


if __name__ == "__main__":
    unittest.main()
