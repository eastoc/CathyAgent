"""接收现有 navigate 层发送的 SONIC packed ZMQ 消息。"""

from __future__ import annotations

from dataclasses import dataclass
import struct
import threading

from ..sonic.wire import HEADER_SIZE


@dataclass(frozen=True)
class PlannerCommand:
    mode: int = 0
    movement: tuple[float, float, float] = (0.0, 0.0, 0.0)
    facing: tuple[float, float, float] = (1.0, 0.0, 0.0)
    speed: float = -1.0
    height: float = -1.0


class ZmqCommandReceiver:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.active = False
        self.command = PlannerCommand()
        self._lock = threading.Lock()
        self._context = None
        self._socket = None

    def open(self) -> None:
        import zmq
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        # command 与 planner 共用一个 SUB。不能启用 CONFLATE，否则紧随启动帧
        # 到达的 planner 帧会覆盖 command，控制器将永久停留在 inactive。
        self._socket.setsockopt(zmq.RCVHWM, 100)
        self._socket.setsockopt(zmq.SUBSCRIBE, b"command")
        self._socket.setsockopt(zmq.SUBSCRIBE, b"planner")
        self._socket.connect(self.endpoint)

    def poll(self) -> None:
        if self._socket is None:
            return
        import zmq
        while True:
            try:
                message = self._socket.recv(flags=zmq.NOBLOCK)
            except zmq.Again:
                return
            if message.startswith(b"command"):
                payload = message[len(b"command") + HEADER_SIZE:]
                if len(payload) >= 3:
                    start, stop, planner = struct.unpack_from("BBB", payload)
                    with self._lock:
                        if stop:
                            self.active = False
                        elif start and planner:
                            self.active = True
            elif message.startswith(b"planner"):
                payload = message[len(b"planner") + HEADER_SIZE:]
                if len(payload) >= 36:
                    values = struct.unpack_from("<i3f3fff", payload)
                    with self._lock:
                        self.command = PlannerCommand(
                            mode=values[0], movement=tuple(values[1:4]),
                            facing=tuple(values[4:7]), speed=values[7],
                            height=values[8],
                        )

    def snapshot(self) -> tuple[bool, PlannerCommand]:
        with self._lock:
            return self.active, self.command

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close(linger=0)
            self._socket = None
        if self._context is not None:
            self._context.term()
            self._context = None


__all__ = ["PlannerCommand", "ZmqCommandReceiver"]
