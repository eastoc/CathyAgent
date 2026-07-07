"""Rule-based link primitive classification for `MechanicalLayout`.

The classifier converts frame/interface diagnostics into CAD-facing link
semantics. It does not build geometry. The result is intentionally written as
metadata so CadQuery adapters, validation, and future layout agents can consume
the same neutral rule output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from typing import Any

from robot_sdk.layout.debug import (
    InterfaceDebugInfo,
    LinkDebugInfo,
    build_layout_debug_report,
)
from robot_sdk.types import LinkPrimitiveType, MechanicalLayout


Vector3 = tuple[float, float, float]

PRIMITIVE_METADATA_KEY = "link_primitive"
PRIMITIVE_TYPE_METADATA_KEY = "primitive_type"
ELBOW_NORMAL_ANGLE_DEG = 20.0
OFFSET_NORMAL_ALIGNMENT_DEG = 20.0
WRIST_TAIL_LINK_COUNT = 4
WRIST_SPACER_MAX_LENGTH_MM = 80.0
WRIST_AXIS_MISMATCH_DEG = 45.0


@dataclass(frozen=True)
class LinkPrimitiveClassification:
    """Classification result for one mechanical link."""

    link_id: str
    primitive_type: LinkPrimitiveType
    length_mm: float
    input_interface: str | None
    output_interface: str | None
    input_normal: Vector3 | None
    output_normal: Vector3 | None
    body_direction: Vector3
    next_joint_axis_direction: Vector3 | None
    interface_normal_angle_deg: float | None
    body_to_input_normal_angle_deg: float | None
    body_to_output_normal_angle_deg: float | None
    next_axis_mismatch_deg: float | None
    reasons: list[str]

    def to_metadata(self) -> dict[str, Any]:
        """Return JSON-friendly metadata for `LinkLayout.metadata`."""

        data = asdict(self)
        data["type"] = self.primitive_type
        return data


def classify_link_primitives(
    layout: MechanicalLayout,
) -> list[LinkPrimitiveClassification]:
    """Classify every link in a `MechanicalLayout` into a CAD primitive family."""

    report = build_layout_debug_report(layout)
    links_by_id = {link.id: link for link in layout.links}
    link_count = len(layout.links)
    classifications: list[LinkPrimitiveClassification] = []

    for debug_link in report.links:
        link = links_by_id[debug_link.link_id]
        input_interface = _find_interface(report.interfaces, debug_link.input_interface)
        output_interface = _find_interface(report.interfaces, debug_link.output_interface)
        segment = report.find_segment(link_id=debug_link.link_id)
        length = float(link.envelope.dimensions.get("length", 0.0))
        index = _link_index(debug_link.link_id, layout.links)
        classifications.append(
            _classify_one_link(
                debug_link,
                input_interface=input_interface,
                output_interface=output_interface,
                length_mm=length,
                link_index=index,
                link_count=link_count,
                next_joint_axis_direction=segment.next_joint_axis_direction,
                next_axis_mismatch_deg=segment.axis_mismatch_deg,
            )
        )

    return classifications


def apply_link_primitive_metadata(layout: MechanicalLayout) -> MechanicalLayout:
    """Return a copy of `layout` with link primitive metadata attached."""

    classifications = {
        classification.link_id: classification
        for classification in classify_link_primitives(layout)
    }
    links = []
    for link in layout.links:
        classification = classifications[link.id]
        primitive_metadata = classification.to_metadata()
        metadata = {
            **link.metadata,
            PRIMITIVE_TYPE_METADATA_KEY: classification.primitive_type,
            PRIMITIVE_METADATA_KEY: primitive_metadata,
        }
        links.append(replace(link, metadata=metadata))
    return replace(layout, links=links)


def _classify_one_link(
    link: LinkDebugInfo,
    *,
    input_interface: InterfaceDebugInfo | None,
    output_interface: InterfaceDebugInfo | None,
    length_mm: float,
    link_index: int,
    link_count: int,
    next_joint_axis_direction: Vector3 | None,
    next_axis_mismatch_deg: float | None,
) -> LinkPrimitiveClassification:
    input_normal = None if input_interface is None else input_interface.normal
    output_normal = None if output_interface is None else output_interface.normal
    interface_angle = _angle_deg(input_normal, output_normal)
    body_to_input = _angle_deg(link.body_direction, input_normal)
    body_to_output = _angle_deg(link.body_direction, output_normal)
    is_wrist_region = (
        link_count >= 6 and link_index >= max(0, link_count - WRIST_TAIL_LINK_COUNT)
    )
    has_wrist_axis_turn = (
        is_wrist_region
        and next_axis_mismatch_deg is not None
        and next_axis_mismatch_deg >= WRIST_AXIS_MISMATCH_DEG
    )
    is_terminal_wrist_spacer = (
        is_wrist_region
        and length_mm <= WRIST_SPACER_MAX_LENGTH_MM
        and next_axis_mismatch_deg is None
    )
    reasons: list[str] = []

    if has_wrist_axis_turn:
        primitive_type: LinkPrimitiveType = "elbow_link"
        reasons.append(
            f"wrist transition has {next_axis_mismatch_deg:g} deg body-to-axis mismatch"
        )
    elif is_terminal_wrist_spacer:
        primitive_type = "wrist_spacer"
        reasons.append(
            "terminal short link in wrist/tail region; use flange/cylindrical spacer semantics"
        )
    elif interface_angle is not None and interface_angle > ELBOW_NORMAL_ANGLE_DEG:
        primitive_type = "elbow_link"
        reasons.append(
            f"input/output interface normals differ by {interface_angle:g} deg"
        )
    elif (
        _is_parallel(input_normal, output_normal)
        and (
            _angle_exceeds(body_to_input, OFFSET_NORMAL_ALIGNMENT_DEG)
            or _angle_exceeds(body_to_output, OFFSET_NORMAL_ALIGNMENT_DEG)
        )
    ):
        primitive_type = "offset_link"
        reasons.append(
            "parallel interfaces are not aligned with the link body direction"
        )
    else:
        primitive_type = "straight_link"
        reasons.append("interfaces are approximately collinear with the link body")

    return LinkPrimitiveClassification(
        link_id=link.link_id,
        primitive_type=primitive_type,
        length_mm=length_mm,
        input_interface=link.input_interface,
        output_interface=link.output_interface,
        input_normal=input_normal,
        output_normal=output_normal,
        body_direction=link.body_direction,
        next_joint_axis_direction=next_joint_axis_direction,
        interface_normal_angle_deg=interface_angle,
        body_to_input_normal_angle_deg=body_to_input,
        body_to_output_normal_angle_deg=body_to_output,
        next_axis_mismatch_deg=next_axis_mismatch_deg,
        reasons=reasons,
    )


def _find_interface(
    interfaces: list[InterfaceDebugInfo],
    interface_id: str | None,
) -> InterfaceDebugInfo | None:
    if interface_id is None:
        return None
    for interface in interfaces:
        if interface.interface_id == interface_id:
            return interface
    return None


def _link_index(link_id: str, links) -> int:
    for index, link in enumerate(links):
        if link.id == link_id:
            return index
    return 0


def _angle_exceeds(value: float | None, threshold: float) -> bool:
    return value is not None and value > threshold


def _is_parallel(left: Vector3 | None, right: Vector3 | None) -> bool:
    angle = _angle_deg(left, right)
    return angle is not None and angle <= ELBOW_NORMAL_ANGLE_DEG


def _angle_deg(left: Vector3 | None, right: Vector3 | None) -> float | None:
    if left is None or right is None:
        return None
    left_unit = _unit(left)
    right_unit = _unit(right)
    if left_unit is None or right_unit is None:
        return None
    dot = max(-1.0, min(1.0, _dot(left_unit, right_unit)))
    return math.degrees(math.acos(abs(dot)))


def _unit(vector: Vector3) -> Vector3 | None:
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    if length <= 1e-9:
        return None
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _dot(left: Vector3, right: Vector3) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
