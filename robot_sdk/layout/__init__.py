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
from .debug import (
    ChainSegmentDebugInfo,
    InterfaceDebugInfo,
    JointDebugInfo,
    LayoutDebugReport,
    LinkDebugInfo,
    build_layout_debug_report,
    format_layout_debug_report,
)
from .link_primitives import (
    LinkPrimitiveClassification,
    apply_link_primitive_metadata,
    classify_link_primitives,
)
from .decision_adapter import (
    LAYOUT_DECISION_METADATA_KEY,
    MORPHOLOGY_METADATA_KEY,
    apply_layout_decision,
    build_mechanical_layout_with_decision,
)
from .structure_adapter import (
    STRUCTURE_PLAN_TEMPLATE,
    build_mechanical_layout_from_structure_plan,
)

__all__ = [
    "TABLETOP_SERIAL_ARM_TEMPLATE",
    "STRUCTURE_PLAN_TEMPLATE",
    "build_tabletop_serial_mechanical_layout",
    "build_tabletop_serial_mechanical_layout_from_model",
    "map_base_kinematic_to_mount",
    "map_end_effector_kinematic_to_mount",
    "map_joint_kinematic_to_axis",
    "map_joint_kinematic_to_link_body",
    "LayoutValidationIssue",
    "LayoutValidationResult",
    "ChainSegmentDebugInfo",
    "InterfaceDebugInfo",
    "JointDebugInfo",
    "LayoutDebugReport",
    "LinkDebugInfo",
    "LinkPrimitiveClassification",
    "LAYOUT_DECISION_METADATA_KEY",
    "MORPHOLOGY_METADATA_KEY",
    "apply_link_primitive_metadata",
    "apply_layout_decision",
    "assert_valid_mechanical_layout",
    "build_layout_debug_report",
    "build_mechanical_layout_from_structure_plan",
    "build_mechanical_layout_with_decision",
    "classify_link_primitives",
    "format_layout_debug_report",
    "validate_mechanical_layout",
]
