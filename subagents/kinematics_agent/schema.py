"""Schemas for the LangGraph kinematics subagent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from robot_sdk.types import KinematicModel, LengthUnit, RobotRequirement, SerializableMixin


KinematicsSourceMode = Literal[
    "exact_profile",
    "scaled_profile",
    "profile_like_template",
    "search_verified",
    "template_fallback",
]


DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "source_mode",
        "profile_name",
        "target_reach",
        "target_reach_unit",
        "search_queries",
        "rationale",
        "assumptions",
        "warnings",
        "needs_user_clarification",
        "clarification_question",
    ],
    "properties": {
        "source_mode": {
            "type": "string",
            "enum": [
                "exact_profile",
                "scaled_profile",
                "profile_like_template",
                "search_verified",
                "template_fallback",
            ],
        },
        "profile_name": {"type": ["string", "null"]},
        "target_reach": {"type": ["number", "null"]},
        "target_reach_unit": {"type": "string", "enum": ["mm", "m"]},
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "needs_user_clarification": {"type": "boolean"},
        "clarification_question": {"type": ["string", "null"]},
    },
}


@dataclass
class KinematicsDecision(SerializableMixin):
    """LLM decision output. It chooses a source route but never contains DH rows."""

    source_mode: KinematicsSourceMode
    profile_name: str | None = None
    target_reach: float | None = None
    target_reach_unit: LengthUnit = "mm"
    search_queries: list[str] = field(default_factory=list)
    rationale: str = ""
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_user_clarification: bool = False
    clarification_question: str | None = None

    def __post_init__(self) -> None:
        allowed = {
            "exact_profile",
            "scaled_profile",
            "profile_like_template",
            "search_verified",
            "template_fallback",
        }
        if self.source_mode not in allowed:
            raise ValueError(f"Unsupported kinematics source_mode: {self.source_mode}")
        if self.target_reach is not None and self.target_reach <= 0:
            raise ValueError("KinematicsDecision.target_reach must be positive")
        if self.target_reach_unit not in {"mm", "m"}:
            raise ValueError("KinematicsDecision.target_reach_unit must be mm or m")
        if self.source_mode in {
            "exact_profile",
            "scaled_profile",
            "profile_like_template",
        } and not (self.profile_name or "").strip():
            raise ValueError(f"{self.source_mode} requires profile_name")
        if self.source_mode in {"scaled_profile", "profile_like_template"}:
            if self.target_reach is None:
                raise ValueError(f"{self.source_mode} requires target_reach")
        if self.source_mode == "search_verified" and not self.search_queries:
            raise ValueError("search_verified requires at least one search query")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KinematicsDecision":
        return cls(
            source_mode=str(data.get("source_mode") or "template_fallback"),
            profile_name=_none_if_blank(data.get("profile_name")),
            target_reach=_optional_float(data.get("target_reach")),
            target_reach_unit=str(data.get("target_reach_unit") or "mm"),
            search_queries=[
                str(item).strip()
                for item in data.get("search_queries") or []
                if str(item).strip()
            ],
            rationale=str(data.get("rationale") or "").strip(),
            assumptions=_string_list(data.get("assumptions")),
            warnings=_string_list(data.get("warnings")),
            needs_user_clarification=bool(data.get("needs_user_clarification")),
            clarification_question=_none_if_blank(data.get("clarification_question")),
        )


@dataclass
class KinematicsAgentResult(SerializableMixin):
    """Structured output consumed by robot_design_agent and final reports."""

    kinematic_model: KinematicModel
    source_mode: KinematicsSourceMode
    source_summary: str
    profile_name: str | None = None
    target_reach: float | None = None
    target_reach_unit: LengthUnit = "mm"
    scale_factor: float | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)


class KinematicsAgentState(TypedDict, total=False):
    user_request: str
    requirement: RobotRequirement
    allow_search: bool
    decision: KinematicsDecision
    kinematic_model: KinematicModel
    result: KinematicsAgentResult
    source_summary: str
    source_mode: str
    profile_name: str | None
    target_reach: float | None
    target_reach_unit: str
    scale_factor: float | None
    assumptions: list[str]
    warnings: list[str]
    trace: list[dict[str, Any]]
    final_answer: str
    finished: bool


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


__all__ = [
    "DECISION_JSON_SCHEMA",
    "KinematicsAgentResult",
    "KinematicsAgentState",
    "KinematicsDecision",
    "KinematicsSourceMode",
]
