"""Session-aware 的异步 ReAct Agent 主循环。

astream() 是唯一真实执行入口；arun() 负责收集结果，run()/run_request()
仅保留为同步兼容层。当前阶段保持工具串行执行，后续在 ToolExecutionPolicy
就绪后再按资源键引入受控并发。
"""

from __future__ import annotations

import asyncio
import functools
import json
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from .contracts import (
    AgentEvent,
    AgentRequest,
    AgentResult,
    AttachmentResolver,
    ModelClient,
    ModelToolCall,
    RunContext,
)
from .contracts.content import serialize_content_blocks, text_content, text_model_content
from .context import ContextAssembler
from .hooks import (
    HookEvent,
    HookManager,
    POST_TOOL_USE,
    PRE_TOOL_USE,
    STOP,
    USER_PROMPT_SUBMIT,
)
from .llm_errors import LLMCallError
from .model_clients.invoke import agenerate_model_response
from .plugins import PluginRegistry
from .session.models import Message, Session
from .session.store import SessionStore


@dataclass
class AgentConfig:
    max_steps: int = 12


@dataclass
class AgentTrace:
    """一次运行的兼容 trace；后续可由 AgentEvent 持久化替代。"""

    steps: list[dict] = field(default_factory=list)

    def add(self, step_type: str, payload: dict) -> None:
        self.steps.append({"type": step_type, **payload})


class _EventFactory:
    def __init__(self, *, run_id: str, session_id: str) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.sequence = 0

    def create(self, event_type: str, payload: dict[str, Any] | None = None) -> AgentEvent:
        self.sequence += 1
        return AgentEvent(
            type=event_type,
            run_id=self.run_id,
            session_id=self.session_id,
            sequence=self.sequence,
            timestamp=time.time(),
            payload=payload or {},
        )


def _model_tool_calls_to_dicts(tool_calls: tuple[ModelToolCall, ...]) -> list[dict]:
    return [
        {
            "id": tc.id,
            "name": tc.name,
            "arguments": dict(tc.arguments),
            "raw_arguments": tc.raw_arguments,
        }
        for tc in tool_calls
    ]


class Agent:
    def __init__(
        self,
        *,
        llm: ModelClient,
        tools: PluginRegistry,
        assembler: ContextAssembler,
        store: SessionStore,
        config: AgentConfig | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        hooks: HookManager | None = None,
        permission_cfg: dict[str, Any] | None = None,
        attachment_resolver: AttachmentResolver | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.assembler = assembler
        self.store = store
        self.config = config or AgentConfig()
        self._on_event = on_event or (lambda _t, _p: None)
        self.hooks = hooks
        self.permission_cfg = permission_cfg or {}
        self.attachment_resolver = attachment_resolver
        # asyncio 原语绑定事件循环。按 loop 保存锁，支持同步兼容层每次创建新 loop。
        self._session_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    # ---------------- 同步兼容入口 ---------------- #

    def run(self, session: Session, user_input: str) -> tuple[str, AgentTrace]:
        return self.run_request(session, AgentRequest.from_text(user_input))

    def run_request(
        self,
        session: Session,
        request: AgentRequest,
    ) -> tuple[str, AgentTrace]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(self.arun(session, request))
            return result.content, result.trace
        raise RuntimeError("当前线程已有事件循环，请使用 await agent.arun(...)")

    # ---------------- 异步公开入口 ---------------- #

    async def arun(
        self,
        session: Session,
        request: AgentRequest | str,
        *,
        context: RunContext | None = None,
    ) -> AgentResult:
        """收集 astream() 事件并返回最终结果。"""
        trace = AgentTrace()
        terminal: AgentEvent | None = None
        trace_type_map = {
            "hook_blocked": "hook_blocked",
            "tool_started": "tool_call",
            "tool_completed": "tool_result",
            "llm_error": "llm_error",
            "run_completed": "final",
            "max_steps": "max_steps",
        }

        async for event in self.astream(session, request, context=context):
            trace_type = trace_type_map.get(event.type)
            if trace_type is not None:
                trace.add(trace_type, dict(event.payload))
            if event.type in {"run_completed", "run_failed", "run_cancelled"}:
                terminal = event

        if terminal is None:
            raise RuntimeError("Agent 事件流结束但没有终止事件")
        status = (
            terminal.type[len("run_") :]
            if terminal.type.startswith("run_")
            else terminal.type
        )
        return AgentResult(
            content=str(terminal.payload.get("content") or ""),
            run_id=terminal.run_id,
            status=status,
            trace=trace,
        )

    async def astream(
        self,
        session: Session,
        request: AgentRequest | str,
        *,
        context: RunContext | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """执行一次 Agent 请求并实时产出统一事件。"""
        normalized = (
            AgentRequest.from_text(request) if isinstance(request, str) else request
        )
        run_context = context or RunContext()
        factory = _EventFactory(run_id=run_context.run_id, session_id=session.id)
        lock = self._get_session_lock(session.id)

        async with lock:
            try:
                async for event in self._astream_locked(
                    session,
                    normalized,
                    run_context,
                    factory,
                ):
                    self._notify_legacy(event)
                    yield event
            except asyncio.CancelledError:
                event = factory.create(
                    "run_cancelled",
                    {"content": "", "reason": "cancelled"},
                )
                self._notify_legacy(event)
                yield event
                raise
            except Exception as exc:
                event = factory.create(
                    "run_failed",
                    {
                        "content": "",
                        "reason": "internal_error",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                self._notify_legacy(event)
                yield event
                raise

    # ---------------- 主状态机 ---------------- #

    async def _astream_locked(
        self,
        session: Session,
        request: AgentRequest,
        context: RunContext,
        factory: _EventFactory,
    ) -> AsyncIterator[AgentEvent]:
        yield factory.create(
            "run_started",
            {
                "episode_id": context.episode_id,
                "env_idx": context.env_idx,
                "metadata": dict(context.metadata),
            },
        )

        user_input = request.text
        injected_after_user: list[str] = []
        decision = await self._adispatch(
            USER_PROMPT_SUBMIT,
            session_id=session.id,
            payload={
                "user_input": user_input,
                "content": serialize_content_blocks(request.content),
                "attachment_ids": list(request.attachment_ids),
                "metadata": dict(request.metadata),
            },
        )
        if decision is not None:
            if decision.rewrite_user_input is not None:
                user_input = str(decision.rewrite_user_input)
                request = request.with_text(user_input)
            if decision.inject_context:
                injected_after_user.append(decision.inject_context)
            if decision.block:
                reason = decision.block_reason or "[hook:UserPromptSubmit] 已阻断"
                user_msg = Message(
                    role="user",
                    content=request.content,
                    metadata=dict(request.metadata),
                )
                self._persist_message(session, user_msg)
                blocked_msg = Message(role="assistant", content=text_content(reason))
                self._persist_message(session, blocked_msg)
                yield factory.create(
                    "hook_blocked",
                    {"event": USER_PROMPT_SUBMIT, "reason": reason},
                )
                yield factory.create("run_completed", {"content": reason})
                return

        user_msg = Message(
            role="user",
            content=request.content,
            metadata=dict(request.metadata),
        )
        self._persist_message(session, user_msg)

        messages = self.assembler.assemble(session)
        for injected in injected_after_user:
            messages.append(
                {
                    "role": "system",
                    "content": text_model_content(
                        f"[hook:UserPromptSubmit] {injected}"
                    ),
                }
            )

        model_tools = self.tools.model_tools() or None
        turn_state = None
        delta_messages: list[dict[str, Any]] | None = None

        for step in range(self.config.max_steps):
            yield factory.create("model_started", {"step": step})
            try:
                response = await agenerate_model_response(
                    self.llm,
                    messages,
                    tools=model_tools,
                    stage="main_react",
                    turn_state=turn_state,
                    delta_messages=delta_messages,
                    attachment_resolver=self.attachment_resolver,
                )
            except LLMCallError as exc:
                content = _llm_error_message(exc)
                self._persist_message(
                    session,
                    Message(role="assistant", content=text_content(content)),
                )
                yield factory.create(
                    "llm_error",
                    {
                        "step": step,
                        "failure": exc.failure.to_dict(),
                    },
                )
                yield factory.create(
                    "run_failed",
                    {
                        "content": content,
                        "reason": "llm_error",
                    },
                )
                return

            yield factory.create(
                "model_completed",
                {
                    "step": step,
                    "tool_call_count": len(response.tool_calls),
                },
            )

            if not response.tool_calls:
                content = (response.text or "").strip()
                stop_decision = await self._adispatch(
                    STOP,
                    session_id=session.id,
                    payload={"final_answer": content, "step": step},
                )
                if stop_decision is not None:
                    if stop_decision.rewrite_final_answer is not None:
                        content = str(stop_decision.rewrite_final_answer)
                    if stop_decision.block:
                        reason = (
                            stop_decision.block_reason
                            or "[hook:Stop] 请改写你的回答"
                        )
                        nudge_message = {
                            "role": "system",
                            "content": text_model_content(
                                f"[hook:Stop] 你刚才的回答被拦截：{reason}。"
                                "请基于现有上下文重新作答。"
                            ),
                        }
                        messages.append(nudge_message)
                        turn_state = response.next_turn_state
                        delta_messages = [nudge_message]
                        yield factory.create(
                            "hook_blocked",
                            {
                                "event": STOP,
                                "reason": reason,
                                "step": step,
                            },
                        )
                        continue

                self._persist_message(
                    session,
                    Message(role="assistant", content=text_content(content)),
                )
                yield factory.create(
                    "run_completed",
                    {"content": content, "step": step},
                )
                return

            assistant_msg = Message(
                role="assistant",
                content=text_content(response.text or ""),
                tool_calls=_model_tool_calls_to_dicts(response.tool_calls),
                reasoning=response.reasoning,
            )
            self._persist_message(session, assistant_msg)
            messages.append(assistant_msg.to_model_dict())

            tool_result_messages: list[dict[str, Any]] = []
            # 第一阶段保持确定性的串行执行；后续由 ToolExecutionPolicy 控制并发。
            for tc in response.tool_calls:
                name = tc.name
                args = dict(tc.arguments)
                desc = None
                get_desc = getattr(self.tools, "get_tool_descriptor", None)
                if callable(get_desc):
                    desc = get_desc(name)
                trust_level = getattr(desc, "trust_level", "untrusted")

                interaction_mode = "non_interactive"
                try:
                    import sys

                    interaction_mode = (
                        "interactive" if sys.stdin.isatty() else "non_interactive"
                    )
                except Exception:
                    pass

                pre_decision = await self._adispatch(
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
                            self.permission_cfg.get("trust_policy") or {}
                        ),
                        "mcp_rules": dict(
                            self.permission_cfg.get("mcp_rules") or {}
                        ),
                        "non_interactive_fallback": str(
                            self.permission_cfg.get("non_interactive_fallback")
                            or "deny"
                        ),
                        "interaction_mode": interaction_mode,
                    },
                )
                if pre_decision is not None:
                    if pre_decision.rewrite_params is not None:
                        args = dict(pre_decision.rewrite_params)
                    if pre_decision.block:
                        reason = (
                            pre_decision.block_reason
                            or "[hook:PreToolUse] 已阻断"
                        )
                        result = f"[ToolError:{name}][BLOCKED] {reason}"
                        yield factory.create(
                            "hook_blocked",
                            {
                                "event": PRE_TOOL_USE,
                                "tool": name,
                                "reason": reason,
                                "step": step,
                            },
                        )
                        tool_message = self._persist_tool_result(
                            session,
                            call_id=tc.id,
                            name=name,
                            result=result,
                        )
                        messages.append(tool_message)
                        tool_result_messages.append(tool_message)
                        continue

                yield factory.create(
                    "tool_started",
                    {
                        "step": step,
                        "call_id": tc.id,
                        "name": name,
                        "args": args,
                    },
                )
                started = time.monotonic()
                try:
                    result = await self._acall_tool(name, args)
                except Exception as exc:
                    result = _tool_error_payload(name, exc)
                latency_ms = int((time.monotonic() - started) * 1000)

                post_decision = await self._adispatch(
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
                    result = (
                        f"{result}\n\n"
                        f"[hook:PostToolUse] {post_decision.inject_context}"
                    )

                yield factory.create(
                    "tool_completed",
                    {
                        "step": step,
                        "call_id": tc.id,
                        "name": name,
                        "result": result,
                        "latency_ms": latency_ms,
                    },
                )
                tool_message = self._persist_tool_result(
                    session,
                    call_id=tc.id,
                    name=name,
                    result=result,
                )
                messages.append(tool_message)
                tool_result_messages.append(tool_message)

            turn_state = response.next_turn_state
            delta_messages = tool_result_messages

        content = f"[已达到最大步数 {self.config.max_steps}，提前结束]"
        yield factory.create("max_steps", {"content": content})
        yield factory.create(
            "run_failed",
            {
                "content": content,
                "reason": "max_steps_exceeded",
            },
        )

    # ---------------- 辅助方法 ---------------- #

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        locks = self._session_locks.get(loop)
        if locks is None:
            locks = {}
            self._session_locks[loop] = locks
        lock = locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[session_id] = lock
        return lock

    async def _adispatch(self, event_type: str, **kwargs: Any):
        if self.hooks is None or not self.hooks.has_hooks_for(event_type):
            return None
        loop = asyncio.get_running_loop()
        call = functools.partial(
            self.hooks.dispatch,
            HookEvent(type=event_type, **kwargs),
        )
        return await loop.run_in_executor(None, call)

    async def _acall_tool(self, name: str, args: dict[str, Any]) -> str:
        acall = getattr(self.tools, "acall", None)
        if callable(acall):
            return await acall(name, args)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self.tools.call, name, args),
        )

    def _persist_message(self, session: Session, message: Message) -> None:
        # SQLite Store 仍是同步边界；下一阶段会改为单 writer queue。
        self.store.append_message(session.id, message)
        session.append(message)

    def _persist_tool_result(
        self,
        session: Session,
        *,
        call_id: str,
        name: str,
        result: str,
    ) -> dict[str, Any]:
        tool_msg = Message(
            role="tool",
            content=text_content(result),
            tool_call_id=call_id,
            name=name,
        )
        self._persist_message(session, tool_msg)
        return tool_msg.to_model_dict()

    def _notify_legacy(self, event: AgentEvent) -> None:
        payload = dict(event.payload)
        if event.type == "tool_started":
            self._on_event(
                "tool_call",
                {
                    "name": payload.get("name"),
                    "args": payload.get("args"),
                },
            )
        elif event.type == "tool_completed":
            self._on_event(
                "tool_result",
                {
                    "name": payload.get("name"),
                    "result": payload.get("result"),
                },
            )
        elif event.type == "run_completed":
            self._on_event("final", {"content": payload.get("content", "")})
        elif event.type == "llm_error":
            self._on_event("llm_error", payload)
        elif event.type == "hook_blocked":
            self._on_event("hook", {**payload, "blocked": True})


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
