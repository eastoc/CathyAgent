"""Coarse CadQuery part generation from `MechanicalLayout`.

This module creates concept-level CAD parts only:
- base mount plate
- cylindrical joint housings with simple flange disks
- box-beam links with simple flange disks
- end-effector mount block

It does not solve assembly constraints. Downstream modules should consume the
returned part catalog together with assembly constraint rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from robot_sdk.assembly.features import (
    CadQueryMateFeatureRule,
    build_cadquery_mate_feature_catalog,
)
from robot_sdk.layout.validation import assert_valid_mechanical_layout
from robot_sdk.types import EnvelopeSpec, LinkLayout, MechanicalLayout


DEFAULT_BASE_LENGTH_MM = 140.0
DEFAULT_BASE_WIDTH_MM = 120.0
DEFAULT_BASE_THICKNESS_MM = 10.0
DEFAULT_FLANGE_RADIUS_MM = 28.0
DEFAULT_FLANGE_THICKNESS_MM = 8.0
DEFAULT_END_EFFECTOR_SIZE_MM = 30.0
DEFAULT_END_EFFECTOR_THICKNESS_MM = 12.0
DEFAULT_TOOL_STUB_LENGTH_MM = 20.0
DEFAULT_TOOL_STUB_RADIUS_MM = 6.0


@dataclass(frozen=True)
class CadQueryPart:
    """One generated CadQuery part plus its mate feature metadata."""

    part_id: str
    kind: str
    solid: Any
    feature_rules: list[CadQueryMateFeatureRule]
    feature_selectors: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def selector_for(self, feature_id: str) -> str:
        try:
            return self.feature_selectors[feature_id]
        except KeyError as exc:
            raise KeyError(
                f"Part `{self.part_id}` has no selector for feature `{feature_id}`"
            ) from exc


@dataclass(frozen=True)
class CadQueryPartCatalog:
    """Indexed generated CadQuery parts."""

    by_part_id: dict[str, CadQueryPart]

    def require(self, part_id: str) -> CadQueryPart:
        try:
            return self.by_part_id[part_id]
        except KeyError as exc:
            raise KeyError(f"Unknown CadQuery part `{part_id}`") from exc

    def part_ids(self) -> list[str]:
        return list(self.by_part_id)


def build_cadquery_parts(
    layout: MechanicalLayout,
    *,
    cq_module: Any | None = None,
) -> CadQueryPartCatalog:
    """Generate coarse CadQuery parts from `MechanicalLayout`.

    `cq_module` is injectable for tests. In production, leave it unset and this
    function will import `cadquery` lazily.
    """

    assert_valid_mechanical_layout(layout)
    cq = cq_module or _import_cadquery()
    feature_catalog = build_cadquery_mate_feature_catalog(layout)
    part_ids = _ordered_part_ids(layout)
    parts: dict[str, CadQueryPart] = {}

    for part_id in part_ids:
        rules = feature_catalog.for_part(part_id)
        kind = _part_kind(part_id, layout)
        solid = _build_part_solid(cq, layout, part_id, kind)
        selectors = _feature_selectors_for_part(part_id, kind, rules)
        tagged_solid = _apply_feature_tags(solid, rules, selectors)
        parts[part_id] = CadQueryPart(
            part_id=part_id,
            kind=kind,
            solid=tagged_solid,
            feature_rules=rules,
            feature_selectors=selectors,
            metadata={
                "units": layout.units,
                "feature_count": len(rules),
            },
        )

    return CadQueryPartCatalog(by_part_id=parts)


def _build_part_solid(
    cq: Any,
    layout: MechanicalLayout,
    part_id: str,
    kind: str,
) -> Any:
    if kind == "base":
        return _build_base_part(cq)
    if kind == "joint":
        return _build_joint_part(cq, _joint_envelope(layout, part_id))
    if kind == "link":
        return _build_link_part(cq, _link_layout(layout, part_id))
    if kind == "end_effector":
        return _build_end_effector_part(cq)
    return _build_generic_part(cq)


def _build_base_part(cq: Any) -> Any:
    return cq.Workplane("XY").box(
        DEFAULT_BASE_LENGTH_MM,
        DEFAULT_BASE_WIDTH_MM,
        DEFAULT_BASE_THICKNESS_MM,
    )


def _build_joint_part(cq: Any, envelope: EnvelopeSpec) -> Any:
    radius = envelope.dimensions.get("radius", 25.0)
    depth = envelope.dimensions.get("length", 35.0)
    flange_radius = max(radius * 1.12, DEFAULT_FLANGE_RADIUS_MM)
    flange_thickness = DEFAULT_FLANGE_THICKNESS_MM

    housing = (
        cq.Workplane("YZ")
        .circle(radius)
        .extrude(depth)
        .translate((-depth / 2.0, 0.0, 0.0))
    )
    input_flange = (
        cq.Workplane("YZ")
        .circle(flange_radius)
        .extrude(flange_thickness)
        .translate((-depth / 2.0 - flange_thickness, 0.0, 0.0))
    )
    output_flange = (
        cq.Workplane("YZ")
        .circle(flange_radius)
        .extrude(flange_thickness)
        .translate((depth / 2.0, 0.0, 0.0))
    )
    return housing.union(input_flange).union(output_flange)


def _build_link_part(cq: Any, link: LinkLayout) -> Any:
    dims = link.envelope.dimensions
    length = dims["length"]
    width = dims["width"]
    height = dims["height"]
    flange_radius = max(width, height) * 0.65
    flange_thickness = DEFAULT_FLANGE_THICKNESS_MM

    body = cq.Workplane("XY").box(length, width, height)
    input_flange = (
        cq.Workplane("YZ")
        .circle(flange_radius)
        .extrude(flange_thickness)
        .translate((-length / 2.0 - flange_thickness, 0.0, 0.0))
    )
    output_flange = (
        cq.Workplane("YZ")
        .circle(flange_radius)
        .extrude(flange_thickness)
        .translate((length / 2.0, 0.0, 0.0))
    )
    return body.union(input_flange).union(output_flange)


def _build_end_effector_part(cq: Any) -> Any:
    mount = cq.Workplane("XY").box(
        DEFAULT_END_EFFECTOR_THICKNESS_MM,
        DEFAULT_END_EFFECTOR_SIZE_MM,
        DEFAULT_END_EFFECTOR_SIZE_MM,
    )
    tool_stub = (
        cq.Workplane("YZ")
        .circle(DEFAULT_TOOL_STUB_RADIUS_MM)
        .extrude(DEFAULT_TOOL_STUB_LENGTH_MM)
        .translate((DEFAULT_END_EFFECTOR_THICKNESS_MM / 2.0, 0.0, 0.0))
    )
    return mount.union(tool_stub)


def _build_generic_part(cq: Any) -> Any:
    return cq.Workplane("XY").box(20.0, 20.0, 20.0)


def _apply_feature_tags(
    solid: Any,
    rules: list[CadQueryMateFeatureRule],
    selectors: dict[str, str],
) -> Any:
    tagged = solid
    for rule in rules:
        selector = selectors.get(rule.feature_id)
        tagged = _tag_one_feature(tagged, rule, selector)
    return tagged


def _tag_one_feature(
    solid: Any,
    rule: CadQueryMateFeatureRule,
    selector: str | None,
) -> Any:
    try:
        if rule.selection_kind == "face" and selector:
            selected = solid.faces(selector).tag(rule.cad_tag)
            return selected.end() if hasattr(selected, "end") else selected
        if rule.selection_kind == "edge" and selector:
            selected = solid.edges(selector).tag(rule.cad_tag)
            return selected.end() if hasattr(selected, "end") else selected
        if rule.selection_kind == "point" and selector:
            selected = solid.vertices(selector).tag(rule.cad_tag)
            return selected.end() if hasattr(selected, "end") else selected
        return solid.tag(rule.cad_tag)
    except AttributeError:
        return solid.tag(rule.cad_tag)


def _feature_selectors_for_part(
    part_id: str,
    kind: str,
    rules: list[CadQueryMateFeatureRule],
) -> dict[str, str]:
    selectors: dict[str, str] = {}
    for rule in rules:
        selectors[rule.feature_id] = _selector_for_rule(part_id, kind, rule)
    return selectors


def _selector_for_rule(
    part_id: str,
    kind: str,
    rule: CadQueryMateFeatureRule,
) -> str:
    tag = rule.cad_tag
    if kind == "base":
        if tag == "top":
            return ">Z"
        if tag == "axis":
            return "axis:Z"
    if kind in {"joint", "link"}:
        if tag in {"bottom", "input_face"}:
            return "<X"
        if tag in {"output_flange", "output_face"}:
            return ">X"
        if tag in {"axis", "body_axis"}:
            return "axis:X"
    if kind == "end_effector":
        if tag == "mount":
            return "<X"
        if tag == "tool":
            return ">X"
    if rule.selection_kind == "face":
        return ">Z"
    if rule.selection_kind == "point":
        return ">X"
    if rule.selection_kind == "edge":
        return "|Z"
    return "axis:X"


def _ordered_part_ids(layout: MechanicalLayout) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for feature in layout.part_features:
        if feature.part_id in seen:
            continue
        seen.add(feature.part_id)
        ordered.append(feature.part_id)
    return ordered


def _part_kind(part_id: str, layout: MechanicalLayout) -> str:
    if part_id == "base":
        return "base"
    if part_id == "end_effector":
        return "end_effector"
    if any(joint.id == part_id for joint in layout.joints):
        return "joint"
    if any(link.id == part_id for link in layout.links):
        return "link"
    return "generic"


def _joint_envelope(layout: MechanicalLayout, joint_id: str) -> EnvelopeSpec:
    for joint in layout.joints:
        if joint.id == joint_id and joint.actuator_envelope:
            return joint.actuator_envelope
    raise KeyError(f"Joint `{joint_id}` has no actuator envelope")


def _link_layout(layout: MechanicalLayout, link_id: str) -> LinkLayout:
    for link in layout.links:
        if link.id == link_id:
            return link
    raise KeyError(f"Unknown link `{link_id}`")


def _import_cadquery() -> Any:
    try:
        import cadquery as cq  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "cadquery is required to build CadQuery parts. Install it or call "
            "build_cadquery_parts(..., cq_module=...) in tests."
        ) from exc
    return cq
