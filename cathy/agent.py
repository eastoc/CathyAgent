"""Session-aware 的异步 ReAct Agent 主循环。

astream() 是普通请求的真实执行入口；astream_task() 负责后台工具完成后的
continuation。arun()/aresume_task() 收集事件，run()/run_request() 仅保留为
同步兼容层。工具通过 ToolExecutionPolicy 做受控并发与后台调度。
"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Sequence

from .contracts import (
    AgentEvent,
    AgentRequest,
    AgentResult,
    AttachmentResolver,
    EventSink,
    ModelClient,
    ModelToolCall,
    ModelTurnState,
    RunContext,
    RunRecord,
    ToolInvocation,
    ToolResult,
)
from .contracts.content import (
    ContentBlock,
    TextBlock,
    coerce_content_blocks,
    serialize_content_blocks,
    text_content,
    text_model_content,
)
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
from .model_clients.invoke import astream_model_response
from .plugins import PluginRegistry, TaskRegistry, ToolScheduler
from .session.models import Message, Session
from .session.store import AsyncSessionStore, SessionStore


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
            "async_execution": tc.async_execution,
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
        store: SessionStore | AsyncSessionStore,
        config: AgentConfig | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        hooks: HookManager | None = None,
        permission_cfg: dict[str, Any] | None = None,
        attachment_resolver: AttachmentResolver | None = None,
        tool_scheduler: ToolScheduler | None = None,
        task_registry: TaskRegistry | None = None,
        event_sink: EventSink | None = None,
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
        self.tool_scheduler = tool_scheduler or ToolScheduler(tools)
        self.task_registry = task_registry or TaskRegistry(
            scheduler=self.tool_scheduler,
            store=store,
        )
        self.event_sink = event_sink
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
            result = asyncio.run(self._arun_sync_compatible(session, request))
            return result.content, result.trace
        raise RuntimeError("当前线程已有事件循环，请使用 await agent.arun(...)")

    async def _arun_sync_compatible(
        self,
        session: Session,
        request: AgentRequest,
    ) -> AgentResult:
        """同步入口无法保留事件循环，因此阻塞收敛所有 pending task。"""

        result = await self.arun(session, request)
        while result.status == "pending":
            if not result.pending_task_ids:
                raise RuntimeError("run_pending 缺少 task_ids")
            result = await self.aresume_task(session, result.pending_task_ids[0])
        return result

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
            "tool_task_queued": "tool_task_queued",
            "run_pending": "pending",
        }

        async for event in self.astream(session, request, context=context):
            trace_type = trace_type_map.get(event.type)
            if trace_type is not None:
                trace.add(trace_type, dict(event.payload))
            if event.type in {
                "run_completed",
                "run_pending",
                "run_failed",
                "run_cancelled",
            }:
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
            pending_task_ids=tuple(terminal.payload.get("task_ids") or ()),
            trace=trace,
        )

    async def aresume_task(
        self,
        session: Session,
        task_id: str,
        *,
        context: RunContext | None = None,
    ) -> AgentResult:
        """等待后台工具结束，并使用原始 call_id/response_id 续跑模型。"""

        trace = AgentTrace()
        terminal: AgentEvent | None = None
        async for event in self.astream_task(
            session,
            task_id,
            context=context,
        ):
            if event.type.startswith("tool_") or event.type.startswith("continuation_"):
                trace.add(event.type, dict(event.payload))
            if event.type in {
                "run_completed",
                "run_pending",
                "run_failed",
                "run_cancelled",
            }:
                terminal = event
        if terminal is None:
            raise RuntimeError("continuation 事件流结束但没有终止事件")
        return AgentResult(
            content=str(terminal.payload.get("content") or ""),
            run_id=terminal.run_id,
            status=(
                terminal.type[len("run_") :]
                if terminal.type.startswith("run_")
                else terminal.type
            ),
            pending_task_ids=tuple(terminal.payload.get("task_ids") or ()),
            trace=trace,
        )

    async def astream_task(
        self,
        session: Session,
        task_id: str,
        *,
        context: RunContext | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """流式等待一个 pending task，并把结果送回对应的模型轮次。"""

        initial = await self.task_registry.aget(task_id)
        if initial is None:
            raise KeyError(f"工具任务不存在: {task_id}")
        if initial.session_id != session.id:
            raise ValueError("工具任务不属于当前 session")
        if initial.metadata.get("continuation_delivered"):
            raise RuntimeError(f"工具任务结果已经续传: {task_id}")

        supplied_context = context or RunContext()
        run_context = RunContext(
            run_id=supplied_context.run_id,
            root_run_id=(
                supplied_context.root_run_id
                or str(initial.metadata.get("root_run_id") or initial.run_id)
            ),
            parent_run_id=supplied_context.parent_run_id or initial.run_id,
            episode_id=supplied_context.episode_id,
            env_idx=supplied_context.env_idx,
            metadata=supplied_context.metadata,
        )
        factory = _EventFactory(run_id=run_context.run_id, session_id=session.id)
        await self._astart_recording(run_context, session.id)

        event = factory.create(
            "run_started",
            {
                "episode_id": run_context.episode_id,
                "env_idx": run_context.env_idx,
                "metadata": dict(run_context.metadata),
                "continuation": True,
                "parent_run_id": run_context.parent_run_id,
            },
        )
        await self._arecord_event(event)
        yield event
        event = factory.create(
            f"tool_task_{initial.status}",
            {
                "task_id": task_id,
                "call_id": initial.provider_call_id,
                "name": initial.invocation.name,
                "status": initial.status,
            },
        )
        await self._arecord_event(event)
        yield event
        record = await self.task_registry.wait(task_id)
        event = factory.create(
            f"tool_task_{record.status}",
            {
                "task_id": task_id,
                "call_id": record.provider_call_id,
                "name": record.invocation.name,
                "status": record.status,
            },
        )
        await self._arecord_event(event)
        yield event
        turn_state, tool_message = await self.task_registry.abuild_model_continuation(
            task_id
        )
        pending_group_id = str(
            record.metadata.get("pending_group_id") or record.run_id
        )
        siblings = await self.task_registry.alist(session_id=record.session_id)
        remaining_task_ids = [
            sibling.task_id
            for sibling in siblings
            if sibling.task_id != task_id
            and sibling.status != "orphaned"
            and not sibling.metadata.get("continuation_delivered")
            and str(
                sibling.metadata.get("pending_group_id") or sibling.run_id
            )
            == pending_group_id
        ]

        terminal_type: str | None = None
        lock = self._get_session_lock(session.id)
        async with lock:
            try:
                async for event in self._astream_locked(
                    session,
                    None,
                    run_context,
                    factory,
                    continuation=(turn_state, [tool_message], remaining_task_ids),
                ):
                    if event.type.startswith("run_"):
                        terminal_type = event.type
                    await self._arecord_event(event)
                    self._notify_legacy(event)
                    yield event
            except asyncio.CancelledError:
                event = factory.create(
                    "run_cancelled",
                    {"content": "", "reason": "cancelled"},
                )
                await self._arecord_event(event)
                self._notify_legacy(event)
                yield event
                raise
            except Exception as exc:
                event = factory.create(
                    "run_failed",
                    {
                        "content": "",
                        "reason": "continuation_error",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
                await self._arecord_event(event)
                self._notify_legacy(event)
                yield event
                raise

        if terminal_type in {"run_completed", "run_pending"}:
            await self.task_registry.aupdate_metadata(
                task_id,
                continuation_delivered=True,
                continuation_run_id=run_context.run_id,
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
        await self._astart_recording(run_context, session.id)
        lock = self._get_session_lock(session.id)

        async with lock:
            try:
                async for event in self._astream_locked(
                    session,
                    normalized,
                    run_context,
                    factory,
                ):
                    await self._arecord_event(event)
                    self._notify_legacy(event)
                    yield event
            except asyncio.CancelledError:
                event = factory.create(
                    "run_cancelled",
                    {"content": "", "reason": "cancelled"},
                )
                await self._arecord_event(event)
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
                await self._arecord_event(event)
                self._notify_legacy(event)
                yield event
                raise

    # ---------------- 主状态机 ---------------- #

    async def _astream_locked(
        self,
        session: Session,
        request: AgentRequest | None,
        context: RunContext,
        factory: _EventFactory,
        *,
        continuation: tuple[ModelTurnState, list[dict[str, Any]], list[str]] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        active_pending_ids: list[str]
        if continuation is None:
            if request is None:
                raise ValueError("普通运行缺少 AgentRequest")
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
                yield factory.create(
                    "request_submitted",
                    {
                        "content": serialize_content_blocks(request.content),
                        "attachment_ids": list(request.attachment_ids),
                        "metadata": dict(request.metadata),
                    },
                )
                if decision.block:
                    reason = decision.block_reason or "[hook:UserPromptSubmit] 已阻断"
                    user_msg = Message(
                        role="user",
                        content=request.content,
                        metadata=dict(request.metadata),
                    )
                    await self._apersist_message(session, user_msg)
                    blocked_msg = Message(role="assistant", content=text_content(reason))
                    await self._apersist_message(session, blocked_msg)
                    yield factory.create(
                        "hook_blocked",
                        {"event": USER_PROMPT_SUBMIT, "reason": reason},
                    )
                    yield factory.create("run_completed", {"content": reason})
                    return

            else:
                yield factory.create(
                    "request_submitted",
                    {
                        "content": serialize_content_blocks(request.content),
                        "attachment_ids": list(request.attachment_ids),
                        "metadata": dict(request.metadata),
                    },
                )

            user_msg = Message(
                role="user",
                content=request.content,
                metadata=dict(request.metadata),
            )
            await self._apersist_message(session, user_msg)

            messages = await self.assembler.aassemble(session)
            for injected in injected_after_user:
                messages.append(
                    {
                        "role": "system",
                        "content": text_model_content(
                            f"[hook:UserPromptSubmit] {injected}"
                        ),
                    }
                )
            turn_state = None
            delta_messages: list[dict[str, Any]] | None = None
            active_pending_ids = []
        else:
            turn_state, delta_messages, active_pending_ids = continuation
            messages = await self.assembler.aassemble(session)
            yield factory.create(
                "continuation_started",
                {
                    "previous_response_id": str(turn_state.value),
                    "pending_task_ids": list(active_pending_ids),
                },
            )

        model_tools = self.tools.model_tools() or None
        pending_group_id = factory.run_id
        if active_pending_ids:
            first_pending = await self.task_registry.aget(active_pending_ids[0])
            if first_pending is not None:
                pending_group_id = str(
                    first_pending.metadata.get("pending_group_id")
                    or first_pending.run_id
                )

        for step in range(self.config.max_steps):
            yield factory.create("model_started", {"step": step})
            try:
                response = None
                async for model_event in astream_model_response(
                    self.llm,
                    messages,
                    tools=model_tools,
                    stage="main_react",
                    turn_state=turn_state,
                    delta_messages=delta_messages,
                    attachment_resolver=self.attachment_resolver,
                ):
                    if model_event.type == "text_delta":
                        yield factory.create(
                            "model_text_delta",
                            {"step": step, "text": model_event.text},
                        )
                    elif model_event.type == "reasoning_delta":
                        yield factory.create(
                            "model_reasoning_delta",
                            {"step": step, "text": model_event.text},
                        )
                    elif model_event.type == "tool_call_started":
                        yield factory.create(
                            "model_tool_call_started",
                            {"step": step, **dict(model_event.metadata)},
                        )
                    elif model_event.type == "tool_call_delta":
                        yield factory.create(
                            "model_tool_call_delta",
                            {
                                "step": step,
                                "delta": model_event.text,
                                **dict(model_event.metadata),
                            },
                        )
                    elif model_event.type == "tool_call_completed":
                        tool_call = model_event.metadata.get("tool_call")
                        payload = {
                            "step": step,
                            "item_id": model_event.metadata.get("item_id"),
                            "output_index": model_event.metadata.get("output_index"),
                        }
                        if isinstance(tool_call, ModelToolCall):
                            payload.update(
                                {
                                    "call_id": tool_call.id,
                                    "name": tool_call.name,
                                    "arguments": dict(tool_call.arguments),
                                    "async_execution": tool_call.async_execution,
                                }
                            )
                        yield factory.create("model_tool_call_completed", payload)
                    elif model_event.type in {
                        "response_completed",
                        "response_incomplete",
                    }:
                        response = model_event.response
                if response is None:
                    raise RuntimeError("模型事件流结束但没有完整 response")
            except LLMCallError as exc:
                content = _llm_error_message(exc)
                await self._apersist_message(
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

            if response.next_turn_state is not None:
                latest_response_id = str(response.next_turn_state.value)
                for task_id in active_pending_ids:
                    await self.task_registry.aupdate_latest_response_id(
                        task_id,
                        latest_response_id,
                    )

            if not response.tool_calls:
                content = (response.text or "").strip()
                if active_pending_ids:
                    await self._apersist_message(
                        session,
                        Message(role="assistant", content=text_content(content)),
                    )
                    yield factory.create(
                        "run_pending",
                        {
                            "content": content,
                            "step": step,
                            "task_ids": list(active_pending_ids),
                        },
                    )
                    return
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

                await self._apersist_message(
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
            await self._apersist_message(session, assistant_msg)
            messages.append(assistant_msg.to_model_dict())

            tool_result_messages: list[dict[str, Any]] = []
            prepared: list[tuple[int, ModelToolCall, str, dict[str, Any]]] = []
            background_prepared: list[
                tuple[int, ModelToolCall, str, dict[str, Any]]
            ] = []
            background_indices: set[int] = set()
            outcomes: dict[int, str | ToolResult] = {}
            effective_args: dict[int, dict[str, Any]] = {}

            # Hook 和权限判断保持确定性的模型输出顺序；通过的工具随后统一提交给
            # ToolScheduler，由 max_concurrency/concurrency_key 决定实际并发度。
            for index, tc in enumerate(response.tool_calls):
                name = tc.name
                args = dict(tc.arguments)
                desc = None
                get_desc = getattr(self.tools, "get_tool_descriptor", None)
                if callable(get_desc):
                    desc = get_desc(name)
                trust_level = getattr(desc, "trust_level", "untrusted")
                policy = self.tool_scheduler.get_policy(name)
                if tc.async_execution and policy.mode != "background":
                    outcomes[index] = (
                        f"[ToolError:{name}][ASYNC_NOT_ALLOWED] "
                        "模型请求异步执行，但工具未声明 execution_mode=background"
                    )
                    effective_args[index] = args
                    continue

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
                    effective_args[index] = args
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
                        outcomes[index] = result
                        continue
                else:
                    effective_args[index] = args

                yield factory.create(
                    "tool_started",
                    {
                        "step": step,
                        "call_id": tc.id,
                        "name": name,
                        "args": args,
                    },
                )
                if policy.mode == "background":
                    background_prepared.append((index, tc, name, args))
                    background_indices.add(index)
                else:
                    prepared.append((index, tc, name, args))

            scheduled_results = await self.tool_scheduler.execute_many(
                [
                    ToolInvocation(
                        call_id=tc.id,
                        name=name,
                        arguments=args,
                    )
                    for _index, tc, name, args in prepared
                ]
            )
            for (index, _tc, _name, _args), tool_result in zip(
                prepared,
                scheduled_results,
            ):
                outcomes[index] = tool_result

            provider_response_id = (
                str(response.next_turn_state.value)
                if response.next_turn_state is not None
                else None
            )
            for index, tc, name, args in background_prepared:
                if provider_response_id is None:
                    outcomes[index] = ToolResult.failed(
                        call_id=tc.id,
                        tool_name=name,
                        message="异步工具调用缺少 provider response_id",
                        error_code="missing_response_id",
                    )
                    background_indices.discard(index)
                    continue
                queued = await self.task_registry.submit(
                    ToolInvocation(call_id=tc.id, name=name, arguments=args),
                    run_id=factory.run_id,
                    session_id=session.id,
                    provider=type(self.llm).__name__,
                    provider_call_id=tc.id,
                    provider_response_id=provider_response_id,
                    latest_response_id=provider_response_id,
                    metadata={
                        "step": step,
                        "output_index": index,
                        "pending_group_id": pending_group_id,
                        "root_run_id": context.root_run_id or context.run_id,
                    },
                )
                active_pending_ids.append(queued.task_id)
                yield factory.create(
                    "tool_task_queued",
                    {
                        "step": step,
                        "task_id": queued.task_id,
                        "call_id": tc.id,
                        "name": name,
                        "status": queued.status,
                    },
                )

            # 结果按模型原始 tool-call 顺序回灌，避免并发完成顺序改变上下文语义。
            for index, tc in enumerate(response.tool_calls):
                name = tc.name
                if index in background_indices:
                    continue
                outcome = outcomes[index]
                if isinstance(outcome, str):
                    result = outcome
                    tool_message = await self._apersist_tool_result(
                        session,
                        call_id=tc.id,
                        name=name,
                        result=result,
                    )
                    messages.append(tool_message)
                    tool_result_messages.append(tool_message)
                    continue

                tool_result = outcome
                result = tool_result.text
                result_content: tuple[ContentBlock, ...] = tuple(tool_result.content)
                latency_ms = int(tool_result.metadata.get("latency_ms", 0))
                args = effective_args[index]

                post_decision = await self._adispatch(
                    POST_TOOL_USE,
                    session_id=session.id,
                    matcher_target=name,
                    payload={
                        "tool": name,
                        "params": args,
                        "result": result,
                        "latency_ms": latency_ms,
                        "status": tool_result.status,
                        "error_code": tool_result.error_code,
                    },
                )
                if post_decision is not None and post_decision.inject_context:
                    injected = f"[hook:PostToolUse] {post_decision.inject_context}"
                    result = f"{result}\n\n{injected}" if result else injected
                    result_content = (*result_content, TextBlock(injected))

                yield factory.create(
                    "tool_completed",
                    {
                        "step": step,
                        "call_id": tc.id,
                        "name": name,
                        "result": result,
                        "latency_ms": latency_ms,
                        "status": tool_result.status,
                        "error_code": tool_result.error_code,
                        "artifacts": list(tool_result.artifacts),
                        "content": serialize_content_blocks(result_content),
                        "metadata": dict(tool_result.metadata),
                    },
                )
                tool_message = await self._apersist_tool_result(
                    session,
                    call_id=tc.id,
                    name=name,
                    result=result_content,
                )
                messages.append(tool_message)
                tool_result_messages.append(tool_message)

            turn_state = response.next_turn_state
            delta_messages = tool_result_messages
            if not tool_result_messages and active_pending_ids:
                content = (response.text or "").strip()
                yield factory.create(
                    "run_pending",
                    {
                        "content": content,
                        "step": step,
                        "task_ids": list(active_pending_ids),
                    },
                )
                return

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

    async def _astart_recording(
        self,
        context: RunContext,
        session_id: str,
    ) -> None:
        if self.event_sink is None:
            return
        await self.event_sink.astart_run(
            RunRecord(
                run_id=context.run_id,
                root_run_id=context.root_run_id or context.run_id,
                parent_run_id=context.parent_run_id,
                session_id=session_id,
                episode_id=context.episode_id,
                model_metadata={
                    "client": type(self.llm).__name__,
                    "model": getattr(self.llm, "model", None),
                },
                context={
                    "env_idx": context.env_idx,
                    "metadata": dict(context.metadata),
                },
            )
        )

    async def _arecord_event(self, event: AgentEvent) -> None:
        if self.event_sink is not None:
            await self.event_sink.aappend_event(event)

    async def _adispatch(self, event_type: str, **kwargs: Any):
        if self.hooks is None or not self.hooks.has_hooks_for(event_type):
            return None
        return await self.hooks.adispatch(HookEvent(type=event_type, **kwargs))

    async def _apersist_message(self, session: Session, message: Message) -> None:
        append = getattr(self.store, "aappend_message", None)
        if callable(append):
            await append(session.id, message)
        else:
            # 同步 Store 仅作为旧调用方和测试兼容路径。
            self.store.append_message(session.id, message)
        session.append(message)

    async def _apersist_tool_result(
        self,
        session: Session,
        *,
        call_id: str,
        name: str,
        result: str | Sequence[ContentBlock],
    ) -> dict[str, Any]:
        tool_msg = Message(
            role="tool",
            content=coerce_content_blocks(result),
            tool_call_id=call_id,
            name=name,
        )
        await self._apersist_message(session, tool_msg)
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
