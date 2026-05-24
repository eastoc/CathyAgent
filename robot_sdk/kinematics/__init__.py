"""Mathematical kinematics layer for robot_sdk."""

from .builders import demo_six_dof_spec, demo_three_dof_spec, planar_two_dof_spec, ur3e_like_spec
from .checks import (
    KinematicCheckIssue,
    KinematicCheckReport,
    check_kinematic_spec,
    check_quadruped_spec,
    check_serial_manipulator_spec,
    check_ur3e_like_spec,
)
from .dh import (
    Matrix4,
    forward_kinematics,
    identity_matrix,
    joint_transform,
    matmul,
    modified_dh_transform,
    standard_dh_transform,
    translation_of,
)
from .model import DHJoint, LegChainSpec, QuadrupedSpec, SerialManipulatorSpec

__all__ = [
    "DHJoint",
    "KinematicCheckIssue",
    "KinematicCheckReport",
    "LegChainSpec",
    "Matrix4",
    "QuadrupedSpec",
    "SerialManipulatorSpec",
    "check_kinematic_spec",
    "check_quadruped_spec",
    "check_serial_manipulator_spec",
    "check_ur3e_like_spec",
    "demo_six_dof_spec",
    "demo_three_dof_spec",
    "forward_kinematics",
    "identity_matrix",
    "joint_transform",
    "matmul",
    "modified_dh_transform",
    "planar_two_dof_spec",
    "standard_dh_transform",
    "translation_of",
    "ur3e_like_spec",
]
