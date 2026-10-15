"""MuJoCo 根位姿反馈：进程内 qpos 读取器与跨进程 ZMQ 桥。"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Sequence

from ..contracts import Pose2D
from .contracts import TimedPose2D
from .wire import build_navigation_pose_message, decode_navigation_pose_message


def yaw_from_quaternion_wxyz(quaternion: Sequence[float]) -> float:
    if len(quaternion) != 4:
        raise ValueError("四元数必须是 wxyz 四个分量")
    w, x, y, z = (float(value) for value in quaternion)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


class MujocoQposPoseProvider:
    """读取 MuJoCo free-joint qpos `[x,y,z,qw,qx,qy,qz,...]`。"""

    def __init__(self, qpos_reader: Callable[[], Sequence[float]]) -> None:
        self.qpos_reader = qpos_reader

    def __call__(self) -> TimedPose2D:
        qpos = self.qpos_reader()
        if len(qpos) < 7:
            raise RuntimeError("G1 qpos 至少需要 7 个根自由度")
        pose = Pose2D(
            float(qpos[0]),
            float(qpos[1]),
            yaw_from_quaternion_wxyz(qpos[3:7]),
        )
        return TimedPose2D(pose, time.monotonic())


class ZmqNavigationPoseProvider:
    def __init__(
        self,
        endpoint: str,
        *,
        topic: str,
        first_sample_timeout_seconds: float = 0.5,
    ) -> None:
        self.endpoint = endpoint
        self.topic = topic
        self.first_sample_timeout_seconds = first_sample_timeout_seconds
        self._context = None
        self._socket = None
        self._latest: TimedPose2D | None = None
        self._lock = threading.RLock()

    def open(self) -> None:
        if self._socket is not None:
            return
        try:
            import zmq
        except ImportError as exc:
            raise RuntimeError("启用跨进程 MuJoCo 位姿反馈需要安装 pyzmq") from exc
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.SUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.CONFLATE, 1)
        self._socket.setsockopt_string(zmq.SUBSCRIBE, self.topic)
        self._socket.connect(self.endpoint)

    def __call__(self) -> TimedPose2D:
        with self._lock:
            self.open()
            assert self._socket is not None
            import zmq

            deadline = time.monotonic() + self.first_sample_timeout_seconds
            while True:
                try:
                    while True:
                        message = self._socket.recv(zmq.NOBLOCK)
                        x, y, yaw = decode_navigation_pose_message(
                            message, topic=self.topic
                        )
                        self._latest = TimedPose2D(
                            Pose2D(x, y, yaw), time.monotonic()
                        )
                except zmq.Again:
                    pass
                if self._latest is not None:
                    return self._latest
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # SUB 首次 connect 是异步的，给 ZMQ 握手和首帧到达留出时间。
                self._socket.poll(timeout=max(1, min(50, round(remaining * 1000))))
            if self._latest is None:
                raise RuntimeError(
                    f"在 {self.first_sample_timeout_seconds:.1f}s 内未收到 MuJoCo 根位姿"
                )
            return self._latest

    def close(self) -> None:
        with self._lock:
            if self._socket is not None:
                self._socket.close(linger=0)
                self._socket = None
            if self._context is not None:
                self._context.term()
                self._context = None


class ZmqNavigationPosePublisher:
    """供 MuJoCo 仿真循环调用；每个仿真步发布一次根 qpos。"""

    def __init__(self, endpoint: str = "tcp://*:5558", *, topic: str = "g1_nav_pose") -> None:
        self.endpoint = endpoint
        self.topic = topic
        self._context = None
        self._socket = None

    def open(self) -> None:
        if self._socket is not None:
            return
        try:
            import zmq
        except ImportError as exc:
            raise RuntimeError("启用 MuJoCo 位姿发布需要安装 pyzmq") from exc
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.PUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.setsockopt(zmq.SNDHWM, 1)
        self._socket.bind(self.endpoint)

    def publish_qpos(self, qpos: Sequence[float]) -> None:
        self.open()
        if len(qpos) < 7:
            raise ValueError("G1 qpos 至少需要 7 个根自由度")
        yaw = yaw_from_quaternion_wxyz(qpos[3:7])
        assert self._socket is not None
        self._socket.send(
            build_navigation_pose_message(
                topic=self.topic, x=float(qpos[0]), y=float(qpos[1]), yaw=yaw
            )
        )

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close(linger=0)
            self._socket = None
        if self._context is not None:
            self._context.term()
            self._context = None


__all__ = [
    "MujocoQposPoseProvider",
    "ZmqNavigationPoseProvider",
    "ZmqNavigationPosePublisher",
    "yaw_from_quaternion_wxyz",
]
