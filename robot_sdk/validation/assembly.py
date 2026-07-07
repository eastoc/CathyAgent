"""Assembly semantic validation for mate-frame driven workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from robot_sdk.assembly.constraint_graph import (
    ConstraintGraphReport,
    build_constraint_graph_report,
    evaluate_full_assembly_solve_gate,
)
from robot_sdk.assembly.constraints import build_assembly_mate_constraint_plan
from robot_sdk.assembly.local_solve import build_local_subassembly_plans
from robot_sdk.assembly.mate_frames import build_mate_frame_catalog
from robot_sdk.types import MechanicalLayout


AssemblyValidationSeverity = Literal["pass", "warning", "error"]
SemanticConstraintApplication = Literal["none", "local_subassembly", "full_assembly"]


@dataclass(frozen=True)
class AssemblyValidationIssue:
    """One assembly semantic validation item."""

    code: str
    severity: AssemblyValidationSeverity
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AssemblyValidationReport:
    """Validation report for mate frames and semantic constraints."""

    issues: list[AssemblyValidationIssue]
    semantic_constraint_application: SemanticConstraintApplication = "none"

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[AssemblyValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[AssemblyValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def passes(self) -> list[AssemblyValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "pass"]


def validate_assembly_semantics(
    layout: MechanicalLayout,
    *,
    semantic_constraint_application: SemanticConstraintApplication = "none",
) -> AssemblyValidationReport:
    """Validate mate-frame readiness for constraint-driven assembly."""

    issues: list[AssemblyValidationIssue] = []
    try:
        mate_catalog = build_mate_frame_catalog(layout)
    except Exception as exc:
        return AssemblyValidationReport(
            issues=[
                AssemblyValidationIssue(
                    code="mate_frame_build_failed",
                    severity="error",
                    message=f"Mate frame catalog could not be built: {exc}",
                )
            ],
            semantic_constraint_application=semantic_constraint_application,
        )

    feature_ids = {feature.id for feature in layout.part_features}
    missing_features = sorted(feature_ids - mate_catalog.feature_ids())
    if missing_features:
        issues.append(
            AssemblyValidationIssue(
                code="missing_mate_frames",
                severity="error",
                message="Some PartFeature records did not produce MateFrame records.",
                details={"feature_ids": missing_features},
            )
        )
    else:
        issues.append(
            AssemblyValidationIssue(
                code="mate_frames_complete",
                severity="pass",
                message=f"Built {len(mate_catalog.by_id)} mate frames from layout features.",
                details={"mate_frame_count": len(mate_catalog.by_id)},
            )
        )

    try:
        plan = build_assembly_mate_constraint_plan(layout)
    except Exception as exc:
        issues.append(
            AssemblyValidationIssue(
                code="constraint_plan_failed",
                severity="error",
                message=f"Mate constraint plan could not be built: {exc}",
            )
        )
        return AssemblyValidationReport(
            issues=issues,
            semantic_constraint_application=semantic_constraint_application,
        )

    issues.append(
        AssemblyValidationIssue(
            code="constraint_plan_resolved",
            severity="pass",
            message=f"Resolved {len(plan.rules)} assembly constraints through mate frames.",
            details={"constraint_count": len(plan.rules)},
        )
    )

    if plan.unsafe_rules:
        issues.append(
            AssemblyValidationIssue(
                code="unsafe_body_axis_joint_axis",
                severity="warning",
                message=(
                    "Some axis constraints still align link body_axis directly to "
                    "joint_axis; these must be replaced by interface mate rules "
                    "before full constraint solve."
                ),
                details={
                    "constraint_ids": [rule.constraint_id for rule in plan.unsafe_rules],
                },
            )
        )

    try:
        graph_report = build_constraint_graph_report(
            build_local_subassembly_plans(layout),
            layout=layout,
        )
    except Exception as exc:
        issues.append(
            AssemblyValidationIssue(
                code="constraint_graph_failed",
                severity="error",
                message=f"Local subassembly constraint graph could not be built: {exc}",
            )
        )
        return AssemblyValidationReport(
            issues=issues,
            semantic_constraint_application=semantic_constraint_application,
        )
    _append_constraint_graph_issues(issues, graph_report)

    if semantic_constraint_application == "none":
        issues.append(
            AssemblyValidationIssue(
                code="semantic_constraints_not_applied",
                severity="warning",
                message=(
                    "Semantic mate constraints are resolved but not yet applied "
                    "to the CadQuery solver."
                ),
            )
        )
    return AssemblyValidationReport(
        issues=issues,
        semantic_constraint_application=semantic_constraint_application,
    )


def _append_constraint_graph_issues(
    issues: list[AssemblyValidationIssue],
    graph_report: ConstraintGraphReport,
) -> None:
    graphs = list(graph_report.graphs)
    if not graphs:
        issues.append(
            AssemblyValidationIssue(
                code="local_subassembly_graph_missing",
                severity="warning",
                message="No local subassembly constraint graph candidates were found.",
            )
        )
        return

    underconstrained = [graph.to_dict() for graph in graph_report.underconstrained]
    cyclic = [graph.to_dict() for graph in graph_report.cyclic]
    dense = [graph.to_dict() for graph in graph_report.potentially_overconstrained]
    whole_graph = graph_report.whole_graph

    if underconstrained:
        issues.append(
            AssemblyValidationIssue(
                code="local_subassembly_graph_underconstrained",
                severity="warning",
                message=(
                    "One or more local subassembly constraint graphs are disconnected "
                    "or contain isolated parts."
                ),
                details={"graphs": underconstrained},
            )
        )
    if cyclic:
        issues.append(
            AssemblyValidationIssue(
                code="local_subassembly_graph_cycle",
                severity="warning",
                message="One or more local subassembly constraint graphs contain cycles.",
                details={"graphs": cyclic},
            )
        )
    if dense:
        issues.append(
            AssemblyValidationIssue(
                code="local_subassembly_graph_dense_pairs",
                severity="warning",
                message=(
                    "One or more part pairs have more than two semantic constraints; "
                    "review for possible overconstraint before full assembly solve."
                ),
                details={"graphs": dense},
            )
        )
    if not underconstrained and not cyclic and not dense:
        issues.append(
            AssemblyValidationIssue(
                code="local_subassembly_graphs_valid",
                severity="pass",
                message=(
                    "Local subassembly constraint graphs are connected and have no "
                    "cycles or dense part-pair constraints."
                ),
                details={
                    "graph_count": len(graphs),
                    "graphs": [graph.to_dict() for graph in graphs],
                },
            )
        )
    if whole_graph is not None:
        if whole_graph.underconstrained:
            issues.append(
                AssemblyValidationIssue(
                    code="whole_assembly_graph_underconstrained",
                    severity="warning",
                    message=(
                        "Whole-machine assembly constraint graph is disconnected, "
                        "contains isolated parts, or has no base-to-terminal path."
                    ),
                    details={"graph": whole_graph.to_dict()},
                )
            )
        elif whole_graph.has_cycles:
            issues.append(
                AssemblyValidationIssue(
                    code="whole_assembly_graph_cycle",
                    severity="warning",
                    message="Whole-machine assembly constraint graph contains cycles.",
                    details={"graph": whole_graph.to_dict()},
                )
            )
        elif whole_graph.potentially_overconstrained:
            issues.append(
                AssemblyValidationIssue(
                    code="whole_assembly_graph_dense_pairs",
                    severity="warning",
                    message=(
                        "Whole-machine part pairs have more than two semantic "
                        "constraints; review before production full solve."
                    ),
                    details={"graph": whole_graph.to_dict()},
                )
            )
        else:
            issues.append(
                AssemblyValidationIssue(
                    code="whole_assembly_graph_valid",
                    severity="pass",
                    message=(
                        "Whole-machine assembly constraint graph is connected "
                        "from base to terminal part."
                    ),
                    details={"graph": whole_graph.to_dict()},
                )
            )
    gate = evaluate_full_assembly_solve_gate(graph_report)
    if gate.clear_for_fixture:
        issues.append(
            AssemblyValidationIssue(
                code="full_assembly_solve_gate_clear_for_fixture",
                severity="pass",
                message=(
                    "Constraint graph checks are clear for starting a full-assembly "
                    "solve fixture; full assembly solve is still disabled."
                ),
                details=gate.to_dict(),
            )
        )
    else:
        issues.append(
            AssemblyValidationIssue(
                code="full_assembly_solve_gate_blocked",
                severity="warning",
                message=(
                    "Full-assembly solve fixture is blocked by local or whole "
                    "assembly constraint graph warnings."
                ),
                details=gate.to_dict(),
            )
        )


__all__ = [
    "AssemblyValidationIssue",
    "AssemblyValidationReport",
    "AssemblyValidationSeverity",
    "SemanticConstraintApplication",
    "validate_assembly_semantics",
]
