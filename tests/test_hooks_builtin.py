"""内置 hook 三件套单测：audit_log / block_dangerous_paths / strip_secrets。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.hooks import HookEvent  # noqa: E402
from cathy.hooks.builtin import (  # noqa: E402
    audit_log,
    block_dangerous_paths,
    strip_secrets,
)
from cathy.hooks.events import (  # noqa: E402
    POST_TOOL_USE,
    PRE_TOOL_USE,
    USER_PROMPT_SUBMIT,
)


class AuditLogTests(unittest.TestCase):
    def test_writes_jsonl_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "audit.jsonl"
            ev = HookEvent(
                type=POST_TOOL_USE,
                session_id="sess1",
                matcher_target="web_search",
                payload={
                    "tool": "web_search",
                    "params": {"query": "x"},
                    "result": "hello",
                    "latency_ms": 12,
                },
                meta={"audit_path": str(log_path)},
            )
            d = audit_log(ev)
            self.assertTrue(d.is_noop())
            text = log_path.read_text(encoding="utf-8").strip()
            entry = json.loads(text)
            self.assertEqual(entry["session"], "sess1")
            self.assertEqual(entry["tool"], "web_search")
            self.assertEqual(entry["latency_ms"], 12)
            self.assertEqual(entry["result_preview"], "hello")

    def test_silent_on_wrong_event(self) -> None:
        ev = HookEvent(type=PRE_TOOL_USE, payload={})
        d = audit_log(ev)
        self.assertTrue(d.is_noop())


class BlockDangerousPathsTests(unittest.TestCase):
    def test_blocks_etc_path(self) -> None:
        ev = HookEvent(
            type=PRE_TOOL_USE,
            matcher_target="write_file",
            payload={"tool": "write_file", "params": {"path": "/etc/passwd"}},
        )
        d = block_dangerous_paths(ev)
        self.assertTrue(d.block)
        self.assertIn("/etc", d.block_reason)

    def test_blocks_dot_ssh_under_home(self) -> None:
        # 模拟用户 home 路径；用 ~ 让 expanduser 解析
        ev = HookEvent(
            type=PRE_TOOL_USE,
            matcher_target="write_file",
            payload={"tool": "write_file", "params": {"path": "~/.ssh/authorized_keys"}},
        )
        d = block_dangerous_paths(ev)
        self.assertTrue(d.block)
        self.assertIn(".ssh", d.block_reason)

    def test_passes_safe_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ev = HookEvent(
                type=PRE_TOOL_USE,
                matcher_target="write_file",
                payload={"tool": "write_file", "params": {"path": os.path.join(td, "report.md")}},
            )
            d = block_dangerous_paths(ev)
            self.assertFalse(d.block)

    def test_other_tools_pass_through(self) -> None:
        ev = HookEvent(
            type=PRE_TOOL_USE,
            matcher_target="web_search",
            payload={"tool": "web_search", "params": {"query": "x"}},
        )
        self.assertTrue(block_dangerous_paths(ev).is_noop())

    def test_silent_on_wrong_event(self) -> None:
        ev = HookEvent(type=POST_TOOL_USE, payload={})
        self.assertTrue(block_dangerous_paths(ev).is_noop())


class StripSecretsTests(unittest.TestCase):
    def test_redacts_openai_key(self) -> None:
        text = "请用这个 key: sk-ABCD1234EFGH5678IJKL 调一次接口"
        ev = HookEvent(type=USER_PROMPT_SUBMIT, payload={"user_input": text})
        d = strip_secrets(ev)
        self.assertIsNotNone(d.rewrite_user_input)
        self.assertIn("[REDACTED]", d.rewrite_user_input)
        self.assertNotIn("sk-ABCD1234", d.rewrite_user_input)
        self.assertIn("strip_secrets", d.inject_context)

    def test_redacts_assignment_form(self) -> None:
        text = 'export api_key="0123456789abcdef0123" && curl ...'
        ev = HookEvent(type=USER_PROMPT_SUBMIT, payload={"user_input": text})
        d = strip_secrets(ev)
        self.assertIsNotNone(d.rewrite_user_input)
        self.assertIn("[REDACTED]", d.rewrite_user_input)

    def test_clean_text_no_change(self) -> None:
        ev = HookEvent(type=USER_PROMPT_SUBMIT, payload={"user_input": "今天天气怎么样？"})
        d = strip_secrets(ev)
        self.assertTrue(d.is_noop())

    def test_silent_on_wrong_event(self) -> None:
        ev = HookEvent(type=POST_TOOL_USE, payload={"user_input": "sk-XXXXXXXXXXXXXXXXXX"})
        self.assertTrue(strip_secrets(ev).is_noop())


if __name__ == "__main__":
    unittest.main()
