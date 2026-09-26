"""把 EventSink 契约适配到同步或异步 SessionStore。"""

from __future__ import annotations

from typing import Any

from ..contracts import AgentEvent, RunRecord
from ..session.store import AsyncSessionStore, SessionStore


class RunJournal:
    """严格写入模式：事件持久化成功后才交还 Agent。"""

    def __init__(self, store: SessionStore | AsyncSessionStore) -> None:
        self._store = store

    async def astart_run(self, run: RunRecord) -> None:
        await self._store_call("acreate_run", "create_run", run)

    async def aappend_event(self, event: AgentEvent) -> None:
        await self._store_call("aappend_event", "append_event", event)

    async def aflush(self) -> None:
        # 当前每次写入都在 Store 内提交事务；保留接口供后续批量 writer 使用。
        return None

    async def _store_call(
        self,
        async_name: str,
        sync_name: str,
        *args: Any,
    ) -> Any:
        async_method = getattr(self._store, async_name, None)
        if callable(async_method):
            return await async_method(*args)
        sync_method = getattr(self._store, sync_name)
        return sync_method(*args)
