"""Report-level validation for the Robot CAD MVP."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from robot_sdk.assembly.constraints import build_cadquery_assembly_constraint_plan
from robot_sdk.assembly.local_promotion import (
    build_local_subassembly_promotion_report,
)
from robot_sdk.cad.cq_assembly import CadQueryAssemblyResult
from robot_sdk.cad.export import CadQueryExportResult, CadQueryStepPackageExportResult
from robot_sdk.kinematics.dh import estimate_reach
from robot_sdk.layout.validation import validate_mechanical_layout
from robot_sdk.structure.robot_structure_plan import RobotStructurePlan
from robot_sdk.types import KinematicModel, MechanicalLayout, RobotRequirement
from robot_sdk.validation.assembly import validate_assembly_semantics
from robot_sdk.validation.source_joint import validate_source_joint_assembly
from robot_sdk.validation.structure import validate_robot_structure
from robot_sdk.validation.visual import validate_visual_geometry


ValidationSeverity = Literal["pass", "warning", "error"]

LOCAL_SUBASSEMBLY_POSE_DELTA_WARNING_MM = 0.1
LOCAL_SUBASSEMBLY_POSE_DELTA_ERROR_MM = 1.0
LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_WARNING_MM = 0.1
LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_ERROR_MM = 1.0
LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG = 0.5
LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_ERROR_DEG = 2.0
FULL_ASSEMBLY_FIXTURE_POSE_DELTA_WARNING_MM = 0.1
FULL_ASSEMBLY_FIXTURE_POSE_DELTA_ERROR_MM = 1.0
FULL_ASSEMBLY_FIXTURE_ORIGIN_RESIDUAL_WARNING_MM = 0.1
FULL_ASSEMBLY_FIXTURE_ORIGIN_RESIDUAL_ERROR_MM = 1.0
FULL_ASSEMBLY_FIXTURE_ANGLE_RESIDUAL_WARNING_DEG = 0.5
FULL_ASSEMBLY_FIXTURE_ANGLE_RESIDUAL_ERROR_DEG = 2.0
PRODUCTION_FULL_SEMANTIC_POSE_DELTA_WARNING_MM = 0.1
PRODUCTION_FULL_SEMANTIC_POSE_DELTA_ERROR_MM = 1.0
PRODUCTION_FULL_SEMANTIC_ORIGIN_RESIDUAL_WARNING_MM = 0.1
PRODUCTION_FULL_SEMANTIC_ORIGIN_RESIDUAL_ERROR_MM = 1.0
PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_WARNING_DEG = 0.5
PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_ERROR_DEG = 2.0
STAGE7_MIN_DOF = 5
STAGE7_REQUIRED_PASS_CODES = {
    "dof_matches",
    "reach_covers_requirement",
    "axis_topology_valid",
    "link_routes_valid",
    "layout_valid",
    "constraints_resolved",
    "cad_solve_passed",
    "local_subassembly_ready_for_promotion",
    "full_assembly_fixture_ready_for_promotion",
    "export_exists",
    "export_bbox_valid",
    "step_package_bboxes_consistent",
    "structure_visual_cues_screened",
    "visual_snapshot_generated",
}


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
    structure_plan: RobotStructurePlan | None = None,
    layout: MechanicalLayout | None = None,
    cad_result: CadQueryAssemblyResult | None = None,
    export_result: CadQueryExportResult | None = None,
    step_package_result: CadQueryStepPackageExportResult | None = None,
    snapshot_result: object | None = None,
    export_path: str | Path | None = None,
) -> RobotDesignValidationReport:
    """Validate the current robot CAD workflow artifacts.

    Every input is optional so this can be used at intermediate stages. Missing
    artifacts are warnings unless they are needed to verify a requested metric.
    """

    issues: list[RobotDesignValidationIssue] = []
    _validate_dof(issues, requirement, kinematic_model, layout)
    _validate_reach(issues, requirement, kinematic_model)
    _validate_structure(issues, requirement, kinematic_model, structure_plan)
    _validate_layout(issues, layout)
    _validate_constraints(issues, layout, cad_result)
    _validate_cad_solve(issues, cad_result)
    _validate_export(issues, export_result, export_path)
    _validate_step_package_geometry(issues, step_package_result)
    _validate_source_joint_gate(issues, cad_result, step_package_result)
    _validate_visual_geometry(
        issues,
        layout,
        kinematic_model,
        structure_plan,
        step_package_result,
        snapshot_result,
    )
    _validate_stage7_completion(
        issues,
        requirement,
        kinematic_model,
        structure_plan,
        layout,
        cad_result,
    )
    return RobotDesignValidationReport(issues=issues)


def _validate_structure(
    issues: list[RobotDesignValidationIssue],
    requirement: RobotRequirement | None,
    kinematic_model: KinematicModel | None,
    structure_plan: RobotStructurePlan | None,
) -> None:
    report = validate_robot_structure(
        requirement=requirement,
        kinematic_model=kinematic_model,
        structure_plan=structure_plan,
        production_cad=True,
    )
    for issue in report.issues:
        _add(
            issues,
            issue.code,
            issue.severity,
            "structure",
            issue.message,
            issue.details,
        )


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
    cad_result: CadQueryAssemblyResult | None = None,
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
    semantic_application = "none"
    if cad_result is not None:
        semantic_application = str(
            cad_result.metadata.get("semantic_constraint_application") or "none"
        )
    assembly_report = validate_assembly_semantics(
        layout,
        semantic_constraint_application=semantic_application,  # type: ignore[arg-type]
    )
    for issue in assembly_report.issues:
        _add(
            issues,
            issue.code,
            issue.severity,
            "assembly",
            issue.message,
            issue.details,
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
        assembly_backend = str(cad_result.metadata.get("assembly_backend") or "cadquery")
        solve_label = (
            "build123d source-joint assembly"
            if assembly_backend == "build123d"
            else "CadQuery Assembly.solve()"
        )
        _add(
            issues,
            "cad_solve_passed",
            "pass",
            "cad_solve",
            f"{solve_label} succeeded with {cad_result.part_count} parts and {cad_result.constraint_count} constraints.",
            {
                "assembly_backend": assembly_backend,
                "part_count": cad_result.part_count,
                "constraint_count": cad_result.constraint_count,
            },
        )
        semantic_solver_application = _semantic_solver_application(cad_result)
        if semantic_solver_application == "source_joint":
            _add(
                issues,
                "source_joint_constraints_applied",
                "pass",
                "cad_solve",
                "Production assembly used source-level build123d joints.",
                {
                    "semantic_constraints_applied_to_solver": semantic_solver_application,
                    "solver_constraint_mode": cad_result.metadata.get("solver_constraint_mode"),
                    "semantic_constraint_count": cad_result.metadata.get(
                        "semantic_constraint_count"
                    ),
                },
            )
            return
        if semantic_solver_application != "full_assembly":
            _add(
                issues,
                "semantic_constraints_not_applied",
                "warning",
                "cad_solve",
                (
                    "Production whole-machine CadQuery solve used fixed layout "
                    "pose constraints; MechanicalLayout semantic mate constraints "
                    "are applied only to local subassembly or fixture solvers."
                ),
                {
                    "semantic_constraints_applied_to_solver": semantic_solver_application,
                    "solver_constraint_mode": cad_result.metadata.get("solver_constraint_mode"),
                    "semantic_constraint_count": cad_result.metadata.get(
                        "semantic_constraint_count"
                    ),
                },
            )
        _validate_local_subassembly_solve(issues, cad_result)
        _validate_full_assembly_fixture(issues, cad_result)
        _validate_production_full_semantic_solve(issues, cad_result)
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
    _validate_production_full_semantic_solve(issues, cad_result)


def _validate_source_joint_gate(
    issues: list[RobotDesignValidationIssue],
    cad_result: CadQueryAssemblyResult | None,
    step_package_result: CadQueryStepPackageExportResult | None,
) -> None:
    report = validate_source_joint_assembly(
        cad_result=cad_result,
        step_package_result=step_package_result,
    )
    for issue in report.issues:
        _add(
            issues,
            issue.code,
            issue.severity,
            "source_joint",
            issue.message,
            issue.details,
        )


def _validate_production_full_semantic_solve(
    issues: list[RobotDesignValidationIssue],
    cad_result: CadQueryAssemblyResult,
) -> None:
    requested = bool(cad_result.metadata.get("production_full_semantic_solve_requested"))
    used = bool(cad_result.metadata.get("production_full_semantic_solve_used"))
    details = {
        "requested_production_assembly_source": cad_result.metadata.get(
            "requested_production_assembly_source"
        ),
        "production_assembly_source": cad_result.metadata.get(
            "production_assembly_source"
        ),
        "production_full_semantic_solve_requested": requested,
        "production_full_semantic_solve_used": used,
        "production_full_semantic_solve_error": cad_result.metadata.get(
            "production_full_semantic_solve_error"
        ),
        "production_full_semantic_solve_gate_status": cad_result.metadata.get(
            "production_full_semantic_solve_gate_status"
        ),
        "production_full_semantic_solve_pose_delta_report": cad_result.metadata.get(
            "production_full_semantic_solve_pose_delta_report"
        ),
        "production_full_semantic_solve_residual_reports": cad_result.metadata.get(
            "production_full_semantic_solve_residual_reports"
        ),
        "production_full_semantic_solve_component_cluster_report": cad_result.metadata.get(
            "production_full_semantic_solve_component_cluster_report"
        ),
        "production_full_semantic_solve_assembly_plan": cad_result.metadata.get(
            "production_full_semantic_solve_assembly_plan"
        ),
        "production_full_semantic_solve_assembly_compiler": cad_result.metadata.get(
            "production_full_semantic_solve_assembly_compiler"
        ),
        "semantic_constraints_applied_to_solver": _semantic_solver_application(
            cad_result
        ),
        **_production_full_semantic_delta_details(cad_result),
    }
    if used:
        _add(
            issues,
            "production_full_semantic_solve_used",
            "pass",
            "cad_solve",
            "Production whole-machine assembly used full semantic mate constraints.",
            details,
        )
        _validate_production_full_semantic_thresholds(issues, details)
        return
    if requested:
        _add(
            issues,
            "production_full_semantic_solve_failed",
            "error",
            "cad_solve",
            "Production full semantic solve was requested but was not used.",
            details,
        )


def _production_full_semantic_delta_details(
    cad_result: CadQueryAssemblyResult,
) -> dict[str, object]:
    max_pose_delta = 0.0
    max_origin_residual = 0.0
    max_normal_residual = 0.0
    max_tangent_residual = 0.0

    pose_report = cad_result.metadata.get(
        "production_full_semantic_solve_pose_delta_report"
    )
    if isinstance(pose_report, dict):
        value = pose_report.get("max_translation_delta_mm")
        if isinstance(value, (int, float)):
            max_pose_delta = max(max_pose_delta, float(value))

    residual_reports = cad_result.metadata.get(
        "production_full_semantic_solve_residual_reports"
    )
    if isinstance(residual_reports, list):
        for residual in residual_reports:
            if not isinstance(residual, dict):
                continue
            value = residual.get("origin_delta_mm")
            if isinstance(value, (int, float)):
                max_origin_residual = max(max_origin_residual, float(value))
            value = residual.get("normal_angle_deg")
            if isinstance(value, (int, float)):
                max_normal_residual = max(max_normal_residual, float(value))
            value = residual.get("tangent_angle_deg")
            if isinstance(value, (int, float)):
                max_tangent_residual = max(max_tangent_residual, float(value))

    return {
        "max_production_full_semantic_pose_delta_mm": max_pose_delta,
        "max_production_full_semantic_origin_residual_mm": max_origin_residual,
        "max_production_full_semantic_normal_residual_deg": max_normal_residual,
        "max_production_full_semantic_tangent_residual_deg": max_tangent_residual,
    }


def _validate_production_full_semantic_thresholds(
    issues: list[RobotDesignValidationIssue],
    details: dict[str, object],
) -> None:
    details = {
        **details,
        **_production_full_semantic_delta_details_from_details(details),
    }
    pose_delta_mm = _float_detail(
        details,
        "max_production_full_semantic_pose_delta_mm",
    )
    origin_residual_mm = _float_detail(
        details,
        "max_production_full_semantic_origin_residual_mm",
    )
    normal_residual_deg = _float_detail(
        details,
        "max_production_full_semantic_normal_residual_deg",
    )
    tangent_residual_deg = _float_detail(
        details,
        "max_production_full_semantic_tangent_residual_deg",
    )
    threshold_details = {
        **details,
        "pose_delta_warning_mm": PRODUCTION_FULL_SEMANTIC_POSE_DELTA_WARNING_MM,
        "pose_delta_error_mm": PRODUCTION_FULL_SEMANTIC_POSE_DELTA_ERROR_MM,
        "origin_residual_warning_mm": PRODUCTION_FULL_SEMANTIC_ORIGIN_RESIDUAL_WARNING_MM,
        "origin_residual_error_mm": PRODUCTION_FULL_SEMANTIC_ORIGIN_RESIDUAL_ERROR_MM,
        "angle_residual_warning_deg": PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_WARNING_DEG,
        "angle_residual_error_deg": PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_ERROR_DEG,
        "contradictory_residual_candidates": _production_full_semantic_residual_exceeders(
            details
        ),
    }
    exceeds_error = (
        pose_delta_mm > PRODUCTION_FULL_SEMANTIC_POSE_DELTA_ERROR_MM
        or origin_residual_mm > PRODUCTION_FULL_SEMANTIC_ORIGIN_RESIDUAL_ERROR_MM
        or normal_residual_deg > PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_ERROR_DEG
        or tangent_residual_deg > PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_ERROR_DEG
    )
    compiler_error = _production_full_semantic_compiler_error(details)
    if compiler_error:
        _add(
            issues,
            "production_full_semantic_assembly_compiler_failed",
            "error",
            "cad_solve",
            (
                "Production full semantic assembly compiler reported "
                "conflicting or unresolved semantic placement."
            ),
            {**threshold_details, **compiler_error},
        )
        return
    cluster_error = _production_full_semantic_cluster_error(details)
    if cluster_error:
        _add(
            issues,
            "production_full_semantic_assembly_geometry_failed",
            "error",
            "cad_solve",
            (
                "Production full semantic assembly produced disconnected or "
                "geometrically invalid mate clusters."
            ),
            {**threshold_details, **cluster_error},
        )
        return
    if exceeds_error:
        _add(
            issues,
            "production_full_semantic_solve_residual_exceeded",
            "error",
            "cad_solve",
            (
                "Production full semantic solve residual or pose delta exceeded "
                "the error threshold."
            ),
            threshold_details,
        )
        return

    exceeds_warning = (
        pose_delta_mm > PRODUCTION_FULL_SEMANTIC_POSE_DELTA_WARNING_MM
        or origin_residual_mm > PRODUCTION_FULL_SEMANTIC_ORIGIN_RESIDUAL_WARNING_MM
        or normal_residual_deg > PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_WARNING_DEG
        or tangent_residual_deg > PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_WARNING_DEG
    )
    if exceeds_warning:
        _add(
            issues,
            "production_full_semantic_solve_residual_warning",
            "warning",
            "cad_solve",
            (
                "Production full semantic solve residual or pose delta exceeded "
                "the warning threshold."
            ),
            threshold_details,
        )
        return

    _add(
        issues,
        "production_full_semantic_solve_ready",
        "pass",
        "cad_solve",
        "Production full semantic solve residuals and pose deltas are within thresholds.",
        threshold_details,
    )


def _production_full_semantic_compiler_error(
    details: dict[str, object],
) -> dict[str, object]:
    compiler = details.get("production_full_semantic_solve_assembly_compiler")
    if not isinstance(compiler, dict) or not compiler:
        return {}
    conflicts = compiler.get("conflicts")
    unresolved = compiler.get("unresolved_part_ids")
    ok = compiler.get("ok")
    conflict_problem = isinstance(conflicts, list) and bool(conflicts)
    unresolved_problem = isinstance(unresolved, list) and bool(unresolved)
    ok_problem = ok is False
    if not (conflict_problem or unresolved_problem or ok_problem):
        return {}
    return {
        "assembly_compiler_failure": {
            "ok": ok,
            "conflicts": conflicts if isinstance(conflicts, list) else [],
            "unresolved_part_ids": unresolved if isinstance(unresolved, list) else [],
            "metadata": compiler.get("metadata") if isinstance(compiler.get("metadata"), dict) else {},
        }
    }


def _production_full_semantic_cluster_error(
    details: dict[str, object],
) -> dict[str, object]:
    report = details.get("production_full_semantic_solve_component_cluster_report")
    if not isinstance(report, dict) or not report:
        return {}
    cluster_count = report.get("cluster_count")
    connected = report.get("base_to_terminal_connected")
    failing_edges = report.get("failing_edges")
    cluster_problem = isinstance(cluster_count, int) and cluster_count > 1
    chain_problem = connected is False
    edge_problem = isinstance(failing_edges, list) and bool(failing_edges)
    if not (cluster_problem or chain_problem or edge_problem):
        return {}
    return {
        "assembly_geometry_failure": {
            "cluster_count": cluster_count,
            "base_to_terminal_connected": connected,
            "failing_edges": failing_edges if isinstance(failing_edges, list) else [],
        }
    }


def _production_full_semantic_delta_details_from_details(
    details: dict[str, object],
) -> dict[str, object]:
    max_pose_delta = 0.0
    max_origin_residual = 0.0
    max_normal_residual = 0.0
    max_tangent_residual = 0.0

    pose_report = details.get("production_full_semantic_solve_pose_delta_report")
    if isinstance(pose_report, dict):
        value = pose_report.get("max_translation_delta_mm")
        if isinstance(value, (int, float)):
            max_pose_delta = max(max_pose_delta, float(value))

    residual_reports = details.get("production_full_semantic_solve_residual_reports")
    if isinstance(residual_reports, list):
        for residual in residual_reports:
            if not isinstance(residual, dict):
                continue
            value = residual.get("origin_delta_mm")
            if isinstance(value, (int, float)):
                max_origin_residual = max(max_origin_residual, float(value))
            value = residual.get("normal_angle_deg")
            if isinstance(value, (int, float)):
                max_normal_residual = max(max_normal_residual, float(value))
            value = residual.get("tangent_angle_deg")
            if isinstance(value, (int, float)):
                max_tangent_residual = max(max_tangent_residual, float(value))

    return {
        "max_production_full_semantic_pose_delta_mm": max_pose_delta,
        "max_production_full_semantic_origin_residual_mm": max_origin_residual,
        "max_production_full_semantic_normal_residual_deg": max_normal_residual,
        "max_production_full_semantic_tangent_residual_deg": max_tangent_residual,
    }


def _production_full_semantic_residual_exceeders(
    details: dict[str, object],
) -> list[dict[str, object]]:
    residual_reports = details.get("production_full_semantic_solve_residual_reports")
    if not isinstance(residual_reports, list):
        return []
    exceeders: list[dict[str, object]] = []
    for residual in residual_reports:
        if not isinstance(residual, dict):
            continue
        origin = residual.get("origin_delta_mm")
        normal = residual.get("normal_angle_deg")
        tangent = residual.get("tangent_angle_deg")
        exceeds = (
            isinstance(origin, (int, float))
            and float(origin) > PRODUCTION_FULL_SEMANTIC_ORIGIN_RESIDUAL_ERROR_MM
        ) or (
            isinstance(normal, (int, float))
            and float(normal) > PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_ERROR_DEG
        ) or (
            isinstance(tangent, (int, float))
            and float(tangent) > PRODUCTION_FULL_SEMANTIC_ANGLE_RESIDUAL_ERROR_DEG
        )
        if exceeds:
            exceeders.append(dict(residual))
    return exceeders


def _validate_local_subassembly_solve(
    issues: list[RobotDesignValidationIssue],
    cad_result: CadQueryAssemblyResult,
) -> None:
    count = int(cad_result.metadata.get("local_subassembly_count") or 0)
    if count <= 0:
        _add(
            issues,
            "local_subassembly_solve_not_run",
            "warning",
            "cad_solve",
            "No local subassembly solve results were recorded.",
        )
        return

    solved = int(cad_result.metadata.get("local_subassembly_solved_count") or 0)
    failed = int(cad_result.metadata.get("local_subassembly_failed_count") or 0)
    details = {
        "local_subassembly_count": count,
        "local_subassembly_solved_count": solved,
        "local_subassembly_failed_count": failed,
        "local_subassembly_names": list(
            cad_result.metadata.get("local_subassembly_names") or []
        ),
        **_local_subassembly_delta_details(cad_result),
    }
    if failed:
        _add(
            issues,
            "local_subassembly_solve_failed",
            "warning",
            "cad_solve",
            f"{failed} of {count} local subassembly solves failed.",
            details,
        )
        return
    _add(
        issues,
        "local_subassembly_solve_passed",
        "pass",
        "cad_solve",
        f"{solved} local subassemblies solved with semantic constraints.",
        details,
    )
    _validate_local_subassembly_thresholds(issues, details)
    _validate_local_subassembly_promotion(issues, cad_result)


def _validate_full_assembly_fixture(
    issues: list[RobotDesignValidationIssue],
    cad_result: CadQueryAssemblyResult,
) -> None:
    requested = bool(cad_result.metadata.get("full_assembly_fixture_requested"))
    if not requested:
        _add(
            issues,
            "full_assembly_fixture_not_requested",
            "warning",
            "cad_solve",
            "Experimental full-assembly semantic solve fixture was not requested.",
        )
        return

    details = {
        "gate_status": cad_result.metadata.get("full_assembly_fixture_gate_status"),
        "ran": cad_result.metadata.get("full_assembly_fixture_ran"),
        "solved": cad_result.metadata.get("full_assembly_fixture_solved"),
        "constraint_count": cad_result.metadata.get(
            "full_assembly_fixture_constraint_count"
        ),
        "semantic_constraint_count": cad_result.metadata.get(
            "full_assembly_fixture_semantic_constraint_count"
        ),
        "solve_error": cad_result.metadata.get("full_assembly_fixture_solve_error"),
        "pose_delta_report": cad_result.metadata.get(
            "full_assembly_fixture_pose_delta_report"
        ),
        **_full_assembly_fixture_delta_details(cad_result),
    }
    fixture = cad_result.metadata.get("full_assembly_fixture")
    if isinstance(fixture, dict):
        details["skipped_reason"] = fixture.get("skipped_reason")
        details["gate"] = fixture.get("gate")

    if not bool(cad_result.metadata.get("full_assembly_fixture_ran")):
        _add(
            issues,
            "full_assembly_fixture_blocked",
            "warning",
            "cad_solve",
            "Full-assembly semantic solve fixture was skipped by its precondition gate.",
            details,
        )
        return
    if bool(cad_result.metadata.get("full_assembly_fixture_solved")):
        _add(
            issues,
            "full_assembly_fixture_solve_passed",
            "pass",
            "cad_solve",
            "Experimental full-assembly semantic solve fixture solved successfully.",
            details,
        )
        _validate_full_assembly_fixture_thresholds(issues, details)
        return
    _add(
        issues,
        "full_assembly_fixture_solve_failed",
        "warning",
        "cad_solve",
        "Experimental full-assembly semantic solve fixture failed; production STEP still uses fixed layout pose.",
        details,
    )


def _full_assembly_fixture_delta_details(
    cad_result: CadQueryAssemblyResult,
) -> dict[str, object]:
    max_pose_delta = 0.0
    max_origin_residual = 0.0
    max_normal_residual = 0.0
    max_tangent_residual = 0.0

    pose_report = cad_result.metadata.get("full_assembly_fixture_pose_delta_report")
    if isinstance(pose_report, dict):
        value = pose_report.get("max_translation_delta_mm")
        if isinstance(value, (int, float)):
            max_pose_delta = max(max_pose_delta, float(value))

    residual_reports = cad_result.metadata.get("full_assembly_fixture_residual_reports")
    if not isinstance(residual_reports, list):
        fixture = cad_result.metadata.get("full_assembly_fixture")
        residual_reports = (
            fixture.get("residual_reports")
            if isinstance(fixture, dict)
            else []
        )
    if isinstance(residual_reports, list):
        for residual in residual_reports:
            if not isinstance(residual, dict):
                continue
            value = residual.get("origin_delta_mm")
            if isinstance(value, (int, float)):
                max_origin_residual = max(max_origin_residual, float(value))
            value = residual.get("normal_angle_deg")
            if isinstance(value, (int, float)):
                max_normal_residual = max(max_normal_residual, float(value))
            value = residual.get("tangent_angle_deg")
            if isinstance(value, (int, float)):
                max_tangent_residual = max(max_tangent_residual, float(value))

    return {
        "max_full_fixture_pose_delta_mm": max_pose_delta,
        "max_full_fixture_origin_residual_mm": max_origin_residual,
        "max_full_fixture_normal_residual_deg": max_normal_residual,
        "max_full_fixture_tangent_residual_deg": max_tangent_residual,
    }


def _validate_full_assembly_fixture_thresholds(
    issues: list[RobotDesignValidationIssue],
    details: dict[str, object],
) -> None:
    pose_delta_mm = _float_detail(details, "max_full_fixture_pose_delta_mm")
    origin_residual_mm = _float_detail(details, "max_full_fixture_origin_residual_mm")
    normal_residual_deg = _float_detail(details, "max_full_fixture_normal_residual_deg")
    tangent_residual_deg = _float_detail(details, "max_full_fixture_tangent_residual_deg")
    threshold_details = {
        **details,
        "pose_delta_warning_mm": FULL_ASSEMBLY_FIXTURE_POSE_DELTA_WARNING_MM,
        "pose_delta_error_mm": FULL_ASSEMBLY_FIXTURE_POSE_DELTA_ERROR_MM,
        "origin_residual_warning_mm": FULL_ASSEMBLY_FIXTURE_ORIGIN_RESIDUAL_WARNING_MM,
        "origin_residual_error_mm": FULL_ASSEMBLY_FIXTURE_ORIGIN_RESIDUAL_ERROR_MM,
        "angle_residual_warning_deg": FULL_ASSEMBLY_FIXTURE_ANGLE_RESIDUAL_WARNING_DEG,
        "angle_residual_error_deg": FULL_ASSEMBLY_FIXTURE_ANGLE_RESIDUAL_ERROR_DEG,
    }

    exceeds_error = (
        pose_delta_mm > FULL_ASSEMBLY_FIXTURE_POSE_DELTA_ERROR_MM
        or origin_residual_mm > FULL_ASSEMBLY_FIXTURE_ORIGIN_RESIDUAL_ERROR_MM
        or normal_residual_deg > FULL_ASSEMBLY_FIXTURE_ANGLE_RESIDUAL_ERROR_DEG
        or tangent_residual_deg > FULL_ASSEMBLY_FIXTURE_ANGLE_RESIDUAL_ERROR_DEG
    )
    if exceeds_error:
        _add(
            issues,
            "full_assembly_fixture_threshold_exceeded",
            "warning",
            "cad_solve",
            "Full-assembly fixture residual or pose delta exceeded the promotion error threshold.",
            threshold_details,
        )
        return

    exceeds_warning = (
        pose_delta_mm > FULL_ASSEMBLY_FIXTURE_POSE_DELTA_WARNING_MM
        or origin_residual_mm > FULL_ASSEMBLY_FIXTURE_ORIGIN_RESIDUAL_WARNING_MM
        or normal_residual_deg > FULL_ASSEMBLY_FIXTURE_ANGLE_RESIDUAL_WARNING_DEG
        or tangent_residual_deg > FULL_ASSEMBLY_FIXTURE_ANGLE_RESIDUAL_WARNING_DEG
    )
    if exceeds_warning:
        _add(
            issues,
            "full_assembly_fixture_threshold_warning",
            "warning",
            "cad_solve",
            "Full-assembly fixture residual or pose delta exceeded the warning threshold.",
            threshold_details,
        )
        return

    _add(
        issues,
        "full_assembly_fixture_ready_for_promotion",
        "pass",
        "cad_solve",
        "Full-assembly fixture metrics are within thresholds; production full solve is still disabled.",
        threshold_details,
    )


def _local_subassembly_delta_details(
    cad_result: CadQueryAssemblyResult,
) -> dict[str, object]:
    max_pose_delta = 0.0
    max_origin_residual = 0.0
    max_normal_residual = 0.0
    max_tangent_residual = 0.0
    for subassembly in cad_result.metadata.get("local_subassemblies") or []:
        if not isinstance(subassembly, dict):
            continue
        pose_report = subassembly.get("pose_delta_report")
        if isinstance(pose_report, dict):
            value = pose_report.get("max_translation_delta_mm")
            if isinstance(value, (int, float)):
                max_pose_delta = max(max_pose_delta, float(value))
        residual_reports = subassembly.get("residual_reports")
        if isinstance(residual_reports, list):
            for residual in residual_reports:
                if not isinstance(residual, dict):
                    continue
                value = residual.get("origin_delta_mm")
                if isinstance(value, (int, float)):
                    max_origin_residual = max(max_origin_residual, float(value))
                value = residual.get("normal_angle_deg")
                if isinstance(value, (int, float)):
                    max_normal_residual = max(max_normal_residual, float(value))
                value = residual.get("tangent_angle_deg")
                if isinstance(value, (int, float)):
                    max_tangent_residual = max(max_tangent_residual, float(value))
    return {
        "max_local_pose_delta_mm": max_pose_delta,
        "max_local_origin_residual_mm": max_origin_residual,
        "max_local_normal_residual_deg": max_normal_residual,
        "max_local_tangent_residual_deg": max_tangent_residual,
    }


def _validate_local_subassembly_promotion(
    issues: list[RobotDesignValidationIssue],
    cad_result: CadQueryAssemblyResult,
) -> None:
    promotion = build_local_subassembly_promotion_report(
        [
            subassembly
            for subassembly in cad_result.metadata.get("local_subassemblies") or []
            if isinstance(subassembly, dict)
        ]
    )
    ready = promotion["ready_local_subassemblies"]
    if not ready:
        return

    _add(
        issues,
        "local_subassembly_ready_for_promotion",
        "pass",
        "cad_solve",
        f"{len(ready)} local subassemblies are within promotion thresholds.",
        promotion,
    )

    joint_link_joint_ready = [
        item
        for item in ready
        if isinstance(item, dict) and item.get("role") == "joint_link_joint"
    ]
    if joint_link_joint_ready:
        _add(
            issues,
            "joint_link_joint_promotion_ready",
            "pass",
            "cad_solve",
            (
                f"{len(joint_link_joint_ready)} joint-link-joint local "
                "subassemblies are within promotion thresholds."
            ),
            {**promotion, "joint_link_joint_ready_local_subassemblies": joint_link_joint_ready},
        )


def _semantic_solver_application(cad_result: CadQueryAssemblyResult) -> str:
    value = cad_result.metadata.get("semantic_constraints_applied_to_solver")
    if isinstance(value, str) and value:
        return value
    if value is True:
        return "full_assembly"
    if value is False:
        return "none"
    value = cad_result.metadata.get("semantic_constraint_application")
    if isinstance(value, str) and value:
        return value
    return "none"


def _validate_local_subassembly_thresholds(
    issues: list[RobotDesignValidationIssue],
    details: dict[str, object],
) -> None:
    pose_delta_mm = _float_detail(details, "max_local_pose_delta_mm")
    origin_residual_mm = _float_detail(details, "max_local_origin_residual_mm")
    normal_residual_deg = _float_detail(details, "max_local_normal_residual_deg")
    tangent_residual_deg = _float_detail(details, "max_local_tangent_residual_deg")
    threshold_details = {
        **details,
        "pose_delta_warning_mm": LOCAL_SUBASSEMBLY_POSE_DELTA_WARNING_MM,
        "pose_delta_error_mm": LOCAL_SUBASSEMBLY_POSE_DELTA_ERROR_MM,
        "origin_residual_warning_mm": LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_WARNING_MM,
        "origin_residual_error_mm": LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_ERROR_MM,
        "angle_residual_warning_deg": LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG,
        "angle_residual_error_deg": LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_ERROR_DEG,
    }

    exceeds_error = (
        pose_delta_mm > LOCAL_SUBASSEMBLY_POSE_DELTA_ERROR_MM
        or origin_residual_mm > LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_ERROR_MM
        or normal_residual_deg > LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_ERROR_DEG
        or tangent_residual_deg > LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_ERROR_DEG
    )
    if exceeds_error:
        _add(
            issues,
            "local_subassembly_residual_threshold_exceeded",
            "error",
            "cad_solve",
            (
                "Local subassembly solve residual or pose delta exceeded the error threshold."
            ),
            threshold_details,
        )
        return

    exceeds_warning = (
        pose_delta_mm > LOCAL_SUBASSEMBLY_POSE_DELTA_WARNING_MM
        or origin_residual_mm > LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_WARNING_MM
        or normal_residual_deg > LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG
        or tangent_residual_deg > LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG
    )
    if exceeds_warning:
        _add(
            issues,
            "local_subassembly_residual_threshold_warning",
            "warning",
            "cad_solve",
            (
                "Local subassembly solve residual or pose delta exceeded the warning threshold."
            ),
            threshold_details,
        )
        return

    _add(
        issues,
        "local_subassembly_residuals_within_threshold",
        "pass",
        "cad_solve",
        "Local subassembly solve residuals and pose deltas are within thresholds.",
        threshold_details,
    )


def _float_detail(details: dict[str, object], key: str) -> float:
    value = details.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


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
            _validate_export_bbox(issues, export_result)
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


def _validate_export_bbox(
    issues: list[RobotDesignValidationIssue],
    export_result: CadQueryExportResult,
) -> None:
    bbox = export_result.bbox
    if bbox is None:
        _add(
            issues,
            "export_bbox_unavailable",
            "warning",
            "export_geometry",
            "Exported CAD object did not provide a bounding box.",
            {"path": str(export_result.path)},
        )
        return
    if not bbox.valid:
        _add(
            issues,
            "export_bbox_degenerate",
            "error",
            "export_geometry",
            "Exported CAD object bounding box is degenerate.",
            {"path": str(export_result.path), "bbox": bbox.to_dict()},
        )
        return
    _add(
        issues,
        "export_bbox_valid",
        "pass",
        "export_geometry",
        "Exported CAD object bounding box is non-degenerate.",
        {"path": str(export_result.path), "bbox": bbox.to_dict()},
    )


def _validate_step_package_geometry(
    issues: list[RobotDesignValidationIssue],
    package_result: CadQueryStepPackageExportResult | None,
) -> None:
    if package_result is None:
        return
    whole_bbox = package_result.whole_machine_export.bbox
    if whole_bbox is None:
        _add(
            issues,
            "step_package_whole_bbox_unavailable",
            "warning",
            "export_geometry",
            "Whole-machine STEP export did not provide a bounding box.",
            {"path": str(package_result.whole_machine_export.path)},
        )
        return
    if not whole_bbox.valid:
        _add(
            issues,
            "step_package_whole_bbox_degenerate",
            "error",
            "export_geometry",
            "Whole-machine STEP export bounding box is degenerate.",
            {
                "path": str(package_result.whole_machine_export.path),
                "bbox": whole_bbox.to_dict(),
            },
        )
        return

    missing_bbox: list[str] = []
    degenerate_bbox: list[dict[str, object]] = []
    outside_whole: list[dict[str, object]] = []
    for subassembly in package_result.subassemblies:
        bbox = subassembly.assembly_export.bbox
        if bbox is None:
            missing_bbox.append(subassembly.name)
            continue
        if not bbox.valid:
            degenerate_bbox.append(
                {
                    "name": subassembly.name,
                    "path": str(subassembly.assembly_export.path),
                    "bbox": bbox.to_dict(),
                }
            )
            continue
        if not _bbox_inside(bbox, whole_bbox, tolerance=1.0):
            outside_whole.append(
                {
                    "name": subassembly.name,
                    "path": str(subassembly.assembly_export.path),
                    "bbox": bbox.to_dict(),
                }
            )

    if degenerate_bbox:
        _add(
            issues,
            "step_package_subassembly_bbox_degenerate",
            "error",
            "export_geometry",
            "One or more subassembly STEP bounding boxes are degenerate.",
            {"subassemblies": degenerate_bbox},
        )
    if missing_bbox:
        _add(
            issues,
            "step_package_subassembly_bbox_unavailable",
            "warning",
            "export_geometry",
            "One or more subassembly STEP exports did not provide bounding boxes.",
            {"subassemblies": missing_bbox},
        )
    if outside_whole:
        _add(
            issues,
            "step_package_subassembly_bbox_outside_whole",
            "warning",
            "export_geometry",
            "One or more subassembly bounding boxes are outside the whole-machine bounding box.",
            {"subassemblies": outside_whole, "whole_bbox": whole_bbox.to_dict()},
        )
    if not missing_bbox and not degenerate_bbox and not outside_whole:
        _add(
            issues,
            "step_package_bboxes_consistent",
            "pass",
            "export_geometry",
            "Whole-machine and subassembly STEP bounding boxes are non-degenerate and spatially consistent.",
            {
                "whole_bbox": whole_bbox.to_dict(),
                "subassembly_count": len(package_result.subassemblies),
            },
        )


def _validate_visual_geometry(
    issues: list[RobotDesignValidationIssue],
    layout: MechanicalLayout | None,
    kinematic_model: KinematicModel | None,
    structure_plan: RobotStructurePlan | None,
    package_result: CadQueryStepPackageExportResult | None,
    snapshot_result: object | None,
) -> None:
    report = validate_visual_geometry(
        layout=layout,
        kinematic_model=kinematic_model,
        structure_plan=structure_plan,
        step_package_result=package_result,
        snapshot_result=snapshot_result,
    )
    for issue in report.issues:
        _add(
            issues,
            issue.code,
            issue.severity,
            "visual_geometry",
            issue.message,
            issue.details,
        )


def _validate_stage7_completion(
    issues: list[RobotDesignValidationIssue],
    requirement: RobotRequirement | None,
    kinematic_model: KinematicModel | None,
    structure_plan: RobotStructurePlan | None,
    layout: MechanicalLayout | None,
    cad_result: CadQueryAssemblyResult | None,
) -> None:
    if not _stage7_gate_applies(requirement, kinematic_model, structure_plan, layout):
        return

    pass_codes = {issue.code for issue in issues if issue.severity == "pass"}
    error_codes = [issue.code for issue in issues if issue.severity == "error"]
    required_pass_codes = _stage7_required_pass_codes(cad_result)
    missing = sorted(required_pass_codes - pass_codes)

    layout_template = _layout_template(layout)
    has_structure_plan = structure_plan is not None or _layout_has_structure_plan(layout)
    if not has_structure_plan:
        missing.append("structure_plan_present")
    if layout_template != "structure_plan":
        missing.append("layout_template_structure_plan")

    details = {
        "required_pass_codes": sorted(STAGE7_REQUIRED_PASS_CODES),
        "effective_required_pass_codes": sorted(required_pass_codes),
        "present_pass_codes": sorted(pass_codes),
        "missing_requirements": sorted(set(missing)),
        "blocking_error_codes": error_codes,
        "dof": _stage7_design_dof(requirement, kinematic_model, layout),
        "layout_template": layout_template,
        "has_structure_plan": has_structure_plan,
        "semantic_constraints_applied_to_solver": (
            _semantic_solver_application(cad_result) if cad_result is not None else "none"
        ),
        "production_full_assembly_solve_enabled": False,
    }
    if error_codes or missing:
        _add(
            issues,
            "stage7_robot_cad_not_ready",
            "warning",
            "stage7",
            (
                "Stage 7 robot CAD gate is not ready; source-first structure, "
                "semantic solve fixture, export, and visual review requirements "
                "have not all passed."
            ),
            details,
        )
        return

    _add(
        issues,
        "stage7_robot_cad_ready",
        "pass",
        "stage7",
        (
            "Stage 7 robot CAD gate passed for the MVP source-first workflow; "
            "production whole-machine STEP still uses fixed layout placement, "
            "with full semantic solve kept as a promotion fixture."
        ),
        details,
    )


def _stage7_required_pass_codes(
    cad_result: CadQueryAssemblyResult | None,
) -> set[str]:
    required = set(STAGE7_REQUIRED_PASS_CODES)
    if cad_result is None or _semantic_solver_application(cad_result) != "source_joint":
        return required
    required.discard("local_subassembly_ready_for_promotion")
    required.discard("full_assembly_fixture_ready_for_promotion")
    required.add("source_joint_promotion_gate_ready")
    required.add("source_joint_step_package_ready")
    return required


def _stage7_gate_applies(
    requirement: RobotRequirement | None,
    kinematic_model: KinematicModel | None,
    structure_plan: RobotStructurePlan | None,
    layout: MechanicalLayout | None,
) -> bool:
    return (
        _stage7_design_dof(requirement, kinematic_model, layout) >= STAGE7_MIN_DOF
        or structure_plan is not None
        or _layout_has_structure_plan(layout)
    )


def _stage7_design_dof(
    requirement: RobotRequirement | None,
    kinematic_model: KinematicModel | None,
    layout: MechanicalLayout | None,
) -> int:
    values = [
        requirement.dof if requirement is not None else None,
        len(kinematic_model.joints) if kinematic_model is not None else None,
        len(layout.joints) if layout is not None else None,
    ]
    return max((int(value) for value in values if value is not None), default=0)


def _layout_template(layout: MechanicalLayout | None) -> str | None:
    if layout is None:
        return None
    value = layout.metadata.get("layout_template")
    return str(value) if value is not None else None


def _layout_has_structure_plan(layout: MechanicalLayout | None) -> bool:
    if layout is None:
        return False
    return layout.metadata.get("layout_template") == "structure_plan" or bool(
        layout.metadata.get("structure_plan")
    )


def _bbox_inside(inner: object, outer: object, *, tolerance: float) -> bool:
    return (
        getattr(inner, "xmin") >= getattr(outer, "xmin") - tolerance
        and getattr(inner, "ymin") >= getattr(outer, "ymin") - tolerance
        and getattr(inner, "zmin") >= getattr(outer, "zmin") - tolerance
        and getattr(inner, "xmax") <= getattr(outer, "xmax") + tolerance
        and getattr(inner, "ymax") <= getattr(outer, "ymax") + tolerance
        and getattr(inner, "zmax") <= getattr(outer, "zmax") + tolerance
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
