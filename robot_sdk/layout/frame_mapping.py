"""Frame mapping helpers between kinematics and mechanical layout.

This module exists to keep one boundary explicit:

    DH / FK frame != CAD assembly frame

The functions here create `FrameMapping` records with rationale text. A mapping
is a documented template decision, not a matrix equality claim. CAD generation
must still consume `MechanicalLayout` semantics such as joint axes, link bodies,
interfaces, mate features, and constraints.
"""

from __future__ import annotations

from robot_sdk.types import FrameMapping, Transform


def map_base_kinematic_to_mount(
    *,
    kinematic_frame_id: str = "base_kinematic",
    mount_frame_id: str,
) -> FrameMapping:
    """Map the abstract base kinematic frame to the physical base mount."""

    return FrameMapping(
        kinematic_frame_id=kinematic_frame_id,
        mechanical_frame_id=mount_frame_id,
        transform_offset=Transform(),
        rationale=(
            "The template anchors the abstract base kinematic frame to the "
            "physical tabletop base mount."
        ),
    )


def map_joint_kinematic_to_axis(
    *,
    template: str,
    joint_id: str,
    kinematic_frame_id: str,
    axis_frame_id: str,
) -> FrameMapping:
    """Map a joint's abstract kinematic station to its mechanical axis frame."""

    return FrameMapping(
        kinematic_frame_id=kinematic_frame_id,
        mechanical_frame_id=axis_frame_id,
        transform_offset=Transform(),
        rationale=(
            f"{template} maps {joint_id}'s abstract DH station onto the physical "
            "joint-axis frame. This is a template mapping, not a claim that the "
            "DH transform is a CAD mate."
        ),
    )


def map_joint_kinematic_to_link_body(
    *,
    joint_id: str,
    link_id: str,
    kinematic_frame_id: str,
    link_body_frame_id: str,
    link_span: float,
) -> FrameMapping:
    """Map a joint kinematic station to the simplified body frame of a link."""

    return FrameMapping(
        kinematic_frame_id=kinematic_frame_id,
        mechanical_frame_id=link_body_frame_id,
        transform_offset=Transform(translation=(link_span / 2.0, 0.0, 0.0)),
        rationale=(
            f"{link_id}'s body frame is offset half the simplified link span from "
            f"{joint_id}'s kinematic station so CAD can place a box beam."
        ),
    )


def map_end_effector_kinematic_to_mount(
    *,
    kinematic_frame_id: str,
    mount_frame_id: str,
) -> FrameMapping:
    """Map the abstract end-effector frame to the physical tool mounting face."""

    return FrameMapping(
        kinematic_frame_id=kinematic_frame_id,
        mechanical_frame_id=mount_frame_id,
        transform_offset=Transform(),
        rationale=(
            "The template maps the abstract end-effector kinematic frame to the "
            "physical tool mounting face before CAD skeleton generation."
        ),
    )
