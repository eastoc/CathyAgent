"""HookManager 单测：matcher / 串联合并 / block 短路 / Python+Command 混用 / .claude 合并。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.hooks import HookDecision, HookEvent, HookManager  # noqa: E402
from cathy.hooks.events import (  # noqa: E402
    POST_TOOL_USE,
    PRE_TOOL_USE,
    USER_PROMPT_SUBMIT,
)


# --- 给 manager 测试准备的 hook 函数 -------------------------------------

CALL_LOG: list[str] = []


def _reset_log() -> None:
    CALL_LOG.clear()


def hk_inject_a(event: HookEvent) -> HookDecision:
    CALL_LOG.append("a")
    return HookDecision(inject_context="A")


def hk_inject_b(event: HookEvent) -> HookDecision:
    CALL_LOG.append("b")
    return HookDecision(inject_context="B")


def hk_block(event: HookEvent) -> HookDecision:
    CALL_LOG.append("block")
    return HookDecision(block=True, block_reason="stop")


def hk_should_not_run(event: HookEvent) -> HookDecision:
    CALL_LOG.append("LATER")
    return HookDecision(inject_context="LATER")


def hk_rewrite_params(event: HookEvent) -> HookDecision:
    return HookDecision(rewrite_params={"x": 1})


# -----------------------------------------------------------------------


class HookManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_log()

    def test_empty_manager_dispatch_returns_noop(self) -> None:
        mgr = HookManager()
        d = mgr.dispatch(HookEvent(type=PRE_TOOL_USE, matcher_target="x"))
        self.assertTrue(d.is_noop())
        self.assertFalse(mgr.has_hooks_for(PRE_TOOL_USE))

    def test_matcher_filters_by_tool_name(self) -> None:
        mgr = HookManager(
            {
                PRE_TOOL_USE: [
                    {
                        "matcher": "write_file",
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"}
                        ],
                    }
                ]
            }
        )
        # 不匹配
        d1 = mgr.dispatch(HookEvent(type=PRE_TOOL_USE, matcher_target="web_search"))
        self.assertTrue(d1.is_noop())
        self.assertNotIn("a", CALL_LOG)
        # 匹配
        d2 = mgr.dispatch(HookEvent(type=PRE_TOOL_USE, matcher_target="write_file"))
        self.assertEqual(d2.inject_context, "A")

    def test_matcher_alternation_pipe(self) -> None:
        mgr = HookManager(
            {
                PRE_TOOL_USE: [
                    {
                        "matcher": "write_file|read_file",
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"}
                        ],
                    }
                ]
            }
        )
        self.assertFalse(mgr.dispatch(HookEvent(type=PRE_TOOL_USE, matcher_target="other")).inject_context)
        self.assertEqual(
            mgr.dispatch(HookEvent(type=PRE_TOOL_USE, matcher_target="read_file")).inject_context,
            "A",
        )

    def test_matcher_glob_prefix(self) -> None:
        """matcher 含 `*` 时按 fnmatch glob 匹配（用于 mcp__server__* 等）。"""
        mgr = HookManager(
            {
                PRE_TOOL_USE: [
                    {
                        "matcher": "mcp__fs__write_*",
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"}
                        ],
                    }
                ]
            }
        )
        self.assertEqual(
            mgr.dispatch(
                HookEvent(type=PRE_TOOL_USE, matcher_target="mcp__fs__write_file")
            ).inject_context,
            "A",
        )
        self.assertEqual(
            mgr.dispatch(
                HookEvent(type=PRE_TOOL_USE, matcher_target="mcp__fs__write_text_file")
            ).inject_context,
            "A",
        )
        self.assertFalse(
            mgr.dispatch(
                HookEvent(type=PRE_TOOL_USE, matcher_target="mcp__fs__read_file")
            ).inject_context
        )

    def test_matcher_alternation_with_glob(self) -> None:
        """每段独立支持 glob：`write_file|mcp__*__write_*`。"""
        mgr = HookManager(
            {
                PRE_TOOL_USE: [
                    {
                        "matcher": "write_file|mcp__*__write_*",
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"}
                        ],
                    }
                ]
            }
        )
        self.assertEqual(
            mgr.dispatch(
                HookEvent(type=PRE_TOOL_USE, matcher_target="write_file")
            ).inject_context,
            "A",
        )
        self.assertEqual(
            mgr.dispatch(
                HookEvent(type=PRE_TOOL_USE, matcher_target="mcp__fs__write_file")
            ).inject_context,
            "A",
        )
        self.assertFalse(
            mgr.dispatch(
                HookEvent(type=PRE_TOOL_USE, matcher_target="mcp__fs__list_dir")
            ).inject_context
        )

    def test_chain_concatenates_inject_context(self) -> None:
        mgr = HookManager(
            {
                POST_TOOL_USE: [
                    {
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"},
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_b"},
                        ]
                    }
                ]
            }
        )
        d = mgr.dispatch(HookEvent(type=POST_TOOL_USE, matcher_target="anything"))
        self.assertEqual(d.inject_context, "A\n\nB")
        self.assertEqual(CALL_LOG, ["a", "b"])

    def test_block_short_circuit(self) -> None:
        mgr = HookManager(
            {
                PRE_TOOL_USE: [
                    {
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"},
                            {"type": "python", "target": "tests.test_hooks_manager:hk_block"},
                            {"type": "python", "target": "tests.test_hooks_manager:hk_should_not_run"},
                        ]
                    }
                ]
            }
        )
        d = mgr.dispatch(HookEvent(type=PRE_TOOL_USE, matcher_target="x"))
        self.assertTrue(d.block)
        self.assertEqual(d.block_reason, "stop")
        # 短路：第三个 hook 不应被调用
        self.assertEqual(CALL_LOG, ["a", "block"])

    def test_python_and_command_mixed(self) -> None:
        cmd = (
            "python3 -c 'import json,sys; print(json.dumps({\"inject_context\": \"from-cmd\"}))'"
        )
        mgr = HookManager(
            {
                USER_PROMPT_SUBMIT: [
                    {
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"},
                            {"type": "command", "command": cmd, "timeout": 5},
                        ]
                    }
                ]
            }
        )
        d = mgr.dispatch(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertEqual(d.inject_context, "A\n\nfrom-cmd")

    def test_invalid_python_target_skipped_at_load(self) -> None:
        # 不存在的模块 / 函数：不应让 manager 构造时崩；后续 dispatch 不调用它
        mgr = HookManager(
            {
                USER_PROMPT_SUBMIT: [
                    {
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"},
                        ]
                    },
                    {
                        "hooks": [
                            # 缺 target 字段
                            {"type": "python"},
                            # 类型未知
                            {"type": "weird", "target": "x:y"},
                        ]
                    },
                ]
            }
        )
        d = mgr.dispatch(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertEqual(d.inject_context, "A")

    def test_summary_lists_active_hooks(self) -> None:
        mgr = HookManager(
            {
                USER_PROMPT_SUBMIT: [
                    {
                        "hooks": [
                            {"type": "python", "target": "tests.test_hooks_manager:hk_inject_a"}
                        ]
                    }
                ]
            }
        )
        s = mgr.summary()
        self.assertIn(USER_PROMPT_SUBMIT, s)
        self.assertEqual(len(s[USER_PROMPT_SUBMIT]), 1)

    def test_merge_claude_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            POST_TOOL_USE: [
                                {
                                    "hooks": [
                                        {
                                            "type": "python",
                                            "target": "tests.test_hooks_manager:hk_inject_b",
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            mgr = HookManager.from_config(
                {
                    POST_TOOL_USE: [
                        {
                            "hooks": [
                                {
                                    "type": "python",
                                    "target": "tests.test_hooks_manager:hk_inject_a",
                                }
                            ]
                        }
                    ]
                },
                project_root=root,
            )
            d = mgr.dispatch(HookEvent(type=POST_TOOL_USE, matcher_target="x"))
            # YAML 在前，.claude 追加在后
            self.assertEqual(d.inject_context, "A\n\nB")


if __name__ == "__main__":
    unittest.main()
