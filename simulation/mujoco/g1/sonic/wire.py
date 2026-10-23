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
    *, topic: str, x: float, y: float, yaw: float,
    root_qpos: Sequence[float] | None = None,
    joint_position_hardware: Sequence[float] | None = None,
    motion_active: bool | None = None,
) -> bytes:
    if not all(math.isfinite(value) for value in (x, y, yaw)):
        raise ValueError("导航位姿必须是有限数")
    fields = [{"name": "pose_xy_yaw", "dtype": "f64", "shape": [3]}]
    payload = struct.pack("<ddd", x, y, yaw)
    if root_qpos is not None or joint_position_hardware is not None:
        if root_qpos is None or len(root_qpos) != 7:
            raise ValueError("SONIC root_qpos 必须包含 7 个分量")
        if joint_position_hardware is None or len(joint_position_hardware) != 29:
            raise ValueError("SONIC joint_position_hardware 必须包含 29 个关节角")
        values = [*root_qpos, *joint_position_hardware]
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("SONIC 根位姿和关节角必须是有限数")
        fields.extend([
            {"name": "root_qpos", "dtype": "f64", "shape": [7]},
            {"name": "joint_position_hardware", "dtype": "f32", "shape": [29]},
            {"name": "motion_active", "dtype": "u8", "shape": [1]},
        ])
        payload += struct.pack("<7d", *(float(value) for value in root_qpos))
        payload += struct.pack(
            "<29f", *(float(value) for value in joint_position_hardware)
        )
        payload += struct.pack("B", int(bool(motion_active)))
    return topic.encode("utf-8") + _header(fields) + payload


def decode_navigation_state_message(
    message: bytes, *, topic: str
) -> tuple[
    tuple[float, float, float],
    tuple[float, ...] | None,
    tuple[float, ...] | None,
    bool | None,
]:
    prefix = topic.encode("utf-8")
    header = decode_header(message, prefix)
    fields = header.get("fields")
    pose_field = {"name": "pose_xy_yaw", "dtype": "f64", "shape": [3]}
    full_fields = [
        pose_field,
        {"name": "root_qpos", "dtype": "f64", "shape": [7]},
        {"name": "joint_position_hardware", "dtype": "f32", "shape": [29]},
        {"name": "motion_active", "dtype": "u8", "shape": [1]},
    ]
    offset = len(prefix) + HEADER_SIZE
    if fields == [pose_field]:
        if len(message) != offset + 24:
            raise ValueError("导航位姿消息长度无效")
        return struct.unpack("<ddd", message[offset:]), None, None, None
    if fields != full_fields:
        raise ValueError("SONIC 状态字段定义无效")
    expected = offset + 24 + 56 + 116 + 1
    if len(message) != expected:
        raise ValueError("SONIC 完整状态消息长度无效")
    pose = struct.unpack("<ddd", message[offset:offset + 24])
    root = struct.unpack("<7d", message[offset + 24:offset + 80])
    joints = struct.unpack("<29f", message[offset + 80:offset + 196])
    motion_active = bool(struct.unpack("B", message[offset + 196:])[0])
    return pose, root, joints, motion_active


def decode_navigation_pose_message(message: bytes, *, topic: str) -> tuple[float, float, float]:
    pose, _, _, _ = decode_navigation_state_message(message, topic=topic)
    return pose


__all__ = [
    "HEADER_SIZE",
    "build_command_message",
    "build_planner_message",
    "build_navigation_pose_message",
    "decode_navigation_pose_message",
    "decode_navigation_state_message",
    "decode_header",
]
