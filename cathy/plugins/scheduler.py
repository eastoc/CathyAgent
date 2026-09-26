"""受控异步工具调度：超时、并发上限和资源串行化。"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..contracts import (
    ToolExecutionPolicy,
    ToolInvocation,
    ToolResult,
)


@dataclass
class _LoopState:
    tool_semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    resource_locks: dict[str, asyncio.Lock] = field(default_factory=dict)


class ToolScheduler:
    """对 Registry 做异步调度，不承担模型循环和后台任务持久化。"""

    def __init__(self, registry: Any) -> None:
        self._registry = registry
        self._loop_states: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        policy = self.get_policy(invocation.name)
        if policy.mode == "background":
            result = ToolResult.failed(
                call_id=invocation.call_id,
                tool_name=invocation.name,
                message="background 工具需要 TaskRegistry，当前阶段拒绝降级为 inline",
                error_code="background_not_available",
            )
            return result.with_metadata(
                latency_ms=0,
                execution_mode=policy.mode,
                concurrency_key=policy.concurrency_key,
            )
        return await self._execute_with_policy(invocation, policy)

    async def execute_background(self, invocation: ToolInvocation) -> ToolResult:
        """仅供 TaskRegistry 调用，避免 background 任务绕过生命周期管理。"""

        policy = self.get_policy(invocation.name)
        if policy.mode != "background":
            return ToolResult.failed(
                call_id=invocation.call_id,
                tool_name=invocation.name,
                message="工具未声明为 background，拒绝后台执行",
                error_code="background_policy_mismatch",
            )
        return await self._execute_with_policy(invocation, policy)

    async def _execute_with_policy(
        self,
        invocation: ToolInvocation,
        policy: ToolExecutionPolicy,
    ) -> ToolResult:
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._execute_guarded(invocation, policy),
                timeout=policy.timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = ToolResult.timed_out(
                call_id=invocation.call_id,
                tool_name=invocation.name,
                timeout_seconds=policy.timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = ToolResult.failed(
                call_id=invocation.call_id,
                tool_name=invocation.name,
                message=f"{type(exc).__name__}: {exc}",
                error_code="scheduler_error",
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        return result.with_metadata(
            latency_ms=latency_ms,
            execution_mode=policy.mode,
            concurrency_key=policy.concurrency_key,
        )

    async def execute_many(
        self,
        invocations: Sequence[ToolInvocation],
    ) -> list[ToolResult]:
        """并行提交；Semaphore 与 concurrency_key 决定实际并发度。"""

        if not invocations:
            return []
        return list(
            await asyncio.gather(
                *(self.execute(invocation) for invocation in invocations)
            )
        )

    async def _execute_guarded(
        self,
        invocation: ToolInvocation,
        policy: ToolExecutionPolicy,
    ) -> ToolResult:
        state = self._state_for_running_loop()
        semaphore = state.tool_semaphores.get(invocation.name)
        if semaphore is None:
            semaphore = asyncio.Semaphore(policy.max_concurrency)
            state.tool_semaphores[invocation.name] = semaphore

        async with semaphore:
            if policy.concurrency_key is None:
                return await self._invoke_registry(invocation)

            lock = state.resource_locks.get(policy.concurrency_key)
            if lock is None:
                lock = asyncio.Lock()
                state.resource_locks[policy.concurrency_key] = lock
            async with lock:
                return await self._invoke_registry(invocation)

    async def _invoke_registry(self, invocation: ToolInvocation) -> ToolResult:
        acall_result = getattr(self._registry, "acall_result", None)
        if callable(acall_result):
            return await acall_result(
                invocation.name,
                dict(invocation.arguments),
                call_id=invocation.call_id,
            )

        acall = getattr(self._registry, "acall", None)
        if not callable(acall):
            raise TypeError("工具 Registry 必须提供 acall_result() 或 acall()")
        output = await acall(invocation.name, dict(invocation.arguments))
        return ToolResult.succeeded(
            call_id=invocation.call_id,
            tool_name=invocation.name,
            content=str(output),
        )

    def get_policy(self, tool_name: str) -> ToolExecutionPolicy:
        get_policy = getattr(self._registry, "get_execution_policy", None)
        if callable(get_policy):
            policy = get_policy(tool_name)
            if policy is not None:
                return policy
        return ToolExecutionPolicy()

    def _state_for_running_loop(self) -> _LoopState:
        loop = asyncio.get_running_loop()
        state = self._loop_states.get(loop)
        if state is None:
            state = _LoopState()
            self._loop_states[loop] = state
        return state
