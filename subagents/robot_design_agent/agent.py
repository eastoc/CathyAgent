"""RobotDesignAgent：固定 MVP 流程的机器人 CAD 总入口。

第一版不拆 requirement_agent / kinematics_agent / layout_agent / cad_agent。
它只做一条确定性链路：

    requirement text
    -> simple requirement parsing
    -> simple serial DH model
    -> MechanicalLayout
    -> deterministic CadQuery assembly placement
    -> STEP export
    -> validation report
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cathy.subagent import Subagent, SubagentResult
from robot_sdk.cad.cq_assembly import build_cadquery_assembly
from robot_sdk.cad.export import CadQueryExportResult, export_step
from robot_sdk.layout.mechanical_layout import build_tabletop_serial_mechanical_layout
from robot_sdk.types import (
    DHParam,
    JointSpec,
    KinematicModel,
    LinkSpec,
    MechanicalLayout,
    RobotRequirement,
)
from robot_sdk.validation.basic import RobotDesignValidationReport, validate_robot_design


DEFAULT_DOF = 4
DEFAULT_REACH_MM = 400.0
DEFAULT_PAYLOAD_G = 500.0
DEFAULT_SESSION_DIR = "default"


@dataclass(frozen=True)
class _ParsedRequirement:
    requirement: RobotRequirement
    assumptions: list[str]
    warnings: list[str]


class RobotDesignAgent(Subagent):
    """固定流程的 Robot CAD MVP 总入口。"""

    name = "robot_design_agent"
    description = (
        "机器人 CAD 设计子 agent。用于把用户的机器人设计需求跑成一个 MVP 闭环："
        "需求解析、简单 DH/KinematicModel、MechanicalLayout、CadQuery 粗 CAD、"
        "Assembly.solve、STEP 导出和基础验证报告。第一版是固定流程，不拆多个子 agent。"
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["request"],
        "properties": {
            "request": {
                "type": "string",
                "minLength": 1,
                "description": "用户的机器人 CAD 设计需求文本，例如桌面 4DOF 机械臂、500g、工作半径 400mm。",
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
                "description": "可选：STEP 文件名，默认 robot_mvp.step。",
            },
            "solve": {
                "type": "boolean",
                "description": "是否调用 CadQuery Assembly.solve()，默认 false。MVP 默认使用确定性 layout frame 放置。",
            },
        },
    }

    def __init__(
        self,
        *,
        cq_module: Any | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self._cq_module = cq_module
        self._workspace_root = Path(workspace_root or Path.cwd())
        self._session_id = DEFAULT_SESSION_DIR

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

            kinematic_model = _build_simple_kinematic_model(requirement)
            trace.append(
                {
                    "type": "kinematic_model",
                    "kinematic_model": kinematic_model.to_dict(),
                }
            )

            layout = build_tabletop_serial_mechanical_layout_from_agent_model(kinematic_model)
            trace.append(
                {
                    "type": "mechanical_layout",
                    "units": layout.units,
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
            cad_result = build_cadquery_assembly(
                layout,
                cq_module=self._cq_module,
                solve=bool(params.get("solve", False)),
                raise_on_solve_error=False,
            )
            trace.append(
                {
                    "type": "cad_assembly",
                    "part_count": cad_result.part_count,
                    "constraint_count": cad_result.constraint_count,
                    "solved": cad_result.solved,
                    "solve_error": cad_result.solve_error,
                }
            )

            export_result: CadQueryExportResult | None = None
            if cad_result.solved or not bool(params.get("solve", False)):
                export_result = export_step(cad_result.assembly, output_path)
                trace.append(
                    {
                        "type": "export",
                        "path": str(export_result.path),
                        "exists": export_result.exists,
                        "size_bytes": export_result.size_bytes,
                    }
                )

            report = validate_robot_design(
                requirement=requirement,
                kinematic_model=kinematic_model,
                layout=layout,
                cad_result=cad_result,
                export_result=export_result,
                export_path=output_path if export_result is None else None,
            )
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
                    cad_result=cad_result,
                    export_result=export_result,
                    report=report,
                    assumptions=parsed.assumptions,
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


def build_tabletop_serial_mechanical_layout_from_agent_model(
    model: KinematicModel,
) -> MechanicalLayout:
    """Small wrapper kept for traceable MVP workflow naming."""

    return build_tabletop_serial_mechanical_layout(
        model.dh_params,
        convention=model.convention,
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
    cad_result: Any,
    export_result: CadQueryExportResult | None,
    report: RobotDesignValidationReport,
    assumptions: list[str],
) -> str:
    status = "通过" if report.ok else "未通过"
    export_line = (
        f"- STEP: {export_result.path} ({export_result.size_bytes} bytes)"
        if export_result and export_result.exists
        else f"- STEP: 未导出，目标路径 {output_path}"
    )
    assumptions_text = "\n".join(f"- {item}" for item in assumptions) or "- 无额外假设"
    error_text = "\n".join(f"- {msg}" for msg in report.messages("error")) or "- 无"
    warning_text = "\n".join(f"- {msg}" for msg in report.messages("warning")) or "- 无"
    link_lengths = ", ".join(f"{param.a:g}mm" for param in kinematic_model.dh_params)

    return (
        "[robot_design_agent] 固定 MVP 流程已完成。\n\n"
        "## 设计摘要\n"
        f"- 任务: {requirement.task}\n"
        f"- DOF: {requirement.dof}\n"
        f"- 负载: {requirement.payload:g} {requirement.payload_unit}\n"
        f"- 工作半径: {requirement.reach:g} {requirement.reach_unit}\n"
        f"- DH link lengths: {link_lengths}\n\n"
        "## CAD 结果\n"
        f"- Parts: {cad_result.part_count}\n"
        f"- Constraints: {cad_result.constraint_count}\n"
        f"- Assembly placement: {cad_result.metadata.get('placement_mode', 'unknown')}\n"
        f"- Assembly.solve: {'成功' if cad_result.solved else '未执行（MVP 默认不让欠约束 solver 重排零件）'}\n"
        f"{export_line}\n\n"
        "## MechanicalLayout\n"
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

    filename = str(params.get("export_filename") or "robot_mvp.step").strip() or "robot_mvp.step"
    if not filename.lower().endswith((".step", ".stp")):
        filename = f"{filename}.step"
    return output_dir / filename


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
        r"(?:工作半径|半径|臂展|reach|radius)[^\d]*(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)?",
        r"(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)\s*(?:工作半径|半径|臂展|reach|radius)",
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
