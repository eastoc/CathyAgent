"""ContextAssembler 装配 + 预算硬截断测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.context import (  # noqa: E402
    ContextAssembler,
    ROBOT_HARNESS_SYSTEM_PROMPT,
    build_system_prompt,
)
from cathy.contracts.content import text_content, text_model_content  # noqa: E402
from cathy.session.models import Message, Session  # noqa: E402


def _mk_session(messages: list[Message]) -> Session:
    return Session(
        id="t1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        metadata={},
        messages=list(messages),
    )


class ContextAssemblerTest(unittest.TestCase):
    def test_system_message_is_first(self) -> None:
        asm = ContextAssembler(token_budget=8000)
        out = asm.assemble(_mk_session([]), user_input="hi")
        self.assertGreaterEqual(len(out), 2)
        self.assertEqual(out[0]["role"], "system")
        self.assertIn("Cathy", out[0]["content"][0]["text"])
        self.assertEqual(
            out[-1],
            {"role": "user", "content": text_model_content("hi")},
        )

    def test_history_passthrough_when_within_budget(self) -> None:
        msgs = [
            Message(role="user", content=text_content("q1")),
            Message(role="assistant", content=text_content("a1")),
            Message(role="user", content=text_content("q2")),
            Message(role="assistant", content=text_content("a2")),
        ]
        asm = ContextAssembler(token_budget=8000)
        out = asm.assemble(_mk_session(msgs), user_input="q3")
        self.assertEqual(out[0]["role"], "system")
        roles = [m["role"] for m in out[1:]]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant", "user"])
        self.assertEqual(out[-1]["content"], text_model_content("q3"))

    def test_history_truncates_at_user_boundary_when_over_budget(self) -> None:
        # 构造 4 段历史，每段 user+assistant，每段约 1200 字符 → ~300 token；
        # 预算 600 token 时仅能保留最后一段 user+assistant
        long = "字" * 1200
        msgs = [
            Message(role="user", content=text_content(f"q1 {long}")),
            Message(role="assistant", content=text_content(f"a1 {long}")),
            Message(role="user", content=text_content(f"q2 {long}")),
            Message(role="assistant", content=text_content(f"a2 {long}")),
            Message(role="user", content=text_content(f"q3 {long}")),
            Message(role="assistant", content=text_content(f"a3 {long}")),
        ]
        asm = ContextAssembler(token_budget=600)
        out = asm.assemble(_mk_session(msgs))
        history_roles = [m["role"] for m in out[1:]]  # 去掉 system
        # 至少切到最近一组完整的 user+assistant 起始
        self.assertEqual(history_roles[0], "user")
        # 不会切到中间，最先出现的 user 必定是真正的对话起点之一
        self.assertNotIn("tool", history_roles)

    def test_user_input_optional(self) -> None:
        asm = ContextAssembler()
        out = asm.assemble(
            _mk_session([Message(role="user", content=text_content("x"))])
        )
        self.assertEqual(
            out[-1],
            {"role": "user", "content": text_model_content("x")},
        )

    def test_project_rules_injected_when_agents_md_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("项目级规则: 必须用中文。", encoding="utf-8")
            asm = ContextAssembler(project_root=root)
            self.assertIn("项目级规则", asm.system_prompt)
            self.assertIn("必须用中文", asm.system_prompt)

    def test_build_system_prompt_backward_compat(self) -> None:
        s = build_system_prompt()
        self.assertIn("Cathy", s)
        self.assertNotIn("web_search", s)

    def test_tool_catalog_injected_when_provided(self) -> None:
        s = build_system_prompt(tool_catalog="## 工具能力概览（自动注入）\n\n- `demo`: 示例工具")
        self.assertIn("工具能力概览", s)
        self.assertIn("demo", s)

    def test_system_override_replaces_general_agent_prompt(self) -> None:
        asm = ContextAssembler(system_override=ROBOT_HARNESS_SYSTEM_PROMPT)
        self.assertIn("具身机器人的高层视觉操作策略", asm.system_prompt)
        self.assertIn("只能输出一个合法 JSON 对象", asm.system_prompt)
        self.assertIn("action_chunk` 保留给原生 VLA 路径", asm.system_prompt)
        self.assertNotIn("Skills 使用约定", asm.system_prompt)
        self.assertNotIn("使用工具完成任务", asm.system_prompt)


if __name__ == "__main__":
    unittest.main()
