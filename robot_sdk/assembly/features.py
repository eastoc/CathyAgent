"""CadQuery-consumable mate feature rules.

`PartFeature` is the neutral layout contract. This module turns those neutral
records into stable rules that CAD builders can consume when they create
CadQuery solids and attach tags used by later assembly constraints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from robot_sdk.types import MechanicalLayout, PartFeature


CadQuerySelectionKind = Literal["face", "axis", "point", "edge"]

_CAD_TAG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class CadQueryMateFeatureRule:
    """How one neutral `PartFeature` should appear in CadQuery.

    `cad_tag` is the tag that `cq_parts.py` should attach to the generated
    Workplane or selected entity. `constraint_ref` is an SDK-stable reference
    that later adapters can resolve back to this rule.
    """

    feature_id: str
    part_id: str
    cad_tag: str
    selection_kind: CadQuerySelectionKind
    semantic: str
    frame: str | None = None
    constraint_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.feature_id:
            raise ValueError("CadQueryMateFeatureRule.feature_id is required")
        if not self.part_id:
            raise ValueError("CadQueryMateFeatureRule.part_id is required")
        if not _CAD_TAG_RE.match(self.cad_tag):
            raise ValueError(
                f"CadQuery tag `{self.cad_tag}` must be a Python/CadQuery-friendly identifier"
            )
        if not self.constraint_ref:
            object.__setattr__(
                self,
                "constraint_ref",
                f"{self.part_id}.{self.cad_tag}",
            )

    @classmethod
    def from_part_feature(cls, feature: PartFeature) -> "CadQueryMateFeatureRule":
        """Create a CadQuery rule from a neutral `PartFeature`."""

        return cls(
            feature_id=feature.id,
            part_id=feature.part_id,
            cad_tag=feature.cad_tag,
            selection_kind=_selection_kind_for(feature),
            semantic=feature.semantic,
            frame=feature.frame,
            constraint_ref=feature.id,
            metadata=dict(feature.metadata),
        )

    @property
    def cq_tag_path(self) -> str:
        """SDK path used by downstream CadQuery adapters for lookup."""

        return f"{self.part_id}@{self.cad_tag}"


@dataclass(frozen=True)
class CadQueryMateFeatureCatalog:
    """Indexed feature rules for CAD part generation and assembly adapters."""

    by_feature_id: dict[str, CadQueryMateFeatureRule]

    @classmethod
    def from_rules(
        cls,
        rules: list[CadQueryMateFeatureRule],
    ) -> "CadQueryMateFeatureCatalog":
        by_feature_id: dict[str, CadQueryMateFeatureRule] = {}
        for rule in rules:
            if rule.feature_id in by_feature_id:
                raise ValueError(f"Duplicate mate feature rule id `{rule.feature_id}`")
            by_feature_id[rule.feature_id] = rule
        return cls(by_feature_id=by_feature_id)

    def require(self, feature_id: str) -> CadQueryMateFeatureRule:
        """Return one rule or raise a precise error."""

        try:
            return self.by_feature_id[feature_id]
        except KeyError as exc:
            raise KeyError(f"Unknown mate feature `{feature_id}`") from exc

    def for_part(self, part_id: str) -> list[CadQueryMateFeatureRule]:
        """Return all feature rules for one CAD part in deterministic order."""

        return [
            rule
            for rule in self.by_feature_id.values()
            if rule.part_id == part_id
        ]

    def constraint_refs(self) -> set[str]:
        return {rule.constraint_ref for rule in self.by_feature_id.values()}


def build_cadquery_mate_feature_rules(
    part_features: list[PartFeature],
) -> list[CadQueryMateFeatureRule]:
    """Convert neutral features into CadQuery tagging rules."""

    rules = [
        CadQueryMateFeatureRule.from_part_feature(feature)
        for feature in part_features
    ]
    CadQueryMateFeatureCatalog.from_rules(rules)
    return rules


def build_cadquery_mate_feature_catalog(
    layout: MechanicalLayout,
) -> CadQueryMateFeatureCatalog:
    """Build an indexed CadQuery feature catalog from `MechanicalLayout`."""

    rules = build_cadquery_mate_feature_rules(layout.part_features)
    catalog = CadQueryMateFeatureCatalog.from_rules(rules)
    _check_constraints_are_resolvable(layout, catalog)
    return catalog


def tag_workplane_feature(workplane: Any, rule: CadQueryMateFeatureRule) -> Any:
    """Apply the rule's CadQuery tag to a Workplane-like object.

    CadQuery is intentionally not imported at module import time. This keeps the
    deterministic SDK tests runnable in environments that do not have CadQuery,
    while still allowing `cq_parts.py` to pass real CadQuery Workplanes here.
    """

    tag = getattr(workplane, "tag", None)
    if not callable(tag):
        raise TypeError("workplane must provide a CadQuery-compatible tag(name) method")
    return tag(rule.cad_tag)


def _check_constraints_are_resolvable(
    layout: MechanicalLayout,
    catalog: CadQueryMateFeatureCatalog,
) -> None:
    for constraint in layout.assembly_constraints:
        catalog.require(constraint.fixed)
        catalog.require(constraint.moving)


def _selection_kind_for(feature: PartFeature) -> CadQuerySelectionKind:
    if feature.type in {"plane", "face"}:
        return "face"
    if feature.type == "axis":
        return "axis"
    if feature.type == "point":
        return "point"
    if feature.type == "edge":
        return "edge"
    raise ValueError(f"Unsupported PartFeature.type `{feature.type}`")
