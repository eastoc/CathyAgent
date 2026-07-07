"""RobotDesignAgent：固定 MVP 流程的机器人 CAD 总入口。

当前版本消费 kinematics_agent 生成可追溯 KinematicModel；未注入时保留
本地 template fallback。layout / cad / export 走固定约束求解链路：

    requirement text
    -> simple requirement parsing
    -> kinematics_agent or local template fallback
    -> MechanicalLayout
    -> CadQuery constraint_solve assembly
    -> STEP export
    -> validation report
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cathy.subagent import Subagent, SubagentResult
from robot_sdk.assembly.constraint_graph import (
    build_constraint_graph_report,
    evaluate_full_assembly_solve_gate,
)
from robot_sdk.assembly.local_solve import build_local_subassembly_plans
from robot_sdk.cad.cq_assembly import build_cadquery_assembly
from robot_sdk.cad.export import (
    CadQueryExportResult,
    CadQueryStepPackageExportResult,
    export_robot_step_package,
)
from robot_sdk.cad.source_assembly import SourceStepExportResult
from robot_sdk.cad.source_export import (
    SourceStepPackageExportResult,
    export_source_robot_step_package,
)
from robot_sdk.cad.source_robot_builder import (
    SourceRobotAssemblyResult,
    build_source_robot_from_layout,
)
from robot_sdk.cad.snapshot import (
    StepSnapshotPackageResult,
    generate_step_package_snapshots,
)
from robot_sdk.kinematics.dh import estimate_reach
from robot_sdk.layout.decision_adapter import (
    apply_layout_decision,
    build_mechanical_layout_with_decision,
)
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.layout.structure_adapter import build_mechanical_layout_from_structure_plan
from robot_sdk.structure import RobotStructurePlan
from robot_sdk.types import (
    DHParam,
    JointSpec,
    KinematicModel,
    LinkSpec,
    MechanicalLayout,
    RobotRequirement,
)
from robot_sdk.validation.basic import (
    RobotDesignValidationIssue,
    RobotDesignValidationReport,
    validate_robot_design,
)
from robot_sdk.validation.structure import (
    RobotStructureValidationReport,
    validate_robot_structure,
)
from subagents.kinematics_agent.schema import KinematicsAgentResult
from subagents.robot_design_agent.rules import (
    build_cad_export_subassembly_specs,
    build_cad_snapshot_subassembly_names,
)


DEFAULT_DOF = 4
DEFAULT_REACH_MM = 400.0
DEFAULT_PAYLOAD_G = 500.0
DEFAULT_SESSION_DIR = "default"


@dataclass(frozen=True)
class _ParsedRequirement:
    requirement: RobotRequirement
    assumptions: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class _KinematicsMetadata:
    source_mode: str
    source_summary: str
    profile_name: str | None = None
    target_reach: float | None = None
    target_reach_unit: str = "mm"
    scale_factor: float | None = None
    assumptions: list[str] | None = None
    warnings: list[str] | None = None


@dataclass(frozen=True)
class _LayoutBuildResult:
    layout: MechanicalLayout
    trace: list[dict[str, Any]]
    decision: dict[str, Any] | None = None
    structure_plan: RobotStructurePlan | None = None


@dataclass(frozen=True)
class _SourceArtifacts:
    structure_plan_path: Path | None
    mechanical_layout_path: Path
    robot_model_path: Path

    def to_trace(self) -> dict[str, Any]:
        return {
            "type": "source_artifacts",
            "structure_plan_path": str(self.structure_plan_path)
            if self.structure_plan_path is not None
            else None,
            "mechanical_layout_path": str(self.mechanical_layout_path),
            "robot_model_path": str(self.robot_model_path),
            "structure_plan_exists": self.structure_plan_path.exists()
            if self.structure_plan_path is not None
            else False,
            "mechanical_layout_exists": self.mechanical_layout_path.exists(),
            "robot_model_exists": self.robot_model_path.exists(),
        }


@dataclass(frozen=True)
class _ConstraintGraphSummary:
    final_answer_line: str
    full_assembly_gate: str
    table_rows: str


@dataclass(frozen=True)
class _SourceCadResultAdapter:
    """Duck-typed CAD result used while source_joint is an opt-in backend."""

    source_result: SourceRobotAssemblyResult
    solved: bool = True
    solve_error: str | None = None

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self.source_result.metadata)

    @property
    def part_count(self) -> int:
        return len(self.source_result.part_ids)

    @property
    def constraint_count(self) -> int:
        return len(self.source_result.relations)

    @property
    def part_catalog(self) -> Any:
        return self.source_result.part_catalog

    @property
    def local_subassembly_results(self) -> list[Any]:
        return []


class RobotDesignAgent(Subagent):
    """固定流程的 Robot CAD MVP 总入口。"""

    name = "robot_design_agent"
    description = (
        "机器人 CAD 建模执行型子 agent。用户要求机器人建模、CAD、STEP、"
        "装配、整机、零件、导出或跑 MVP 建模时必须调用本工具，"
        "不要只调用 kinematics_agent。用于在已经明确要生成 CAD/STEP 时，"
        "把机器人设计需求跑成一个 MVP 闭环：需求解析、简单 DH/KinematicModel、"
        "MechanicalLayout、CadQuery 粗 CAD、STEP 导出和基础验证报告。"
        "设计方法论和审查清单由 robot_cad_design skill 提供。"
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["request"],
        "properties": {
            "request": {
                "type": "string",
                "minLength": 1,
                "description": "用户的机器人 CAD/STEP/装配/建模需求文本，例如桌面 4DOF 机械臂、500g、工作半径 400mm。",
            },
            "dof": {
                "type": "integer",
                "minimum": 1,
                "maximum": 12,
                "description": "可选：覆盖从 request 中解析出的自由度。",
            },
            "reach_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "可选：覆盖从 request 中解析出的工作半径，单位 mm。",
            },
            "payload_g": {
                "type": "number",
                "minimum": 0,
                "description": "可选：覆盖从 request 中解析出的负载，单位 g。",
            },
            "output_dir": {
                "type": "string",
                "description": "可选：STEP 输出目录；不传时默认写入配置 workspace_root 下的当前 session 目录。",
            },
            "export_filename": {
                "type": "string",
                "description": "可选：整机 STEP 文件名，默认 整机.step。",
            },
            "production_assembly_source": {
                "type": "string",
                "enum": ["fixed_layout_pose", "full_semantic_solve"],
                "description": "可选：整机 production STEP 的装配来源。默认 full_semantic_solve；fixed_layout_pose 可作为保守回退。",
            },
            "cad_backend": {
                "type": "string",
                "enum": ["cadquery", "source_joint"],
                "description": "可选：CAD 后端。默认 source_joint；cadquery 为旧实验路径；source_joint 使用 build123d part-local joints 和 connect_to() 生成 resolved static STEP。",
            },
        },
    }

    def __init__(
        self,
        *,
        cq_module: Any | None = None,
        workspace_root: str | Path | None = None,
        kinematics_agent: Any | None = None,
        layout_agent: Any | None = None,
    ) -> None:
        self._cq_module = cq_module
        self._workspace_root = Path(workspace_root or Path.cwd())
        self._session_id = DEFAULT_SESSION_DIR
        self._kinematics_agent = kinematics_agent
        self._layout_agent = layout_agent

    def attach_session(self, session_id: str) -> None:
        """Receive the parent CathyAgent session id for per-session CAD output."""

        self._session_id = _safe_session_dir(session_id)

    def run(self, params: dict[str, Any]) -> SubagentResult:
        trace: list[dict[str, Any]] = []
        try:
            request = str(params.get("request") or "").strip()
            if not request:
                return SubagentResult(
                    final_answer="[robot_design_agent] request 不能为空",
                    finished=False,
                )

            parsed = _parse_requirement(request, params)
            requirement = parsed.requirement
            trace.append(
                {
                    "type": "requirement",
                    "requirement": requirement.to_dict(),
                    "assumptions": parsed.assumptions,
                    "warnings": parsed.warnings,
                }
            )

            kinematics_result = self._build_kinematics(
                request=request,
                requirement=requirement,
                params=params,
            )
            kinematic_model = kinematics_result.kinematic_model
            requirement = _requirement_with_kinematics_defaults(
                requirement,
                kinematics_result=kinematics_result,
            )
            kinematics_metadata = _metadata_from_kinematics_result(kinematics_result)
            combined_assumptions = [
                *requirement.assumptions,
                *kinematics_result.assumptions,
            ]
            combined_warnings = [
                *requirement.warnings,
                *kinematics_result.warnings,
            ]
            trace.append(
                {
                    "type": "kinematic_model",
                    "kinematic_model": kinematic_model.to_dict(),
                    "source_mode": kinematics_result.source_mode,
                    "source_summary": kinematics_result.source_summary,
                    "profile_name": kinematics_result.profile_name,
                    "scale_factor": kinematics_result.scale_factor,
                    "kinematics_trace": kinematics_result.trace,
                }
            )

            layout_result = self._build_layout(
                request=request,
                kinematic_model=kinematic_model,
                kinematics=kinematics_metadata,
            )
            layout = layout_result.layout
            trace.append(
                {
                    "type": "mechanical_layout",
                    "units": layout.units,
                    "layout_source": layout.metadata.get("layout_source", "unknown"),
                    "robot_family": layout.metadata.get("robot_family", "unknown"),
                    "layout_decision": layout_result.decision,
                    "structure_plan": layout_result.structure_plan.to_dict()
                    if layout_result.structure_plan is not None
                    else None,
                    "layout_template": layout.metadata.get("layout_template"),
                    "structure_plan_name": layout.metadata.get("structure_plan_name"),
                    "layout_agent_trace": layout_result.trace,
                    "link_morphology": {
                        link.id: link.metadata.get("morphology")
                        for link in layout.links
                    },
                    "frames": len(layout.frames),
                    "joints": len(layout.joints),
                    "links": len(layout.links),
                    "part_features": len(layout.part_features),
                    "assembly_constraints": len(layout.assembly_constraints),
                }
            )

            output_path = _output_path(
                params,
                workspace_root=self._workspace_root,
                session_id=self._session_id,
            )
            structure_report = validate_robot_structure(
                requirement=requirement,
                kinematic_model=kinematic_model,
                structure_plan=layout_result.structure_plan,
                production_cad=True,
            )
            if not structure_report.ok:
                requirement_doc_path = _write_structure_failure_document(
                    output_path.parent,
                    requirement=requirement,
                    kinematic_model=kinematic_model,
                    kinematics=kinematics_metadata,
                    report=structure_report,
                    assumptions=combined_assumptions,
                    warnings=combined_warnings,
                )
                trace.append(
                    {
                        "type": "requirement_document",
                        "path": str(requirement_doc_path),
                        "exists": requirement_doc_path.exists(),
                    }
                )
                trace.append(
                    {
                        "type": "structure_validation",
                        "ok": False,
                        "errors": [
                            f"ERROR structure.{issue.code}: {issue.message}"
                            for issue in structure_report.errors
                        ],
                        "warnings": [
                            f"WARNING structure.{issue.code}: {issue.message}"
                            for issue in structure_report.warnings
                        ],
                    }
                )
                return SubagentResult(
                    final_answer=_format_structure_validation_failure(
                        requirement=requirement,
                        kinematic_model=kinematic_model,
                        report=structure_report,
                        kinematics=kinematics_metadata,
                        requirement_doc_path=requirement_doc_path,
                    ),
                    finished=False,
                    trace=trace,
                )

            requirement_doc_path = _write_requirement_document(
                output_path.parent,
                requirement=requirement,
                kinematic_model=kinematic_model,
                layout=layout,
                kinematics=kinematics_metadata,
                assumptions=combined_assumptions,
                warnings=combined_warnings,
            )
            trace.append(
                {
                    "type": "requirement_document",
                    "path": str(requirement_doc_path),
                    "exists": requirement_doc_path.exists(),
                }
            )
            source_artifacts = _write_source_artifacts(
                output_path.parent,
                requirement=requirement,
                kinematic_model=kinematic_model,
                layout=layout,
                structure_plan=layout_result.structure_plan,
                kinematics=kinematics_metadata,
            )
            trace.append(source_artifacts.to_trace())

            cad_backend = _cad_backend(params)
            cad_result: Any
            export_result: CadQueryExportResult | SourceStepExportResult | None = None
            package_result: CadQueryStepPackageExportResult | SourceStepPackageExportResult | None = None
            snapshot_result: StepSnapshotPackageResult | None = None
            if cad_backend == "source_joint":
                source_result = build_source_robot_from_layout(
                    layout,
                    name="source_joint_robot",
                )
                cad_result = _SourceCadResultAdapter(source_result=source_result)
                trace.append(
                    {
                        "type": "cad_assembly",
                        "cad_backend": cad_backend,
                        "part_count": cad_result.part_count,
                        "constraint_count": cad_result.constraint_count,
                        "solved": cad_result.solved,
                        "solve_error": cad_result.solve_error,
                        "production_assembly_source": cad_result.metadata.get(
                            "production_assembly_source"
                        ),
                        "assembly_backend": cad_result.metadata.get("assembly_backend"),
                        "source_builder": cad_result.metadata.get("source"),
                        "layout_source": cad_result.metadata.get("layout_source"),
                        "robot_family": cad_result.metadata.get("robot_family"),
                        "link_routes": cad_result.metadata.get("link_routes"),
                        "source_mates": source_result.source_mates(),
                    }
                )
                package_result = export_source_robot_step_package(
                    source_result,
                    output_path.parent,
                    whole_machine_filename=output_path.name,
                    subassemblies=build_cad_export_subassembly_specs(cad_result),
                )
                export_result = package_result.whole_machine_export
                trace.append(
                    {
                        "type": "export",
                        "cad_backend": cad_backend,
                        "root_dir": str(package_result.root_dir),
                        "whole_machine_path": str(export_result.path),
                        "whole_machine_assembly_source": package_result.whole_machine_assembly_source,
                        "exists": export_result.exists,
                        "size_bytes": export_result.size_bytes,
                        "file_count": package_result.file_count,
                        "subassemblies": [
                            {
                                "name": item.name,
                                "directory": str(item.directory),
                                "part_ids": item.part_ids,
                                "assembly_path": str(item.assembly_export.path),
                                "part_paths": [str(part.path) for part in item.part_exports],
                                "assembly_source": item.assembly_source,
                                "local_solve_usage": item.local_solve_usage,
                            }
                            for item in package_result.subassemblies
                        ],
                    }
                )
                snapshot_result = generate_step_package_snapshots(
                    package_result,
                    subassembly_names=build_cad_snapshot_subassembly_names(package_result),
                    cq_module=self._cq_module,
                )
                trace.append(
                    {
                        "type": "visual_snapshots",
                        **snapshot_result.to_dict(),
                    }
                )
            else:
                cad_result = build_cadquery_assembly(
                    layout,
                    cq_module=self._cq_module,
                    solve=True,
                    raise_on_solve_error=False,
                    production_assembly_source=_production_assembly_source(params),
                    raise_on_full_semantic_solve_error=False,
                )
                trace.append(
                    {
                        "type": "cad_assembly",
                        "cad_backend": cad_backend,
                        "part_count": cad_result.part_count,
                        "constraint_count": cad_result.constraint_count,
                        "solved": cad_result.solved,
                        "solve_error": cad_result.solve_error,
                        "requested_production_assembly_source": cad_result.metadata.get(
                            "requested_production_assembly_source"
                        ),
                        "production_assembly_source": cad_result.metadata.get(
                            "production_assembly_source"
                        ),
                        "production_full_semantic_solve_requested": cad_result.metadata.get(
                            "production_full_semantic_solve_requested"
                        ),
                        "production_full_semantic_solve_used": cad_result.metadata.get(
                            "production_full_semantic_solve_used"
                        ),
                        "production_full_semantic_solve_error": cad_result.metadata.get(
                            "production_full_semantic_solve_error"
                        ),
                        "full_assembly_fixture_requested": cad_result.metadata.get(
                            "full_assembly_fixture_requested"
                        ),
                        "full_assembly_fixture_ran": cad_result.metadata.get(
                            "full_assembly_fixture_ran"
                        ),
                        "full_assembly_fixture_solved": cad_result.metadata.get(
                            "full_assembly_fixture_solved"
                        ),
                        "full_assembly_fixture_gate_status": cad_result.metadata.get(
                            "full_assembly_fixture_gate_status"
                        ),
                    }
                )

            if cad_backend == "cadquery" and cad_result.solved:
                package_result = export_robot_step_package(
                    cad_result,
                    output_path.parent,
                    whole_machine_filename=output_path.name,
                    subassemblies=build_cad_export_subassembly_specs(cad_result),
                )
                export_result = package_result.whole_machine_export
                trace.append(
                    {
                        "type": "export",
                        "root_dir": str(package_result.root_dir),
                        "whole_machine_path": str(export_result.path),
                        "whole_machine_assembly_source": package_result.whole_machine_assembly_source,
                        "exists": export_result.exists,
                        "size_bytes": export_result.size_bytes,
                        "file_count": package_result.file_count,
                        "subassemblies": [
                            {
                                "name": item.name,
                                "directory": str(item.directory),
                                "part_ids": item.part_ids,
                                "assembly_path": str(item.assembly_export.path),
                                "part_paths": [str(part.path) for part in item.part_exports],
                                "assembly_source": item.assembly_source,
                                "local_solve_usage": item.local_solve_usage,
                            }
                            for item in package_result.subassemblies
                        ],
                    }
                )
                snapshot_result = generate_step_package_snapshots(
                    package_result,
                    subassembly_names=build_cad_snapshot_subassembly_names(package_result),
                    cq_module=self._cq_module,
                )
                trace.append(
                    {
                        "type": "visual_snapshots",
                        **snapshot_result.to_dict(),
                    }
                )

            report = validate_robot_design(
                requirement=requirement,
                kinematic_model=kinematic_model,
                structure_plan=layout_result.structure_plan,
                layout=layout,
                cad_result=cad_result,
                export_result=export_result,
                step_package_result=package_result,
                snapshot_result=snapshot_result,
                export_path=output_path if export_result is None else None,
            )
            _append_validation_snapshot_section(requirement_doc_path, snapshot_result)
            _append_validation_review_section(requirement_doc_path, report)
            trace.append(
                {
                    "type": "validation",
                    "ok": report.ok,
                    "errors": report.messages("error"),
                    "warnings": report.messages("warning"),
                    "passes": report.messages("pass"),
                }
            )

            return SubagentResult(
                final_answer=_format_final_answer(
                    requirement=requirement,
                    kinematic_model=kinematic_model,
                    layout=layout,
                    output_path=output_path,
                    requirement_doc_path=requirement_doc_path,
                    cad_result=cad_result,
                    export_result=export_result,
                    package_result=package_result,
                    snapshot_result=snapshot_result,
                    report=report,
                    kinematics=kinematics_metadata,
                    source_artifacts=source_artifacts,
                    assumptions=combined_assumptions,
                ),
                finished=report.ok,
                trace=trace,
            )
        except Exception as exc:
            trace.append(
                {
                    "type": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            return SubagentResult(
                final_answer=f"[robot_design_agent] 执行失败: {type(exc).__name__}: {exc}",
                finished=False,
                trace=trace,
            )

    def _build_kinematics(
        self,
        *,
        request: str,
        requirement: RobotRequirement,
        params: dict[str, Any],
    ) -> KinematicsAgentResult:
        if self._kinematics_agent is not None:
            build_result = getattr(self._kinematics_agent, "build_result", None)
            if callable(build_result):
                return build_result(
                    {
                        "request": request,
                        "dof": requirement.dof,
                        "reach_mm": requirement.reach,
                        "allow_search": params.get("allow_search", True),
                    }
                )

        model = _build_simple_kinematic_model(requirement)
        return KinematicsAgentResult(
            kinematic_model=model,
            source_mode="template_fallback",
            source_summary="robot_design_agent local template fallback.",
            target_reach=requirement.reach,
            target_reach_unit=requirement.reach_unit,
            assumptions=list(model.assumptions),
            warnings=list(model.warnings),
            trace=[
                {
                    "type": "template_fallback",
                    "source": "robot_design_agent",
                    "dof": requirement.dof,
                    "reach": requirement.reach,
                }
            ],
        )

    def _build_layout(
        self,
        *,
        request: str,
        kinematic_model: KinematicModel,
        kinematics: _KinematicsMetadata,
    ) -> _LayoutBuildResult:
        if self._layout_agent is not None:
            build_result = getattr(self._layout_agent, "build_result", None)
            if callable(build_result):
                try:
                    layout_agent_result = build_result(
                        {
                            "request": request,
                            "kinematic_model": kinematic_model,
                            "profile_name": kinematics.profile_name,
                            "source_mode": kinematics.source_mode,
                            "scale_factor": kinematics.scale_factor,
                            "reach_mm": _kinematics_reach_mm(kinematics),
                        }
                    )
                    decision = layout_agent_result.decision
                    structure_plan = getattr(layout_agent_result, "structure_plan", None)
                    if isinstance(structure_plan, RobotStructurePlan):
                        layout = build_mechanical_layout_from_structure_plan(
                            kinematic_model,
                            structure_plan,
                        )
                        layout = apply_layout_decision(layout, decision)
                    else:
                        structure_plan = None
                        layout = build_mechanical_layout_with_decision(
                            kinematic_model,
                            decision,
                        )
                    return _LayoutBuildResult(
                        layout=layout,
                        trace=list(layout_agent_result.trace),
                        decision=decision.to_dict()
                        if hasattr(decision, "to_dict")
                        else None,
                        structure_plan=structure_plan,
                    )
                except Exception as exc:
                    layout = build_tabletop_serial_mechanical_layout_from_agent_model(
                        kinematic_model
                    )
                    return _LayoutBuildResult(
                        layout=replace(
                            layout,
                            warnings=[
                                *layout.warnings,
                                f"layout_agent failed; used rule fallback ({type(exc).__name__}: {exc}).",
                            ],
                            metadata={
                                **layout.metadata,
                                "layout_source": "rule_fallback",
                                "robot_family": "unknown",
                                "layout_agent_error": f"{type(exc).__name__}: {exc}",
                            },
                        ),
                        trace=[
                            {
                                "type": "layout_agent_error",
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                        ],
                    )

        layout = build_tabletop_serial_mechanical_layout_from_agent_model(kinematic_model)
        return _LayoutBuildResult(
            layout=replace(
                layout,
                metadata={
                    **layout.metadata,
                    "layout_source": "rule_fallback",
                    "robot_family": "unknown",
                },
            ),
            trace=[{"type": "layout_agent_skipped"}],
        )


def build_tabletop_serial_mechanical_layout_from_agent_model(
    model: KinematicModel,
) -> MechanicalLayout:
    """Small wrapper kept for traceable MVP workflow naming."""

    return build_tabletop_serial_mechanical_layout(
        model.dh_params,
        convention=model.convention,
    )


def _requirement_with_kinematics_defaults(
    requirement: RobotRequirement,
    *,
    kinematics_result: KinematicsAgentResult,
) -> RobotRequirement:
    model = kinematics_result.kinematic_model
    dof = len(model.joints)
    reach = kinematics_result.target_reach
    if reach is None:
        reach = estimate_reach(model.dh_params)

    assumptions = list(requirement.assumptions)
    if dof != requirement.dof:
        assumptions.append(
            f"运动学模型返回 {dof} DOF，已覆盖需求解析阶段的 {requirement.dof} DOF。"
        )
    if reach and requirement.reach and abs(float(reach) - float(requirement.reach)) > 1e-6:
        assumptions.append(
            f"运动学模型 reach {float(reach):g} mm 覆盖需求解析阶段的 {requirement.reach:g} mm。"
        )

    return RobotRequirement(
        task=requirement.task,
        dof=dof,
        payload=requirement.payload,
        payload_unit=requirement.payload_unit,
        reach=float(reach) if reach is not None else requirement.reach,
        reach_unit=kinematics_result.target_reach_unit or requirement.reach_unit,
        workspace=requirement.workspace,
        environment=requirement.environment,
        mounting=requirement.mounting,
        preferred_architecture=requirement.preferred_architecture,
        assumptions=assumptions,
        warnings=list(requirement.warnings),
    )


def _parse_requirement(text: str, params: dict[str, Any]) -> _ParsedRequirement:
    assumptions: list[str] = []
    warnings: list[str] = []

    dof = _number_param(params, "dof")
    if dof is None:
        dof = _parse_dof(text)
    if dof is None:
        dof = DEFAULT_DOF
        assumptions.append(f"未明确自由度，默认使用 {DEFAULT_DOF} DOF。")

    reach_mm = _number_param(params, "reach_mm")
    if reach_mm is None:
        reach_mm = _parse_length_mm(text)
    if reach_mm is None:
        reach_mm = DEFAULT_REACH_MM
        assumptions.append(f"未明确工作半径，默认使用 {DEFAULT_REACH_MM:g} mm。")

    payload_g = _number_param(params, "payload_g")
    if payload_g is None:
        payload_g = _parse_payload_g(text)
    if payload_g is None:
        payload_g = DEFAULT_PAYLOAD_G
        assumptions.append(f"未明确负载，默认使用 {DEFAULT_PAYLOAD_G:g} g。")

    if dof <= 0:
        raise ValueError("dof must be positive")
    if reach_mm <= 0:
        raise ValueError("reach_mm must be positive")
    if payload_g < 0:
        raise ValueError("payload_g cannot be negative")

    if "桌面" not in text and "desktop" not in text.lower():
        assumptions.append("MVP 第一版固定使用 tabletop serial arm 模板。")

    requirement = RobotRequirement(
        task=text,
        dof=int(dof),
        payload=float(payload_g),
        payload_unit="g",
        reach=float(reach_mm),
        reach_unit="mm",
        workspace="desktop serial-arm MVP",
        environment="tabletop",
        mounting="fixed tabletop base",
        preferred_architecture="tabletop_serial_arm",
        assumptions=assumptions,
        warnings=warnings,
    )
    return _ParsedRequirement(requirement=requirement, assumptions=assumptions, warnings=warnings)


def _build_simple_kinematic_model(requirement: RobotRequirement) -> KinematicModel:
    dof = requirement.dof
    reach = float(requirement.reach or DEFAULT_REACH_MM)
    link_lengths = _distribute_link_lengths(reach, dof)

    joints: list[JointSpec] = []
    links: list[LinkSpec] = []
    dh_params: list[DHParam] = []
    previous_link = "base"
    for index, length in enumerate(link_lengths, start=1):
        joint_id = f"J{index}"
        link_id = f"L{index}"
        joints.append(
            JointSpec(
                id=joint_id,
                type="revolute",
                parent_link=previous_link,
                child_link=link_id,
                limit=(-3.141592653589793, 3.141592653589793),
                notes=["MVP default revolute joint."],
            )
        )
        links.append(
            LinkSpec(
                id=link_id,
                length=length,
                length_unit="mm",
                parent_joint=joint_id,
                material="aluminum_placeholder",
                notes=["MVP box-beam link placeholder."],
            )
        )
        dh_params.append(
            DHParam(
                joint_id=joint_id,
                a=length,
                alpha=0.0,
                d=0.0,
                theta=0.0,
                joint_type="revolute",
                variable=f"theta{index}",
            )
        )
        previous_link = link_id

    return KinematicModel(
        convention="dh",
        joints=joints,
        links=links,
        dh_params=dh_params,
        assumptions=[
            "MVP uses a planar serial DH chain with revolute joints.",
            "DH lengths are sizing heuristics; CAD assembly semantics are generated by MechanicalLayout.",
        ],
    )


def _distribute_link_lengths(reach_mm: float, dof: int) -> list[float]:
    """Return simple positive link lengths whose sum covers requested reach."""

    if dof == 1:
        return [reach_mm]
    first = reach_mm * 0.3
    remaining = reach_mm - first
    tail = remaining / (dof - 1)
    return [first, *[tail for _ in range(dof - 1)]]


def _format_final_answer(
    *,
    requirement: RobotRequirement,
    kinematic_model: KinematicModel,
    layout: MechanicalLayout,
    output_path: Path,
    requirement_doc_path: Path,
    cad_result: Any,
    export_result: CadQueryExportResult | None,
    package_result: CadQueryStepPackageExportResult | SourceStepPackageExportResult | None,
    snapshot_result: StepSnapshotPackageResult | None,
    report: RobotDesignValidationReport,
    kinematics: _KinematicsMetadata,
    source_artifacts: _SourceArtifacts,
    assumptions: list[str],
) -> str:
    status = "通过" if report.ok else "未通过"
    export_line = (
        f"- Whole machine STEP: {export_result.path} ({export_result.size_bytes} bytes)"
        if export_result and export_result.exists
        else f"- STEP: 未导出，目标路径 {output_path}"
    )
    package_line = (
        f"- STEP package: {package_result.root_dir} ({package_result.file_count} files)"
        if package_result
        else "- STEP package: 未导出"
    )
    subassembly_line = (
        f"- Subassemblies: {len(package_result.subassemblies)}"
        if package_result
        else "- Subassemblies: 0"
    )
    assumptions_text = "\n".join(f"- {item}" for item in assumptions) or "- 无额外假设"
    error_text = "\n".join(f"- {msg}" for msg in report.messages("error")) or "- 无"
    warning_text = "\n".join(f"- {msg}" for msg in report.messages("warning")) or "- 无"
    link_lengths = _dh_span_summary(kinematic_model)
    scale_factor = "无" if kinematics.scale_factor is None else f"{kinematics.scale_factor:g}"
    solve_text = "成功" if cad_result.solved else f"失败（{cad_result.solve_error or '未知错误'}）"
    link_morphology = _link_morphology_summary(layout)
    graph_summary = _constraint_graph_summary(layout)
    fixture_text = _full_assembly_fixture_summary(cad_result)
    local_promotion_text = _local_subassembly_promotion_summary(report)
    production_full_solve_text = _production_full_semantic_solve_summary(report)
    stage7_gate_text = _stage7_gate_summary(report)
    local_export_text = _local_solve_export_summary(package_result)
    source_artifact_text = _source_artifact_summary(source_artifacts)
    snapshot_text = _snapshot_summary(snapshot_result)
    source_review_bbox_text = _source_joint_review_bbox_summary(report)

    return (
        "[robot_design_agent] 固定 MVP 流程已完成。\n\n"
        "## 设计摘要\n"
        f"- 任务: {requirement.task}\n"
        f"- DOF: {requirement.dof}\n"
        f"- 负载: {requirement.payload:g} {requirement.payload_unit}\n"
        f"- 工作半径: {requirement.reach:g} {requirement.reach_unit}\n"
        f"- 运动学来源: {kinematics.source_mode} ({kinematics.source_summary})\n"
        f"- Profile: {kinematics.profile_name or '无'}\n"
        f"- Scale factor: {scale_factor}\n"
        f"- DH link lengths: {link_lengths}\n\n"
        "## CAD 结果\n"
        f"- Requirement document: {requirement_doc_path}\n"
        f"- Parts: {cad_result.part_count}\n"
        f"- Constraints: {cad_result.constraint_count}\n"
        f"- Assembly mode: {cad_result.metadata.get('assembly_mode', 'unknown')}\n"
        f"- Production assembly source: {cad_result.metadata.get('production_assembly_source', 'unknown')}\n"
        f"- Assembly placement: {cad_result.metadata.get('placement_mode', 'unknown')}\n"
        f"- Solver constraint mode: {cad_result.metadata.get('solver_constraint_mode', 'unknown')}\n"
        f"- Semantic constraints applied: {cad_result.metadata.get('semantic_constraints_applied_to_solver', 'none')}\n"
        f"- Assembly.solve: {solve_text}\n"
        f"- Constraint graph: {graph_summary.final_answer_line}\n"
        f"- Full assembly solve gate: {graph_summary.full_assembly_gate}\n"
        f"- Full assembly fixture: {fixture_text}\n"
        f"- Production full semantic solve: {production_full_solve_text}\n"
        f"- Local semantic promotion: {local_promotion_text}\n"
        f"- Stage 7 gate: {stage7_gate_text}\n"
        f"- Local solved exports: {local_export_text}\n"
        f"- Source review bbox: {source_review_bbox_text}\n"
        f"- Source artifacts: {source_artifact_text}\n"
        f"- Visual snapshots: {snapshot_text}\n"
        f"{package_line}\n"
        f"{subassembly_line}\n"
        f"{export_line}\n\n"
        "## MechanicalLayout\n"
        f"- Layout source: {layout.metadata.get('layout_source', 'unknown')}\n"
        f"- Robot family: {layout.metadata.get('robot_family', 'unknown')}\n"
        f"- Layout template: {layout.metadata.get('layout_template', 'tabletop_serial_arm')}\n"
        f"- Link morphology: {link_morphology}\n"
        f"- Frames: {len(layout.frames)}\n"
        f"- PartFeatures: {len(layout.part_features)}\n"
        f"- AssemblyConstraints: {len(layout.assembly_constraints)}\n\n"
        "## 验证\n"
        f"- 状态: {status}\n"
        f"- Errors:\n{error_text}\n"
        f"- Warnings:\n{warning_text}\n\n"
        "## 假设\n"
        f"{assumptions_text}"
    )


def _source_artifact_summary(source_artifacts: _SourceArtifacts) -> str:
    items = [
        f"mechanical_layout={source_artifacts.mechanical_layout_path}",
        f"robot_model={source_artifacts.robot_model_path}",
    ]
    if source_artifacts.structure_plan_path is not None:
        items.insert(0, f"structure_plan={source_artifacts.structure_plan_path}")
    return "; ".join(items)


def _format_structure_validation_failure(
    *,
    requirement: RobotRequirement,
    kinematic_model: KinematicModel,
    report: RobotStructureValidationReport,
    kinematics: _KinematicsMetadata,
    requirement_doc_path: Path,
) -> str:
    errors = "\n".join(f"- {issue.code}: {issue.message}" for issue in report.errors)
    warnings = "\n".join(f"- {issue.code}: {issue.message}" for issue in report.warnings) or "- 无"
    return (
        "[robot_design_agent] 已停止 CAD 生成：结构规划未通过。\n\n"
        f"- DOF: {requirement.dof}\n"
        f"- 工作半径: {requirement.reach:g} {requirement.reach_unit}\n"
        f"- 运动学来源: {kinematics.source_mode}\n"
        f"- DH rows: {len(kinematic_model.dh_params)}\n\n"
        f"- Requirement document: {requirement_doc_path}\n\n"
        "## 错误\n"
        f"{errors}\n\n"
        "## 警告\n"
        f"{warnings}\n\n"
        "需要先由 layout/structure agent 生成 RobotStructurePlan，再进入 MechanicalLayout 和 CAD 导出。"
    )


def _link_morphology_summary(layout: MechanicalLayout) -> str:
    items = [
        f"{link.id}={link.metadata.get('morphology') or link.metadata.get('primitive_type') or 'unknown'}"
        for link in layout.links
    ]
    return ", ".join(items) if items else "无"


def _full_assembly_fixture_summary(cad_result: Any) -> str:
    metadata = getattr(cad_result, "metadata", {}) or {}
    if not metadata.get("full_assembly_fixture_requested"):
        return "not requested"
    status = str(metadata.get("full_assembly_fixture_gate_status") or "unknown")
    if not metadata.get("full_assembly_fixture_ran"):
        return f"{status}, skipped"
    solved = "solved" if metadata.get("full_assembly_fixture_solved") else "failed"
    semantic_count = metadata.get("full_assembly_fixture_semantic_constraint_count", 0)
    return f"{status}, {solved}, semantic_constraints={semantic_count}"


def _local_subassembly_promotion_summary(
    report: RobotDesignValidationReport,
) -> str:
    issue = next(
        (
            item
            for item in report.passes
            if item.code == "local_subassembly_ready_for_promotion"
        ),
        None,
    )
    if issue is None:
        return "not ready"
    ready = issue.details.get("ready_local_subassemblies")
    if not isinstance(ready, list) or not ready:
        return "not ready"
    names = [
        str(item.get("name"))
        for item in ready
        if isinstance(item, dict) and item.get("name")
    ]
    display = ", ".join(names[:5])
    if len(names) > 5:
        display = f"{display}, ..."
    return f"{len(names)} ready ({display})"


def _production_full_semantic_solve_summary(
    report: RobotDesignValidationReport,
) -> str:
    if any(
        item.code == "production_full_semantic_solve_ready"
        for item in report.passes
    ):
        return "ready"
    warning = next(
        (
            item
            for item in report.warnings
            if item.code == "production_full_semantic_solve_residual_warning"
        ),
        None,
    )
    if warning is not None:
        return "warning residuals"
    error = next(
        (
            item
            for item in report.errors
            if item.code
            in {
                "production_full_semantic_solve_failed",
                "production_full_semantic_solve_residual_exceeded",
            }
        ),
        None,
    )
    if error is not None:
        return f"failed ({error.code})"
    if any(
        item.code == "production_full_semantic_solve_used"
        for item in report.passes
    ):
        return "used"
    return "not requested"


def _stage7_gate_summary(report: RobotDesignValidationReport) -> str:
    ready_issue = next(
        (item for item in report.passes if item.code == "stage7_robot_cad_ready"),
        None,
    )
    if ready_issue is not None:
        return "ready"
    not_ready_issue = next(
        (item for item in report.warnings if item.code == "stage7_robot_cad_not_ready"),
        None,
    )
    if not_ready_issue is None:
        return "not applicable"
    missing = not_ready_issue.details.get("missing_requirements")
    if not isinstance(missing, list) or not missing:
        return "not ready"
    display = ", ".join(str(item) for item in missing[:5])
    if len(missing) > 5:
        display = f"{display}, ..."
    return f"not ready ({display})"


def _local_solve_export_summary(
    package_result: CadQueryStepPackageExportResult | SourceStepPackageExportResult | None,
) -> str:
    if package_result is None:
        return "not exported"
    production = [
        item.name
        for item in package_result.subassemblies
        if item.local_solve_usage == "production_local_solve"
    ]
    experimental = [
        item.name
        for item in package_result.subassemblies
        if item.local_solve_usage == "experimental_local_solve"
    ]
    if not production and not experimental:
        return "none"
    parts: list[str] = []
    if production:
        parts.append(f"production={len(production)} ({', '.join(production[:5])})")
    if experimental:
        parts.append(f"experimental={len(experimental)} ({', '.join(experimental[:5])})")
    return "; ".join(parts)


def _snapshot_summary(snapshot_result: StepSnapshotPackageResult | None) -> str:
    if snapshot_result is None:
        return "not generated"
    whole_real = sum(
        1
        for snapshot in snapshot_result.whole_machine_snapshots
        if snapshot.generated and not snapshot.fallback_used
    )
    subassembly_real = sum(
        1
        for snapshot in snapshot_result.subassembly_snapshots
        if snapshot.generated and not snapshot.fallback_used
    )
    if snapshot_result.real_step_snapshot_count > 0:
        first = next(
            (
                snapshot.svg_path
                for snapshot in snapshot_result.whole_machine_snapshots
                if snapshot.generated and not snapshot.fallback_used
            ),
            None,
        )
        path_text = f", first={first}" if first is not None else ""
        return (
            f"{snapshot_result.real_step_snapshot_count} STEP SVG "
            f"(whole={whole_real}, subassembly={subassembly_real})"
            f"{path_text}, fallback={snapshot_result.fallback_count}"
        )
    if snapshot_result.generated_count > 0:
        return (
            f"fallback only ({snapshot_result.generated_count} SVG), "
            f"dir={snapshot_result.snapshots_dir}"
        )
    return f"failed, dir={snapshot_result.snapshots_dir}"


def _source_joint_review_bbox_summary(report: RobotDesignValidationReport) -> str:
    issue = _source_joint_review_bbox_issue(report)
    if issue is None:
        return "not applicable"
    metrics = issue.details.get("review_bbox_metrics")
    if not isinstance(metrics, dict) or not metrics:
        return "unavailable"
    names = [
        name
        for name in (
            "base_shoulder",
            "upper_arm",
            "forearm",
            "wrist_l5_j6",
            "tool_end",
        )
        if name in metrics
    ]
    ratios = []
    for name in names:
        item = metrics.get(name)
        if not isinstance(item, dict):
            continue
        ratio = item.get("x_to_cross_ratio")
        if isinstance(ratio, (int, float)):
            ratios.append(f"{name}={float(ratio):.2f}")
    status = "ready" if issue.severity == "pass" else "failed"
    return f"{status} ({', '.join(ratios)})" if ratios else status


def _source_joint_review_bbox_issue(
    report: RobotDesignValidationReport,
) -> RobotDesignValidationIssue | None:
    for issue in report.passes:
        if issue.code == "source_joint_review_bbox_ratios_ready":
            return issue
    for issue in report.errors:
        if issue.code == "source_joint_review_bbox_ratio_failed":
            return issue
    return None


def _append_validation_snapshot_section(
    requirement_doc_path: Path,
    snapshot_result: StepSnapshotPackageResult | None,
) -> None:
    if snapshot_result is None:
        section = (
            "\n\n## CAD Visual Snapshot\n"
            "- Status: not generated\n"
        )
    else:
        whole_rows = _snapshot_table_rows(snapshot_result.whole_machine_snapshots)
        subassembly_rows = _snapshot_table_rows(snapshot_result.subassembly_snapshots)
        section = (
            "\n\n## CAD Visual Snapshot\n"
            f"- Snapshot directory: {snapshot_result.snapshots_dir}\n"
            f"- Renderer-backed STEP snapshots: {snapshot_result.real_step_snapshot_count}\n"
            f"- Fallback snapshots: {snapshot_result.fallback_count}\n\n"
            "### Whole Machine\n"
            "| Target | View | Renderer | Generated | Fallback | SVG | Error |\n"
            "|---|---|---|---|---|---|---|\n"
            f"{whole_rows}\n\n"
            "### Focus Subassemblies\n"
            "| Target | View | Renderer | Generated | Fallback | SVG | Error |\n"
            "|---|---|---|---|---|---|---|\n"
            f"{subassembly_rows or '| none | - | - | false | false | - | - |'}\n"
        )
    with requirement_doc_path.open("a", encoding="utf-8") as handle:
        handle.write(section)


def _append_validation_review_section(
    requirement_doc_path: Path,
    report: RobotDesignValidationReport,
) -> None:
    issue = _source_joint_review_bbox_issue(report)
    if issue is None:
        return
    status = "pass" if issue.severity == "pass" else issue.severity
    rows = _source_joint_review_bbox_rows(issue)
    failures = _source_joint_review_bbox_failure_rows(issue)
    section = (
        "\n\n## CAD Review Metrics\n"
        f"- Source joint review bbox gate: {status}\n\n"
        "### Source Joint Review BBox\n"
        "| Review subassembly | x/cross | z/x | y/x | minor/major | bbox x/y/z(mm) |\n"
        "|---|---:|---:|---:|---:|---|\n"
        f"{rows or '| none | - | - | - | - | - |'}\n"
    )
    if failures:
        section += (
            "\n### Review Failures\n"
            "| Review subassembly | Reason |\n"
            "|---|---|\n"
            f"{failures}\n"
        )
    with requirement_doc_path.open("a", encoding="utf-8") as handle:
        handle.write(section)


def _source_joint_review_bbox_rows(issue: RobotDesignValidationIssue) -> str:
    metrics = issue.details.get("review_bbox_metrics")
    if not isinstance(metrics, dict):
        return ""
    rows: list[str] = []
    for name in (
        "base_shoulder",
        "upper_arm",
        "forearm",
        "wrist_l5_j6",
        "tool_end",
    ):
        item = metrics.get(name)
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox")
        bbox_text = "-"
        if isinstance(bbox, dict):
            bbox_text = "{x}/{y}/{z}".format(
                x=_metric_number(bbox.get("xlen")),
                y=_metric_number(bbox.get("ylen")),
                z=_metric_number(bbox.get("zlen")),
            )
        rows.append(
            "| {name} | {x_cross} | {z_x} | {y_x} | {minor_major} | {bbox} |".format(
                name=name,
                x_cross=_metric_number(item.get("x_to_cross_ratio")),
                z_x=_metric_number(item.get("z_to_x_ratio")),
                y_x=_metric_number(item.get("y_to_x_ratio")),
                minor_major=_metric_number(item.get("minor_to_major_ratio")),
                bbox=bbox_text,
            )
        )
    return "\n".join(rows)


def _source_joint_review_bbox_failure_rows(issue: RobotDesignValidationIssue) -> str:
    failures = issue.details.get("review_bbox_failures")
    if not isinstance(failures, list):
        return ""
    rows: list[str] = []
    for item in failures:
        if not isinstance(item, dict):
            continue
        rows.append(
            "| {name} | {reason} |".format(
                name=str(item.get("name") or "unknown").replace("|", "/"),
                reason=str(item.get("reason") or "").replace("|", "/"),
            )
        )
    return "\n".join(rows)


def _metric_number(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    return "-"


def _snapshot_table_rows(snapshots: list[object]) -> str:
    return "\n".join(
        "| {target} | {view} | {renderer} | {generated} | {fallback} | {path} | {error} |".format(
            target=(getattr(snapshot, "target_name", None) or "whole_machine"),
            view=getattr(snapshot, "view", ""),
            renderer=getattr(snapshot, "renderer", ""),
            generated=str(bool(getattr(snapshot, "generated", False))).lower(),
            fallback=str(bool(getattr(snapshot, "fallback_used", False))).lower(),
            path=getattr(snapshot, "svg_path", ""),
            error=(getattr(snapshot, "error", None) or "").replace("|", "/"),
        )
        for snapshot in snapshots
    )


def _constraint_graph_summary(layout: MechanicalLayout) -> _ConstraintGraphSummary:
    try:
        report = build_constraint_graph_report(
            build_local_subassembly_plans(layout),
            layout=layout,
        )
    except Exception as exc:
        message = f"unavailable ({type(exc).__name__}: {exc})"
        return _ConstraintGraphSummary(
            final_answer_line=message,
            full_assembly_gate="blocked: constraint graph unavailable",
            table_rows=f"| unavailable | - | 0 | 0 | 0 | - | - | {message} |",
        )

    graph_count = len(report.graphs)
    underconstrained = len(report.underconstrained)
    cycles = len(report.cyclic)
    dense = len(report.potentially_overconstrained)
    whole = report.whole_graph
    gate = evaluate_full_assembly_solve_gate(report)
    gate_line = f"{gate.status}: {'; '.join(gate.reasons)}"
    rows = [_constraint_graph_row(graph) for graph in report.graphs]
    if whole is not None:
        rows.append(_whole_constraint_graph_row(whole))
    return _ConstraintGraphSummary(
        final_answer_line=(
            f"{graph_count} local graphs, "
            f"underconstrained={underconstrained}, cycles={cycles}, dense_pairs={dense}, "
            f"whole_connected={whole.connected if whole is not None else 'n/a'}"
        ),
        full_assembly_gate=gate_line,
        table_rows="\n".join(rows) if rows else "| none | - | 0 | 0 | 0 | - | - | missing |",
    )


def _constraint_graph_row(graph: Any) -> str:
    isolated = ", ".join(graph.isolated_part_ids) if graph.isolated_part_ids else "-"
    dense = ", ".join(
        f"{edge.part_a}-{edge.part_b}({edge.constraint_count})"
        for edge in graph.dense_edges
    ) or "-"
    if graph.underconstrained:
        status = "underconstrained"
    elif graph.has_cycles:
        status = "cycle"
    elif graph.potentially_overconstrained:
        status = "dense_pair"
    else:
        status = "ok"
    return (
        f"| {graph.name} | {', '.join(graph.part_ids)} | {len(graph.edges)} | "
        f"{graph.component_count} | {graph.cycle_count} | {isolated} | {dense} | {status} |"
    )


def _whole_constraint_graph_row(graph: Any) -> str:
    isolated = ", ".join(graph.isolated_part_ids) if graph.isolated_part_ids else "-"
    dense = ", ".join(
        f"{edge.part_a}-{edge.part_b}({edge.constraint_count})"
        for edge in graph.dense_edges
    ) or "-"
    if graph.underconstrained:
        status = "whole_underconstrained"
    elif graph.has_cycles:
        status = "whole_cycle"
    elif graph.potentially_overconstrained:
        status = "whole_dense_pair"
    else:
        status = "whole_ok"
    parts = f"{graph.anchor_part_id}->{graph.terminal_part_id}; {', '.join(graph.part_ids)}"
    return (
        f"| {graph.name} | {parts} | {len(graph.edges)} | "
        f"{graph.component_count} | {graph.cycle_count} | {isolated} | {dense} | {status} |"
    )


def _dh_span_summary(kinematic_model: KinematicModel) -> str:
    return ", ".join(
        (
            f"{param.joint_id}: span={math.hypot(param.a, param.d):g}mm "
            f"(a={param.a:g}, d={param.d:g})"
        )
        for param in kinematic_model.dh_params
    )


def _write_requirement_document(
    output_dir: Path,
    *,
    requirement: RobotRequirement,
    kinematic_model: KinematicModel,
    layout: MechanicalLayout,
    kinematics: _KinematicsMetadata,
    assumptions: list[str],
    warnings: list[str],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "需求文档.md"
    path.write_text(
        _format_requirement_document(
            requirement=requirement,
            kinematic_model=kinematic_model,
            layout=layout,
            kinematics=kinematics,
            assumptions=assumptions,
            warnings=warnings,
        ),
        encoding="utf-8",
    )
    return path


def _write_structure_failure_document(
    output_dir: Path,
    *,
    requirement: RobotRequirement,
    kinematic_model: KinematicModel,
    kinematics: _KinematicsMetadata,
    report: RobotStructureValidationReport,
    assumptions: list[str],
    warnings: list[str],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "需求文档.md"
    path.write_text(
        _format_structure_failure_document(
            requirement=requirement,
            kinematic_model=kinematic_model,
            kinematics=kinematics,
            report=report,
            assumptions=assumptions,
            warnings=warnings,
        ),
        encoding="utf-8",
    )
    return path


def _write_source_artifacts(
    output_dir: Path,
    *,
    requirement: RobotRequirement,
    kinematic_model: KinematicModel,
    layout: MechanicalLayout,
    structure_plan: RobotStructurePlan | None,
    kinematics: _KinematicsMetadata,
) -> _SourceArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    structure_plan_path = output_dir / "structure_plan.json"
    mechanical_layout_path = output_dir / "mechanical_layout.json"
    robot_model_path = output_dir / "robot_model.py"

    structure_payload: dict[str, Any]
    if structure_plan is None:
        structure_payload = {
            "structure_plan": None,
            "reason": "No RobotStructurePlan was produced for this design path.",
            "layout_template": layout.metadata.get("layout_template", "tabletop_serial_arm"),
        }
    else:
        structure_payload = structure_plan.to_dict()

    _write_json(structure_plan_path, structure_payload)
    _write_json(mechanical_layout_path, layout.to_dict())
    robot_model_path.write_text(
        _format_robot_model_source(
            requirement=requirement,
            kinematic_model=kinematic_model,
            layout=layout,
            structure_plan_path=structure_plan_path.name,
            mechanical_layout_path=mechanical_layout_path.name,
            kinematics=kinematics,
        ),
        encoding="utf-8",
    )
    return _SourceArtifacts(
        structure_plan_path=structure_plan_path,
        mechanical_layout_path=mechanical_layout_path,
        robot_model_path=robot_model_path,
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _format_robot_model_source(
    *,
    requirement: RobotRequirement,
    kinematic_model: KinematicModel,
    layout: MechanicalLayout,
    structure_plan_path: str,
    mechanical_layout_path: str,
    kinematics: _KinematicsMetadata,
) -> str:
    """Return a small reviewable Python source artifact for the CAD session."""

    manifest = {
        "task": requirement.task,
        "dof": requirement.dof,
        "reach": requirement.reach,
        "reach_unit": requirement.reach_unit,
        "kinematics_source_mode": kinematics.source_mode,
        "kinematics_profile_name": kinematics.profile_name,
        "kinematic_convention": kinematic_model.convention,
        "layout_template": layout.metadata.get("layout_template", "tabletop_serial_arm"),
        "layout_source": layout.metadata.get("layout_source", "unknown"),
        "robot_family": layout.metadata.get("robot_family", "unknown"),
        "structure_plan_json": structure_plan_path,
        "mechanical_layout_json": mechanical_layout_path,
    }
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        '"""Generated Robot CAD source manifest.\n\n'
        "This file is the review entrypoint for the session artifacts. STEP files\n"
        "are derived outputs; structure_plan.json and mechanical_layout.json are\n"
        "the source artifacts that should be reviewed before changing geometry.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import json\n"
        "from pathlib import Path\n\n\n"
        f"MANIFEST = json.loads({manifest_json!r})\n\n\n"
        "def session_dir() -> Path:\n"
        "    return Path(__file__).resolve().parent\n\n\n"
        "def load_structure_plan() -> dict:\n"
        "    return json.loads((session_dir() / MANIFEST['structure_plan_json']).read_text(encoding='utf-8'))\n\n\n"
        "def load_mechanical_layout() -> dict:\n"
        "    return json.loads((session_dir() / MANIFEST['mechanical_layout_json']).read_text(encoding='utf-8'))\n\n\n"
        "def summary() -> dict:\n"
        "    return dict(MANIFEST)\n\n\n"
        "if __name__ == '__main__':\n"
        "    print(json.dumps(summary(), ensure_ascii=False, indent=2, sort_keys=True))\n"
    )


def _format_structure_failure_document(
    *,
    requirement: RobotRequirement,
    kinematic_model: KinematicModel,
    kinematics: _KinematicsMetadata,
    report: RobotStructureValidationReport,
    assumptions: list[str],
    warnings: list[str],
) -> str:
    dh_rows = "\n".join(
        "| {joint_id} | {a:g} | {alpha:g} | {d:g} | {theta:g} | {joint_type} | {variable} |".format(
            joint_id=param.joint_id,
            a=param.a,
            alpha=param.alpha,
            d=param.d,
            theta=param.theta,
            joint_type=param.joint_type,
            variable=param.variable or "",
        )
        for param in kinematic_model.dh_params
    )
    assumption_lines = "\n".join(f"- {item}" for item in assumptions) or "- 无"
    warning_lines = "\n".join(f"- {item}" for item in warnings) or "- 无"
    error_lines = "\n".join(
        f"- {issue.code}: {issue.message}" for issue in report.errors
    ) or "- 无"
    structure_warning_lines = "\n".join(
        f"- {issue.code}: {issue.message}" for issue in report.warnings
    ) or "- 无"
    scale_factor = "无" if kinematics.scale_factor is None else f"{kinematics.scale_factor:g}"
    return (
        "# 机器人设计需求文档\n\n"
        "## 设计需求\n"
        f"- 原始需求: {requirement.task}\n"
        f"- 推荐构型: {requirement.preferred_architecture}\n"
        f"- 工作空间: {requirement.workspace}\n"
        f"- 工作环境: {requirement.environment}\n"
        f"- 安装方式: {requirement.mounting}\n\n"
        "## 约束\n"
        "| 约束项 | 值 | 单位 |\n"
        "|---|---:|---|\n"
        f"| DoF | {requirement.dof} | - |\n"
        f"| Payload | {requirement.payload:g} | {requirement.payload_unit} |\n"
        f"| Reach | {requirement.reach:g} | {requirement.reach_unit} |\n\n"
        "## DH 模型\n"
        f"- Source mode: {kinematics.source_mode}\n"
        f"- Source summary: {kinematics.source_summary}\n"
        f"- Profile: {kinematics.profile_name or '无'}\n"
        f"- Scale factor: {scale_factor}\n"
        f"- Convention: {kinematic_model.convention}\n\n"
        "| Joint | a(mm) | alpha(rad) | d(mm) | theta(rad) | Type | Variable |\n"
        "|---|---:|---:|---:|---:|---|---|\n"
        f"{dh_rows}\n\n"
        "## Structure Validation\n"
        "- CAD generation stopped before MechanicalLayout / CadQuery export.\n"
        "- Reason: high-DOF planar template fallback is not a valid production robot structure.\n\n"
        "### Errors\n"
        f"{error_lines}\n\n"
        "### Warnings\n"
        f"{structure_warning_lines}\n\n"
        "## 假设\n"
        f"{assumption_lines}\n\n"
        "## 警告\n"
        f"{warning_lines}\n"
    )


def _format_requirement_document(
    *,
    requirement: RobotRequirement,
    kinematic_model: KinematicModel,
    layout: MechanicalLayout,
    kinematics: _KinematicsMetadata,
    assumptions: list[str],
    warnings: list[str],
) -> str:
    joint_rows = "\n".join(
        "| {id} | {type} | {parent} | {child} | {lower:g}..{upper:g} |".format(
            id=joint.id,
            type=joint.type,
            parent=joint.parent_link,
            child=joint.child_link,
            lower=joint.limit[0] if joint.limit else 0.0,
            upper=joint.limit[1] if joint.limit else 0.0,
        )
        for joint in kinematic_model.joints
    )
    link_rows = "\n".join(
        f"| {link.id} | {link.length:g} | {link.length_unit} | {link.parent_joint} | {link.material or ''} |"
        for link in kinematic_model.links
    )
    dh_rows = "\n".join(
        "| {joint_id} | {a:g} | {alpha:g} | {d:g} | {theta:g} | {joint_type} | {variable} |".format(
            joint_id=param.joint_id,
            a=param.a,
            alpha=param.alpha,
            d=param.d,
            theta=param.theta,
            joint_type=param.joint_type,
            variable=param.variable or "",
        )
        for param in kinematic_model.dh_params
    )
    assumption_lines = "\n".join(f"- {item}" for item in assumptions) or "- 无"
    warning_lines = "\n".join(f"- {item}" for item in warnings) or "- 无"
    model_assumption_lines = "\n".join(
        f"- {item}" for item in kinematic_model.assumptions
    ) or "- 无"
    scale_factor = "无" if kinematics.scale_factor is None else f"{kinematics.scale_factor:g}"
    profile_name = kinematics.profile_name or "无"
    layout_source = layout.metadata.get("layout_source", "unknown")
    robot_family = layout.metadata.get("robot_family", "unknown")
    layout_confidence = layout.metadata.get("layout_decision_confidence", "unknown")
    layout_template = layout.metadata.get("layout_template", "tabletop_serial_arm")
    layout_assumption_lines = "\n".join(f"- {item}" for item in layout.assumptions) or "- 无"
    layout_warning_lines = "\n".join(f"- {item}" for item in layout.warnings) or "- 无"
    structure_plan_lines = _structure_plan_document_section(layout)
    joint_morphology_rows = _joint_morphology_rows(layout)
    link_morphology_rows = _link_morphology_rows(layout)
    graph_summary = _constraint_graph_summary(layout)

    return (
        "# 机器人设计需求文档\n\n"
        "## 设计需求\n"
        f"- 原始需求: {requirement.task}\n"
        f"- 推荐构型: {requirement.preferred_architecture}\n"
        f"- 工作空间: {requirement.workspace}\n"
        f"- 工作环境: {requirement.environment}\n"
        f"- 安装方式: {requirement.mounting}\n\n"
        "## 约束\n"
        "| 约束项 | 值 | 单位 |\n"
        "|---|---:|---|\n"
        f"| DoF | {requirement.dof} | - |\n"
        f"| Payload | {requirement.payload:g} | {requirement.payload_unit} |\n"
        f"| Reach | {requirement.reach:g} | {requirement.reach_unit} |\n\n"
        "## Joint Spec\n"
        "| Joint | Type | Parent Link | Child Link | Limit(rad) |\n"
        "|---|---|---|---|---|\n"
        f"{joint_rows}\n\n"
        "## Link Spec\n"
        "| Link | Length | Unit | Parent Joint | Material |\n"
        "|---|---:|---|---|---|\n"
        f"{link_rows}\n\n"
        "## DH 模型\n"
        f"- Source mode: {kinematics.source_mode}\n"
        f"- Source summary: {kinematics.source_summary}\n"
        f"- Profile: {profile_name}\n"
        f"- Scale factor: {scale_factor}\n"
        f"- Convention: {kinematic_model.convention}\n\n"
        "| Joint | a(mm) | alpha(rad) | d(mm) | theta(rad) | Type | Variable |\n"
        "|---|---:|---:|---:|---:|---|---|\n"
        f"{dh_rows}\n\n"
        "## MechanicalLayout 决策\n"
        f"- Layout source: {layout_source}\n"
        f"- Robot family: {robot_family}\n"
        f"- Decision confidence: {layout_confidence}\n"
        f"- Template: {layout_template}\n"
        f"- MVP simplification: DH/FK frames are not used as direct CAD mate transforms.\n\n"
        "### Structure Plan\n"
        f"{structure_plan_lines}\n\n"
        "### Joint Morphology\n"
        "| Joint | Morphology | Reason | Confidence | Source signals |\n"
        "|---|---|---|---:|---|\n"
        f"{joint_morphology_rows}\n\n"
        "### Link Morphology\n"
        "| Link | Morphology | Primitive | Reason | Confidence | Source signals |\n"
        "|---|---|---|---|---:|---|\n"
        f"{link_morphology_rows}\n\n"
        "### Layout 假设\n"
        f"{layout_assumption_lines}\n\n"
        "### Layout 警告\n"
        f"{layout_warning_lines}\n\n"
        "## Assembly Constraint Graph\n"
        f"- Full assembly solve gate: {graph_summary.full_assembly_gate}\n"
        f"- Summary: {graph_summary.final_answer_line}\n\n"
        "| Local subassembly | Parts | Edges | Components | Cycles | Isolated parts | Dense pairs | Status |\n"
        "|---|---|---:|---:|---:|---|---|---|\n"
        f"{graph_summary.table_rows}\n\n"
        "## 假设\n"
        f"{assumption_lines}\n\n"
        "## 运动学模型假设\n"
        f"{model_assumption_lines}\n\n"
        "## 警告\n"
        f"{warning_lines}\n"
    )


def _joint_morphology_rows(layout: MechanicalLayout) -> str:
    return "\n".join(
        "| {id} | {morphology} | {reason} | {confidence} | {signals} |".format(
            id=joint.id,
            morphology=joint.metadata.get("morphology") or "unknown",
            reason=_metadata_text(joint.metadata.get("layout_decision"), "reason"),
            confidence=_metadata_text(joint.metadata.get("layout_decision"), "confidence"),
            signals=_metadata_signals(joint.metadata.get("layout_decision")),
        )
        for joint in layout.joints
    )


def _structure_plan_document_section(layout: MechanicalLayout) -> str:
    plan = layout.metadata.get("structure_plan")
    if not isinstance(plan, dict):
        return "- Structure plan: 未使用\n"

    stations = [
        f"{item.get('id')}({item.get('role')})"
        for item in plan.get("stations") or []
        if isinstance(item, dict)
    ]
    axes = [
        f"{item.get('joint_id')}:{item.get('role')} axis={tuple(item.get('direction') or [])}"
        for item in plan.get("joint_axes") or []
        if isinstance(item, dict)
    ]
    routes = [
        (
            f"{item.get('link_id')}:{item.get('route_type')} "
            f"{item.get('from_station_id')}->{item.get('to_station_id')}"
        )
        for item in plan.get("link_routes") or []
        if isinstance(item, dict)
    ]
    return (
        f"- Structure plan: {plan.get('name', 'unknown')}\n"
        f"- Structure source: {plan.get('source', 'unknown')}\n"
        f"- Structure family: {plan.get('family', 'unknown')}\n"
        f"- Stations: {', '.join(stations) if stations else '无'}\n"
        f"- Joint axes: {', '.join(axes) if axes else '无'}\n"
        f"- Link routes: {', '.join(routes) if routes else '无'}\n"
    )


def _link_morphology_rows(layout: MechanicalLayout) -> str:
    return "\n".join(
        "| {id} | {morphology} | {primitive} | {reason} | {confidence} | {signals} |".format(
            id=link.id,
            morphology=link.metadata.get("morphology") or "unknown",
            primitive=link.metadata.get("primitive_type") or "unknown",
            reason=_metadata_text(link.metadata.get("layout_decision"), "reason"),
            confidence=_metadata_text(link.metadata.get("layout_decision"), "confidence"),
            signals=_metadata_signals(link.metadata.get("layout_decision")),
        )
        for link in layout.links
    )


def _metadata_text(metadata: object, key: str) -> str:
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get(key)
    if value is None:
        return ""
    return str(value).replace("|", "/")


def _metadata_signals(metadata: object) -> str:
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get("source_signals")
    if not isinstance(value, list):
        return ""
    return ", ".join(str(item).replace("|", "/") for item in value)


def _metadata_from_kinematics_result(
    result: KinematicsAgentResult,
) -> _KinematicsMetadata:
    return _KinematicsMetadata(
        source_mode=result.source_mode,
        source_summary=result.source_summary,
        profile_name=result.profile_name,
        target_reach=result.target_reach,
        target_reach_unit=result.target_reach_unit,
        scale_factor=result.scale_factor,
        assumptions=list(result.assumptions),
        warnings=list(result.warnings),
    )


def _kinematics_reach_mm(kinematics: _KinematicsMetadata) -> float | None:
    if kinematics.target_reach is None:
        return None
    return _to_mm(float(kinematics.target_reach), kinematics.target_reach_unit)


def _output_path(
    params: dict[str, Any],
    *,
    workspace_root: str | Path | None = None,
    session_id: str = DEFAULT_SESSION_DIR,
) -> Path:
    root = Path(workspace_root or Path.cwd())
    session_dir = _safe_session_dir(session_id)

    raw_output_dir = params.get("output_dir")
    if raw_output_dir:
        output_dir = Path(str(raw_output_dir))
        if not output_dir.is_absolute():
            output_dir = root / session_dir / output_dir
    else:
        output_dir = root / session_dir

    filename = str(params.get("export_filename") or "整机.step").strip() or "整机.step"
    if not filename.lower().endswith((".step", ".stp")):
        filename = f"{filename}.step"
    return output_dir / filename


def _production_assembly_source(params: dict[str, Any]) -> str:
    value = str(params.get("production_assembly_source") or "full_semantic_solve").strip()
    if value in {"fixed_layout_pose", "full_semantic_solve"}:
        return value
    return "full_semantic_solve"


def _cad_backend(params: dict[str, Any]) -> str:
    value = str(params.get("cad_backend") or "source_joint").strip()
    if value in {"cadquery", "source_joint"}:
        return value
    return "source_joint"


def _safe_session_dir(session_id: str | None) -> str:
    raw = str(session_id or "").strip()
    if not raw:
        return DEFAULT_SESSION_DIR
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
    return safe or DEFAULT_SESSION_DIR


def _number_param(params: dict[str, Any], key: str) -> float | None:
    value = params.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _parse_dof(text: str) -> int | None:
    patterns = [
        r"(\d+)\s*(?:dof|DOF|自由度)",
        r"(\d+)\s*(?:轴|关节)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def _parse_length_mm(text: str) -> float | None:
    patterns = [
        r"(?:工作空间|工作半径|半径|臂展|reach|radius)[^\d]*(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)?",
        r"(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)\s*(?:工作空间|工作半径|半径|臂展|reach|radius)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2) or "mm"
            return _to_mm(value, unit)
    return None


def _parse_payload_g(text: str) -> float | None:
    patterns = [
        r"(?:负载|载荷|payload)[^\d]*(\d+(?:\.\d+)?)\s*(kg|千克|g|克)?",
        r"(\d+(?:\.\d+)?)\s*(kg|千克|g|克)\s*(?:负载|载荷|payload|物体)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2) or "g"
            if unit.lower() == "kg" or unit == "千克":
                return value * 1000.0
            return value
    return None


def _to_mm(value: float, unit: str) -> float:
    normalized = unit.lower()
    if normalized in {"mm", "毫米"}:
        return value
    if normalized in {"cm", "厘米"}:
        return value * 10.0
    if normalized in {"m", "米"}:
        return value * 1000.0
    return value
