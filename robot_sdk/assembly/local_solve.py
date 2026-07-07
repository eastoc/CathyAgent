"""Local subassembly planning for constraint-driven assembly.

This module does not import CadQuery. It selects small, reviewable groups of
parts and filters semantic constraints before a CAD adapter attempts a local
solve. The goal is to migrate away from full-assembly fixed-pose locking
without letting unsafe legacy axis assumptions enter the solver.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from robot_sdk.assembly.constraints import (
    AssemblyMateConstraintPlan,
    AssemblyMateConstraintRule,
    build_assembly_mate_constraint_plan,
)
from robot_sdk.types import MechanicalLayout


@dataclass(frozen=True)
class LocalSubassemblySpec:
    """Declarative local subassembly target."""

    name: str
    part_ids: list[str]
    anchor_part_id: str
    allow_unsafe_axis_constraints: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("LocalSubassemblySpec.name is required")
        if not self.part_ids:
            raise ValueError("LocalSubassemblySpec.part_ids cannot be empty")
        if self.anchor_part_id not in self.part_ids:
            raise ValueError("LocalSubassemblySpec.anchor_part_id must be in part_ids")
        if len(set(self.part_ids)) != len(self.part_ids):
            raise ValueError("LocalSubassemblySpec.part_ids must be unique")


@dataclass(frozen=True)
class LocalConstraintResidual:
    """Mate-frame residual before/after a local solve adapter consumes it."""

    constraint_id: str
    fixed_feature_id: str
    moving_feature_id: str
    origin_delta_mm: float
    normal_angle_deg: float
    tangent_angle_deg: float

    def to_dict(self) -> dict[str, object]:
        return {
            "constraint_id": self.constraint_id,
            "fixed_feature_id": self.fixed_feature_id,
            "moving_feature_id": self.moving_feature_id,
            "origin_delta_mm": self.origin_delta_mm,
            "normal_angle_deg": self.normal_angle_deg,
            "tangent_angle_deg": self.tangent_angle_deg,
        }


@dataclass(frozen=True)
class LocalSubassemblyPlan:
    """Resolved local subassembly constraints."""

    spec: LocalSubassemblySpec
    applied_rules: list[AssemblyMateConstraintRule]
    skipped_rules: list[AssemblyMateConstraintRule]
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    residuals: list[LocalConstraintResidual] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def part_ids(self) -> list[str]:
        return list(self.spec.part_ids)

    @property
    def anchor_part_id(self) -> str:
        return self.spec.anchor_part_id

    @property
    def applied_constraint_ids(self) -> list[str]:
        return [rule.constraint_id for rule in self.applied_rules]

    @property
    def skipped_constraint_ids(self) -> list[str]:
        return [rule.constraint_id for rule in self.skipped_rules]

    @property
    def unsafe_constraint_ids(self) -> list[str]:
        return [
            rule.constraint_id
            for rule in [*self.applied_rules, *self.skipped_rules]
            if bool(rule.metadata.get("unsafe_body_axis_joint_axis"))
        ]


def default_local_subassembly_specs(layout: MechanicalLayout) -> list[LocalSubassemblySpec]:
    """Return stage-6 default local subassemblies in solve order.

    Agent-provided candidates in `layout.metadata["local_subassemblies"]` are
    preferred. If no decision metadata exists, a deterministic fallback keeps
    standalone SDK tests and generic serial arms usable.
    """

    metadata_specs = _metadata_local_subassembly_specs(layout)
    if metadata_specs:
        return _append_base_mount_spec_if_available(layout, metadata_specs)
    return _fallback_local_subassembly_specs(layout)


def _fallback_local_subassembly_specs(layout: MechanicalLayout) -> list[LocalSubassemblySpec]:
    """Return deterministic fallback local subassemblies in solve order."""

    available_parts = _layout_part_ids(layout)
    specs: list[LocalSubassemblySpec] = []
    if layout.joints and layout.links:
        last_joint = layout.joints[-1].id
        last_link = layout.links[-1].id
        terminal_parts = [last_joint, last_link, "end_effector"]
        if all(part_id in available_parts for part_id in terminal_parts):
            specs.append(
                LocalSubassemblySpec(
                    name=f"{last_joint}_{last_link}_end_effector",
                    part_ids=terminal_parts,
                    anchor_part_id=last_joint,
                    metadata={"role": "terminal_tool"},
                )
            )

    chain_start = max(len(layout.joints) - 5, -1)
    for joint_index in range(len(layout.joints) - 2, chain_start, -1):
        from_joint = layout.joints[joint_index].id
        link = layout.links[joint_index].id
        to_joint = layout.joints[joint_index + 1].id
        parts = [from_joint, link, to_joint]
        if all(part_id in available_parts for part_id in parts):
            specs.append(
                LocalSubassemblySpec(
                    name=f"{from_joint}_{link}_{to_joint}",
                    part_ids=parts,
                    anchor_part_id=from_joint,
                    metadata={"role": "joint_link_joint"},
                )
            )
    if len(layout.joints) >= 6 and len(layout.links) >= 6:
        wrist_parts = [
            layout.joints[3].id,
            layout.links[3].id,
            layout.joints[4].id,
            layout.links[4].id,
            layout.joints[5].id,
            layout.links[5].id,
            "end_effector",
        ]
        if all(part_id in available_parts for part_id in wrist_parts):
            specs.append(
                LocalSubassemblySpec(
                    name=f"wrist_group_{layout.joints[3].id}_to_end_effector",
                    part_ids=wrist_parts,
                    anchor_part_id=layout.joints[3].id,
                    metadata={"role": "wrist_group"},
                )
            )
    return _append_base_mount_spec_if_available(layout, specs)


def _append_base_mount_spec_if_available(
    layout: MechanicalLayout,
    specs: list[LocalSubassemblySpec],
) -> list[LocalSubassemblySpec]:
    """Append the promoted base-to-first-joint semantic subassembly."""

    base_spec = _base_mount_subassembly_spec(layout)
    if base_spec is None:
        return specs
    if any(spec.name == base_spec.name for spec in specs):
        return specs
    return [*specs, base_spec]


def _base_mount_subassembly_spec(
    layout: MechanicalLayout,
) -> LocalSubassemblySpec | None:
    available_parts = _layout_part_ids(layout)
    if not layout.joints:
        return None
    first_joint_id = layout.joints[0].id
    parts = ["base", first_joint_id]
    if not all(part_id in available_parts for part_id in parts):
        return None
    return LocalSubassemblySpec(
        name=f"base_{first_joint_id}_mount",
        part_ids=parts,
        anchor_part_id="base",
        metadata={
            "role": "base_mount",
            "source": "sdk_promoted_fixture",
            "reason": (
                "Base top to first joint bottom mate frames are promotion-clean "
                "and can run as a production local semantic subassembly."
            ),
        },
    )


def _metadata_local_subassembly_specs(layout: MechanicalLayout) -> list[LocalSubassemblySpec]:
    items = layout.metadata.get("local_subassemblies")
    if not isinstance(items, list):
        return []
    specs: list[LocalSubassemblySpec] = []
    available_parts = _layout_part_ids(layout)
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        part_ids = [
            str(part_id).strip()
            for part_id in item.get("part_ids") or []
            if str(part_id).strip()
        ]
        anchor_part_id = str(item.get("anchor_part_id") or "").strip()
        if not name or not part_ids or anchor_part_id not in part_ids:
            continue
        if not set(part_ids).issubset(available_parts):
            continue
        specs.append(
            LocalSubassemblySpec(
                name=name,
                part_ids=part_ids,
                anchor_part_id=anchor_part_id,
                metadata={
                    "role": str(item.get("role") or "local_subassembly"),
                    "reason": str(item.get("reason") or ""),
                    "confidence": float(item.get("confidence") or 0.0),
                    "source_signals": [
                        str(signal)
                        for signal in item.get("source_signals") or []
                    ],
                    "source": "layout_decision",
                },
            )
        )
    return specs


def build_local_subassembly_plans(
    layout: MechanicalLayout,
    *,
    specs: list[LocalSubassemblySpec] | None = None,
) -> list[LocalSubassemblyPlan]:
    """Resolve local subassembly plans from a layout."""

    constraint_plan = build_assembly_mate_constraint_plan(layout)
    selected_specs = specs or default_local_subassembly_specs(layout)
    available_parts = _layout_part_ids(layout)
    return [
        build_local_subassembly_plan(
            spec,
            constraint_plan=constraint_plan,
            available_parts=available_parts,
        )
        for spec in selected_specs
    ]


def build_local_subassembly_plan(
    spec: LocalSubassemblySpec,
    *,
    constraint_plan: AssemblyMateConstraintPlan,
    available_parts: set[str],
) -> LocalSubassemblyPlan:
    """Build one local plan and filter unsafe constraints."""

    missing = sorted(set(spec.part_ids) - available_parts)
    if missing:
        raise KeyError(
            f"Local subassembly `{spec.name}` references unknown parts: {missing}"
        )

    part_set = set(spec.part_ids)
    candidate_rules = [
        rule
        for rule in constraint_plan.rules
        if rule.fixed_mate.part_id in part_set and rule.moving_mate.part_id in part_set
    ]
    applied: list[AssemblyMateConstraintRule] = []
    skipped: list[AssemblyMateConstraintRule] = []
    skipped_reasons: dict[str, str] = {}
    warnings: list[str] = []

    for rule in candidate_rules:
        if (
            bool(rule.metadata.get("unsafe_body_axis_joint_axis"))
            and not spec.allow_unsafe_axis_constraints
        ):
            skipped.append(rule)
            skipped_reasons[rule.constraint_id] = (
                "unsafe_body_axis_joint_axis: replace with interface mate rules "
                "before local solve."
            )
            continue
        applied.append(rule)

    if not applied:
        warnings.append(
            f"Local subassembly `{spec.name}` has no safe semantic constraints to solve."
        )
    if skipped:
        warnings.append(
            f"Local subassembly `{spec.name}` skipped {len(skipped)} unsafe constraints."
        )
    residuals = [_constraint_residual(rule) for rule in applied]

    return LocalSubassemblyPlan(
        spec=spec,
        applied_rules=applied,
        skipped_rules=skipped,
        skipped_reasons=skipped_reasons,
        residuals=residuals,
        warnings=warnings,
    )


def _layout_part_ids(layout: MechanicalLayout) -> set[str]:
    return {feature.part_id for feature in layout.part_features}


def _constraint_residual(rule: AssemblyMateConstraintRule) -> LocalConstraintResidual:
    return LocalConstraintResidual(
        constraint_id=rule.constraint_id,
        fixed_feature_id=rule.fixed_feature_id,
        moving_feature_id=rule.moving_feature_id,
        origin_delta_mm=_distance(rule.fixed_mate.origin, rule.moving_mate.origin),
        normal_angle_deg=_angle_deg(rule.fixed_mate.normal, rule.moving_mate.normal),
        tangent_angle_deg=_angle_deg(rule.fixed_mate.tangent, rule.moving_mate.tangent),
    )


def _distance(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return math.sqrt(
        (left[0] - right[0]) ** 2
        + (left[1] - right[1]) ** 2
        + (left[2] - right[2]) ** 2
    )


def _angle_deg(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    left_len = math.sqrt(left[0] ** 2 + left[1] ** 2 + left[2] ** 2)
    right_len = math.sqrt(right[0] ** 2 + right[1] ** 2 + right[2] ** 2)
    if left_len <= 1e-12 or right_len <= 1e-12:
        return 0.0
    dot = (
        left[0] * right[0]
        + left[1] * right[1]
        + left[2] * right[2]
    ) / (left_len * right_len)
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(math.acos(dot))


__all__ = [
    "LocalSubassemblyPlan",
    "LocalSubassemblySpec",
    "LocalConstraintResidual",
    "build_local_subassembly_plan",
    "build_local_subassembly_plans",
    "default_local_subassembly_specs",
]
