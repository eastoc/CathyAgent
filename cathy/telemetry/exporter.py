"""无损导出 RunJournal；不生成 reward 或训练框架专用字段。"""

from __future__ import annotations

import asyncio
import functools
import json
from pathlib import Path
from typing import Any, Iterable

from ..session.store import AsyncSessionStore, SessionStore


def _write_jsonl(path: Path, bundles: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for bundle in bundles:
            stream.write(
                json.dumps(bundle, ensure_ascii=False, sort_keys=True) + "\n"
            )
    return len(bundles)


class RawRunExporter:
    """每个 root_run_id 输出一条 ``cathy.raw-run.v1`` JSONL。"""

    def __init__(self, store: SessionStore | AsyncSessionStore) -> None:
        self._store = store

    def export_jsonl(
        self,
        path: Path | str,
        *,
        root_run_ids: Iterable[str] | None = None,
        limit: int = 10000,
    ) -> int:
        if isinstance(self._store, AsyncSessionStore):
            raise RuntimeError("异步 Store 请使用 await aexport_jsonl()")
        ids = (
            list(root_run_ids)
            if root_run_ids is not None
            else self._store.list_root_run_ids(limit)
        )
        bundles = [self._store.load_run_bundle(root_id) for root_id in ids]
        return _write_jsonl(Path(path), [item for item in bundles if item is not None])

    async def aexport_jsonl(
        self,
        path: Path | str,
        *,
        root_run_ids: Iterable[str] | None = None,
        limit: int = 10000,
    ) -> int:
        ids = list(root_run_ids) if root_run_ids is not None else None
        if ids is None:
            if isinstance(self._store, AsyncSessionStore):
                ids = await self._store.alist_root_run_ids(limit)
            else:
                ids = self._store.list_root_run_ids(limit)

        if isinstance(self._store, AsyncSessionStore):
            bundles = [
                await self._store.aload_run_bundle(root_id) for root_id in ids
            ]
        else:
            bundles = [self._store.load_run_bundle(root_id) for root_id in ids]
        materialized = [item for item in bundles if item is not None]
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(_write_jsonl, Path(path), materialized),
        )
