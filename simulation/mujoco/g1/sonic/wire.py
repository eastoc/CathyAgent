"""与 NVIDIA GEAR-SONIC zmq_manager 兼容的打包协议。"""

from __future__ import annotations

import json
import struct
import math
from collections.abc import Sequence

HEADER_SIZE = 1280


def _header(fields: list[dict[str, object]]) -> bytes:
    raw = json.dumps(
        {"v": 1, "endian": "le", "count": 1, "fields": fields},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > HEADER_SIZE:
        raise ValueError(f"SONIC 消息头过大: {len(raw)} > {HEADER_SIZE}")
    return raw.ljust(HEADER_SIZE, b"\x00")


def build_command_message(*, start: bool, stop: bool, planner: bool = True) -> bytes:
    fields = [
        {"name": "start", "dtype": "u8", "shape": [1]},
        {"name": "stop", "dtype": "u8", "shape": [1]},
        {"name": "planner", "dtype": "u8", "shape": [1]},
    ]
    payload = struct.pack("BBB", int(start), int(stop), int(planner))
    return b"command" + _header(fields) + payload


def build_planner_message(
    *,
    mode: int,
    movement: Sequence[float],
    facing: Sequence[float],
    speed: float = -1.0,
    height: float = -1.0,
) -> bytes:
    if len(movement) != 3 or len(facing) != 3:
        raise ValueError("movement 和 facing 必须各包含 3 个分量")
    fields = [
        {"name": "mode", "dtype": "i32", "shape": [1]},
        {"name": "movement", "dtype": "f32", "shape": [3]},
        {"name": "facing", "dtype": "f32", "shape": [3]},
        {"name": "speed", "dtype": "f32", "shape": [1]},
        {"name": "height", "dtype": "f32", "shape": [1]},
    ]
    payload = b"".join(
        (
            struct.pack("<i", int(mode)),
            struct.pack("<fff", *(float(value) for value in movement)),
            struct.pack("<fff", *(float(value) for value in facing)),
            struct.pack("<f", float(speed)),
            struct.pack("<f", float(height)),
        )
    )
    return b"planner" + _header(fields) + payload


def decode_header(message: bytes, topic: bytes) -> dict[str, object]:
    """供协议单元测试和诊断使用。"""
    if not message.startswith(topic) or len(message) < len(topic) + HEADER_SIZE:
        raise ValueError("不是有效的 SONIC packed message")
    header = message[len(topic) : len(topic) + HEADER_SIZE].split(b"\x00", 1)[0]
    return json.loads(header.decode("utf-8"))


def build_navigation_pose_message(
    *, topic: str, x: float, y: float, yaw: float
) -> bytes:
    if not all(math.isfinite(value) for value in (x, y, yaw)):
        raise ValueError("导航位姿必须是有限数")
    fields = [{"name": "pose_xy_yaw", "dtype": "f64", "shape": [3]}]
    return topic.encode("utf-8") + _header(fields) + struct.pack("<ddd", x, y, yaw)


def decode_navigation_pose_message(message: bytes, *, topic: str) -> tuple[float, float, float]:
    prefix = topic.encode("utf-8")
    header = decode_header(message, prefix)
    fields = header.get("fields")
    if not isinstance(fields, list) or fields != [
        {"name": "pose_xy_yaw", "dtype": "f64", "shape": [3]}
    ]:
        raise ValueError("导航位姿字段必须是 pose_xy_yaw:f64[3]")
    offset = len(prefix) + HEADER_SIZE
    if len(message) != offset + 24:
        raise ValueError("导航位姿消息长度无效")
    return struct.unpack("<ddd", message[offset:])


__all__ = [
    "HEADER_SIZE",
    "build_command_message",
    "build_planner_message",
    "build_navigation_pose_message",
    "decode_navigation_pose_message",
    "decode_header",
]
