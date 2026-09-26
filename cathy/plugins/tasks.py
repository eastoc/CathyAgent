"""后台工具任务注册表与可恢复生命周期管理。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from ..contracts import ModelTurnState, ToolInvocation, ToolResult, ToolTaskRecord
from ..contracts.content import serialize_content_blocks
from ..logger import get_logger
from ..session.store import SessionStore
from .scheduler import ToolScheduler

logger = get_logger(__name__)


_TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "orphaned",
}


class TaskRegistry:
    """维护进程内 asyncio.Task，并把可恢复状态写入 SessionStore。"""

    def __init__(
        self,
        *,
        scheduler: ToolScheduler,
        store: SessionStore,
        on_transition: Callable[[ToolTaskRecord], None] | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._store = store
        self._on_transition = on_transition or (lambda _record: None)
        self._running: dict[str, asyncio.Task[ToolTaskRecord]] = {}

    async def submit(
        self,
        invocation: ToolInvocation,
        *,
        run_id: str,
        session_id: str,
        provider: str | None = None,
        provider_call_id: str | None = None,
        provider_response_id: str | None = None,
        latest_response_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> ToolTaskRecord:
        """持久化 queued 状态并立即启动后台协程。"""

        asyncio.get_running_loop()
        policy = self._scheduler.get_policy(invocation.name)
        if policy.mode != "background":
            raise ValueError(f"工具 {invocation.name} 未声明 execution_mode=background")
        if policy.side_effect == "irreversible":
            raise ValueError("不可逆工具不能提交为 background")

        record = ToolTaskRecord.queued(
            task_id=task_id,
            run_id=run_id,
            session_id=session_id,
            invocation=invocation,
            provider=provider,
            provider_call_id=provider_call_id,
            provider_response_id=provider_response_id,
            latest_response_id=latest_response_id,
            metadata=metadata,
        )
        self._store.create_tool_task(record)
        self._notify(record)

        task = asyncio.create_task(
            self._run(record.task_id),
            name=f"cathy-tool:{record.task_id}",
        )
        self._running[record.task_id] = task
        task.add_done_callback(
            lambda _task, task_id=record.task_id: self._running.pop(task_id, None)
        )
        return record

    async def wait(self, task_id: str) -> ToolTaskRecord:
        task = self._running.get(task_id)
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
        record = self._require(task_id)
        if record.status not in _TERMINAL_STATUSES and task is None:
            raise RuntimeError(f"任务 {task_id} 没有运行句柄且状态为 {record.status}")
        return record

    async def cancel(self, task_id: str) -> ToolTaskRecord:
        record = self._require(task_id)
        if record.status in _TERMINAL_STATUSES:
            return record
        policy = self._scheduler.get_policy(record.invocation.name)
        if not policy.cancellable:
            raise RuntimeError(f"工具任务不可取消: {task_id}")

        task = self._running.get(task_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        record = self._require(task_id)
        if task is None and record.status not in _TERMINAL_STATUSES:
            record = self._transition(
                record,
                status="cancelled",
                result=ToolResult.cancelled(
                    call_id=record.invocation.call_id,
                    tool_name=record.invocation.name,
                ),
                error_message="任务已取消",
                finished_at=time.time(),
            )
        return record

    async def cancel_run(self, run_id: str) -> list[ToolTaskRecord]:
        records = self._store.list_tool_tasks(run_id=run_id)
        return await asyncio.gather(*(self.cancel(record.task_id) for record in records))

    def get(self, task_id: str) -> ToolTaskRecord | None:
        return self._store.load_tool_task(task_id)

    def list(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[ToolTaskRecord]:
        return self._store.list_tool_tasks(
            run_id=run_id,
            session_id=session_id,
            statuses=statuses,
            limit=limit,
        )

    def update_latest_response_id(
        self,
        task_id: str,
        latest_response_id: str,
    ) -> ToolTaskRecord:
        record = self._require(task_id)
        updated = record.with_updates(latest_response_id=latest_response_id)
        self._store.update_tool_task(updated)
        self._notify(updated)
        return updated

    def build_model_continuation(
        self,
        task_id: str,
    ) -> tuple[ModelTurnState, dict[str, Any]]:
        """把已完成任务转换为模型 continuation 状态和 tool 消息。"""

        record = self._require(task_id)
        if record.status not in {"succeeded", "failed", "timed_out", "cancelled"}:
            raise RuntimeError(f"任务 {task_id} 尚未结束: {record.status}")
        if record.result is None:
            raise RuntimeError(f"任务 {task_id} 没有可返回的 ToolResult")
        if not record.latest_response_id:
            raise RuntimeError(f"任务 {task_id} 缺少 latest_response_id")
        call_id = record.provider_call_id or record.invocation.call_id
        return (
            ModelTurnState(record.latest_response_id),
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": record.invocation.name,
                "content": serialize_content_blocks(record.result.content),
            },
        )

    def mark_interrupted_tasks_orphaned(self) -> list[ToolTaskRecord]:
        """启动恢复时调用；不自动重放可能带副作用的工具。"""

        interrupted = self._store.list_tool_tasks(statuses=("queued", "running"))
        out: list[ToolTaskRecord] = []
        for record in interrupted:
            if record.task_id in self._running:
                continue
            updated = self._transition(
                record,
                status="orphaned",
                error_message="进程重启或任务句柄丢失，需要显式恢复",
                finished_at=time.time(),
            )
            out.append(updated)
        return out

    async def shutdown(self, *, cancel: bool = True) -> None:
        running = list(self._running.items())
        tasks = [task for _task_id, task in running]
        if cancel:
            for task_id, task in running:
                record = self._store.load_tool_task(task_id)
                if record is None:
                    continue
                policy = self._scheduler.get_policy(record.invocation.name)
                if policy.cancellable:
                    task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, task_id: str) -> ToolTaskRecord:
        record = self._require(task_id)
        record = self._transition(
            record,
            status="running",
            started_at=time.time(),
        )
        try:
            result = await self._scheduler.execute_background(record.invocation)
        except asyncio.CancelledError:
            cancelled = ToolResult.cancelled(
                call_id=record.invocation.call_id,
                tool_name=record.invocation.name,
            )
            self._transition(
                record,
                status="cancelled",
                result=cancelled,
                error_message=cancelled.error_message,
                finished_at=time.time(),
            )
            raise
        except Exception as exc:
            result = ToolResult.failed(
                call_id=record.invocation.call_id,
                tool_name=record.invocation.name,
                message=f"{type(exc).__name__}: {exc}",
                error_code="background_task_error",
            )

        final_status = result.status
        if final_status not in {"succeeded", "failed", "cancelled", "timed_out"}:
            final_status = "failed"
        return self._transition(
            record,
            status=final_status,
            result=result,
            error_message=result.error_message,
            finished_at=time.time(),
        )

    def _transition(self, record: ToolTaskRecord, **values: Any) -> ToolTaskRecord:
        updated = record.with_updates(**values)
        self._store.update_tool_task(updated)
        self._notify(updated)
        return updated

    def _require(self, task_id: str) -> ToolTaskRecord:
        record = self._store.load_tool_task(task_id)
        if record is None:
            raise KeyError(f"工具任务不存在: {task_id}")
        return record

    def _notify(self, record: ToolTaskRecord) -> None:
        try:
            self._on_transition(record)
        except Exception as exc:
            logger.warning(
                "[tool-task][transition-callback] task=%s status=%s error=%s",
                record.task_id,
                record.status,
                exc,
            )
