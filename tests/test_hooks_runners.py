"""PythonRunner / CommandRunner 后端单测。

CommandRunner 用 /bin/sh 跑内联命令，不依赖外部脚本。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.hooks import HookDecision, HookEvent  # noqa: E402
from cathy.hooks.events import PRE_TOOL_USE, USER_PROMPT_SUBMIT  # noqa: E402
from cathy.hooks.runners import (  # noqa: E402
    CommandRunner,
    HookSpec,
    PythonRunner,
    build_runner,
)


# 给 PythonRunner 准备的目标函数；通过 'tests.test_hooks_runners:<name>' 引用。

def hook_returns_decision(event: HookEvent) -> HookDecision:
    return HookDecision(inject_context=f"got:{event.type}")


def hook_returns_dict(event: HookEvent) -> dict:
    return {"block": True, "block_reason": f"blocked:{event.matcher_target}"}


def hook_returns_none(event: HookEvent) -> None:
    return None


def hook_raises(event: HookEvent) -> HookDecision:
    raise RuntimeError("boom")


class PythonRunnerTests(unittest.TestCase):
    def test_returns_decision_passthrough(self) -> None:
        spec = HookSpec(runner_type="python", target="tests.test_hooks_runners:hook_returns_decision")
        runner = build_runner(spec)
        self.assertIsInstance(runner, PythonRunner)
        d, err = runner.run(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertEqual(err, "")
        self.assertEqual(d.inject_context, f"got:{USER_PROMPT_SUBMIT}")

    def test_returns_dict_converted(self) -> None:
        spec = HookSpec(runner_type="python", target="tests.test_hooks_runners:hook_returns_dict")
        runner = build_runner(spec)
        d, err = runner.run(HookEvent(type=PRE_TOOL_USE, matcher_target="write_file"))
        self.assertEqual(err, "")
        self.assertTrue(d.block)
        self.assertEqual(d.block_reason, "blocked:write_file")

    def test_returns_none_is_noop(self) -> None:
        spec = HookSpec(runner_type="python", target="tests.test_hooks_runners:hook_returns_none")
        runner = build_runner(spec)
        d, err = runner.run(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertEqual(err, "")
        self.assertTrue(d.is_noop())

    def test_exception_swallowed(self) -> None:
        spec = HookSpec(runner_type="python", target="tests.test_hooks_runners:hook_raises")
        runner = build_runner(spec)
        d, err = runner.run(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertTrue(d.is_noop())
        self.assertIn("RuntimeError", err)
        self.assertIn("boom", err)

    def test_invalid_target_format(self) -> None:
        spec = HookSpec(runner_type="python", target="bad_no_colon")
        runner = build_runner(spec)
        d, err = runner.run(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertTrue(d.is_noop())
        self.assertIn("module:func", err)


class CommandRunnerTests(unittest.TestCase):
    def test_stdout_json_to_decision(self) -> None:
        cmd = """python3 -c 'import json,sys; print(json.dumps({"inject_context": "hi"}))'"""
        runner = build_runner(HookSpec(runner_type="command", command=cmd, timeout_sec=5))
        d, err = runner.run(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertEqual(err, "")
        self.assertEqual(d.inject_context, "hi")

    def test_exit_2_means_block(self) -> None:
        # 兼容 Claude Code 的 hook 协议：exit 2 = block，stderr 作为 reason
        cmd = """python3 -c 'import sys; print("forbidden", file=sys.stderr); sys.exit(2)'"""
        runner = build_runner(HookSpec(runner_type="command", command=cmd, timeout_sec=5))
        d, err = runner.run(HookEvent(type=PRE_TOOL_USE, matcher_target="write_file"))
        self.assertEqual(err, "")
        self.assertTrue(d.block)
        self.assertIn("forbidden", d.block_reason)

    def test_timeout_swallowed_as_noop(self) -> None:
        # sleep 远超 timeout_sec=0.5；runner 应当 kill 子进程并返回 noop + 错误描述
        cmd = """python3 -c 'import time; time.sleep(5)'"""
        runner = build_runner(HookSpec(runner_type="command", command=cmd, timeout_sec=0.5))
        d, err = runner.run(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertTrue(d.is_noop())
        self.assertIn("超时", err)

    def test_empty_stdout_is_noop(self) -> None:
        cmd = """python3 -c 'pass'"""
        runner = build_runner(HookSpec(runner_type="command", command=cmd, timeout_sec=5))
        d, err = runner.run(HookEvent(type=USER_PROMPT_SUBMIT))
        self.assertEqual(err, "")
        self.assertTrue(d.is_noop())

    def test_event_payload_passed_via_stdin(self) -> None:
        # 子进程读 stdin 解析 JSON，把 payload.tool 回写到 inject_context
        cmd = (
            "python3 -c 'import json,sys; ev=json.load(sys.stdin); "
            "print(json.dumps({\"inject_context\": ev[\"payload\"][\"tool\"]}))'"
        )
        runner = build_runner(HookSpec(runner_type="command", command=cmd, timeout_sec=5))
        d, err = runner.run(
            HookEvent(
                type=PRE_TOOL_USE,
                matcher_target="write_file",
                payload={"tool": "write_file"},
            )
        )
        self.assertEqual(err, "")
        self.assertEqual(d.inject_context, "write_file")


if __name__ == "__main__":
    unittest.main()
