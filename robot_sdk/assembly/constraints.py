"""CadQuery-adapter-ready assembly constraint rules.

`AssemblyConstraint` is the neutral layout contract. This module resolves each
constraint through the mate feature catalog and checks that the referenced
feature kinds make sense before a CadQuery adapter calls `Assembly.constrain`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from robot_sdk.assembly.features import (
    CadQueryMateFeatureCatalog,
    CadQueryMateFeatureRule,
    build_cadquery_mate_feature_catalog,
)
from robot_sdk.assembly.mate_frames import (
    MateFrame,
    MateFrameCatalog,
    build_mate_frame_catalog,
)
from robot_sdk.types import AssemblyConstraint, MechanicalLayout


_NORMALIZED_KIND = {
    "Fixed": "FixedSeed",
    "Plane": "MatePlane",
    "Axis": "MateAxis",
    "Point": "MateFrame",
}


@dataclass(frozen=True)
class CadQueryAssemblyConstraintRule:
    """Resolved assembly constraint for a future CadQuery adapter."""

    constraint_id: str
    kind: str
    fixed: CadQueryMateFeatureRule
    moving: CadQueryMateFeatureRule
    offset: float | None = None
    flipped: bool = False
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.constraint_id:
            raise ValueError("CadQueryAssemblyConstraintRule.constraint_id is required")
        if not self.rationale.strip():
            raise ValueError("CadQueryAssemblyConstraintRule.rationale is required")
        _validate_feature_kinds(self)

    @classmethod
    def from_constraint(
        cls,
        constraint: AssemblyConstraint,
        feature_catalog: CadQueryMateFeatureCatalog,
    ) -> "CadQueryAssemblyConstraintRule":
        """Resolve a neutral `AssemblyConstraint` into feature rules."""

        return cls(
            constraint_id=constraint.id,
            kind=constraint.kind,
            fixed=feature_catalog.require(constraint.fixed),
            moving=feature_catalog.require(constraint.moving),
            offset=constraint.offset,
            flipped=constraint.flipped,
            rationale=constraint.rationale,
            metadata={
                "fixed_feature_id": constraint.fixed,
                "moving_feature_id": constraint.moving,
            },
        )

    @property
    def fixed_ref(self) -> str:
        return self.fixed.constraint_ref

    @property
    def moving_ref(self) -> str:
        return self.moving.constraint_ref

    @property
    def fixed_cq_tag_path(self) -> str:
        return self.fixed.cq_tag_path

    @property
    def moving_cq_tag_path(self) -> str:
        return self.moving.cq_tag_path


@dataclass(frozen=True)
class CadQueryAssemblyConstraintPlan:
    """Ordered constraint rules plus the feature catalog they depend on."""

    feature_catalog: CadQueryMateFeatureCatalog
    rules: list[CadQueryAssemblyConstraintRule]

    def require(self, constraint_id: str) -> CadQueryAssemblyConstraintRule:
        for rule in self.rules:
            if rule.constraint_id == constraint_id:
                return rule
        raise KeyError(f"Unknown assembly constraint `{constraint_id}`")

    def by_kind(self, kind: str) -> list[CadQueryAssemblyConstraintRule]:
        return [rule for rule in self.rules if rule.kind == kind]


@dataclass(frozen=True)
class AssemblyMateConstraintRule:
    """Constraint rule resolved through MateFrame objects.

    This is the stage-6 neutral rule. It keeps the original
    `AssemblyConstraint.kind` for traceability and exposes `normalized_kind`
    for the newer mate-frame assembly pipeline.
    """

    constraint_id: str
    kind: str
    normalized_kind: str
    fixed_mate: MateFrame
    moving_mate: MateFrame
    offset: float | None = None
    flipped: bool = False
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.constraint_id:
            raise ValueError("AssemblyMateConstraintRule.constraint_id is required")
        if not self.rationale.strip():
            raise ValueError("AssemblyMateConstraintRule.rationale is required")
        _validate_mate_kinds(self)

    @classmethod
    def from_constraint(
        cls,
        constraint: AssemblyConstraint,
        mate_catalog: MateFrameCatalog,
    ) -> "AssemblyMateConstraintRule":
        fixed_mate = mate_catalog.require_feature(constraint.fixed)
        moving_mate = mate_catalog.require_feature(constraint.moving)
        normalized_kind = normalize_constraint_kind(constraint.kind)
        return cls(
            constraint_id=constraint.id,
            kind=constraint.kind,
            normalized_kind=normalized_kind,
            fixed_mate=fixed_mate,
            moving_mate=moving_mate,
            offset=constraint.offset,
            flipped=constraint.flipped,
            rationale=constraint.rationale,
            metadata={
                "fixed_feature_id": constraint.fixed,
                "moving_feature_id": constraint.moving,
                "fixed_part_id": fixed_mate.part_id,
                "moving_part_id": moving_mate.part_id,
                "unsafe_body_axis_joint_axis": _is_unsafe_body_axis_joint_axis(
                    fixed_mate,
                    moving_mate,
                    normalized_kind,
                ),
            },
        )

    @property
    def fixed_feature_id(self) -> str:
        return self.fixed_mate.feature_id

    @property
    def moving_feature_id(self) -> str:
        return self.moving_mate.feature_id


@dataclass(frozen=True)
class AssemblyMateConstraintPlan:
    """Ordered mate-frame constraints plus their mate-frame catalog."""

    mate_catalog: MateFrameCatalog
    rules: list[AssemblyMateConstraintRule]

    def require(self, constraint_id: str) -> AssemblyMateConstraintRule:
        for rule in self.rules:
            if rule.constraint_id == constraint_id:
                return rule
        raise KeyError(f"Unknown mate constraint `{constraint_id}`")

    def by_kind(self, kind: str) -> list[AssemblyMateConstraintRule]:
        return [
            rule
            for rule in self.rules
            if rule.kind == kind or rule.normalized_kind == kind
        ]

    @property
    def unsafe_rules(self) -> list[AssemblyMateConstraintRule]:
        return [
            rule
            for rule in self.rules
            if bool(rule.metadata.get("unsafe_body_axis_joint_axis"))
        ]


def build_cadquery_assembly_constraint_rules(
    constraints: list[AssemblyConstraint],
    feature_catalog: CadQueryMateFeatureCatalog,
) -> list[CadQueryAssemblyConstraintRule]:
    """Resolve neutral constraints into CadQuery-adapter-ready rules."""

    seen: set[str] = set()
    rules: list[CadQueryAssemblyConstraintRule] = []
    for constraint in constraints:
        if constraint.id in seen:
            raise ValueError(f"Duplicate assembly constraint rule id `{constraint.id}`")
        seen.add(constraint.id)
        rules.append(
            CadQueryAssemblyConstraintRule.from_constraint(
                constraint,
                feature_catalog,
            )
        )
    return rules


def build_cadquery_assembly_constraint_plan(
    layout: MechanicalLayout,
) -> CadQueryAssemblyConstraintPlan:
    """Build a full constraint plan from `MechanicalLayout`."""

    feature_catalog = build_cadquery_mate_feature_catalog(layout)
    rules = build_cadquery_assembly_constraint_rules(
        layout.assembly_constraints,
        feature_catalog,
    )
    return CadQueryAssemblyConstraintPlan(
        feature_catalog=feature_catalog,
        rules=rules,
    )


def normalize_constraint_kind(kind: str) -> str:
    """Return the stage-6 mate-frame kind for a legacy or new constraint kind."""

    normalized = _NORMALIZED_KIND.get(kind, kind)
    allowed = {
        "MatePlane",
        "MateAxis",
        "MateFrame",
        "Coaxial",
        "Flush",
        "Offset",
        "FixedSeed",
    }
    if normalized not in allowed:
        raise ValueError(f"Unsupported assembly constraint kind `{kind}`")
    return normalized


def build_assembly_mate_constraint_rules(
    constraints: list[AssemblyConstraint],
    mate_catalog: MateFrameCatalog,
) -> list[AssemblyMateConstraintRule]:
    """Resolve neutral constraints into mate-frame rules."""

    seen: set[str] = set()
    rules: list[AssemblyMateConstraintRule] = []
    for constraint in constraints:
        if constraint.id in seen:
            raise ValueError(f"Duplicate assembly constraint rule id `{constraint.id}`")
        seen.add(constraint.id)
        rules.append(
            AssemblyMateConstraintRule.from_constraint(
                constraint,
                mate_catalog,
            )
        )
    return rules


def build_assembly_mate_constraint_plan(
    layout: MechanicalLayout,
) -> AssemblyMateConstraintPlan:
    """Build the stage-6 mate-frame constraint plan from `MechanicalLayout`."""

    mate_catalog = build_mate_frame_catalog(layout)
    rules = build_assembly_mate_constraint_rules(
        layout.assembly_constraints,
        mate_catalog,
    )
    return AssemblyMateConstraintPlan(
        mate_catalog=mate_catalog,
        rules=rules,
    )


def _validate_feature_kinds(rule: CadQueryAssemblyConstraintRule) -> None:
    expected = _expected_selection_kind(rule.kind)
    if expected is None:
        return
    if rule.fixed.selection_kind != expected:
        raise ValueError(
            f"{rule.kind} constraint `{rule.constraint_id}` fixed feature "
            f"`{rule.fixed.feature_id}` must be `{expected}`, got "
            f"`{rule.fixed.selection_kind}`"
        )
    if rule.moving.selection_kind != expected:
        raise ValueError(
            f"{rule.kind} constraint `{rule.constraint_id}` moving feature "
            f"`{rule.moving.feature_id}` must be `{expected}`, got "
            f"`{rule.moving.selection_kind}`"
        )


def _expected_selection_kind(kind: str) -> str | None:
    normalized = normalize_constraint_kind(kind)
    if normalized in {"FixedSeed", "MatePlane", "Flush", "Offset"}:
        return "face"
    if normalized in {"MateAxis", "Coaxial"}:
        return "axis"
    if normalized == "MateFrame":
        return "point"
    raise ValueError(f"Unsupported assembly constraint kind `{kind}`")


def _validate_mate_kinds(rule: AssemblyMateConstraintRule) -> None:
    if rule.normalized_kind in {"MatePlane", "Flush", "Offset", "FixedSeed"}:
        _require_feature_type(rule, {"plane", "face"})
    elif rule.normalized_kind in {"MateAxis", "Coaxial"}:
        _require_feature_type(rule, {"axis"})
    elif rule.normalized_kind == "MateFrame":
        return
    else:
        raise ValueError(
            f"Unsupported assembly constraint kind `{rule.normalized_kind}`"
        )


def _require_feature_type(
    rule: AssemblyMateConstraintRule,
    allowed: set[str],
) -> None:
    fixed_type = str(rule.fixed_mate.metadata.get("feature_type"))
    moving_type = str(rule.moving_mate.metadata.get("feature_type"))
    if fixed_type not in allowed:
        raise ValueError(
            f"{rule.normalized_kind} constraint `{rule.constraint_id}` fixed mate "
            f"`{rule.fixed_feature_id}` must reference {sorted(allowed)}, got `{fixed_type}`"
        )
    if moving_type not in allowed:
        raise ValueError(
            f"{rule.normalized_kind} constraint `{rule.constraint_id}` moving mate "
            f"`{rule.moving_feature_id}` must reference {sorted(allowed)}, got `{moving_type}`"
        )


def _is_unsafe_body_axis_joint_axis(
    fixed_mate: MateFrame,
    moving_mate: MateFrame,
    normalized_kind: str,
) -> bool:
    if normalized_kind not in {"MateAxis", "Coaxial"}:
        return False
    semantics = {fixed_mate.semantic, moving_mate.semantic}
    feature_ids = {fixed_mate.feature_id, moving_mate.feature_id}
    return "joint_axis" in semantics and any(
        feature_id.endswith(".body_axis") for feature_id in feature_ids
    )
