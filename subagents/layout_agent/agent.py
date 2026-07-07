"""LangGraph layout morphology subagent."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from typing import Any

from langgraph.graph import END, StateGraph

from cathy.llm_errors import AgentFailure, LLMCallError
from cathy.subagent import Subagent, SubagentResult
from robot_sdk.kinematics.dh import estimate_reach
from robot_sdk.layout.debug import build_layout_debug_report
from robot_sdk.layout.mechanical_layout import (
    build_tabletop_serial_mechanical_layout_from_model,
)
from robot_sdk.structure import (
    RobotStructurePlan,
    build_generic_6axis_cobot_structure_plan,
)
from robot_sdk.types import KinematicModel, MechanicalLayout
from robot_sdk.validation.structure import validate_robot_structure

from .prompt import LAYOUT_DECISION_SYSTEM, build_layout_decision_user_prompt
from .schema import (
    InterfaceMorphologyDecision,
    JointMorphologyDecision,
    LayoutAgentResult,
    LayoutAgentState,
    LayoutDecision,
    LocalSubassemblyDecision,
    LinkMorphologyDecision,
)


class LayoutAgent(Subagent):
    """Choose mechanical morphology before CAD generation."""

    name = "layout_agent"
    description = (
        "机器人机械布局形态子 agent。用于在已有 KinematicModel/DH 后，"
        "根据机器人族、DH 信号和 layout debug 信息，决定 joint/link/interface "
        "的机械形态标签，并在高自由度机器人场景输出 RobotStructurePlan。"
        "它不生成 CadQuery/STEP，只输出可追溯 LayoutDecision / structure_plan，"
        "供 robot_design_agent 和 robot_sdk 应用。"
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["request"],
        "properties": {
            "request": {
                "type": "string",
                "minLength": 1,
                "description": "布局形态分析需求文本；完整 CAD/STEP 建模仍应调用 robot_design_agent。",
            },
            "profile_name": {
                "type": ["string", "null"],
                "description": "可选：运动学 profile 名称，例如 ur3e。",
            },
            "source_mode": {
                "type": ["string", "null"],
                "description": "可选：运动学来源模式，例如 exact_profile/scaled_profile。",
            },
            "reach_mm": {
                "type": ["number", "null"],
                "description": "可选：已解析或运动学 agent 输出的目标工作半径，单位 mm。",
            },
        },
    }

    def __init__(self, *, llm: Any | None = None) -> None:
        self._llm = llm
        self._graph = self._build_graph()

    def run(self, params: dict[str, Any]) -> SubagentResult:
        try:
            result = self.build_result(params)
        except Exception as exc:
            failure = _subagent_failure(exc, stage="layout_agent")
            return SubagentResult(
                final_answer=f"[layout_agent] 执行失败: {type(exc).__name__}: {exc}",
                finished=False,
                status="failed",
                failure=failure,
                trace=[
                    {
                        "type": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                ],
            )
        return SubagentResult(
            final_answer=_format_final_answer(result),
            finished=True,
            status=result.status,
            failure=result.failure,
            trace=result.trace,
        )

    def build_result(self, params: dict[str, Any]) -> LayoutAgentResult:
        request = str(params.get("request") or "").strip()
        if not request:
            raise ValueError("request 不能为空")
        model = params.get("kinematic_model")
        if not isinstance(model, KinematicModel):
            raise ValueError("layout_agent.build_result requires kinematic_model")

        initial: LayoutAgentState = {
            "user_request": request,
            "kinematic_model": model,
            "profile_name": _none_if_blank(params.get("profile_name")),
            "source_mode": _none_if_blank(params.get("source_mode")),
            "scale_factor": _optional_float(params.get("scale_factor")),
            "reach_mm": _optional_float(params.get("reach_mm")),
            "trace": [],
        }
        final_state = self._graph.invoke(initial)
        result = final_state.get("result")
        if not isinstance(result, LayoutAgentResult):
            raise RuntimeError("layout_agent graph did not produce a result")
        return result

    def _build_graph(self):
        graph = StateGraph(LayoutAgentState)
        graph.add_node("prepare", self._node_prepare)
        graph.add_node("decision", self._node_decision)
        graph.add_node("finalize", self._node_finalize)
        graph.set_entry_point("prepare")
        graph.add_edge("prepare", "decision")
        graph.add_edge("decision", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    def _node_prepare(self, state: LayoutAgentState) -> LayoutAgentState:
        layout = build_tabletop_serial_mechanical_layout_from_model(
            state["kinematic_model"]
        )
        report = build_layout_debug_report(layout)
        trace = list(state.get("trace") or [])
        trace.append(
            {
                "type": "prepare_layout_context",
                "joints": [joint.id for joint in layout.joints],
                "links": [link.id for link in layout.links],
                "interfaces": [interface.id for interface in layout.interfaces],
                "debug_warnings": list(report.warnings),
            }
        )
        return {**state, "preliminary_layout": layout, "trace": trace}

    def _node_decision(self, state: LayoutAgentState) -> LayoutAgentState:
        layout = state["preliminary_layout"]
        fallback = _fallback_decision(
            layout,
            request=state["user_request"],
            profile_name=state.get("profile_name"),
            source_mode=state.get("source_mode"),
        )
        fallback_structure_plan = _fallback_structure_plan(
            layout,
            model=state["kinematic_model"],
            decision=fallback,
            request=state["user_request"],
            reach_mm=state.get("reach_mm"),
            profile_name=state.get("profile_name"),
            source_mode=state.get("source_mode"),
        )
        raw: str | None = None
        decision = fallback
        structure_plan = fallback_structure_plan
        decision_error: str | None = None
        failure: AgentFailure | None = None

        if self._llm is not None:
            try:
                report = build_layout_debug_report(layout)
                raw = _llm_text(
                    self._llm,
                    LAYOUT_DECISION_SYSTEM,
                    build_layout_decision_user_prompt(
                        user_request=state["user_request"],
                        profile_name=state.get("profile_name"),
                        source_mode=state.get("source_mode"),
                        scale_factor=state.get("scale_factor"),
                        model_summary=_model_summary(state["kinematic_model"]),
                        debug_summary=_debug_summary(report),
                    ),
                )
                parsed, parsed_structure_plan = _parse_decision_payload(raw)
                decision = _complete_decision(parsed, fallback)
                structure_plan = parsed_structure_plan or fallback_structure_plan
            except Exception as exc:  # LLM decision is advisory; SDK fallback keeps flow alive.
                decision_error = f"{type(exc).__name__}: {exc}"
                failure = _subagent_failure(exc, stage="layout_decision")
                decision = replace(
                    fallback,
                    warnings=[
                        *fallback.warnings,
                        f"layout_agent LLM decision failed; used rule fallback ({decision_error}).",
                    ],
                )
                structure_plan = fallback_structure_plan

        structure_validation = (
            validate_robot_structure(
                kinematic_model=state["kinematic_model"],
                structure_plan=structure_plan,
                production_cad=True,
            )
            if structure_plan is not None
            else None
        )

        trace = list(state.get("trace") or [])
        trace.append(
            {
                "type": "layout_decision",
                "layout_source": decision.layout_source,
                "robot_family": decision.robot_family,
                "confidence": decision.confidence,
                "raw": raw,
                "error": decision_error,
                "decision": decision.to_dict(),
                "structure_plan": structure_plan.to_dict()
                if structure_plan is not None
                else None,
                "structure_validation": {
                    "ok": structure_validation.ok,
                    "errors": [issue.code for issue in structure_validation.errors],
                    "warnings": [issue.code for issue in structure_validation.warnings],
                    "passes": [issue.code for issue in structure_validation.passes],
                }
                if structure_validation is not None
                else None,
            }
        )
        return {
            **state,
            "decision": decision,
            "structure_plan": structure_plan,
            "raw_decision": raw,
            "decision_error": decision_error,
            "status": "degraded" if decision_error else "ok",
            "failure": failure,
            "trace": trace,
        }

    def _node_finalize(self, state: LayoutAgentState) -> LayoutAgentState:
        result = LayoutAgentResult(
            decision=state["decision"],
            structure_plan=state.get("structure_plan"),
            trace=[*list(state.get("trace") or []), {"type": "finalize"}],
            status=str(state.get("status") or "ok"),
            failure=state.get("failure"),
        )
        return {**state, "result": result, "trace": result.trace}


def _fallback_decision(
    layout: MechanicalLayout,
    *,
    request: str,
    profile_name: str | None,
    source_mode: str | None,
) -> LayoutDecision:
    if _is_ur_style(request=request, profile_name=profile_name) and len(layout.links) >= 6:
        return _ur_style_decision(layout, profile_name=profile_name, source_mode=source_mode)
    return _generic_decision(layout)


def _ur_style_decision(
    layout: MechanicalLayout,
    *,
    profile_name: str | None,
    source_mode: str | None,
) -> LayoutDecision:
    joint_map = {
        "J1": "base_yaw_joint",
        "J2": "shoulder_joint",
        "J3": "elbow_joint",
        "J4": "wrist_pitch_joint",
        "J5": "wrist_roll_joint",
        "J6": "tool_flange_joint",
    }
    link_map = {
        "L1": "base_column",
        "L2": "upper_arm_link",
        "L3": "forearm_link",
        "L4": "wrist1_offset_housing",
        "L5": "wrist2_elbow_cylinder",
        "L6": "wrist3_tool_flange",
    }
    source = _signals(profile_name=profile_name, source_mode=source_mode)
    return LayoutDecision(
        layout_source="layout_agent",
        robot_family="ur_style_6axis_cobot",
        confidence=0.86,
        joints=[
            JointMorphologyDecision(
                joint_id=joint.id,
                morphology=joint_map.get(joint.id, "generic_revolute_joint"),
                reason=_joint_reason(joint.id, joint_map.get(joint.id)),
                confidence=0.86 if joint.id in joint_map else 0.62,
                source_signals=source,
            )
            for joint in layout.joints
        ],
        links=[
            LinkMorphologyDecision(
                link_id=link.id,
                morphology=link_map.get(link.id, _generic_link_morphology(link)),
                reason=_link_reason(link.id, link_map.get(link.id), link),
                confidence=0.88 if link.id in link_map else 0.62,
                source_signals=[
                    *source,
                    f"primitive_type={link.metadata.get('primitive_type')}",
                ],
            )
            for link in layout.links
        ],
        interfaces=[
            InterfaceMorphologyDecision(
                interface_id=interface.id,
                morphology=_ur_interface_morphology(interface.id),
                reason=_interface_reason(interface.id),
                confidence=0.8,
                source_signals=source,
            )
            for interface in layout.interfaces
        ],
        subassemblies=_ur_style_subassembly_decisions(layout, source=source),
        assumptions=[
            "UR-style 6-axis morphology is selected as a mechanical layout intent, not as a direct DH-to-solid transform.",
        ],
        warnings=[],
    )


def _generic_decision(layout: MechanicalLayout) -> LayoutDecision:
    return LayoutDecision(
        layout_source="layout_agent",
        robot_family="generic_serial_arm",
        confidence=0.62,
        joints=[
            JointMorphologyDecision(
                joint_id=joint.id,
                morphology="generic_revolute_joint",
                reason="No known robot-family morphology matched; keep generic revolute joint housing.",
                confidence=0.62,
                source_signals=[
                    "fallback=generic_serial_arm",
                    "layout_agent_decision_mode=rule_fallback",
                ],
            )
            for joint in layout.joints
        ],
        links=[
            LinkMorphologyDecision(
                link_id=link.id,
                morphology=_generic_link_morphology(link),
                reason=(
                    "No known robot-family morphology matched; mapped existing "
                    f"primitive_type={link.metadata.get('primitive_type')} to generic morphology."
                ),
                confidence=0.62,
                source_signals=[
                    "layout_agent_decision_mode=rule_fallback",
                    f"primitive_type={link.metadata.get('primitive_type')}",
                ],
            )
            for link in layout.links
        ],
        interfaces=[
            InterfaceMorphologyDecision(
                interface_id=interface.id,
                morphology="generic_flange",
                reason="Generic serial-arm fallback keeps flange interface semantics.",
                confidence=0.62,
                source_signals=[
                    "fallback=generic_serial_arm",
                    "layout_agent_decision_mode=rule_fallback",
                ],
            )
            for interface in layout.interfaces
        ],
        subassemblies=_generic_subassembly_decisions(layout),
        assumptions=[
            "No robot-family-specific morphology was selected; generic primitive metadata drives coarse CAD.",
        ],
        warnings=[],
    )


def _fallback_structure_plan(
    layout: MechanicalLayout,
    *,
    model: KinematicModel,
    decision: LayoutDecision,
    request: str,
    reach_mm: float | None,
    profile_name: str | None,
    source_mode: str | None,
) -> RobotStructurePlan | None:
    if len(model.joints) < 6:
        return None

    reach = _select_structure_reach_mm(
        request=request,
        explicit_reach_mm=reach_mm,
        model=model,
        layout=layout,
    )
    if reach <= 0:
        reach = 500.0
    plan = build_generic_6axis_cobot_structure_plan(
        reach_mm=reach,
        name=f"{decision.robot_family}_structure",
    )
    return replace(
        plan,
        family=decision.robot_family,
        source="layout_agent",
        assumptions=[
            *plan.assumptions,
            (
                "layout_agent generated this RobotStructurePlan so high-DOF CAD "
                "does not use the legacy planar tabletop skeleton."
            ),
        ],
        metadata={
            **plan.metadata,
            "reach_source": _structure_reach_source(
                request=request,
                explicit_reach_mm=reach_mm,
                model=model,
            ),
            "layout_source": decision.layout_source,
            "robot_family": decision.robot_family,
            "profile_name": profile_name,
            "source_mode": source_mode,
            "generated_by": "layout_agent",
        },
    )


def _select_structure_reach_mm(
    *,
    request: str,
    explicit_reach_mm: float | None,
    model: KinematicModel,
    layout: MechanicalLayout,
) -> float:
    """Choose the semantic structure-plan reach before falling back to DH extent."""

    if explicit_reach_mm is not None and explicit_reach_mm > 0:
        return explicit_reach_mm
    requested_reach = _parse_length_mm(request)
    if requested_reach is not None and requested_reach > 0:
        return requested_reach
    if model.dh_params:
        return estimate_reach(model.dh_params)
    return _layout_reach(layout)


def _structure_reach_source(
    *,
    request: str,
    explicit_reach_mm: float | None,
    model: KinematicModel,
) -> str:
    if explicit_reach_mm is not None and explicit_reach_mm > 0:
        return "explicit_reach_mm"
    if _parse_length_mm(request) is not None:
        return "request_text"
    if model.dh_params:
        return "dh_estimate"
    return "layout_extent"


def _layout_reach(layout: MechanicalLayout) -> float:
    xs = [frame.transform.translation[0] for frame in layout.frames]
    ys = [frame.transform.translation[1] for frame in layout.frames]
    zs = [frame.transform.translation[2] for frame in layout.frames]
    if not xs:
        return 0.0
    return max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def _parse_length_mm(text: str) -> float | None:
    patterns = [
        r"(?:工作空间|工作半径|半径|臂展|reach|radius)[^\d]*(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)?",
        r"(\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)\s*(?:工作空间|工作半径|半径|臂展|reach|radius)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _to_mm(float(match.group(1)), match.group(2) or "mm")
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


def _complete_decision(parsed: LayoutDecision, fallback: LayoutDecision) -> LayoutDecision:
    joints = _merge_by_id(
        parsed.joints,
        fallback.joints,
        parsed_key=lambda item: item.joint_id,
    )
    links = _merge_by_id(
        parsed.links,
        fallback.links,
        parsed_key=lambda item: item.link_id,
    )
    interfaces = _merge_by_id(
        parsed.interfaces,
        fallback.interfaces,
        parsed_key=lambda item: item.interface_id,
    )
    subassemblies = _merge_subassemblies(parsed.subassemblies, fallback.subassemblies)
    warnings = list(parsed.warnings)
    if (
        len(joints) != len(parsed.joints)
        or len(links) != len(parsed.links)
        or len(subassemblies) != len(parsed.subassemblies)
    ):
        warnings.append("layout_agent decision was normalized to match SDK layout ids.")
    return LayoutDecision(
        layout_source="layout_agent",
        robot_family=parsed.robot_family or fallback.robot_family,
        confidence=parsed.confidence if parsed.confidence > 0 else fallback.confidence,
        joints=joints,
        links=links,
        interfaces=interfaces,
        subassemblies=subassemblies,
        assumptions=[*parsed.assumptions, *fallback.assumptions],
        warnings=warnings,
    )


def _merge_by_id(
    parsed_items: list[Any],
    fallback_items: list[Any],
    *,
    parsed_key,
) -> list[Any]:
    parsed_by_id = {parsed_key(item): item for item in parsed_items}
    merged = []
    for fallback in fallback_items:
        key = parsed_key(fallback)
        merged.append(parsed_by_id.get(key, fallback))
    return merged


def _merge_subassemblies(
    parsed_items: list[LocalSubassemblyDecision],
    fallback_items: list[LocalSubassemblyDecision],
) -> list[LocalSubassemblyDecision]:
    if not parsed_items:
        return list(fallback_items)
    known_parts = {part_id for item in fallback_items for part_id in item.part_ids}
    merged: list[LocalSubassemblyDecision] = []
    seen: set[str] = set()
    for item in [*parsed_items, *fallback_items]:
        if item.name in seen:
            continue
        if not set(item.part_ids).issubset(known_parts):
            continue
        merged.append(item)
        seen.add(item.name)
    return merged


def _generic_link_morphology(link) -> str:
    primitive = link.metadata.get("primitive_type")
    if primitive == "offset_link":
        return "generic_offset_link"
    if primitive == "elbow_link":
        return "generic_elbow_link"
    if primitive == "wrist_spacer":
        return "generic_wrist_spacer"
    return "generic_straight_link"


def _is_ur_style(*, request: str, profile_name: str | None) -> bool:
    text = f"{request} {profile_name or ''}".lower()
    normalized = re.sub(r"[^a-z0-9]+", "", text)
    return "ur3e" in normalized or "universalrobots" in normalized


def _signals(*, profile_name: str | None, source_mode: str | None) -> list[str]:
    signals = [
        "family=ur_style_6axis_cobot",
        "layout_agent_decision_mode=rule_fallback",
    ]
    if profile_name:
        signals.append(f"profile_name={profile_name}")
    if source_mode:
        signals.append(f"source_mode={source_mode}")
    return signals


def _joint_reason(joint_id: str, morphology: str | None) -> str:
    if morphology:
        return f"{joint_id} matches the UR-style 6-axis cobot joint order as {morphology}."
    return f"{joint_id} is outside the known UR-style six-axis set; keep generic revolute joint."


def _link_reason(link_id: str, morphology: str | None, link) -> str:
    primitive = link.metadata.get("primitive_type")
    if morphology:
        return (
            f"{link_id} matches the UR-style 6-axis cobot link order as {morphology}; "
            f"existing primitive_type={primitive} remains a lower-level CAD hint."
        )
    return f"{link_id} has no UR-style role mapping; use generic primitive_type={primitive}."


def _ur_interface_morphology(interface_id: str) -> str:
    if interface_id.startswith("base_to_J1"):
        return "base_mount_face"
    if "end_effector" in interface_id:
        return "tool_mount_flange"
    if any(item in interface_id for item in ("L3_to_J4", "L4_to_J5", "L5_to_J6")):
        return "wrist_cross_axis_interface"
    if re.match(r"J\d+_to_L\d+_", interface_id):
        return "actuator_flange"
    if re.match(r"L\d+_to_J\d+_", interface_id):
        return "arm_flange"
    return "generic_flange"


def _interface_reason(interface_id: str) -> str:
    morphology = _ur_interface_morphology(interface_id)
    return f"{interface_id} is classified as {morphology} for UR-style assembly intent."


def _ur_style_subassembly_decisions(
    layout: MechanicalLayout,
    *,
    source: list[str],
) -> list[LocalSubassemblyDecision]:
    decisions = _chain_tail_subassembly_decisions(layout, source=source)
    if len(layout.joints) >= 6 and len(layout.links) >= 6:
        decisions.append(
            LocalSubassemblyDecision(
                name="wrist_group_J4_to_end_effector",
                part_ids=["J4", "L4", "J5", "L5", "J6", "L6", "end_effector"],
                anchor_part_id="J4",
                role="wrist_group",
                reason=(
                    "UR-style wrist group should be solved as a local cluster "
                    "because J4/J5/J6 and wrist links carry compact cross-axis interfaces."
                ),
                confidence=0.86,
                source_signals=[*source, "local_solve_candidate=ur_wrist_group"],
            )
        )
    return decisions


def _generic_subassembly_decisions(layout: MechanicalLayout) -> list[LocalSubassemblyDecision]:
    return _chain_tail_subassembly_decisions(
        layout,
        source=["layout_agent_decision_mode=rule_fallback"],
    )


def _chain_tail_subassembly_decisions(
    layout: MechanicalLayout,
    *,
    source: list[str],
) -> list[LocalSubassemblyDecision]:
    decisions: list[LocalSubassemblyDecision] = []
    if layout.joints and layout.links:
        last_joint = layout.joints[-1].id
        last_link = layout.links[-1].id
        decisions.append(
            LocalSubassemblyDecision(
                name=f"{last_joint}_{last_link}_end_effector",
                part_ids=[last_joint, last_link, "end_effector"],
                anchor_part_id=last_joint,
                role="terminal_tool",
                reason="Terminal joint, final link, and end effector form the first local solve candidate.",
                confidence=0.78,
                source_signals=[*source, "local_solve_candidate=terminal_tool"],
            )
        )
    chain_start = max(len(layout.joints) - 5, -1)
    for joint_index in range(len(layout.joints) - 2, chain_start, -1):
        from_joint = layout.joints[joint_index].id
        link = layout.links[joint_index].id
        to_joint = layout.joints[joint_index + 1].id
        decisions.append(
            LocalSubassemblyDecision(
                name=f"{from_joint}_{link}_{to_joint}",
                part_ids=[from_joint, link, to_joint],
                anchor_part_id=from_joint,
                role="joint_link_joint",
                reason=(
                    f"{from_joint}-{link}-{to_joint} is a local joint-link-joint "
                    "assembly candidate near the chain tail."
                ),
                confidence=0.74,
                source_signals=[*source, "local_solve_candidate=joint_link_joint"],
            )
        )
    return decisions


def _model_summary(model: KinematicModel) -> dict[str, Any]:
    return {
        "convention": model.convention,
        "dof": len(model.joints),
        "dh_params": [
            {
                "joint_id": item.joint_id,
                "a": item.a,
                "alpha": item.alpha,
                "d": item.d,
                "theta": item.theta,
                "joint_type": item.joint_type,
            }
            for item in model.dh_params
        ],
    }


def _debug_summary(report) -> dict[str, Any]:
    return {
        "chain_segments": [
            {
                "segment_id": item.segment_id,
                "link_id": item.link_id,
                "link_body_direction": item.link_body_direction,
                "next_joint_axis_direction": item.next_joint_axis_direction,
                "axis_mismatch_deg": item.axis_mismatch_deg,
                "warnings": item.warnings,
            }
            for item in report.chain_segments
        ],
        "links": [asdict(item) for item in report.links],
        "warnings": list(report.warnings),
    }


def _llm_text(llm: Any, system: str, user: str) -> str:
    response = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    return (response.choices[0].message.content or "").strip()


def _parse_decision_payload(text: str) -> tuple[LayoutDecision, RobotStructurePlan | None]:
    obj = _parse_json_object(text)
    if obj is None:
        raise ValueError("LLM layout decision was not a valid JSON object")
    structure_plan = None
    if isinstance(obj.get("structure_plan"), dict):
        structure_plan = RobotStructurePlan.from_dict(obj["structure_plan"])
    return LayoutDecision.from_dict(obj), structure_plan


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _format_final_answer(result: LayoutAgentResult) -> str:
    decision = result.decision
    link_lines = "\n".join(
        f"- {item.link_id}: {item.morphology} ({item.confidence:g})"
        for item in decision.links
    )
    warnings = "\n".join(f"- {item}" for item in decision.warnings) or "- 无"
    return (
        "[layout_agent] 机械布局形态决策已生成。\n\n"
        f"- layout_source: {decision.layout_source}\n"
        f"- robot_family: {decision.robot_family}\n"
        f"- confidence: {decision.confidence:g}\n\n"
        "## Link Morphology\n"
        f"{link_lines}\n\n"
        "## 警告\n"
        f"{warnings}"
    )


def _subagent_failure(exc: Exception, *, stage: str) -> AgentFailure:
    if isinstance(exc, LLMCallError):
        return exc.failure
    return AgentFailure(
        stage=stage,
        error_type=type(exc).__name__,
        reason="unknown",
        retryable=False,
        message=str(exc),
    )


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


__all__ = ["LayoutAgent"]
