"""LangGraph kinematics subagent.

The LLM node only makes a structured route decision. Deterministic SDK nodes
then build the actual KinematicModel from profiles, scaling, or a fallback
template.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from cathy.subagent import Subagent, SubagentResult
from robot_sdk.kinematics.dh import estimate_reach
from robot_sdk.kinematics.profiles import require_profile
from robot_sdk.kinematics.scaling import (
    KinematicScalingRequest,
    build_profile_kinematic_model,
)
from robot_sdk.types import DHParam, JointSpec, KinematicModel, LinkSpec, RobotRequirement

from .prompt import KINEMATICS_DECISION_SYSTEM, build_decision_user_prompt
from .schema import (
    KinematicsAgentResult,
    KinematicsAgentState,
    KinematicsDecision,
)


DEFAULT_DOF = 4
DEFAULT_REACH_MM = 400.0


@dataclass(frozen=True)
class _ParsedRequirement:
    requirement: RobotRequirement
    assumptions: list[str]
    warnings: list[str]


class KinematicsAgent(Subagent):
    """Produce traceable robot kinematics before mechanical layout/CAD."""

    name = "kinematics_agent"
    description = (
        "机器人运动学子 agent，仅用于纯运动学/DH/FK/profile 选择问题，"
        "或被 robot_design_agent 在 CAD 主流程内部调用。"
        "如果用户要求 CAD、STEP、装配、整机、零件、导出或机器人 CAD 建模，"
        "不要直接调用本工具，应调用 robot_design_agent。"
        "本工具用于把机器人需求转换为可追溯 KinematicModel："
        "由 LLM decision schema 判断 exact_profile / scaled_profile / "
        "profile_like_template / search_verified / template_fallback，"
        "再调用 robot_sdk.kinematics 的 profile/scaling/DH 能力生成模型。"
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["request"],
        "properties": {
            "request": {
                "type": "string",
                "minLength": 1,
                "description": "用户的纯运动学/机器人 DH 需求文本；CAD/STEP/装配/导出/建模请求应交给 robot_design_agent。",
            },
            "dof": {
                "type": "integer",
                "minimum": 1,
                "maximum": 12,
                "description": "可选：结构化自由度，用于 fallback 或 decision 参考。",
            },
            "reach_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "可选：结构化目标 reach，单位 mm。",
            },
            "allow_search": {
                "type": "boolean",
                "description": "是否允许 decision 选择 search_verified；默认 true。首版仅保留接口。",
            },
        },
    }

    def __init__(self, *, llm: Any, tools: Any | None = None) -> None:
        self._llm = llm
        self._tools = tools
        self._graph = self._build_graph()

    def run(self, params: dict[str, Any]) -> SubagentResult:
        try:
            result = self.build_result(params)
        except Exception as exc:
            return SubagentResult(
                final_answer=f"[kinematics_agent] 执行失败: {type(exc).__name__}: {exc}",
                finished=False,
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
            trace=result.trace,
        )

    def build_result(self, params: dict[str, Any]) -> KinematicsAgentResult:
        request = str(params.get("request") or "").strip()
        if not request:
            raise ValueError("request 不能为空")

        parsed = _parse_requirement(request, params)
        initial: KinematicsAgentState = {
            "user_request": request,
            "requirement": parsed.requirement,
            "allow_search": bool(params.get("allow_search", True)),
            "assumptions": list(parsed.assumptions),
            "warnings": list(parsed.warnings),
            "trace": [
                {
                    "type": "requirement",
                    "requirement": parsed.requirement.to_dict(),
                    "assumptions": parsed.assumptions,
                    "warnings": parsed.warnings,
                }
            ],
        }
        final_state = self._graph.invoke(initial)
        result = final_state.get("result")
        if not isinstance(result, KinematicsAgentResult):
            raise RuntimeError("kinematics_agent graph did not produce a result")
        return result

    def _build_graph(self):
        graph = StateGraph(KinematicsAgentState)
        graph.add_node("decision", self._node_decision)
        graph.add_node("profile_model", self._node_profile_model)
        graph.add_node("search_model", self._node_search_model)
        graph.add_node("template_fallback", self._node_template_fallback)
        graph.add_node("validate", self._node_validate)
        graph.add_node("finalize", self._node_finalize)
        graph.set_entry_point("decision")
        graph.add_conditional_edges(
            "decision",
            self._route_after_decision,
            {
                "profile_model": "profile_model",
                "search_model": "search_model",
                "template_fallback": "template_fallback",
            },
        )
        graph.add_edge("profile_model", "validate")
        graph.add_edge("search_model", "validate")
        graph.add_edge("template_fallback", "validate")
        graph.add_edge("validate", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    def _node_decision(self, state: KinematicsAgentState) -> KinematicsAgentState:
        requirement = state["requirement"]
        user_prompt = build_decision_user_prompt(
            user_request=state["user_request"],
            requirement_summary=_requirement_summary(requirement),
            allow_search=bool(state.get("allow_search", True)),
        )
        raw = _llm_text(self._llm, KINEMATICS_DECISION_SYSTEM, user_prompt)
        decision = _parse_decision(raw)
        decision = _guard_profile_reach_decision(
            decision=decision,
            requirement=requirement,
            user_request=state["user_request"],
        )

        if not state.get("allow_search", True) and decision.source_mode == "search_verified":
            decision = KinematicsDecision(
                source_mode="template_fallback",
                rationale=(
                    "LLM selected search_verified, but allow_search=false; "
                    "falling back to template_fallback."
                ),
                warnings=[
                    *decision.warnings,
                    "Search was disabled for this request, so template fallback is used.",
                ],
            )

        trace = list(state.get("trace") or [])
        trace.append(
            {
                "type": "decision",
                "raw": raw,
                "decision": decision.to_dict(),
            }
        )
        return {**state, "decision": decision, "trace": trace}

    def _route_after_decision(self, state: KinematicsAgentState) -> str:
        decision = state["decision"]
        if decision.source_mode in {
            "exact_profile",
            "scaled_profile",
            "profile_like_template",
        }:
            return "profile_model"
        if decision.source_mode == "search_verified":
            return "search_model"
        return "template_fallback"

    def _node_profile_model(self, state: KinematicsAgentState) -> KinematicsAgentState:
        decision = state["decision"]
        scaling_result = build_profile_kinematic_model(
            KinematicScalingRequest(
                profile_name=str(decision.profile_name),
                mode=decision.source_mode,
                target_reach=decision.target_reach,
                target_reach_unit=decision.target_reach_unit,
            )
        )
        model = scaling_result.model
        assumptions = [
            *list(state.get("assumptions") or []),
            *decision.assumptions,
            *scaling_result.assumptions,
        ]
        warnings = [
            *list(state.get("warnings") or []),
            *decision.warnings,
            *scaling_result.warnings,
        ]
        trace = list(state.get("trace") or [])
        trace.append(
            {
                "type": "profile_model",
                "mode": scaling_result.mode,
                "profile_name": scaling_result.profile_name,
                "source_reach": scaling_result.source_reach,
                "target_reach": scaling_result.target_reach,
                "scale_factor": scaling_result.scale_factor,
            }
        )
        return {
            **state,
            "kinematic_model": model,
            "source_mode": scaling_result.mode,
            "source_summary": _profile_source_summary(scaling_result.mode, scaling_result.profile_name),
            "profile_name": scaling_result.profile_name,
            "target_reach": scaling_result.target_reach,
            "target_reach_unit": scaling_result.target_reach_unit,
            "scale_factor": scaling_result.scale_factor,
            "assumptions": assumptions,
            "warnings": warnings,
            "trace": trace,
        }

    def _node_search_model(self, state: KinematicsAgentState) -> KinematicsAgentState:
        decision = state["decision"]
        warnings = [
            *list(state.get("warnings") or []),
            *decision.warnings,
            "search_verified path is reserved; DH extraction from search results is not implemented yet.",
        ]
        if self._tools is not None and decision.search_queries:
            raw = self._tools.call(
                "search_agent",
                {
                    "question": (
                        "Find traceable DH parameters for robot kinematics. "
                        + " / ".join(decision.search_queries)
                    ),
                    "max_queries": min(max(len(decision.search_queries), 1), 4),
                    "max_results_per_query": 5,
                },
            )
            trace_extra = {"search_agent_result": str(raw)}
        else:
            trace_extra = {"search_agent_result": None}

        fallback_state = self._build_template_state(
            state,
            source_mode="template_fallback",
            source_summary="search_verified requested but not implemented; used template fallback.",
            assumptions=[*decision.assumptions],
            warnings=warnings,
        )
        trace = list(fallback_state.get("trace") or [])
        trace.append(
            {
                "type": "search_model",
                "queries": decision.search_queries,
                "implemented": False,
                **trace_extra,
            }
        )
        return {**fallback_state, "trace": trace}

    def _node_template_fallback(self, state: KinematicsAgentState) -> KinematicsAgentState:
        decision = state["decision"]
        return self._build_template_state(
            state,
            source_mode="template_fallback",
            source_summary="Generic MVP serial DH template fallback.",
            assumptions=[*decision.assumptions],
            warnings=[*list(state.get("warnings") or []), *decision.warnings],
        )

    def _build_template_state(
        self,
        state: KinematicsAgentState,
        *,
        source_mode: str,
        source_summary: str,
        assumptions: list[str],
        warnings: list[str],
    ) -> KinematicsAgentState:
        requirement = state["requirement"]
        model = _build_simple_kinematic_model(requirement)
        merged_assumptions = [
            *list(state.get("assumptions") or []),
            *assumptions,
            *model.assumptions,
        ]
        trace = list(state.get("trace") or [])
        trace.append(
            {
                "type": "template_fallback",
                "dof": requirement.dof,
                "reach": requirement.reach,
            }
        )
        return {
            **state,
            "kinematic_model": model,
            "source_mode": source_mode,
            "source_summary": source_summary,
            "profile_name": None,
            "target_reach": requirement.reach,
            "target_reach_unit": requirement.reach_unit,
            "scale_factor": None,
            "assumptions": merged_assumptions,
            "warnings": warnings,
            "trace": trace,
        }

    def _node_validate(self, state: KinematicsAgentState) -> KinematicsAgentState:
        model = state["kinematic_model"]
        requirement = state["requirement"]
        warnings = list(state.get("warnings") or [])
        reach_estimate = estimate_reach(model.dh_params)
        if requirement.reach is not None and reach_estimate + 1e-6 < requirement.reach:
            warnings.append(
                f"Estimated DH reach {reach_estimate:g} mm is below requested reach {requirement.reach:g} mm."
            )
        trace = list(state.get("trace") or [])
        trace.append(
            {
                "type": "validate",
                "estimated_reach": reach_estimate,
                "requested_reach": requirement.reach,
                "dof": len(model.joints),
            }
        )
        return {**state, "warnings": warnings, "trace": trace}

    def _node_finalize(self, state: KinematicsAgentState) -> KinematicsAgentState:
        result = KinematicsAgentResult(
            kinematic_model=state["kinematic_model"],
            source_mode=state["source_mode"],
            source_summary=state["source_summary"],
            profile_name=state.get("profile_name"),
            target_reach=state.get("target_reach"),
            target_reach_unit=state.get("target_reach_unit") or "mm",
            scale_factor=state.get("scale_factor"),
            assumptions=list(state.get("assumptions") or []),
            warnings=list(state.get("warnings") or []),
            trace=list(state.get("trace") or []),
        )
        trace = [*result.trace, {"type": "finalize", "source_mode": result.source_mode}]
        result.trace = trace
        return {**state, "result": result, "trace": trace, "finished": True}


def _llm_text(llm: Any, system: str, user: str) -> str:
    response = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    return (response.choices[0].message.content or "").strip()


def _parse_decision(text: str) -> KinematicsDecision:
    obj = _parse_json_object(text)
    if obj is None:
        raise ValueError("LLM decision was not valid JSON object")
    return KinematicsDecision.from_dict(obj)


def _guard_profile_reach_decision(
    *,
    decision: KinematicsDecision,
    requirement: RobotRequirement,
    user_request: str,
) -> KinematicsDecision:
    """Correct unsafe exact-profile decisions for profile-like reach requests.

    The LLM owns route selection, but this deterministic guard protects the CAD
    workflow from a common regression: "UR3e-like, reach 500 mm" must not use
    the original UR3e dimensions as an exact manufacturer profile.
    """

    if decision.source_mode != "exact_profile":
        return decision
    if not decision.profile_name or requirement.reach is None:
        return decision
    if _explicit_exact_profile_request(user_request):
        return decision
    if not _profile_reference_with_custom_reach(user_request):
        return decision

    try:
        profile = require_profile(decision.profile_name)
    except ValueError:
        return decision

    source_reach = estimate_reach(profile.dh_params)
    target_reach = float(requirement.reach)
    if math.isclose(source_reach, target_reach, rel_tol=0.02, abs_tol=5.0):
        return decision

    source_mode = (
        "profile_like_template"
        if _profile_like_language(user_request)
        else "scaled_profile"
    )
    warning = (
        f"LLM selected exact_profile for `{profile.name}`, but the request also "
        f"asks for a custom reach ({target_reach:g} mm vs source {source_reach:g} mm); "
        f"overriding to {source_mode}."
    )
    rationale = decision.rationale.strip()
    if rationale:
        rationale = f"{rationale} Guard override: custom reach requires {source_mode}."
    else:
        rationale = f"Guard override: custom reach requires {source_mode}."
    return KinematicsDecision(
        source_mode=source_mode,
        profile_name=profile.name,
        target_reach=target_reach,
        target_reach_unit=requirement.reach_unit,
        search_queries=decision.search_queries,
        rationale=rationale,
        assumptions=list(decision.assumptions),
        warnings=[*decision.warnings, warning],
        needs_user_clarification=decision.needs_user_clarification,
        clarification_question=decision.clarification_question,
    )


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


def _parse_requirement(text: str, params: dict[str, Any]) -> _ParsedRequirement:
    assumptions: list[str] = []
    warnings: list[str] = []
    dof = _int_param(params, "dof") or _parse_dof(text)
    if dof is None:
        dof = DEFAULT_DOF
        assumptions.append(f"未明确自由度，默认使用 {DEFAULT_DOF} DOF。")
    reach = _number_param(params, "reach_mm")
    if reach is None:
        reach = _parse_length_mm(text)
    if reach is None:
        reach = DEFAULT_REACH_MM
        assumptions.append(f"未明确工作半径，默认使用 {DEFAULT_REACH_MM:g} mm。")
    requirement = RobotRequirement(
        task=text,
        dof=int(dof),
        reach=float(reach),
        reach_unit="mm",
        workspace="kinematics_agent input",
        preferred_architecture="serial_arm",
        assumptions=assumptions,
        warnings=warnings,
    )
    return _ParsedRequirement(requirement=requirement, assumptions=assumptions, warnings=warnings)


def _explicit_exact_profile_request(text: str) -> bool:
    normalized = text.lower()
    patterns = [
        r"\bofficial\b",
        r"\boriginal\b",
        r"\bexact\b",
        r"\bmanufacturer\b",
        r"\bno\s+scal",
        r"官方",
        r"原始",
        r"原厂",
        r"精确",
        r"不要缩放",
        r"不缩放",
        r"无需缩放",
        r"原尺寸",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def _profile_reference_with_custom_reach(text: str) -> bool:
    normalized = text.lower()
    has_profile_reference = bool(
        re.search(r"\bur\s*-?\s*3\s*e\b|\bur3e\b|universal\s+robots", normalized)
        or re.search(r"参考|类似|仿|像|同构|拓扑|结构", text)
    )
    return has_profile_reference and _parse_length_mm(text) is not None


def _profile_like_language(text: str) -> bool:
    normalized = text.lower()
    return bool(
        re.search(r"\b(?:like|style|similar|inspired|reference)\b", normalized)
        or re.search(r"参考|类似|仿|像|同构|拓扑|结构", text)
    )


def _build_simple_kinematic_model(requirement: RobotRequirement) -> KinematicModel:
    link_lengths = _distribute_link_lengths(float(requirement.reach or DEFAULT_REACH_MM), requirement.dof)
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
                limit=(-math.pi, math.pi),
                notes=["Template fallback revolute joint."],
            )
        )
        links.append(
            LinkSpec(
                id=link_id,
                length=length,
                length_unit="mm",
                parent_joint=joint_id,
                material="template_placeholder",
                notes=["Template fallback link placeholder."],
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
            "Template fallback uses a planar serial DH chain with revolute joints.",
            "Template DH lengths are sizing heuristics, not sourced manufacturer data.",
        ],
        warnings=["Template fallback DH should be replaced with profile or verified source data when available."],
    )


def _distribute_link_lengths(reach_mm: float, dof: int) -> list[float]:
    if dof == 1:
        return [reach_mm]
    first = reach_mm * 0.3
    tail = (reach_mm - first) / (dof - 1)
    return [first, *[tail for _ in range(dof - 1)]]


def _requirement_summary(requirement: RobotRequirement) -> str:
    return json.dumps(
        {
            "task": requirement.task,
            "dof": requirement.dof,
            "reach": requirement.reach,
            "reach_unit": requirement.reach_unit,
            "workspace": requirement.workspace,
            "preferred_architecture": requirement.preferred_architecture,
        },
        ensure_ascii=False,
    )


def _profile_source_summary(mode: str, profile_name: str) -> str:
    if mode == "exact_profile":
        return f"Exact built-in profile `{profile_name}`."
    if mode == "scaled_profile":
        return f"Scaled built-in profile `{profile_name}`."
    return f"Profile-like template based on `{profile_name}`."


def _format_final_answer(result: KinematicsAgentResult) -> str:
    model = result.kinematic_model
    rows = "\n".join(
        f"- {p.joint_id}: a={p.a:g}mm, alpha={p.alpha:g}rad, d={p.d:g}mm, theta={p.theta:g}rad"
        for p in model.dh_params
    )
    scale = "无" if result.scale_factor is None else f"{result.scale_factor:g}"
    warnings = "\n".join(f"- {item}" for item in result.warnings) or "- 无"
    return (
        "[kinematics_agent] 运动学模型已生成。\n\n"
        f"- source_mode: {result.source_mode}\n"
        f"- source_summary: {result.source_summary}\n"
        f"- profile_name: {result.profile_name or '无'}\n"
        f"- scale_factor: {scale}\n"
        f"- DOF: {len(model.joints)}\n"
        f"- convention: {model.convention}\n\n"
        "## DH 模型\n"
        f"{rows}\n\n"
        "## 警告\n"
        f"{warnings}"
    )


def _number_param(params: dict[str, Any], key: str) -> float | None:
    value = params.get(key)
    if value is None or value == "":
        return None
    return float(value)


def _int_param(params: dict[str, Any], key: str) -> int | None:
    value = _number_param(params, key)
    return None if value is None else int(value)


def _parse_dof(text: str) -> int | None:
    for pattern in [r"(\d+)\s*(?:dof|DOF|自由度)", r"(\d+)\s*(?:轴|关节)"]:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def _parse_length_mm(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(mm|毫米|m|米)", text, flags=re.IGNORECASE)
    if match:
        value = float(match.group(1))
        unit = match.group(2).lower()
        return value if unit in {"mm", "毫米"} else value * 1000.0
    return None


__all__ = ["KinematicsAgent"]
