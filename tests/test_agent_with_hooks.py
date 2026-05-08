"""Agent + HookManager 集成测试。

验证目标：
1. UserPromptSubmit hook block 时直接返回 reason，不走 LLM。
2. UserPromptSubmit hook rewrite_user_input：messages 第一个 user 用改写后的文本。
3. PreToolUse hook block：tool 不被实际调用，[BLOCKED] 错误回灌给 LLM；LLM 第二轮收到错误并改写。
4. PostToolUse hook inject_context：拼到 tool result 末尾。
5. Stop hook block：强制再循环一次。

手段：
- 用一个 _ScriptedLLM 假 LLM，按"轮次"返回预先编排的响应。
- 用 _SpyPlugin 假插件，记录被实际调用过哪些工具。
- 用 SessionStore 临时 SQLite。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.agent import Agent, AgentConfig  # noqa: E402
from cathy.context import ContextAssembler  # noqa: E402
from cathy.hooks import HookDecision, HookEvent, HookManager  # noqa: E402
from cathy.hooks.events import (  # noqa: E402
    POST_TOOL_USE,
    PRE_TOOL_USE,
    STOP,
    USER_PROMPT_SUBMIT,
)
from cathy.plugins.base import ToolPlugin  # noqa: E402
from cathy.plugins.manifest import Execution, PluginManifest, ToolSpec  # noqa: E402
from cathy.plugins.registry import PluginRegistry  # noqa: E402
from cathy.session.store import SessionStore  # noqa: E402


# ---------- 工具 ---------- #


class _SpyPlugin(ToolPlugin):
    """一个会记录调用历史的工具：echo(msg) -> "echo:msg"。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        self.calls.append({"tool": tool_name, "params": dict(params)})
        return f"echo:{params.get('msg', '')}"


def _spy_manifest() -> PluginManifest:
    return PluginManifest(
        name="spy",
        version="0.0.1",
        description="spy",
        tools=[
            ToolSpec(
                name="echo",
                description="echo a message",
                input_schema={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                },
            )
        ],
        permissions={"network": False, "filesystem": False},
        execution=Execution(runtime="python", entrypoint="<internal>:_SpyPlugin"),
        metadata={"trust_level": "builtin"},
        source_dir=Path(__file__).parent,
    )


# ---------- 假 LLM ---------- #


@dataclass
class _ToolCall:
    name: str
    arguments: str  # JSON 字符串
    id: str = "tc1"


def _build_response(text: str = "", tool_calls: list[_ToolCall] | None = None) -> Any:
    if tool_calls:
        wire = [
            SimpleNamespace(
                id=tc.id,
                type="function",
                function=SimpleNamespace(name=tc.name, arguments=tc.arguments),
            )
            for tc in tool_calls
        ]
        msg = SimpleNamespace(content=text or "", tool_calls=wire, reasoning_content=None)
    else:
        msg = SimpleNamespace(content=text, tool_calls=None, reasoning_content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class _ScriptedLLM:
    """按预设脚本逐轮返回。`saw_messages` 记录每次 chat() 入参的 messages 副本。"""

    model = "fake"

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.saw_messages: list[list[dict]] = []

    def chat(self, messages: list[dict[str, Any]], *, tools=None, tool_choice=None):
        self.saw_messages.append([dict(m) for m in messages])
        if not self._script:
            raise AssertionError("脚本耗尽：LLM 被多调了一次")
        return self._script.pop(0)


# ---------- 测试夹具构造器 ---------- #


def _build_agent(
    tmpdir: Path,
    *,
    llm: _ScriptedLLM,
    hooks: HookManager,
    spy: _SpyPlugin | None = None,
) -> tuple[Agent, SessionStore, _SpyPlugin]:
    spy = spy or _SpyPlugin()
    registry = PluginRegistry(plugins_dirs=[])
    registry.register_internal_plugin(_spy_manifest(), spy)
    assembler = ContextAssembler(token_budget=4096, hooks=hooks)
    store = SessionStore(tmpdir / "sess.db")
    agent = Agent(
        llm=llm,  # type: ignore[arg-type]
        tools=registry,
        assembler=assembler,
        store=store,
        config=AgentConfig(max_steps=6),
        hooks=hooks,
    )
    return agent, store, spy


# ---------- 给 hook 用的模块级函数 ---------- #


def hk_block_user(_event: HookEvent) -> HookDecision:
    return HookDecision(block=True, block_reason="禁止该话题")


def hk_rewrite_user(_event: HookEvent) -> HookDecision:
    return HookDecision(rewrite_user_input="改写后的输入")


def hk_pre_tool_block(event: HookEvent) -> HookDecision:
    if (event.payload or {}).get("tool") == "echo":
        return HookDecision(block=True, block_reason="echo 暂停服务")
    return HookDecision.noop()


def hk_post_tool_inject(_event: HookEvent) -> HookDecision:
    return HookDecision(inject_context="附加备注：来自 hook")


_STOP_BLOCK_STATE = {"hits": 0}


def hk_stop_block_once(_event: HookEvent) -> HookDecision:
    _STOP_BLOCK_STATE["hits"] += 1
    if _STOP_BLOCK_STATE["hits"] == 1:
        return HookDecision(block=True, block_reason="第一稿不行，重写")
    return HookDecision.noop()


# ---------------- Tests ---------------- #


class AgentHooksIntegrationTests(unittest.TestCase):
    def test_user_prompt_submit_block_short_circuits(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            llm = _ScriptedLLM([])  # 不应被调用
            hooks = HookManager(
                {
                    USER_PROMPT_SUBMIT: [
                        {
                            "hooks": [
                                {
                                    "type": "python",
                                    "target": "tests.test_agent_with_hooks:hk_block_user",
                                }
                            ]
                        }
                    ]
                }
            )
            agent, store, spy = _build_agent(Path(td), llm=llm, hooks=hooks)
            session = store.create()

            reply, trace = agent.run(session, "敏感问题")
            self.assertEqual(reply, "禁止该话题")
            self.assertEqual(llm.saw_messages, [])  # LLM 没被调用
            self.assertEqual(spy.calls, [])
            # session 里能看到 user + assistant 一对消息
            session2 = store.load(session.id)
            assert session2 is not None
            roles = [m.role for m in session2.messages]
            self.assertEqual(roles, ["user", "assistant"])
            store.close()

    def test_user_prompt_submit_rewrite_propagates_to_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            llm = _ScriptedLLM([_build_response(text="ok")])
            hooks = HookManager(
                {
                    USER_PROMPT_SUBMIT: [
                        {
                            "hooks": [
                                {
                                    "type": "python",
                                    "target": "tests.test_agent_with_hooks:hk_rewrite_user",
                                }
                            ]
                        }
                    ]
                }
            )
            agent, store, _ = _build_agent(Path(td), llm=llm, hooks=hooks)
            session = store.create()
            reply, _ = agent.run(session, "原始输入")
            self.assertEqual(reply, "ok")
            # 第一轮发给 LLM 的 messages 里 user 内容应是改写后的
            self.assertTrue(llm.saw_messages)
            user_msgs = [m for m in llm.saw_messages[0] if m["role"] == "user"]
            self.assertTrue(user_msgs)
            self.assertEqual(user_msgs[-1]["content"], "改写后的输入")
            store.close()

    def test_pre_tool_use_block_prevents_actual_call_and_self_corrects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            # 第一轮：LLM 调 echo；hook block 后，第二轮：LLM 直接给文字回答
            scripted = [
                _build_response(
                    tool_calls=[_ToolCall(name="echo", arguments='{"msg": "hi"}', id="t1")]
                ),
                _build_response(text="改用文字回答"),
            ]
            llm = _ScriptedLLM(scripted)
            hooks = HookManager(
                {
                    PRE_TOOL_USE: [
                        {
                            "matcher": "echo",
                            "hooks": [
                                {
                                    "type": "python",
                                    "target": "tests.test_agent_with_hooks:hk_pre_tool_block",
                                }
                            ],
                        }
                    ]
                }
            )
            agent, store, spy = _build_agent(Path(td), llm=llm, hooks=hooks)
            session = store.create()

            reply, trace = agent.run(session, "请帮我 echo 一下")
            self.assertEqual(reply, "改用文字回答")
            # 真实工具没被调用
            self.assertEqual(spy.calls, [])
            # 第二轮 messages 中能看到 [BLOCKED] 错误以 role=tool 形式回灌
            self.assertEqual(len(llm.saw_messages), 2)
            second_round_tool_msgs = [m for m in llm.saw_messages[1] if m.get("role") == "tool"]
            self.assertTrue(second_round_tool_msgs)
            self.assertIn("[BLOCKED]", second_round_tool_msgs[-1]["content"])
            self.assertIn("echo 暂停服务", second_round_tool_msgs[-1]["content"])
            # trace 里也有 hook_blocked 记录
            self.assertTrue(any(s.get("type") == "hook_blocked" for s in trace.steps))
            store.close()

    def test_post_tool_use_inject_context_appended_to_result(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            scripted = [
                _build_response(
                    tool_calls=[_ToolCall(name="echo", arguments='{"msg": "hi"}', id="t1")]
                ),
                _build_response(text="完成"),
            ]
            llm = _ScriptedLLM(scripted)
            hooks = HookManager(
                {
                    POST_TOOL_USE: [
                        {
                            "hooks": [
                                {
                                    "type": "python",
                                    "target": "tests.test_agent_with_hooks:hk_post_tool_inject",
                                }
                            ]
                        }
                    ]
                }
            )
            agent, store, spy = _build_agent(Path(td), llm=llm, hooks=hooks)
            session = store.create()
            reply, _ = agent.run(session, "echo")
            self.assertEqual(reply, "完成")
            self.assertEqual(len(spy.calls), 1)
            second_round = llm.saw_messages[1]
            tool_contents = [m["content"] for m in second_round if m.get("role") == "tool"]
            self.assertTrue(tool_contents)
            self.assertIn("echo:hi", tool_contents[-1])
            self.assertIn("hook:PostToolUse", tool_contents[-1])
            self.assertIn("附加备注：来自 hook", tool_contents[-1])
            store.close()

    def test_stop_block_forces_one_more_round(self) -> None:
        _STOP_BLOCK_STATE["hits"] = 0  # 重置
        with tempfile.TemporaryDirectory() as td:
            # 第一轮 LLM 给一份不通过的草稿；hook block 后第二轮才接受
            scripted = [
                _build_response(text="第一稿"),
                _build_response(text="第二稿(终稿)"),
            ]
            llm = _ScriptedLLM(scripted)
            hooks = HookManager(
                {
                    STOP: [
                        {
                            "hooks": [
                                {
                                    "type": "python",
                                    "target": "tests.test_agent_with_hooks:hk_stop_block_once",
                                }
                            ]
                        }
                    ]
                }
            )
            agent, store, _ = _build_agent(Path(td), llm=llm, hooks=hooks)
            session = store.create()
            reply, trace = agent.run(session, "随便写一段")
            self.assertEqual(reply, "第二稿(终稿)")
            # LLM 被多调了一次
            self.assertEqual(len(llm.saw_messages), 2)
            self.assertEqual(_STOP_BLOCK_STATE["hits"], 2)
            self.assertTrue(
                any(s.get("type") == "hook_blocked" and s.get("event") == STOP for s in trace.steps)
            )
            # session 里只持久化了最终 assistant，没把第一稿写进去
            session2 = store.load(session.id)
            assert session2 is not None
            assistant_contents = [m.content for m in session2.messages if m.role == "assistant"]
            self.assertEqual(assistant_contents, ["第二稿(终稿)"])
            store.close()


if __name__ == "__main__":
    unittest.main()
