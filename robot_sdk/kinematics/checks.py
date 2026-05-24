"""Validation helpers for kinematic specifications."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Union

from .model import DHJoint, LegChainSpec, QuadrupedSpec, SerialManipulatorSpec


@dataclass(frozen=True)
class KinematicCheckIssue:
    """One validation issue from a kinematic specification."""

    severity: str
    code: str
    message: str


@dataclass
class KinematicCheckReport:
    """Accumulated kinematic validation report."""

    issues: list[KinematicCheckIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def error(self, code: str, message: str) -> None:
        self.issues.append(KinematicCheckIssue("error", code, message))

    def warning(self, code: str, message: str) -> None:
        self.issues.append(KinematicCheckIssue("warning", code, message))

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "issues": [
                {"severity": issue.severity, "code": issue.code, "message": issue.message}
                for issue in self.issues
            ],
        }

    def assert_ok(self) -> None:
        if not self.ok:
            errors = [issue.message for issue in self.issues if issue.severity == "error"]
            raise ValueError("; ".join(errors))


def check_serial_manipulator_spec(spec: SerialManipulatorSpec) -> KinematicCheckReport:
    """Validate a serial manipulator kinematic spec."""

    report = KinematicCheckReport()
    if spec.representation not in {"dh", "modified_dh"}:
        report.error("unsupported_representation", f"unsupported representation: {spec.representation!r}")
    if spec.dof < 0:
        report.error("invalid_dof", "dof cannot be negative")

    moving_joints = [joint for joint in spec.joints if joint.joint_type != "fixed"]
    if spec.dof != len(moving_joints):
        report.error(
            "dof_mismatch",
            f"spec dof is {spec.dof}, but {len(moving_joints)} non-fixed joints were provided",
        )
    _check_unique([joint.name for joint in spec.joints], "joint", report)
    for joint in spec.joints:
        _check_dh_joint(joint, report)
    return report


def check_quadruped_spec(spec: QuadrupedSpec) -> KinematicCheckReport:
    """Validate a quadruped kinematic spec."""

    report = KinematicCheckReport()
    if len(spec.legs) != 4:
        report.error("quadruped_leg_count", f"quadruped must have 4 legs; found {len(spec.legs)}")
    _check_unique([leg.name for leg in spec.legs], "leg", report)
    leg_names = {leg.name for leg in spec.legs}
    for leg in spec.legs:
        _check_leg_chain(leg, leg_names, report)
    return report


def check_ur3e_like_spec(spec: SerialManipulatorSpec) -> KinematicCheckReport:
    """Validate the UR3e-like kinematic spec contract.

    This check only inspects the spec-level contract. Generated link names are
    derived later by the semantic compiler from joint names.
    """

    report = check_serial_manipulator_spec(spec)
    moving_joints = [joint for joint in spec.joints if joint.joint_type != "fixed"]
    revolute_joints = [joint for joint in moving_joints if joint.joint_type == "revolute"]
    if len(revolute_joints) != 6:
        report.error("ur3e_revolute_count", f"UR3e-like arm must have 6 revolute joints; found {len(revolute_joints)}")

    required_roles = {"wrist_1_pitch", "wrist_2_yaw", "wrist_3_roll"}
    roles = {joint.role for joint in spec.joints}
    missing_roles = sorted(role for role in required_roles if role not in roles)
    if missing_roles:
        report.error("ur3e_missing_wrist_axes", f"UR3e-like arm is missing wrist roles: {', '.join(missing_roles)}")

    missing_axis_hints = [joint.name for joint in moving_joints if joint.axis_hint is None]
    if missing_axis_hints:
        report.error("ur3e_missing_axis_hint", f"UR3e-like joints must declare axis_hint: {', '.join(missing_axis_hints)}")

    if spec.tool_frame != "tool0":
        report.error("ur3e_missing_tool0", "UR3e-like arm must use tool_frame='tool0'")

    return report


def check_kinematic_spec(spec: Union[SerialManipulatorSpec, QuadrupedSpec]) -> KinematicCheckReport:
    """Validate any supported kinematic specification."""

    if isinstance(spec, SerialManipulatorSpec):
        return check_serial_manipulator_spec(spec)
    if isinstance(spec, QuadrupedSpec):
        return check_quadruped_spec(spec)
    report = KinematicCheckReport()
    report.error("unsupported_spec_type", f"unsupported kinematic spec type: {type(spec).__name__}")
    return report


def _check_dh_joint(joint: DHJoint, report: KinematicCheckReport) -> None:
    if joint.joint_type not in {"revolute", "continuous", "prismatic", "fixed"}:
        report.error("unsupported_joint_type", f"joint {joint.name!r} has unsupported type {joint.joint_type!r}")
    for field_name, value in {"alpha": joint.alpha, "a": joint.a}.items():
        if not math.isfinite(float(value)):
            report.error("nonfinite_dh_parameter", f"joint {joint.name!r} has non-finite {field_name}")
    for field_name, value in {"d": joint.d, "theta": joint.theta}.items():
        if isinstance(value, str):
            if not value.strip():
                report.error("empty_variable_name", f"joint {joint.name!r} has empty variable {field_name}")
        elif not math.isfinite(float(value)):
            report.error("nonfinite_dh_parameter", f"joint {joint.name!r} has non-finite {field_name}")
    if joint.joint_type in {"revolute", "prismatic"} and joint.limit is None:
        report.warning("missing_joint_limit", f"joint {joint.name!r} has no limit")
    if joint.axis_hint is not None and sum(float(v) ** 2 for v in joint.axis_hint) <= 0.0:
        report.error("zero_axis_hint", f"joint {joint.name!r} axis_hint must be non-zero")


def _check_leg_chain(
    leg: LegChainSpec,
    leg_names: set[str],
    report: KinematicCheckReport,
) -> None:
    if leg.mirror_of is not None and leg.mirror_of not in leg_names:
        report.error("missing_mirror_leg", f"leg {leg.name!r} mirrors missing leg {leg.mirror_of!r}")
    if not leg.joints:
        report.error("leg_without_joints", f"leg {leg.name!r} must contain at least one joint")
    _check_unique([joint.name for joint in leg.joints], f"{leg.name}_joint", report)
    for joint in leg.joints:
        _check_dh_joint(joint, report)


def _check_unique(names: list[str], label: str, report: KinematicCheckReport) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            report.error(f"duplicate_{label}", f"duplicate {label} name: {name!r}")
        seen.add(name)
