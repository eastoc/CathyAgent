"""Sandbox 后端工厂。

职责只有一个：根据配置字符串创建对应执行器实例。
不要在这里塞策略逻辑，避免“创建对象”和“执行策略”耦合。
"""

from __future__ import annotations

from .base import SandboxError, SandboxExecutor
from .local_restricted import LocalRestrictedExecutor
from .seatbelt import SeatbeltExecutor


def create_executor(backend: str) -> SandboxExecutor:
    # 统一把配置值标准化，避免大小写/空字符串导致分支歧义。
    key = (backend or "").strip().lower() or "local_restricted"
    if key == "local_restricted":
        return LocalRestrictedExecutor()
    if key == "seatbelt":
        return SeatbeltExecutor()
    raise SandboxError(f"未知 sandbox backend: {backend!r}")

