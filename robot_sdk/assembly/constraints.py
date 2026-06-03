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
from robot_sdk.types import AssemblyConstraint, MechanicalLayout


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
    if kind in {"Fixed", "Plane"}:
        return "face"
    if kind == "Axis":
        return "axis"
    if kind == "Point":
        return "point"
    raise ValueError(f"Unsupported assembly constraint kind `{kind}`")
