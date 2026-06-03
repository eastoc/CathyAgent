"""Adapter from neutral assembly constraints to CadQuery calls.

The functions here do one job: translate resolved SDK constraint rules into
`Assembly.constrain(...)` calls. They do not create parts and do not solve the
assembly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from robot_sdk.assembly.constraints import (
    CadQueryAssemblyConstraintPlan,
    build_cadquery_assembly_constraint_plan,
)
from robot_sdk.types import MechanicalLayout

if TYPE_CHECKING:
    from robot_sdk.cad.cq_parts import CadQueryPartCatalog


@dataclass(frozen=True)
class CadQueryConstraintCall:
    """One planned call to `Assembly.constrain(...)`."""

    constraint_id: str
    kind: str
    fixed_query: str
    moving_query: str | None = None
    param: Any | None = None
    flipped: bool = False
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.constraint_id:
            raise ValueError("CadQueryConstraintCall.constraint_id is required")
        if not self.fixed_query:
            raise ValueError("CadQueryConstraintCall.fixed_query is required")
        if self.kind != "Fixed" and not self.moving_query:
            raise ValueError("Binary CadQuery constraints require moving_query")

    def args(self) -> tuple[Any, ...]:
        """Return positional args for `Assembly.constrain`."""

        if self.kind == "Fixed":
            if self.param is None:
                return (self.fixed_query, self.kind)
            return (self.fixed_query, self.kind, self.param)
        if self.param is None:
            return (self.fixed_query, self.moving_query, self.kind)
        return (self.fixed_query, self.moving_query, self.kind, self.param)


def build_cadquery_constraint_calls(
    constraint_plan: CadQueryAssemblyConstraintPlan,
    part_catalog: "CadQueryPartCatalog",
) -> list[CadQueryConstraintCall]:
    """Build ordered CadQuery `Assembly.constrain` calls."""

    calls: list[CadQueryConstraintCall] = []
    for rule in constraint_plan.rules:
        fixed_query = _query_for_feature(
            part_catalog,
            part_id=rule.fixed.part_id,
            feature_id=rule.fixed.feature_id,
            cad_tag=rule.fixed.cad_tag,
            selection_kind=rule.fixed.selection_kind,
        )
        moving_query = _query_for_feature(
            part_catalog,
            part_id=rule.moving.part_id,
            feature_id=rule.moving.feature_id,
            cad_tag=rule.moving.cad_tag,
            selection_kind=rule.moving.selection_kind,
        )
        calls.append(
            CadQueryConstraintCall(
                constraint_id=rule.constraint_id,
                kind=rule.kind,
                fixed_query=fixed_query,
                moving_query=None if rule.kind == "Fixed" else moving_query,
                param=rule.offset,
                flipped=rule.flipped,
                rationale=rule.rationale,
                metadata={
                    "fixed_ref": rule.fixed_ref,
                    "moving_ref": rule.moving_ref,
                    "fixed_part_id": rule.fixed.part_id,
                    "moving_part_id": rule.moving.part_id,
                    "fixed_cq_tag_path": rule.fixed_cq_tag_path,
                    "moving_cq_tag_path": rule.moving_cq_tag_path,
                },
            )
        )
    return calls


def build_layout_cadquery_constraint_calls(
    layout: MechanicalLayout,
    part_catalog: "CadQueryPartCatalog",
) -> list[CadQueryConstraintCall]:
    """Build CadQuery constraint calls directly from a `MechanicalLayout`."""

    plan = build_cadquery_assembly_constraint_plan(layout)
    return build_cadquery_constraint_calls(plan, part_catalog)


def apply_cadquery_constraint_calls(
    assembly: Any,
    calls: list[CadQueryConstraintCall],
) -> Any:
    """Apply planned calls to a CadQuery Assembly-like object."""

    constrain = getattr(assembly, "constrain", None)
    if not callable(constrain):
        raise TypeError("assembly must provide a CadQuery-compatible constrain method")
    for call in calls:
        constrain(*call.args())
    return assembly


def apply_layout_constraints_to_assembly(
    assembly: Any,
    layout: MechanicalLayout,
    part_catalog: "CadQueryPartCatalog",
) -> Any:
    """Resolve and apply layout constraints to a CadQuery Assembly-like object."""

    calls = build_layout_cadquery_constraint_calls(layout, part_catalog)
    return apply_cadquery_constraint_calls(assembly, calls)


def _query_for_feature(
    part_catalog: "CadQueryPartCatalog",
    *,
    part_id: str,
    feature_id: str,
    cad_tag: str,
    selection_kind: str,
) -> str:
    part = part_catalog.require(part_id)
    selector = part.selector_for(feature_id)
    if selection_kind == "face":
        return f"{part_id}?{cad_tag}"
    if selection_kind == "axis":
        return _axis_query(part_id, selector)
    if selection_kind == "point":
        return f"{part_id}@vertices@{selector}"
    if selection_kind == "edge":
        return f"{part_id}@edges@{selector}"
    raise ValueError(f"Unsupported CadQuery selection kind `{selection_kind}`")


def _axis_query(part_id: str, selector: str) -> str:
    """Turn an SDK axis selector into a CadQuery query.

    CadQuery constraints can derive an axis from a planar face normal. MVP axis
    features are therefore resolved to representative faces.
    """

    if selector.startswith("axis:"):
        axis = selector.split(":", 1)[1]
        return f"{part_id}@faces@>{axis}"
    return f"{part_id}@faces@{selector}"
