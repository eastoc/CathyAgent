"""Mechanical layout builders for robot CAD generation."""

from .mechanical_layout import (
    TABLETOP_SERIAL_ARM_TEMPLATE,
    build_tabletop_serial_mechanical_layout,
    build_tabletop_serial_mechanical_layout_from_model,
)
from .frame_mapping import (
    map_base_kinematic_to_mount,
    map_end_effector_kinematic_to_mount,
    map_joint_kinematic_to_axis,
    map_joint_kinematic_to_link_body,
)
from .validation import (
    LayoutValidationIssue,
    LayoutValidationResult,
    assert_valid_mechanical_layout,
    validate_mechanical_layout,
)

__all__ = [
    "TABLETOP_SERIAL_ARM_TEMPLATE",
    "build_tabletop_serial_mechanical_layout",
    "build_tabletop_serial_mechanical_layout_from_model",
    "map_base_kinematic_to_mount",
    "map_end_effector_kinematic_to_mount",
    "map_joint_kinematic_to_axis",
    "map_joint_kinematic_to_link_body",
    "LayoutValidationIssue",
    "LayoutValidationResult",
    "assert_valid_mechanical_layout",
    "validate_mechanical_layout",
]
