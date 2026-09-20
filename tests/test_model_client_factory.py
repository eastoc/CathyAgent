"""模型客户端工厂测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.model_clients import OpenAICompatibleChatClient, build_model_client  # noqa: E402


class ModelClientFactoryTest(unittest.TestCase):
    @patch("cathy.model_clients.openai_compatible_chat.OpenAI")
    def test_builds_qwen_compatible_chat_client(self, openai_cls) -> None:
        client = build_model_client(
            {
                "type": "openai_compatible_chat",
                "api_key": "qwen-key",
                "api_base": "https://dashscope.example/v1",
                "model": "qwen3.5-small",
                "extra_body": {"enable_thinking": True},
            }
        )

        self.assertIsInstance(client, OpenAICompatibleChatClient)
        self.assertEqual(client.model, "qwen3.5-small")
        self.assertEqual(client.extra_body, {"enable_thinking": True})

        client.chat([{"role": "user", "content": "你好"}])
        kwargs = openai_cls.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": True})

    @patch("cathy.model_clients.openai_compatible_chat.OpenAI")
    def test_legacy_openai_compatible_type_remains_supported(self, _openai_cls) -> None:
        client = build_model_client(
            {
                "type": "openai_compatible",
                "api_key": "qwen-key",
                "api_base": "https://dashscope.example/v1",
                "model": "qwen3.5-small",
            }
        )
        self.assertIsInstance(client, OpenAICompatibleChatClient)

    def test_rejects_unknown_client_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "暂不支持"):
            build_model_client(
                {
                    "type": "unknown",
                    "api_key": "key",
                    "api_base": "https://example.test/v1",
                    "model": "example",
                }
            )


if __name__ == "__main__":
    unittest.main()
