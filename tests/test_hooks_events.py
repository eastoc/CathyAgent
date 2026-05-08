"""HookDecision 字段 + merge_decisions 行为单测。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.hooks import HookDecision, HookEvent, merge_decisions  # noqa: E402
from cathy.hooks import (  # noqa: E402
    PRE_TOOL_USE,
    USER_PROMPT_SUBMIT,
)


class HookDecisionTests(unittest.TestCase):
    def test_noop_decision_is_noop(self) -> None:
        self.assertTrue(HookDecision.noop().is_noop())

    def test_from_dict_unknown_keys_ignored(self) -> None:
        d = HookDecision.from_dict(
            {
                "block": True,
                "block_reason": "x",
                "inject_context": "ctx",
                "rewrite_params": {"a": 1},
                "garbage_field": 42,  # 未知字段不应抛
            }
        )
        self.assertTrue(d.block)
        self.assertEqual(d.block_reason, "x")
        self.assertEqual(d.inject_context, "ctx")
        self.assertEqual(d.rewrite_params, {"a": 1})

    def test_from_dict_none_returns_noop(self) -> None:
        self.assertTrue(HookDecision.from_dict(None).is_noop())
        self.assertTrue(HookDecision.from_dict({}).is_noop())


class MergeDecisionsTests(unittest.TestCase):
    def test_inject_context_concatenated_in_order(self) -> None:
        out = merge_decisions(
            [
                HookDecision(inject_context="A"),
                HookDecision(inject_context="B"),
                HookDecision(inject_context=" C "),
            ]
        )
        self.assertEqual(out.inject_context, "A\n\nB\n\nC")

    def test_block_takes_first_reason(self) -> None:
        out = merge_decisions(
            [
                HookDecision(),
                HookDecision(block=True, block_reason="first"),
                HookDecision(block=True, block_reason="second"),
            ]
        )
        self.assertTrue(out.block)
        self.assertEqual(out.block_reason, "first")

    def test_rewrite_last_writer_wins(self) -> None:
        out = merge_decisions(
            [
                HookDecision(rewrite_user_input="early"),
                HookDecision(rewrite_user_input="middle"),
                HookDecision(rewrite_user_input="last"),
            ]
        )
        self.assertEqual(out.rewrite_user_input, "last")

    def test_extra_shallow_merge(self) -> None:
        out = merge_decisions(
            [
                HookDecision(extra={"a": 1, "b": 2}),
                HookDecision(extra={"b": 99, "c": 3}),
            ]
        )
        self.assertEqual(out.extra, {"a": 1, "b": 99, "c": 3})


class HookEventTests(unittest.TestCase):
    def test_to_json_roundtrip(self) -> None:
        ev = HookEvent(
            type=PRE_TOOL_USE,
            session_id="abc",
            matcher_target="write_file",
            payload={"tool": "write_file", "params": {"path": "/tmp/x"}},
        )
        raw = ev.to_json()
        self.assertIn("write_file", raw)
        # 仅检查能被反序列化，不强校验所有字段
        import json

        d = json.loads(raw)
        self.assertEqual(d["type"], PRE_TOOL_USE)
        self.assertEqual(d["session_id"], "abc")

    def test_default_type_constants(self) -> None:
        self.assertEqual(USER_PROMPT_SUBMIT, "UserPromptSubmit")


if __name__ == "__main__":
    unittest.main()
