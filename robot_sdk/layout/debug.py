"""MechanicalLayout debug reporting helpers.

This module intentionally does not validate or mutate the layout. It turns the
current frame graph into reviewable numbers so assembly mistakes can be traced
before CadQuery gets involved.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from robot_sdk.types import FrameSpec, InterfaceSpec, JointLayout, LinkLayout, MechanicalLayout


Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class JointDebugInfo:
    """Debug view for one mechanical joint."""

    joint_id: str
    axis_frame: str
    axis_origin: Vector3
    axis_direction: Vector3
    parent_link: str
    child_link: str
    input_interface: str | None
    output_interface: str | None


@dataclass(frozen=True)
class LinkDebugInfo:
    """Debug view for one mechanical link."""

    link_id: str
    body_frame: str
    body_origin: Vector3
    body_direction: Vector3
    primitive_type: str | None
    input_interface: str | None
    output_interface: str | None
    input_origin: Vector3 | None
    output_origin: Vector3 | None
    connects_joint_from: str | None
    connects_joint_to: str | None


@dataclass(frozen=True)
class InterfaceDebugInfo:
    """Debug view for one mechanical interface."""

    interface_id: str
    frame: str
    origin: Vector3 | None
    normal: Vector3 | None
    tangent: Vector3 | None
    type: str
    mates_to: str | None


@dataclass(frozen=True)
class ChainSegmentDebugInfo:
    """Debug view for a local joint-link-joint segment."""

    segment_id: str
    from_joint_id: str | None
    link_id: str
    to_joint_id: str | None
    link_output_origin: Vector3 | None
    next_joint_input_origin: Vector3 | None
    interface_gap: float | None
    link_body_direction: Vector3
    next_joint_axis_direction: Vector3 | None
    axis_mismatch_deg: float | None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LayoutDebugReport:
    """Reviewable mechanical-layout diagnostics."""

    joints: list[JointDebugInfo]
    links: list[LinkDebugInfo]
    interfaces: list[InterfaceDebugInfo]
    chain_segments: list[ChainSegmentDebugInfo]
    warnings: list[str] = field(default_factory=list)

    def find_interface(self, interface_id: str) -> InterfaceDebugInfo:
        """Return interface debug info or raise a precise error."""

        for interface in self.interfaces:
            if interface.interface_id == interface_id:
                return interface
        raise KeyError(f"Unknown interface `{interface_id}`")

    def find_segment(
        self,
        *,
        from_joint_id: str | None = None,
        link_id: str,
        to_joint_id: str | None = None,
    ) -> ChainSegmentDebugInfo:
        """Return the matching local chain segment or raise a precise error."""

        for segment in self.chain_segments:
            if segment.link_id != link_id:
                continue
            if from_joint_id is not None and segment.from_joint_id != from_joint_id:
                continue
            if to_joint_id is not None and segment.to_joint_id != to_joint_id:
                continue
            return segment
        parts = [from_joint_id or "*", link_id, to_joint_id or "*"]
        raise KeyError(f"Unknown chain segment `{' -> '.join(parts)}`")


def build_layout_debug_report(layout: MechanicalLayout) -> LayoutDebugReport:
    """Build a debug report from a `MechanicalLayout` frame graph."""

    frame_by_id = {frame.id: frame for frame in layout.frames}
    frame_origins = _global_frame_origins(layout.frames)
    interfaces_by_id = {interface.id: interface for interface in layout.interfaces}

    joint_infos = [
        _joint_debug_info(
            joint,
            layout.links,
            frame_by_id=frame_by_id,
            frame_origins=frame_origins,
        )
        for joint in layout.joints
    ]
    joint_by_id = {info.joint_id: info for info in joint_infos}

    link_infos = [
        _link_debug_info(
            link,
            frame_by_id=frame_by_id,
            frame_origins=frame_origins,
            interfaces_by_id=interfaces_by_id,
        )
        for link in layout.links
    ]
    interface_infos = [
        _interface_debug_info(
            interface,
            frame_by_id=frame_by_id,
            frame_origins=frame_origins,
        )
        for interface in layout.interfaces
    ]
    chain_segments = [
        _chain_segment_debug_info(
            link_info,
            joint_by_id=joint_by_id,
            interfaces_by_id=interfaces_by_id,
            frame_origins=frame_origins,
        )
        for link_info in link_infos
    ]
    warnings = _report_warnings(chain_segments)
    return LayoutDebugReport(
        joints=joint_infos,
        links=link_infos,
        interfaces=interface_infos,
        chain_segments=chain_segments,
        warnings=warnings,
    )


def format_layout_debug_report(report: LayoutDebugReport) -> str:
    """Render the debug report as compact text for logs or review docs."""

    lines = ["# MechanicalLayout Debug Report", "", "## Chain Segments"]
    for segment in report.chain_segments:
        gap = "n/a" if segment.interface_gap is None else f"{segment.interface_gap:g} mm"
        angle = (
            "n/a"
            if segment.axis_mismatch_deg is None
            else f"{segment.axis_mismatch_deg:g} deg"
        )
        lines.append(
            "- "
            f"{segment.segment_id}: gap={gap}, "
            f"link_body_direction={segment.link_body_direction}, "
            f"next_joint_axis_direction={segment.next_joint_axis_direction}, "
            f"axis_mismatch={angle}"
        )
        for warning in segment.warnings:
            lines.append(f"  - warning: {warning}")
    lines.extend(["", "## Interfaces"])
    for interface in report.interfaces:
        lines.append(
            "- "
            f"{interface.interface_id}: origin={interface.origin}, "
            f"normal={interface.normal}, tangent={interface.tangent}, "
            f"mates_to={interface.mates_to}"
        )
    if report.warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in report.warnings)
    lines.extend(["", "## Link Primitives"])
    for link in report.links:
        lines.append(
            f"- {link.link_id}: primitive={link.primitive_type}, "
            f"body_direction={link.body_direction}"
        )
    return "\n".join(lines)


def _joint_debug_info(
    joint: JointLayout,
    links: list[LinkLayout],
    *,
    frame_by_id: dict[str, FrameSpec],
    frame_origins: dict[str, Vector3],
) -> JointDebugInfo:
    frame = _require_frame(frame_by_id, joint.axis_frame)
    return JointDebugInfo(
        joint_id=joint.id,
        axis_frame=joint.axis_frame,
        axis_origin=frame_origins[joint.axis_frame],
        axis_direction=_frame_axis_direction(frame),
        parent_link=joint.parent_link,
        child_link=joint.child_link,
        input_interface=_input_interface_for_joint(joint.id, links),
        output_interface=_output_interface_for_joint(joint.id, links),
    )


def _link_debug_info(
    link: LinkLayout,
    *,
    frame_by_id: dict[str, FrameSpec],
    frame_origins: dict[str, Vector3],
    interfaces_by_id: dict[str, InterfaceSpec],
) -> LinkDebugInfo:
    frame = _require_frame(frame_by_id, link.body_frame)
    input_origin = _interface_origin(link.from_interface, interfaces_by_id, frame_origins)
    output_origin = _interface_origin(link.to_interface, interfaces_by_id, frame_origins)
    body_direction = _link_body_direction(frame, input_origin, output_origin)
    return LinkDebugInfo(
        link_id=link.id,
        body_frame=link.body_frame,
        body_origin=frame_origins[link.body_frame],
        body_direction=body_direction,
        primitive_type=_link_primitive_type(link),
        input_interface=link.from_interface,
        output_interface=link.to_interface,
        input_origin=input_origin,
        output_origin=output_origin,
        connects_joint_from=_first_endpoint(link.from_interface),
        connects_joint_to=_second_endpoint(link.to_interface),
    )


def _interface_debug_info(
    interface: InterfaceSpec,
    *,
    frame_by_id: dict[str, FrameSpec],
    frame_origins: dict[str, Vector3],
) -> InterfaceDebugInfo:
    frame = frame_by_id.get(interface.frame)
    return InterfaceDebugInfo(
        interface_id=interface.id,
        frame=interface.frame,
        origin=frame_origins.get(interface.frame),
        normal=_interface_vector(interface, frame, "normal", (0.0, 0.0, 1.0)),
        tangent=_interface_vector(interface, frame, "tangent", (1.0, 0.0, 0.0)),
        type=interface.type,
        mates_to=interface.mates_to,
    )


def _link_primitive_type(link: LinkLayout) -> str | None:
    value = link.metadata.get("primitive_type")
    if isinstance(value, str):
        return value
    detail = link.metadata.get("link_primitive")
    if isinstance(detail, dict) and isinstance(detail.get("type"), str):
        return detail["type"]
    return None


def _chain_segment_debug_info(
    link: LinkDebugInfo,
    *,
    joint_by_id: dict[str, JointDebugInfo],
    interfaces_by_id: dict[str, InterfaceSpec],
    frame_origins: dict[str, Vector3],
) -> ChainSegmentDebugInfo:
    next_joint = joint_by_id.get(link.connects_joint_to or "")
    next_joint_input_origin = None
    if next_joint is not None:
        next_joint_input_origin = _interface_origin(
            next_joint.input_interface,
            interfaces_by_id,
            frame_origins,
        )
    gap = _distance(link.output_origin, next_joint_input_origin)
    axis_mismatch = None
    if next_joint is not None:
        axis_mismatch = _angle_deg(link.body_direction, next_joint.axis_direction)
    warnings: list[str] = []
    if axis_mismatch is not None and axis_mismatch > 15.0:
        warnings.append(
            "link body direction is not aligned with the next joint axis; "
            "do not use link.body_axis == joint.axis as a placement assumption."
        )
    if gap is not None and gap > 1.0:
        warnings.append(
            "link output interface and next joint input interface have a visible gap."
        )
    segment_id = " -> ".join(
        item for item in (link.connects_joint_from, link.link_id, link.connects_joint_to) if item
    )
    return ChainSegmentDebugInfo(
        segment_id=segment_id,
        from_joint_id=link.connects_joint_from,
        link_id=link.link_id,
        to_joint_id=link.connects_joint_to,
        link_output_origin=link.output_origin,
        next_joint_input_origin=next_joint_input_origin,
        interface_gap=gap,
        link_body_direction=link.body_direction,
        next_joint_axis_direction=None if next_joint is None else next_joint.axis_direction,
        axis_mismatch_deg=axis_mismatch,
        warnings=warnings,
    )


def _report_warnings(segments: list[ChainSegmentDebugInfo]) -> list[str]:
    warnings: list[str] = []
    for segment in segments:
        for warning in segment.warnings:
            warnings.append(f"{segment.segment_id}: {warning}")
    return warnings


def _global_frame_origins(frames: list[FrameSpec]) -> dict[str, Vector3]:
    by_id = {frame.id: frame for frame in frames}
    cache: dict[str, Vector3] = {}

    def resolve(frame_id: str) -> Vector3:
        if frame_id in cache:
            return cache[frame_id]
        frame = by_id[frame_id]
        tx, ty, tz = frame.transform.translation
        if frame.parent and frame.parent in by_id:
            px, py, pz = resolve(frame.parent)
            value = (px + tx, py + ty, pz + tz)
        else:
            value = (tx, ty, tz)
        cache[frame_id] = value
        return value

    return {frame.id: resolve(frame.id) for frame in frames}


def _frame_axis_direction(frame: FrameSpec) -> Vector3:
    return _apply_rpy_to_vector(frame.transform.rotation_rpy, (0.0, 0.0, 1.0))


def _link_body_direction(
    frame: FrameSpec,
    input_origin: Vector3 | None,
    output_origin: Vector3 | None,
) -> Vector3:
    if input_origin is not None and output_origin is not None:
        direction = _unit(_vector_between(input_origin, output_origin))
        if direction is not None:
            return direction
    direction = _unit(frame.transform.translation)
    return direction or (1.0, 0.0, 0.0)


def _interface_origin(
    interface_id: str | None,
    interfaces_by_id: dict[str, InterfaceSpec],
    frame_origins: dict[str, Vector3],
) -> Vector3 | None:
    if not interface_id:
        return None
    interface = interfaces_by_id.get(interface_id)
    if interface is None:
        return None
    return frame_origins.get(interface.frame)


def _interface_vector(
    interface: InterfaceSpec,
    frame: FrameSpec | None,
    key: str,
    fallback_local_vector: Vector3,
) -> Vector3 | None:
    value = interface.metadata.get(key)
    if _is_vector3(value):
        return _unit((float(value[0]), float(value[1]), float(value[2])))
    if frame is None:
        return None
    return _apply_rpy_to_vector(frame.transform.rotation_rpy, fallback_local_vector)


def _is_vector3(value: object) -> bool:
    return isinstance(value, (tuple, list)) and len(value) == 3


def _input_interface_for_joint(joint_id: str, links: list[LinkLayout]) -> str | None:
    for link in links:
        if _second_endpoint(link.to_interface) == joint_id:
            return link.to_interface
    return None


def _output_interface_for_joint(joint_id: str, links: list[LinkLayout]) -> str | None:
    for link in links:
        if _first_endpoint(link.from_interface) == joint_id:
            return link.from_interface
    return None


def _first_endpoint(interface_id: str | None) -> str | None:
    if not interface_id or "_to_" not in interface_id:
        return None
    return interface_id.split("_to_", 1)[0]


def _second_endpoint(interface_id: str | None) -> str | None:
    if not interface_id or "_to_" not in interface_id:
        return None
    tail = interface_id.split("_to_", 1)[1]
    for suffix in ("_flange", "_end_effector_mount"):
        if tail.endswith(suffix):
            return tail[: -len(suffix)]
    return tail


def _require_frame(frame_by_id: dict[str, FrameSpec], frame_id: str) -> FrameSpec:
    try:
        return frame_by_id[frame_id]
    except KeyError as exc:
        raise KeyError(f"Unknown layout frame `{frame_id}`") from exc


def _vector_between(start: Vector3, end: Vector3) -> Vector3:
    return (end[0] - start[0], end[1] - start[1], end[2] - start[2])


def _distance(left: Vector3 | None, right: Vector3 | None) -> float | None:
    if left is None or right is None:
        return None
    diff = _vector_between(left, right)
    return math.sqrt(diff[0] ** 2 + diff[1] ** 2 + diff[2] ** 2)


def _angle_deg(left: Vector3, right: Vector3) -> float:
    left_unit = _unit(left)
    right_unit = _unit(right)
    if left_unit is None or right_unit is None:
        return 0.0
    dot = max(-1.0, min(1.0, _dot(left_unit, right_unit)))
    return math.degrees(math.acos(abs(dot)))


def _unit(vector: Vector3) -> Vector3 | None:
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    if length <= 1e-9:
        return None
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _dot(left: Vector3, right: Vector3) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _apply_rpy_to_vector(rpy: Vector3, vector: Vector3) -> Vector3:
    """Apply roll-pitch-yaw rotation to a vector."""

    roll, pitch, yaw = rpy
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)

    x, y, z = vector
    # Roll around X.
    y, z = (cr * y - sr * z, sr * y + cr * z)
    # Pitch around Y.
    x, z = (cp * x + sp * z, -sp * x + cp * z)
    # Yaw around Z.
    x, y = (cy * x - sy * y, sy * x + cy * y)
    return _unit((x, y, z)) or (0.0, 0.0, 1.0)
