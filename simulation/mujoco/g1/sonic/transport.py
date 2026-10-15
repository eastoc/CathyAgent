"""SONIC 命令传输接口及官方 packed-message 的 ZeroMQ 实现。"""

from __future__ import annotations

from typing import Protocol

from .contracts import SonicCommand
from .wire import build_command_message, build_planner_message


class SonicTransport(Protocol):
    def open(self) -> None: ...

    def start_planner(self) -> None: ...

    def send(self, command: SonicCommand) -> None: ...

    def idle(self, facing: tuple[float, float, float]) -> None: ...

    def close(self) -> None: ...


class ZmqSonicTransport:
    """PUB 端；官方 `--input-type zmq_manager` 作为 SUB 端连接。"""

    def __init__(self, endpoint: str, *, bind: bool = True) -> None:
        self.endpoint = endpoint
        self.bind = bind
        self._context = None
        self._socket = None

    def open(self) -> None:
        if self._socket is not None:
            return
        try:
            import zmq
        except ImportError as exc:
            raise RuntimeError("启用 SONIC 执行需要安装 pyzmq") from exc
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.PUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.SNDHWM, 3)
        if self.bind:
            self._socket.bind(self.endpoint)
        else:
            self._socket.connect(self.endpoint)

    def _send(self, message: bytes) -> None:
        if self._socket is None:
            raise RuntimeError("SONIC ZMQ transport 尚未打开")
        self._socket.send(message)

    def start_planner(self) -> None:
        self._send(build_command_message(start=True, stop=False, planner=True))

    def send(self, command: SonicCommand) -> None:
        self._send(
            build_planner_message(
                mode=command.mode,
                movement=command.movement,
                facing=command.facing,
                speed=command.speed,
                height=-1.0,
            )
        )

    def idle(self, facing: tuple[float, float, float]) -> None:
        self._send(
            build_planner_message(
                mode=0,
                movement=(0.0, 0.0, 0.0),
                facing=facing,
                speed=-1.0,
                height=-1.0,
            )
        )

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close(linger=0)
            self._socket = None
        if self._context is not None:
            self._context.term()
            self._context = None


__all__ = ["SonicTransport", "ZmqSonicTransport"]
