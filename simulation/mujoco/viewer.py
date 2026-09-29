"""随 MuJoCo Runtime 生命周期启动和同步的被动图形 Viewer。"""

from __future__ import annotations

import importlib
from contextlib import nullcontext
from typing import Any

from .backend import MujocoBackend
from .config import ViewerConfig
from .errors import MujocoExecutionError


class MujocoViewer:
    def __init__(self, backend: MujocoBackend, config: ViewerConfig) -> None:
        self.backend = backend
        self.config = config
        self._handle: Any | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def is_running(self) -> bool:
        return self._handle is not None and bool(self._handle.is_running())

    def open(self) -> None:
        if not self.enabled or self._handle is not None:
            return
        try:
            viewer = importlib.import_module("mujoco.viewer")
            self._handle = viewer.launch_passive(
                self.backend.model,
                self.backend.data,
                show_left_ui=self.config.show_left_ui,
                show_right_ui=self.config.show_right_ui,
            )
            self.sync()
        except Exception as exc:
            self._handle = None
            raise MujocoExecutionError(
                "MuJoCo Viewer 启动失败；macOS 请使用 mjpython 启动，"
                "无桌面环境请传 --no-viewer"
            ) from exc

    def sync(self) -> None:
        if self.is_running:
            self._handle.sync()

    def lock(self):
        if self.is_running:
            return self._handle.lock()
        return nullcontext()

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.close()


__all__ = ["MujocoViewer"]
