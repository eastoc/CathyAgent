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


if __name__ == "__main__":
    unittest.main()
