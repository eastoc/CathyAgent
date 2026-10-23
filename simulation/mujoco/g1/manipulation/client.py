"""AutoDL GN1x HTTP 客户端。"""

from __future__ import annotations

import os
from typing import Any, Mapping

import httpx


class Gr00tClientError(RuntimeError):
    pass


class Gr00tClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("GN1x 服务 URL 必须是 HTTP(S)")
        headers = {}
        if os.environ.get("GROOT_API_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GROOT_API_TOKEN']}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=float(timeout_seconds),
        )
        self._metadata: dict[str, Any] | None = None

    async def metadata(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._metadata is not None and not refresh:
            return dict(self._metadata)
        try:
            response = await self._client.get("/metadata")
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, Mapping):
                raise Gr00tClientError("GN1x metadata 必须是对象")
            self._metadata = dict(value)
            return dict(self._metadata)
        except (httpx.HTTPError, ValueError) as exc:
            raise Gr00tClientError(f"读取 GN1x metadata 失败: {exc}") from exc

    async def infer(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post("/infer", json=dict(observation))
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, Mapping):
                raise Gr00tClientError("GN1x 动作返回值必须是对象")
            return dict(value)
        except (httpx.HTTPError, ValueError) as exc:
            raise Gr00tClientError(f"GN1x 推理请求失败: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    def close(self) -> None:
        if self._client.is_closed:
            return
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.aclose())
        else:
            loop.create_task(self._client.aclose())


# 兼容现有插件导入名。
UnifolmClient = Gr00tClient
UnifolmClientError = Gr00tClientError

__all__ = [
    "Gr00tClient", "Gr00tClientError", "UnifolmClient", "UnifolmClientError",
]
