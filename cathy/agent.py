"""Single-loop ReAct Agent（session-aware，Phase 3.5 起接入 hooks）。

主循环职责：
1. 用 ContextAssembler 把 [system] + 历史(预算内) + 当前 user 输入 拼成 messages；
2. 调用 LLM 得到下一条 assistant 消息；
3. 若包含 tool_calls，逐个执行并把结果以 role=tool 写回 messages，并**实时持久化**到 Session；
4. 否则把最终 assistant 消息持久化并返回。

Hooks 接入点（Phase 3.5）：
- UserPromptSubmit : run() 入口、user 消息持久化前
- PreToolUse       : 单个 tool_call 执行前
- PostToolUse      : 单个 tool_call 执行后
- Stop             : 准备 return final_answer 之前
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .context import ContextAssembler
from .hooks import (
    HookEvent,
    HookManager,
    POST_TOOL_USE,
    PRE_TOOL_USE,
    STOP,
    USER_PROMPT_SUBMIT,
)
from .llm import LLMClient
from .llm_errors import LLMCallError
from .plugins import PluginRegistry
from .session.models import Message, Session
from .session.store import SessionStore


@dataclass
class AgentConfig:
    max_steps: int = 12


@dataclass
class AgentTrace:
    """一次 run() 的可观测结构，便于后续接日志/审计。"""

    steps: list[dict] = field(default_factory=list)

    def add(self, step_type: str, payload: dict) -> None:
        self.steps.append({"type": step_type, **payload})


def _openai_tool_calls_to_dicts(tool_calls: Any) -> list[dict]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            },
        }
        for tc in tool_calls
    ]


class Agent:
    def __init__(
        self,
        *,
        llm: LLMClient,
        tools: PluginRegistry,
        assembler: ContextAssembler,
        store: SessionStore,
        config: AgentConfig | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        hooks: HookManager | None = None,
        permission_cfg: dict[str, Any] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.assembler = assembler
        self.store = store
        self.config = config or AgentConfig()
        self._on_event = on_event or (lambda _t, _p: None)
        self.hooks = hooks  # None -> 等价于"没装 hook"，零开销
        self.permission_cfg = permission_cfg or {}

    # ---------------- hooks 辅助 ---------------- #

    def _dispatch(self, event_type: str, **kwargs: Any):
        """便利包装：无 manager 或该事件无 hook 时直接返回 None。"""
        if self.hooks is None or not self.hooks.has_hooks_for(event_type):
            return None
        return self.hooks.dispatch(HookEvent(type=event_type, **kwargs))

    # ---------------- 主入口 ---------------- #

    def run(self, session: Session, user_input: str) -> tuple[str, AgentTrace]:
        # === Hook 1/4: UserPromptSubmit（user 消息持久化前） ===
        injected_after_user: list[str] = []
        decision = self._dispatch(
            USER_PROMPT_SUBMIT,
            session_id=session.id,
            payload={"user_input": user_input},
        )
        if decision is not None:
            if decision.rewrite_user_input is not None:
                user_input = str(decision.rewrite_user_input)
            if decision.inject_context:
                injected_after_user.append(decision.inject_context)
            if decision.block:
                reason = decision.block_reason or "[hook:UserPromptSubmit] 已阻断"
                # 持久化 user + assistant(=block 提示)，让会话历史可解释
                user_msg = Message(role="user", content=user_input)
                self.store.append_message(session.id, user_msg)
                session.append(user_msg)
                blocked_msg = Message(role="assistant", content=reason)
                self.store.append_message(session.id, blocked_msg)
                session.append(blocked_msg)
                trace = AgentTrace()
                trace.add("hook_blocked", {"event": USER_PROMPT_SUBMIT, "reason": reason})
                self._on_event("hook", {"event": USER_PROMPT_SUBMIT, "blocked": True, "reason": reason})
                return reason, trace

        # 1) 持久化 user 消息
        user_msg = Message(role="user", content=user_input)
        self.store.append_message(session.id, user_msg)
        session.append(user_msg)

        # 2) 装配本轮 messages
        messages = self.assembler.assemble(session)
        # UserPromptSubmit 注入的临时上下文以 system 形式放在 user 之后，仅本轮可见、不持久化
        for ctx in injected_after_user:
            messages.append({"role": "system", "content": f"[hook:UserPromptSubmit] {ctx}"})

        trace = AgentTrace()
        schemas = self.tools.openai_schemas() or None

        for step in range(self.config.max_steps):
            try:
                response = _chat_with_optional_stage(
                    self.llm,
                    messages,
                    tools=schemas,
                    stage="main_react",
                )
            except LLMCallError as exc:
                content = _llm_error_message(exc)
                assistant_msg = Message(role="assistant", content=content)
                self.store.append_message(session.id, assistant_msg)
                session.append(assistant_msg)
                trace.add("llm_error", {"step": step, "failure": exc.failure.to_dict()})
                self._on_event("llm_error", {"failure": exc.failure.to_dict()})
                return content, trace
            msg = response.choices[0].message

            # ---- 终止分支：无 tool_calls，准备返回 ----
            if not msg.tool_calls:
                content = (msg.content or "").strip()

                # === Hook 4/4: Stop ===
                stop_decision = self._dispatch(
                    STOP,
                    session_id=session.id,
                    payload={"final_answer": content, "step": step},
                )
                if stop_decision is not None:
                    if stop_decision.rewrite_final_answer is not None:
                        content = str(stop_decision.rewrite_final_answer)
                    if stop_decision.block:
                        # 强制再循环：把 block_reason 作为 system 提示拼进去，让 LLM 改写
                        reason = stop_decision.block_reason or "[hook:Stop] 请改写你的回答"
                        nudge = (
                            f"[hook:Stop] 你刚才的回答被拦截：{reason}。"
                            "请基于现有上下文重新作答。"
                        )
                        messages.append({"role": "system", "content": nudge})
                        trace.add("hook_blocked", {"event": STOP, "reason": reason, "step": step})
                        self._on_event("hook", {"event": STOP, "blocked": True, "reason": reason})
                        continue

                assistant_msg = Message(role="assistant", content=content)
                self.store.append_message(session.id, assistant_msg)
                session.append(assistant_msg)
                trace.add("final", {"step": step, "content": content})
                self._on_event("final", {"content": content})
                return content, trace

            # ---- tool_calls 分支 ----
            tool_calls_dicts = _openai_tool_calls_to_dicts(msg.tool_calls)
            assistant_msg = Message(
                role="assistant",
                content=msg.content or "",
                tool_calls=tool_calls_dicts,
            )
            self.store.append_message(session.id, assistant_msg)
            session.append(assistant_msg)

            # DeepSeek thinking 模式：带 tool_calls 的 assistant 消息必须把
            # reasoning_content 一起回灌，否则下一轮 400 invalid_request。
            api_assistant = assistant_msg.to_openai_dict()
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                api_assistant["reasoning_content"] = reasoning
            messages.append(api_assistant)

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                # === Hook 2/4: PreToolUse ===
                desc = None
                get_desc = getattr(self.tools, "get_tool_descriptor", None)
                if callable(get_desc):
                    desc = get_desc(name)
                trust_level = getattr(desc, "trust_level", "untrusted")

                interaction_mode = "interactive"
                try:
                    import sys as _sys

                    interaction_mode = "interactive" if _sys.stdin.isatty() else "non_interactive"
                except Exception:
                    interaction_mode = "non_interactive"

                pre_decision = self._dispatch(
                    PRE_TOOL_USE,
                    session_id=session.id,
                    matcher_target=name,
                    payload={
                        "tool": name,
                        "params": args,
                        "trust_level": trust_level,
                    },
                    meta={
                        "trust_policy": dict(
                            (self.permission_cfg.get("trust_policy") or {})
                        ),
                        "mcp_rules": dict(
                            (self.permission_cfg.get("mcp_rules") or {})
                        ),
                        "non_interactive_fallback": str(
                            self.permission_cfg.get("non_interactive_fallback") or "deny"
                        ),
                        "interaction_mode": interaction_mode,
                    },
                )
                if pre_decision is not None:
                    if pre_decision.rewrite_params is not None:
                        args = dict(pre_decision.rewrite_params)
                    if pre_decision.block:
                        reason = pre_decision.block_reason or "[hook:PreToolUse] 已阻断"
                        result = f"[ToolError:{name}][BLOCKED] {reason}"
                        self._on_event(
                            "hook",
                            {"event": PRE_TOOL_USE, "tool": name, "blocked": True, "reason": reason},
                        )
                        trace.add("tool_call", {"step": step, "name": name, "args": args})
                        trace.add(
                            "hook_blocked",
                            {"event": PRE_TOOL_USE, "tool": name, "reason": reason, "step": step},
                        )
                        tool_msg = Message(role="tool", content=result, tool_call_id=tc.id, name=name)
                        self.store.append_message(session.id, tool_msg)
                        session.append(tool_msg)
                        messages.append(tool_msg.to_openai_dict())
                        continue

                self._on_event("tool_call", {"name": name, "args": args})
                trace.add("tool_call", {"step": step, "name": name, "args": args})

                started_ms = int(time.time() * 1000)
                try:
                    result = self.tools.call(name, args)
                except Exception as exc:
                    result = _tool_error_payload(name, exc)
                latency_ms = int(time.time() * 1000) - started_ms

                # === Hook 3/4: PostToolUse ===
                post_decision = self._dispatch(
                    POST_TOOL_USE,
                    session_id=session.id,
                    matcher_target=name,
                    payload={
                        "tool": name,
                        "params": args,
                        "result": result,
                        "latency_ms": latency_ms,
                    },
                )
                if post_decision is not None and post_decision.inject_context:
                    result = f"{result}\n\n[hook:PostToolUse] {post_decision.inject_context}"

                self._on_event("tool_result", {"name": name, "result": result})
                trace.add("tool_result", {"step": step, "name": name, "result": result})

                tool_msg = Message(role="tool", content=result, tool_call_id=tc.id, name=name)
                self.store.append_message(session.id, tool_msg)
                session.append(tool_msg)
                messages.append(tool_msg.to_openai_dict())

        msg_text = f"[已达到最大步数 {self.config.max_steps}，提前结束]"
        trace.add("max_steps", {"content": msg_text})
        return msg_text, trace


def _llm_error_message(exc: LLMCallError) -> str:
    failure = exc.failure
    retry_text = "可重试错误已耗尽重试次数" if failure.retryable else "不可重试错误"
    return (
        f"[LLMError:{failure.reason}] {retry_text}: "
        f"{failure.error_type}: {failure.message}"
    )


def _tool_error_payload(tool_name: str, exc: Exception) -> str:
    return json.dumps(
        {
            "type": "tool_error",
            "tool": tool_name,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "retryable": False,
        },
        ensure_ascii=False,
    )


def _chat_with_optional_stage(
    llm: Any,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict] | None,
    stage: str,
) -> Any:
    try:
        return llm.chat(messages, tools=tools, stage=stage)
    except TypeError as exc:
        if "stage" not in str(exc):
            raise
        return llm.chat(messages, tools=tools)
