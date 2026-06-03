"""Report-level validation for the Robot CAD MVP."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from robot_sdk.assembly.constraints import build_cadquery_assembly_constraint_plan
from robot_sdk.cad.cq_assembly import CadQueryAssemblyResult
from robot_sdk.cad.export import CadQueryExportResult
from robot_sdk.kinematics.dh import estimate_reach
from robot_sdk.layout.validation import validate_mechanical_layout
from robot_sdk.types import KinematicModel, MechanicalLayout, RobotRequirement


ValidationSeverity = Literal["pass", "warning", "error"]


@dataclass(frozen=True)
class RobotDesignValidationIssue:
    """One report-level validation item."""

    code: str
    severity: ValidationSeverity
    message: str
    category: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RobotDesignValidationReport:
    """Validation report consumed by agents and final design summaries."""

    issues: list[RobotDesignValidationIssue]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[RobotDesignValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[RobotDesignValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def passes(self) -> list[RobotDesignValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "pass"]

    def messages(self, severity: ValidationSeverity | None = None) -> list[str]:
        selected = self.issues
        if severity:
            selected = [issue for issue in selected if issue.severity == severity]
        return [
            f"{issue.severity.upper()} {issue.category}.{issue.code}: {issue.message}"
            for issue in selected
        ]


def validate_robot_design(
    *,
    requirement: RobotRequirement | None = None,
    kinematic_model: KinematicModel | None = None,
    layout: MechanicalLayout | None = None,
    cad_result: CadQueryAssemblyResult | None = None,
    export_result: CadQueryExportResult | None = None,
    export_path: str | Path | None = None,
) -> RobotDesignValidationReport:
    """Validate the current robot CAD workflow artifacts.

    Every input is optional so this can be used at intermediate stages. Missing
    artifacts are warnings unless they are needed to verify a requested metric.
    """

    issues: list[RobotDesignValidationIssue] = []
    _validate_dof(issues, requirement, kinematic_model, layout)
    _validate_reach(issues, requirement, kinematic_model)
    _validate_layout(issues, layout)
    _validate_constraints(issues, layout)
    _validate_cad_solve(issues, cad_result)
    _validate_export(issues, export_result, export_path)
    return RobotDesignValidationReport(issues=issues)


def _validate_dof(
    issues: list[RobotDesignValidationIssue],
    requirement: RobotRequirement | None,
    kinematic_model: KinematicModel | None,
    layout: MechanicalLayout | None,
) -> None:
    expected = requirement.dof if requirement else None
    kinematic_dof = len(kinematic_model.joints) if kinematic_model else None
    dh_dof = len(kinematic_model.dh_params) if kinematic_model else None
    layout_dof = len(layout.joints) if layout else None

    if expected is None:
        _add(
            issues,
            "missing_requirement",
            "warning",
            "dof",
            "No requirement was provided, so requested DOF cannot be checked.",
        )
        return

    mismatches: dict[str, int] = {}
    if kinematic_dof is not None and kinematic_dof != expected:
        mismatches["kinematic_joints"] = kinematic_dof
    if dh_dof is not None and dh_dof != expected:
        mismatches["dh_rows"] = dh_dof
    if layout_dof is not None and layout_dof != expected:
        mismatches["layout_joints"] = layout_dof

    if mismatches:
        _add(
            issues,
            "dof_mismatch",
            "error",
            "dof",
            f"Requested DOF is {expected}, but generated artifacts disagree.",
            {"requested_dof": expected, **mismatches},
        )
        return

    checked = {
        name: value
        for name, value in {
            "kinematic_joints": kinematic_dof,
            "dh_rows": dh_dof,
            "layout_joints": layout_dof,
        }.items()
        if value is not None
    }
    if not checked:
        _add(
            issues,
            "no_dof_artifact",
            "warning",
            "dof",
            "Requested DOF exists, but no kinematic or layout artifact was provided.",
            {"requested_dof": expected},
        )
        return

    _add(
        issues,
        "dof_matches",
        "pass",
        "dof",
        f"Requested DOF {expected} matches available generated artifacts.",
        {"requested_dof": expected, **checked},
    )


def _validate_reach(
    issues: list[RobotDesignValidationIssue],
    requirement: RobotRequirement | None,
    kinematic_model: KinematicModel | None,
) -> None:
    if requirement is None or requirement.reach is None:
        _add(
            issues,
            "missing_reach_requirement",
            "warning",
            "reach",
            "No requested reach was provided, so reach coverage cannot be checked.",
        )
        return
    if kinematic_model is None or not kinematic_model.dh_params:
        _add(
            issues,
            "missing_kinematic_model",
            "warning",
            "reach",
            "No DH kinematic model was provided, so reach coverage cannot be estimated.",
            {"required_reach_mm": _length_to_mm(requirement.reach, requirement.reach_unit)},
        )
        return

    required_mm = _length_to_mm(requirement.reach, requirement.reach_unit)
    estimated_mm = estimate_reach(kinematic_model.dh_params)
    if estimated_mm + 1e-9 < required_mm:
        _add(
            issues,
            "reach_shortfall",
            "error",
            "reach",
            f"Estimated reach {estimated_mm:g} mm is below required reach {required_mm:g} mm.",
            {"required_reach_mm": required_mm, "estimated_reach_mm": estimated_mm},
        )
        return

    _add(
        issues,
        "reach_covers_requirement",
        "pass",
        "reach",
        f"Estimated reach {estimated_mm:g} mm covers required reach {required_mm:g} mm.",
        {"required_reach_mm": required_mm, "estimated_reach_mm": estimated_mm},
    )


def _validate_layout(
    issues: list[RobotDesignValidationIssue],
    layout: MechanicalLayout | None,
) -> None:
    if layout is None:
        _add(
            issues,
            "missing_layout",
            "warning",
            "layout",
            "No MechanicalLayout was provided.",
        )
        return

    result = validate_mechanical_layout(layout)
    if result.ok:
        _add(
            issues,
            "layout_valid",
            "pass",
            "layout",
            "MechanicalLayout passed structural validation.",
        )
    for error in result.errors:
        _add(
            issues,
            error.code,
            "error",
            "layout",
            error.message,
            {"object_id": error.object_id} if error.object_id else {},
        )
    for warning in result.warnings:
        _add(
            issues,
            warning.code,
            "warning",
            "layout",
            warning.message,
            {"object_id": warning.object_id} if warning.object_id else {},
        )


def _validate_constraints(
    issues: list[RobotDesignValidationIssue],
    layout: MechanicalLayout | None,
) -> None:
    if layout is None:
        _add(
            issues,
            "missing_layout",
            "warning",
            "constraints",
            "No MechanicalLayout was provided, so constraints cannot be checked.",
        )
        return
    if not layout.assembly_constraints:
        _add(
            issues,
            "missing_constraints",
            "error",
            "constraints",
            "MechanicalLayout has no assembly constraints.",
        )
        return

    try:
        plan = build_cadquery_assembly_constraint_plan(layout)
    except Exception as exc:
        _add(
            issues,
            "constraint_plan_failed",
            "error",
            "constraints",
            f"Assembly constraint plan failed: {exc}",
        )
        return

    if len(plan.rules) != len(layout.assembly_constraints):
        _add(
            issues,
            "constraint_count_mismatch",
            "error",
            "constraints",
            "Constraint plan count does not match layout assembly constraint count.",
            {
                "layout_constraints": len(layout.assembly_constraints),
                "planned_constraints": len(plan.rules),
            },
        )
        return

    _add(
        issues,
        "constraints_resolved",
        "pass",
        "constraints",
        f"Resolved {len(plan.rules)} assembly constraints into CadQuery-ready rules.",
        {"constraint_count": len(plan.rules)},
    )


def _validate_cad_solve(
    issues: list[RobotDesignValidationIssue],
    cad_result: CadQueryAssemblyResult | None,
) -> None:
    if cad_result is None:
        _add(
            issues,
            "missing_cad_result",
            "warning",
            "cad_solve",
            "No CAD assembly result was provided.",
        )
        return
    if cad_result.solved:
        _add(
            issues,
            "cad_solve_passed",
            "pass",
            "cad_solve",
            f"CadQuery Assembly.solve() succeeded with {cad_result.part_count} parts and {cad_result.constraint_count} constraints.",
            {
                "part_count": cad_result.part_count,
                "constraint_count": cad_result.constraint_count,
            },
        )
        return

    severity: ValidationSeverity = "error" if cad_result.solve_error else "warning"
    message = cad_result.solve_error or "CAD assembly was built but solve was not requested."
    _add(
        issues,
        "cad_solve_failed" if cad_result.solve_error else "cad_solve_not_run",
        severity,
        "cad_solve",
        message,
        {
            "part_count": cad_result.part_count,
            "constraint_count": cad_result.constraint_count,
        },
    )


def _validate_export(
    issues: list[RobotDesignValidationIssue],
    export_result: CadQueryExportResult | None,
    export_path: str | Path | None,
) -> None:
    if export_result is not None:
        if export_result.exists and (export_result.size_bytes or 0) > 0:
            _add(
                issues,
                "export_exists",
                "pass",
                "export",
                f"{export_result.export_type} export exists and is non-empty.",
                {
                    "path": str(export_result.path),
                    "size_bytes": export_result.size_bytes,
                    "export_type": export_result.export_type,
                },
            )
            return
        _add(
            issues,
            "export_missing_or_empty",
            "error",
            "export",
            f"{export_result.export_type} export is missing or empty.",
            {
                "path": str(export_result.path),
                "size_bytes": export_result.size_bytes,
                "export_type": export_result.export_type,
            },
        )
        return

    if export_path is None:
        _add(
            issues,
            "missing_export",
            "warning",
            "export",
            "No export result or export path was provided.",
        )
        return

    path = Path(export_path)
    if path.exists() and path.stat().st_size > 0:
        _add(
            issues,
            "export_path_exists",
            "pass",
            "export",
            "Export path exists and is non-empty.",
            {"path": str(path), "size_bytes": path.stat().st_size},
        )
        return
    _add(
        issues,
        "export_path_missing_or_empty",
        "error",
        "export",
        "Export path is missing or empty.",
        {"path": str(path)},
    )


def _length_to_mm(value: float, unit: str) -> float:
    if unit == "mm":
        return value
    if unit == "m":
        return value * 1000.0
    raise ValueError(f"Unsupported length unit: {unit}")


def _add(
    issues: list[RobotDesignValidationIssue],
    code: str,
    severity: ValidationSeverity,
    category: str,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    issues.append(
        RobotDesignValidationIssue(
            code=code,
            severity=severity,
            category=category,
            message=message,
            details=details or {},
        )
    )
