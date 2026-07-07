"""Schemas for the layout morphology subagent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, get_args

from cathy.llm_errors import AgentFailure
from robot_sdk.structure import RobotStructurePlan
from robot_sdk.types import (
    InterfaceMorphologyType,
    JointMorphologyType,
    KinematicModel,
    LayoutSource,
    LinkMorphologyType,
    MechanicalLayout,
    SerializableMixin,
)


LAYOUT_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "layout_source",
        "robot_family",
        "confidence",
        "joints",
        "links",
        "interfaces",
        "subassemblies",
        "assumptions",
        "warnings",
    ],
    "properties": {
        "layout_source": {
            "type": "string",
            "enum": ["layout_agent", "rule_fallback", "manual", "unknown"],
        },
        "robot_family": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "joints": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["joint_id", "morphology", "reason", "confidence", "source_signals"],
                "properties": {
                    "joint_id": {"type": "string"},
                    "morphology": {
                        "type": "string",
                        "enum": [
                            "base_yaw_joint",
                            "shoulder_joint",
                            "elbow_joint",
                            "wrist_pitch_joint",
                            "wrist_roll_joint",
                            "tool_flange_joint",
                            "generic_revolute_joint",
                        ],
                    },
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_signals": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["link_id", "morphology", "reason", "confidence", "source_signals"],
                "properties": {
                    "link_id": {"type": "string"},
                    "morphology": {
                        "type": "string",
                        "enum": [
                            "base_column",
                            "upper_arm_link",
                            "forearm_link",
                            "wrist1_offset_housing",
                            "wrist2_elbow_cylinder",
                            "wrist3_tool_flange",
                            "terminal_tool_spacer",
                            "generic_straight_link",
                            "generic_offset_link",
                            "generic_elbow_link",
                            "generic_wrist_spacer",
                        ],
                    },
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_signals": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "interfaces": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["interface_id", "morphology", "reason", "confidence", "source_signals"],
                "properties": {
                    "interface_id": {"type": "string"},
                    "morphology": {
                        "type": "string",
                        "enum": [
                            "base_mount_face",
                            "actuator_flange",
                            "arm_flange",
                            "wrist_cross_axis_interface",
                            "tool_mount_flange",
                            "generic_flange",
                        ],
                    },
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_signals": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "subassemblies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "part_ids", "anchor_part_id", "role", "reason", "confidence", "source_signals"],
                "properties": {
                    "name": {"type": "string"},
                    "part_ids": {"type": "array", "items": {"type": "string"}},
                    "anchor_part_id": {"type": "string"},
                    "role": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "source_signals": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "structure_plan": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": [
                "name",
                "family",
                "source",
                "units",
                "stations",
                "joint_axes",
                "link_routes",
                "datums",
                "subassemblies",
                "assumptions",
                "warnings",
                "metadata",
            ],
            "properties": {
                "name": {"type": "string"},
                "family": {"type": "string"},
                "source": {
                    "type": "string",
                    "enum": [
                        "layout_agent",
                        "generic_6axis_cobot_profile",
                        "profile_structure",
                        "manual",
                        "unknown",
                    ],
                },
                "units": {"type": "string", "enum": ["mm", "m"]},
                "stations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "role", "origin"],
                        "properties": {
                            "id": {"type": "string"},
                            "role": {
                                "type": "string",
                                "enum": [
                                    "base",
                                    "shoulder",
                                    "elbow",
                                    "wrist1",
                                    "wrist2",
                                    "wrist3",
                                    "tool",
                                ],
                            },
                            "origin": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "parent_id": {"type": ["string", "null"]},
                            "description": {"type": "string"},
                        },
                    },
                },
                "joint_axes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["joint_id", "station_id", "role", "direction"],
                        "properties": {
                            "joint_id": {"type": "string"},
                            "station_id": {"type": "string"},
                            "role": {
                                "type": "string",
                                "enum": [
                                    "base_yaw",
                                    "shoulder_pitch",
                                    "elbow_pitch",
                                    "wrist_roll",
                                    "wrist_pitch",
                                    "tool_roll",
                                    "generic",
                                ],
                            },
                            "direction": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "origin": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "rationale": {"type": "string"},
                        },
                    },
                },
                "link_routes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["link_id", "from_station_id", "to_station_id", "route_type"],
                        "properties": {
                            "link_id": {"type": "string"},
                            "from_station_id": {"type": "string"},
                            "to_station_id": {"type": "string"},
                            "route_type": {
                                "type": "string",
                                "enum": ["straight", "offset", "elbow", "wrist_spacer", "tool_stub"],
                            },
                            "waypoints": {
                                "type": "array",
                                "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            },
                            "rationale": {"type": "string"},
                        },
                    },
                },
                "datums": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["id", "owner_id", "semantic", "origin", "normal"],
                        "properties": {
                            "id": {"type": "string"},
                            "owner_id": {"type": "string"},
                            "semantic": {
                                "type": "string",
                                "enum": ["mount_plane", "joint_axis", "interface_plane", "tool_plane"],
                            },
                            "origin": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "normal": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "tangent": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                            "description": {"type": "string"},
                        },
                    },
                },
                "subassemblies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "role", "part_ids", "anchor_part_id"],
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string"},
                            "part_ids": {"type": "array", "items": {"type": "string"}},
                            "anchor_part_id": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                },
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass
class JointMorphologyDecision(SerializableMixin):
    """Agent-selected mechanical role for one joint."""

    joint_id: str
    morphology: JointMorphologyType
    reason: str
    confidence: float = 0.0
    source_signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text(self.joint_id, "JointMorphologyDecision.joint_id")
        _validate_literal(
            self.morphology,
            JointMorphologyType,
            "JointMorphologyDecision.morphology",
        )
        _require_text(self.reason, "JointMorphologyDecision.reason")
        _validate_confidence(self.confidence, "JointMorphologyDecision.confidence")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JointMorphologyDecision":
        return cls(
            joint_id=str(data.get("joint_id") or "").strip(),
            morphology=str(data.get("morphology") or "generic_revolute_joint"),
            reason=str(data.get("reason") or "").strip(),
            confidence=float(data.get("confidence") or 0.0),
            source_signals=_string_list(data.get("source_signals")),
        )


@dataclass
class LinkMorphologyDecision(SerializableMixin):
    """Agent-selected mechanical role for one link."""

    link_id: str
    morphology: LinkMorphologyType
    reason: str
    confidence: float = 0.0
    source_signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text(self.link_id, "LinkMorphologyDecision.link_id")
        _validate_literal(
            self.morphology,
            LinkMorphologyType,
            "LinkMorphologyDecision.morphology",
        )
        _require_text(self.reason, "LinkMorphologyDecision.reason")
        _validate_confidence(self.confidence, "LinkMorphologyDecision.confidence")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LinkMorphologyDecision":
        return cls(
            link_id=str(data.get("link_id") or "").strip(),
            morphology=str(data.get("morphology") or "generic_straight_link"),
            reason=str(data.get("reason") or "").strip(),
            confidence=float(data.get("confidence") or 0.0),
            source_signals=_string_list(data.get("source_signals")),
        )


@dataclass
class InterfaceMorphologyDecision(SerializableMixin):
    """Agent-selected mechanical role for one interface."""

    interface_id: str
    morphology: InterfaceMorphologyType
    reason: str
    confidence: float = 0.0
    source_signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text(self.interface_id, "InterfaceMorphologyDecision.interface_id")
        _validate_literal(
            self.morphology,
            InterfaceMorphologyType,
            "InterfaceMorphologyDecision.morphology",
        )
        _require_text(self.reason, "InterfaceMorphologyDecision.reason")
        _validate_confidence(self.confidence, "InterfaceMorphologyDecision.confidence")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InterfaceMorphologyDecision":
        return cls(
            interface_id=str(data.get("interface_id") or "").strip(),
            morphology=str(data.get("morphology") or "generic_flange"),
            reason=str(data.get("reason") or "").strip(),
            confidence=float(data.get("confidence") or 0.0),
            source_signals=_string_list(data.get("source_signals")),
        )


@dataclass
class LocalSubassemblyDecision(SerializableMixin):
    """Agent-selected local subassembly candidate for constraint solve."""

    name: str
    part_ids: list[str]
    anchor_part_id: str
    role: str
    reason: str
    confidence: float = 0.0
    source_signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text(self.name, "LocalSubassemblyDecision.name")
        if not self.part_ids:
            raise ValueError("LocalSubassemblyDecision.part_ids cannot be empty")
        _reject_duplicates(self.part_ids, f"subassembly `{self.name}` part")
        _require_text(self.anchor_part_id, "LocalSubassemblyDecision.anchor_part_id")
        if self.anchor_part_id not in self.part_ids:
            raise ValueError("LocalSubassemblyDecision.anchor_part_id must be in part_ids")
        _require_text(self.role, "LocalSubassemblyDecision.role")
        _require_text(self.reason, "LocalSubassemblyDecision.reason")
        _validate_confidence(self.confidence, "LocalSubassemblyDecision.confidence")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LocalSubassemblyDecision":
        return cls(
            name=str(data.get("name") or "").strip(),
            part_ids=_string_list(data.get("part_ids")),
            anchor_part_id=str(data.get("anchor_part_id") or "").strip(),
            role=str(data.get("role") or "local_subassembly").strip(),
            reason=str(data.get("reason") or "").strip(),
            confidence=float(data.get("confidence") or 0.0),
            source_signals=_string_list(data.get("source_signals")),
        )


@dataclass
class LayoutDecision(SerializableMixin):
    """Complete morphology decision that an SDK adapter can apply to a layout."""

    layout_source: LayoutSource
    robot_family: str
    joints: list[JointMorphologyDecision]
    links: list[LinkMorphologyDecision]
    interfaces: list[InterfaceMorphologyDecision] = field(default_factory=list)
    subassemblies: list[LocalSubassemblyDecision] = field(default_factory=list)
    confidence: float = 0.0
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_literal(
            self.layout_source,
            LayoutSource,
            "LayoutDecision.layout_source",
        )
        _require_text(self.robot_family, "LayoutDecision.robot_family")
        _validate_confidence(self.confidence, "LayoutDecision.confidence")
        if not self.joints:
            raise ValueError("LayoutDecision.joints cannot be empty")
        if not self.links:
            raise ValueError("LayoutDecision.links cannot be empty")
        _reject_duplicates([item.joint_id for item in self.joints], "joint decision")
        _reject_duplicates([item.link_id for item in self.links], "link decision")
        _reject_duplicates(
            [item.interface_id for item in self.interfaces],
            "interface decision",
        )
        _reject_duplicates(
            [item.name for item in self.subassemblies],
            "local subassembly decision",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LayoutDecision":
        return cls(
            layout_source=str(data.get("layout_source") or "layout_agent"),
            robot_family=str(data.get("robot_family") or "unknown").strip(),
            confidence=float(data.get("confidence") or 0.0),
            joints=[
                JointMorphologyDecision.from_dict(item)
                for item in data.get("joints") or []
                if isinstance(item, dict)
            ],
            links=[
                LinkMorphologyDecision.from_dict(item)
                for item in data.get("links") or []
                if isinstance(item, dict)
            ],
            interfaces=[
                InterfaceMorphologyDecision.from_dict(item)
                for item in data.get("interfaces") or []
                if isinstance(item, dict)
            ],
            subassemblies=[
                LocalSubassemblyDecision.from_dict(item)
                for item in data.get("subassemblies") or []
                if isinstance(item, dict)
            ],
            assumptions=_string_list(data.get("assumptions")),
            warnings=_string_list(data.get("warnings")),
        )


@dataclass
class LayoutAgentResult(SerializableMixin):
    """Structured layout_agent output consumed by robot_design_agent."""

    decision: LayoutDecision
    structure_plan: RobotStructurePlan | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    status: Literal["ok", "failed", "degraded", "incomplete"] = "ok"
    failure: AgentFailure | None = None


class LayoutAgentState(TypedDict, total=False):
    user_request: str
    kinematic_model: KinematicModel
    preliminary_layout: MechanicalLayout
    profile_name: str | None
    source_mode: str | None
    scale_factor: float | None
    reach_mm: float | None
    decision: LayoutDecision
    structure_plan: RobotStructurePlan | None
    result: LayoutAgentResult
    trace: list[dict[str, Any]]
    raw_decision: str | None
    decision_error: str | None
    status: str
    failure: AgentFailure | None


def _require_text(value: str, field_name: str) -> None:
    if not str(value or "").strip():
        raise ValueError(f"{field_name} is required")


def _validate_confidence(value: float, field_name: str) -> None:
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")


def _validate_literal(value: str, literal: object, field_name: str) -> None:
    allowed = set(get_args(literal))
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}")


def _reject_duplicates(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"Duplicate {label} ids: {sorted(set(duplicates))}")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


__all__ = [
    "InterfaceMorphologyDecision",
    "JointMorphologyDecision",
    "LAYOUT_DECISION_JSON_SCHEMA",
    "LayoutAgentResult",
    "LayoutAgentState",
    "LayoutDecision",
    "LocalSubassemblyDecision",
    "LinkMorphologyDecision",
]
