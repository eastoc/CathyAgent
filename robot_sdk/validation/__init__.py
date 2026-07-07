"""Report-level validation helpers for robot CAD workflows."""

from .basic import (
    RobotDesignValidationIssue,
    RobotDesignValidationReport,
    validate_robot_design,
)
from .assembly import (
    AssemblyValidationIssue,
    AssemblyValidationReport,
    validate_assembly_semantics,
)
from .assembly_geometry import (
    AssemblyGeometryReport,
    build_assembly_geometry_report,
)
from .structure import (
    RobotStructureValidationIssue,
    RobotStructureValidationReport,
    is_planar_high_dof_template,
    validate_robot_structure,
)
from .source_joint import (
    SourceJointValidationIssue,
    SourceJointValidationReport,
    validate_source_joint_assembly,
)
from .visual import (
    VisualValidationIssue,
    VisualValidationReport,
    validate_visual_geometry,
)

__all__ = [
    "AssemblyValidationIssue",
    "AssemblyValidationReport",
    "AssemblyGeometryReport",
    "RobotDesignValidationIssue",
    "RobotDesignValidationReport",
    "RobotStructureValidationIssue",
    "RobotStructureValidationReport",
    "SourceJointValidationIssue",
    "SourceJointValidationReport",
    "VisualValidationIssue",
    "VisualValidationReport",
    "is_planar_high_dof_template",
    "validate_assembly_semantics",
    "build_assembly_geometry_report",
    "validate_robot_design",
    "validate_robot_structure",
    "validate_source_joint_assembly",
    "validate_visual_geometry",
]
