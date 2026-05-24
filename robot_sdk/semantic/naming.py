"""Naming helpers for compiling kinematic specs into RobotModel objects."""

from __future__ import annotations


def clean_identifier(value: str, *, fallback: str = "item") -> str:
    """Return a stable RobotModel-friendly identifier."""

    text = str(value or "").strip()
    if not text:
        text = fallback
    chars = []
    for char in text:
        if char.isalnum() or char == "_":
            chars.append(char)
        elif char in {"-", " ", "."}:
            chars.append("_")
    cleaned = "".join(chars).strip("_")
    return cleaned or fallback


def link_name_for_joint(joint_name: str, *, index: int, tool_frame: str) -> str:
    """Return the child link name for a serial joint."""

    base = clean_identifier(joint_name, fallback=f"joint_{index + 1}")
    return clean_identifier(f"{base}_link", fallback=f"link_{index + 1}")


def actuator_name_for_joint(joint_name: str) -> str:
    return clean_identifier(f"{joint_name}_motor", fallback="joint_motor")


def sensor_name_for_joint(joint_name: str) -> str:
    return clean_identifier(f"{joint_name}_pos", fallback="joint_pos")
