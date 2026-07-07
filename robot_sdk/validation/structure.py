"""Structure-first validation for robot CAD workflows."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from robot_sdk.structure.robot_structure_plan import (
    RobotStructurePlan,
    axes_are_parallel,
)
from robot_sdk.types import KinematicModel, RobotRequirement


StructureValidationSeverity = Literal["pass", "warning", "error"]


@dataclass(frozen=True)
class RobotStructureValidationIssue:
    """One structure/topology validation item."""

    code: str
    severity: StructureValidationSeverity
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RobotStructureValidationReport:
    """Validation report for structure-first robot design intent."""

    issues: list[RobotStructureValidationIssue]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[RobotStructureValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[RobotStructureValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def passes(self) -> list[RobotStructureValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "pass"]


def validate_robot_structure(
    *,
    requirement: RobotRequirement | None = None,
    kinematic_model: KinematicModel | None = None,
    structure_plan: RobotStructurePlan | None = None,
    production_cad: bool = True,
) -> RobotStructureValidationReport:
    """Validate structure intent before accepting production CAD output."""

    issues: list[RobotStructureValidationIssue] = []
    high_dof = _high_dof_serial_arm(requirement, kinematic_model)
    planar_template = (
        is_planar_high_dof_template(kinematic_model) if kinematic_model else False
    )

    if structure_plan is None:
        if production_cad and high_dof and planar_template:
            _add(
                issues,
                "six_axis_planar_template_rejected",
                "error",
                (
                    "High-DOF production CAD cannot be accepted from a planar "
                    "template DH chain where all alpha and d values are zero."
                ),
                {
                    "dof": _dof(requirement, kinematic_model),
                    "dh_rows": len(kinematic_model.dh_params) if kinematic_model else 0,
                },
            )
            _add(
                issues,
                "structure_plan_missing",
                "error",
                "High-DOF CAD requires a RobotStructurePlan before MechanicalLayout/CAD acceptance.",
            )
            return RobotStructureValidationReport(issues=issues)

        if high_dof:
            _add(
                issues,
                "structure_plan_missing",
                "warning",
                "High-DOF design has no RobotStructurePlan; CAD is relying on legacy layout semantics.",
            )
        else:
            _add(
                issues,
                "structure_plan_not_required",
                "pass",
                "Structure plan is optional for this low-DOF MVP path.",
            )
        return RobotStructureValidationReport(issues=issues)

    _validate_station_roles(issues, structure_plan)
    _validate_joint_axis_topology(issues, structure_plan)
    _validate_link_routes(issues, structure_plan)
    _validate_datums(issues, structure_plan)
    if planar_template and high_dof:
        _add(
            issues,
            "structure_plan_overrides_planar_template",
            "pass" if not any(issue.severity == "error" for issue in issues) else "warning",
            (
                "Planar high-DOF DH template was detected, but a non-planar "
                "RobotStructurePlan is present and must drive the downstream layout."
            ),
        )
    return RobotStructureValidationReport(issues=issues)


def is_planar_high_dof_template(
    kinematic_model: KinematicModel,
    *,
    min_dof: int = 5,
    tolerance: float = 1e-9,
) -> bool:
    """Return true for the old high-DOF planar placeholder DH chain."""

    if len(kinematic_model.dh_params) < min_dof:
        return False
    if kinematic_model.convention not in {"dh", "modified_dh"}:
        return False
    if not all(param.joint_type == "revolute" for param in kinematic_model.dh_params):
        return False
    return all(_near_zero(param.alpha, tolerance) for param in kinematic_model.dh_params) and all(
        _near_zero(param.d, tolerance) for param in kinematic_model.dh_params
    )


def _validate_station_roles(
    issues: list[RobotStructureValidationIssue],
    plan: RobotStructurePlan,
) -> None:
    roles = {station.role for station in plan.stations}
    required = {"base", "shoulder", "elbow", "wrist1", "wrist2", "wrist3", "tool"}
    missing = sorted(required - roles)
    if missing:
        _add(
            issues,
            "robot_family_structure_mismatch",
            "error",
            "6-axis cobot structure plan is missing required station roles.",
            {"missing_station_roles": missing, "station_roles": sorted(roles)},
        )
        return
    _add(
        issues,
        "structure_plan_station_roles_valid",
        "pass",
        "Structure plan contains base, shoulder, elbow, wrist, and tool stations.",
        {"station_roles": sorted(roles)},
    )


def _validate_joint_axis_topology(
    issues: list[RobotStructureValidationIssue],
    plan: RobotStructurePlan,
) -> None:
    if len(plan.joint_axes) < 6:
        _add(
            issues,
            "joint_axis_topology_invalid",
            "error",
            "6-axis structure plan must contain at least six joint axes.",
            {"joint_axis_count": len(plan.joint_axes)},
        )
        return

    all_parallel = True
    for index, axis in enumerate(plan.joint_axes):
        for other in plan.joint_axes[index + 1 :]:
            if not axes_are_parallel(axis.direction, other.direction):
                all_parallel = False
                break
        if not all_parallel:
            break

    if all_parallel:
        _add(
            issues,
            "joint_axis_topology_invalid",
            "error",
            "All joint axes are parallel; a 6-axis arm structure needs shoulder/elbow/wrist axis changes.",
            {"joint_axis_count": len(plan.joint_axes)},
        )
        return

    role_by_joint = {axis.joint_id: axis.role for axis in plan.joint_axes}
    expected_roles = {
        "base_yaw",
        "shoulder_pitch",
        "elbow_pitch",
        "wrist_roll",
        "wrist_pitch",
        "tool_roll",
    }
    missing_roles = sorted(expected_roles - set(role_by_joint.values()))
    if missing_roles:
        _add(
            issues,
            "joint_axis_roles_incomplete",
            "warning",
            "Joint axes are not fully role-labeled for a generic 6-axis cobot.",
            {"missing_roles": missing_roles, "roles": role_by_joint},
        )
    _add(
        issues,
        "axis_topology_valid",
        "pass",
        "Joint axes are not collapsed into one planar direction.",
        {"joint_axis_roles": role_by_joint},
    )


def _validate_link_routes(
    issues: list[RobotStructureValidationIssue],
    plan: RobotStructurePlan,
) -> None:
    route_types = {route.route_type for route in plan.link_routes}
    if len(plan.link_routes) < 6:
        _add(
            issues,
            "link_route_count_low",
            "warning",
            "Structure plan has fewer link routes than expected for a 6-axis chain.",
            {"link_route_count": len(plan.link_routes)},
        )
    if route_types <= {"straight"}:
        _add(
            issues,
            "link_routes_too_simple",
            "error",
            "All link routes are straight; wrist/offset structure is not represented.",
            {"route_types": sorted(route_types)},
        )
        return
    _add(
        issues,
        "link_routes_valid",
        "pass",
        "Structure plan contains non-straight link routing for base/wrist offsets.",
        {"route_types": sorted(route_types)},
    )


def _validate_datums(
    issues: list[RobotStructureValidationIssue],
    plan: RobotStructurePlan,
) -> None:
    semantics = {datum.semantic for datum in plan.datums}
    missing = sorted({"mount_plane", "joint_axis", "tool_plane"} - semantics)
    if missing:
        _add(
            issues,
            "structure_datums_incomplete",
            "warning",
            "Structure plan is missing datum semantics needed by CAD adapters.",
            {"missing_datum_semantics": missing},
        )
        return
    _add(
        issues,
        "structure_datums_valid",
        "pass",
        "Structure plan has mount, joint-axis, and tool datums.",
        {"datum_count": len(plan.datums)},
    )


def _high_dof_serial_arm(
    requirement: RobotRequirement | None,
    kinematic_model: KinematicModel | None,
) -> bool:
    return _dof(requirement, kinematic_model) >= 5


def _dof(
    requirement: RobotRequirement | None,
    kinematic_model: KinematicModel | None,
) -> int:
    if requirement is not None:
        return int(requirement.dof)
    if kinematic_model is not None:
        return len(kinematic_model.joints)
    return 0


def _near_zero(value: float, tolerance: float) -> bool:
    return math.isclose(float(value), 0.0, abs_tol=tolerance)


def _add(
    issues: list[RobotStructureValidationIssue],
    code: str,
    severity: StructureValidationSeverity,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    issues.append(
        RobotStructureValidationIssue(
            code=code,
            severity=severity,
            message=message,
            details=details or {},
        )
    )


__all__ = [
    "RobotStructureValidationIssue",
    "RobotStructureValidationReport",
    "is_planar_high_dof_template",
    "validate_robot_structure",
]
