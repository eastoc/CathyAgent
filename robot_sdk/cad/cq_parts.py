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
from robot_sdk.types import JointLayout, LinkLayout, MechanicalLayout


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
        solid = _build_part_solid(cq, layout, part_id, kind, rules)
        selectors = _feature_selectors_for_part(part_id, kind, rules)
        tagged_solid = _apply_feature_tags(solid, rules, selectors)
        parts[part_id] = CadQueryPart(
            part_id=part_id,
            kind=kind,
            solid=tagged_solid,
            feature_rules=rules,
            feature_selectors=selectors,
            metadata=_part_metadata(layout, part_id, kind, rules),
        )

    return CadQueryPartCatalog(by_part_id=parts)


def _build_part_solid(
    cq: Any,
    layout: MechanicalLayout,
    part_id: str,
    kind: str,
    rules: list[CadQueryMateFeatureRule],
) -> Any:
    if kind == "base":
        return _add_datum_geometry(cq, _build_base_part(cq), "base", rules)
    if kind == "joint":
        return _add_datum_geometry(cq, _build_joint_part(cq, _joint_layout(layout, part_id)), "joint", rules)
    if kind == "link":
        return _add_datum_geometry(cq, _build_link_part(cq, _link_layout(layout, part_id)), "link", rules)
    if kind == "end_effector":
        return _add_datum_geometry(cq, _build_end_effector_part(cq), "end_effector", rules)
    return _add_datum_geometry(cq, _build_generic_part(cq), "generic", rules)


def _build_base_part(cq: Any) -> Any:
    return cq.Workplane("XY").box(
        DEFAULT_BASE_LENGTH_MM,
        DEFAULT_BASE_WIDTH_MM,
        DEFAULT_BASE_THICKNESS_MM,
    )


def _build_joint_part(cq: Any, joint: JointLayout) -> Any:
    if joint.actuator_envelope is None:
        raise KeyError(f"Joint `{joint.id}` has no actuator envelope")
    envelope = joint.actuator_envelope
    radius = envelope.dimensions.get("radius", 25.0)
    depth = envelope.dimensions.get("length", 35.0)
    morphology = joint.metadata.get("morphology")

    if morphology == "base_yaw_joint":
        return _build_base_yaw_joint_part(cq, radius, depth)
    if morphology == "shoulder_joint":
        return _build_shoulder_joint_part(cq, radius, depth)
    if morphology == "elbow_joint":
        return _build_elbow_joint_part(cq, radius, depth)
    if morphology == "wrist_pitch_joint":
        return _build_wrist_pitch_joint_part(cq, radius, depth)
    if morphology == "wrist_roll_joint":
        return _build_wrist_roll_joint_part(cq, radius, depth)
    if morphology == "tool_flange_joint":
        return _build_tool_flange_joint_part(cq, radius, depth)
    return _build_generic_joint_part(cq, radius, depth)


def _build_generic_joint_part(cq: Any, radius: float, depth: float) -> Any:
    return _build_inline_joint_housing(
        cq,
        radius=radius,
        depth=depth,
        input_flange_radius=max(radius * 1.12, DEFAULT_FLANGE_RADIUS_MM),
        output_flange_radius=max(radius * 1.12, DEFAULT_FLANGE_RADIUS_MM),
    )


def _build_base_yaw_joint_part(cq: Any, radius: float, depth: float) -> Any:
    housing = _build_inline_joint_housing(
        cq,
        radius=radius * 1.08,
        depth=depth * 1.05,
        input_flange_radius=max(radius * 1.45, DEFAULT_FLANGE_RADIUS_MM),
        output_flange_radius=max(radius * 1.2, DEFAULT_FLANGE_RADIUS_MM),
    )
    skirt = (
        cq.Workplane("XY")
        .box(depth * 1.15, radius * 2.4, radius * 0.55)
        .translate((0.0, 0.0, -radius * 0.65))
    )
    return housing.union(skirt)


def _build_shoulder_joint_part(cq: Any, radius: float, depth: float) -> Any:
    housing = _build_inline_joint_housing(
        cq,
        radius=radius * 1.12,
        depth=depth * 1.18,
        input_flange_radius=max(radius * 1.25, DEFAULT_FLANGE_RADIUS_MM),
        output_flange_radius=max(radius * 1.2, DEFAULT_FLANGE_RADIUS_MM),
    )
    motor_bulge = (
        cq.Workplane("XY")
        .box(depth * 0.78, radius * 1.25, radius * 1.75)
        .translate((0.0, radius * 0.95, 0.0))
    )
    return housing.union(motor_bulge)


def _build_elbow_joint_part(cq: Any, radius: float, depth: float) -> Any:
    housing = _build_inline_joint_housing(
        cq,
        radius=radius,
        depth=depth,
        input_flange_radius=max(radius * 1.16, DEFAULT_FLANGE_RADIUS_MM),
        output_flange_radius=max(radius * 1.16, DEFAULT_FLANGE_RADIUS_MM),
    )
    side_cap = (
        cq.Workplane("XY")
        .box(depth * 0.55, radius * 0.9, radius * 1.25)
        .translate((0.0, -radius * 0.78, 0.0))
    )
    return housing.union(side_cap)


def _build_wrist_pitch_joint_part(cq: Any, radius: float, depth: float) -> Any:
    return _build_inline_joint_housing(
        cq,
        radius=radius * 0.78,
        depth=depth * 0.78,
        input_flange_radius=max(radius * 0.96, DEFAULT_FLANGE_RADIUS_MM * 0.78),
        output_flange_radius=max(radius * 0.96, DEFAULT_FLANGE_RADIUS_MM * 0.78),
        flange_thickness=DEFAULT_FLANGE_THICKNESS_MM * 0.78,
    )


def _build_wrist_roll_joint_part(cq: Any, radius: float, depth: float) -> Any:
    return _build_inline_joint_housing(
        cq,
        radius=radius * 0.68,
        depth=depth * 0.64,
        input_flange_radius=max(radius * 0.86, DEFAULT_FLANGE_RADIUS_MM * 0.68),
        output_flange_radius=max(radius * 0.86, DEFAULT_FLANGE_RADIUS_MM * 0.68),
        flange_thickness=DEFAULT_FLANGE_THICKNESS_MM * 0.68,
    )


def _build_tool_flange_joint_part(cq: Any, radius: float, depth: float) -> Any:
    core_depth = depth * 0.52
    housing = _build_inline_joint_housing(
        cq,
        radius=radius * 0.58,
        depth=core_depth,
        input_flange_radius=max(radius * 0.86, DEFAULT_FLANGE_RADIUS_MM * 0.7),
        output_flange_radius=max(radius * 1.08, DEFAULT_FLANGE_RADIUS_MM * 0.85),
        flange_thickness=DEFAULT_FLANGE_THICKNESS_MM * 0.62,
    )
    tool_register = (
        cq.Workplane("YZ")
        .circle(radius * 0.42)
        .extrude(depth * 0.28)
        .translate((core_depth / 2.0, 0.0, 0.0))
    )
    return housing.union(tool_register)


def _build_inline_joint_housing(
    cq: Any,
    *,
    radius: float,
    depth: float,
    input_flange_radius: float,
    output_flange_radius: float,
    flange_thickness: float = DEFAULT_FLANGE_THICKNESS_MM,
) -> Any:
    flange_radius = max(radius * 1.12, DEFAULT_FLANGE_RADIUS_MM)

    housing = (
        cq.Workplane("YZ")
        .circle(radius)
        .extrude(depth)
        .translate((-depth / 2.0, 0.0, 0.0))
    )
    input_flange = (
        cq.Workplane("YZ")
        .circle(input_flange_radius or flange_radius)
        .extrude(flange_thickness)
        .translate((-depth / 2.0 - flange_thickness, 0.0, 0.0))
    )
    output_flange = (
        cq.Workplane("YZ")
        .circle(output_flange_radius or flange_radius)
        .extrude(flange_thickness)
        .translate((depth / 2.0, 0.0, 0.0))
    )
    return housing.union(input_flange).union(output_flange)


def _build_link_part(cq: Any, link: LinkLayout) -> Any:
    dims = link.envelope.dimensions
    length = dims["length"]
    width = dims["width"]
    height = dims["height"]
    morphology = link.metadata.get("morphology")
    primitive_type = link.metadata.get("primitive_type", "straight_link")

    if morphology == "wrist1_offset_housing":
        return _build_offset_link_part(cq, link, length, width, height)
    if morphology == "wrist2_elbow_cylinder":
        return _build_wrist_elbow_cylinder_link_part(cq, link, length, width, height)
    if morphology in {"wrist3_tool_flange", "terminal_tool_spacer"}:
        return _build_wrist_spacer_link_part(cq, length, width, height)
    if morphology in {"generic_offset_link"}:
        return _build_offset_link_part(cq, link, length, width, height)
    if morphology in {"generic_elbow_link"}:
        return _build_elbow_link_part(cq, link, length, width, height)
    if morphology in {"generic_wrist_spacer"}:
        return _build_wrist_spacer_link_part(cq, length, width, height)

    if primitive_type == "offset_link":
        return _build_offset_link_part(cq, link, length, width, height)
    if primitive_type == "elbow_link":
        return _build_elbow_link_part(cq, link, length, width, height)
    if primitive_type == "wrist_spacer":
        return _build_wrist_spacer_link_part(cq, length, width, height)
    return _build_straight_link_part(cq, length, width, height)


def _build_straight_link_part(
    cq: Any,
    length: float,
    width: float,
    height: float,
) -> Any:
    """Build the default straight beam link along local X."""

    body = cq.Workplane("XY").box(length, width, height)
    return _add_link_flange_disks(cq, body, length, width, height)


def _build_offset_link_part(
    cq: Any,
    link: LinkLayout,
    length: float,
    width: float,
    height: float,
) -> Any:
    """Build a concept offset link as two parallel beams plus a route-aware bridge."""

    segment_length = max(length * 0.42, width)
    offset = max(width * 0.75, height * 0.75)
    input_beam = (
        cq.Workplane("XY")
        .box(segment_length, width, height)
        .translate(_offset_translation(link, -length * 0.25, -offset / 2.0))
    )
    output_beam = (
        cq.Workplane("XY")
        .box(segment_length, width, height)
        .translate(_offset_translation(link, length * 0.25, offset / 2.0))
    )
    bridge = _build_offset_bridge(cq, link, width, offset, height)
    body = input_beam.union(output_beam).union(bridge)
    return _add_link_flange_disks(cq, body, length, width, height)


def _build_elbow_link_part(
    cq: Any,
    link: LinkLayout,
    length: float,
    width: float,
    height: float,
) -> Any:
    """Build a concept elbow link with an output-side wrist turn.

    The primary beam still spans the link's input/output interface positions
    along local X. A secondary branch is added near the output interface and is
    oriented toward the next joint axis when that direction is available in
    `link_primitive` metadata. This keeps the coarse part visually honest for
    UR-style wrist transitions such as L5 -> J6.
    """

    branch = _elbow_branch_rule(link)
    branch_length = max(min(length * 0.6, width * 1.8), width)
    axial = cq.Workplane("XY").box(length, width, height)
    elbow = _build_elbow_branch(cq, branch, length, width, height, branch_length)
    hub_radius = max(width, height) * 0.5
    hub = (
        cq.Workplane("XY")
        .circle(hub_radius)
        .extrude(height)
        .translate((length / 2.0 - width / 2.0, 0.0, -height / 2.0))
    )
    body = axial.union(elbow).union(hub)
    return _add_link_flange_disks(cq, body, length, width, height)


def _offset_translation(
    link: LinkLayout,
    x: float,
    signed_offset: float,
) -> tuple[float, float, float]:
    axis, sign = _route_offset_axis(link)
    value = signed_offset * sign
    if axis == "Z":
        return (x, 0.0, value)
    return (x, value, 0.0)


def _build_offset_bridge(
    cq: Any,
    link: LinkLayout,
    width: float,
    offset: float,
    height: float,
) -> Any:
    axis, _sign = _route_offset_axis(link)
    if axis == "Z":
        return cq.Workplane("XY").box(width, width, offset + height)
    return cq.Workplane("XY").box(width, offset + width, height)


def _route_offset_axis(link: LinkLayout) -> tuple[str, float]:
    primitive = link.metadata.get("link_primitive")
    route_offset = None
    if isinstance(primitive, dict):
        route_offset = _vector3_or_none(primitive.get("route_offset_vector"))
    if route_offset is None:
        return ("Y", 1.0)
    y_component = route_offset[1]
    z_component = route_offset[2]
    if abs(z_component) > abs(y_component):
        return ("Z", 1.0 if z_component >= 0 else -1.0)
    return ("Y", 1.0 if y_component >= 0 else -1.0)


def _build_wrist_elbow_cylinder_link_part(
    cq: Any,
    link: LinkLayout,
    length: float,
    width: float,
    height: float,
) -> Any:
    """Build a cylindrical wrist elbow for compact cross-axis transitions."""

    radius = max(width, height) * 0.45
    branch = _elbow_branch_rule(link)
    branch_length = max(min(length * 0.55, width * 1.8), width)
    axial = (
        cq.Workplane("YZ")
        .circle(radius)
        .extrude(length)
        .translate((-length / 2.0, 0.0, 0.0))
    )
    elbow = _build_cylindrical_elbow_branch(
        cq,
        branch,
        length,
        radius,
        branch_length,
    )
    hub = (
        cq.Workplane("YZ")
        .circle(radius * 1.15)
        .extrude(width)
        .translate((length / 2.0 - width, 0.0, 0.0))
    )
    body = axial.union(elbow).union(hub)
    return _add_link_flange_disks(cq, body, length, width, height)


def _build_cylindrical_elbow_branch(
    cq: Any,
    branch: tuple[str, float],
    length: float,
    radius: float,
    branch_length: float,
) -> Any:
    axis, sign = branch
    x = length / 2.0 - radius
    if axis == "Z":
        return (
            cq.Workplane("XY")
            .circle(radius)
            .extrude(branch_length)
            .translate((x, 0.0, sign * branch_length / 2.0))
        )
    return (
        cq.Workplane("XZ")
        .circle(radius)
        .extrude(branch_length)
        .translate((x, sign * branch_length / 2.0, 0.0))
    )


def _build_elbow_branch(
    cq: Any,
    branch: tuple[str, float],
    length: float,
    width: float,
    height: float,
    branch_length: float,
) -> Any:
    axis, sign = branch
    x = length / 2.0 - width / 2.0
    if axis == "Z":
        return (
            cq.Workplane("XY")
            .box(width, width, branch_length)
            .translate((x, 0.0, sign * branch_length / 2.0))
        )
    return (
        cq.Workplane("XY")
        .box(width, branch_length, height)
        .translate((x, sign * branch_length / 2.0, 0.0))
    )


def _elbow_branch_rule(link: LinkLayout) -> tuple[str, float]:
    primitive = link.metadata.get("link_primitive")
    if not isinstance(primitive, dict):
        return ("Y", 1.0)
    route_offset = _vector3_or_none(primitive.get("route_offset_vector"))
    if route_offset is not None:
        y_component = route_offset[1]
        z_component = route_offset[2]
        if abs(z_component) > 1e-9 or abs(y_component) > 1e-9:
            if abs(z_component) > abs(y_component):
                return ("Z", 1.0 if z_component >= 0 else -1.0)
            return ("Y", 1.0 if y_component >= 0 else -1.0)
    body_direction = _vector3_or_none(primitive.get("body_direction"))
    next_axis = _vector3_or_none(primitive.get("next_joint_axis_direction"))
    if body_direction is None or next_axis is None:
        return ("Y", 1.0)

    local_x = _unit_vector(body_direction)
    local_z = _normal_for_x_dir(local_x)
    local_y = _unit_vector(_cross(local_z, local_x))
    next_axis_unit = _unit_vector(next_axis)
    y_dot = _dot(local_y, next_axis_unit)
    z_dot = _dot(local_z, next_axis_unit)
    if abs(z_dot) > abs(y_dot):
        return ("Z", 1.0 if z_dot >= 0 else -1.0)
    return ("Y", 1.0 if y_dot >= 0 else -1.0)


def _vector3_or_none(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _unit_vector(
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    length = (vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2) ** 0.5
    if length <= 1e-9:
        return (1.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _normal_for_x_dir(
    x_dir: tuple[float, float, float],
) -> tuple[float, float, float]:
    candidate = (0.0, 0.0, 1.0)
    if abs(_dot(x_dir, candidate)) > 0.95:
        candidate = (0.0, 1.0, 0.0)
    projection = _dot(candidate, x_dir)
    normal = (
        candidate[0] - projection * x_dir[0],
        candidate[1] - projection * x_dir[1],
        candidate[2] - projection * x_dir[2],
    )
    return _unit_vector(normal)


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _build_wrist_spacer_link_part(
    cq: Any,
    length: float,
    width: float,
    height: float,
) -> Any:
    """Build a short cylindrical spacer for wrist/flange transition links."""

    radius = max(width, height) * 0.45
    body = (
        cq.Workplane("YZ")
        .circle(radius)
        .extrude(length)
        .translate((-length / 2.0, 0.0, 0.0))
    )
    return _add_link_flange_disks(cq, body, length, width, height)


def _add_link_flange_disks(
    cq: Any,
    body: Any,
    length: float,
    width: float,
    height: float,
) -> Any:
    """Add local-X input/output flange disks to a concept link body."""

    flange_radius = max(width, height) * 0.65
    flange_thickness = DEFAULT_FLANGE_THICKNESS_MM

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


def _add_datum_geometry(
    cq: Any,
    solid: Any,
    kind: str,
    rules: list[CadQueryMateFeatureRule],
) -> Any:
    body = solid
    semantics = {rule.semantic for rule in rules}
    if "joint_axis" in semantics and kind == "joint":
        body = body.union(_datum_axis_pin(cq))
    if "tool_mount" in semantics and kind == "end_effector":
        body = body.union(_tool_mount_register(cq))
    if "mount_face" in semantics and kind == "base":
        body = body.union(_mount_face_reference_pad(cq))
    return body


def _datum_axis_pin(cq: Any) -> Any:
    return (
        cq.Workplane("YZ")
        .circle(4.0)
        .extrude(70.0)
        .translate((-35.0, 0.0, 0.0))
    )


def _tool_mount_register(cq: Any) -> Any:
    outer = (
        cq.Workplane("YZ")
        .circle(12.0)
        .extrude(3.0)
        .translate((DEFAULT_END_EFFECTOR_THICKNESS_MM / 2.0 + DEFAULT_TOOL_STUB_LENGTH_MM, 0.0, 0.0))
    )
    pilot = (
        cq.Workplane("YZ")
        .circle(5.0)
        .extrude(6.0)
        .translate((DEFAULT_END_EFFECTOR_THICKNESS_MM / 2.0 + DEFAULT_TOOL_STUB_LENGTH_MM + 3.0, 0.0, 0.0))
    )
    return outer.union(pilot)


def _mount_face_reference_pad(cq: Any) -> Any:
    return (
        cq.Workplane("XY")
        .box(DEFAULT_BASE_LENGTH_MM * 0.55, DEFAULT_BASE_WIDTH_MM * 0.5, 1.5)
        .translate((0.0, 0.0, DEFAULT_BASE_THICKNESS_MM / 2.0 + 0.75))
    )


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
        if tag in {"axis", "body_axis", "bottom_tangent", "output_tangent", "input_tangent"}:
            return "axis:X"
    if kind == "end_effector":
        if tag == "mount":
            return "<X"
        if tag == "mount_tangent":
            return "axis:X"
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


def _part_metadata(
    layout: MechanicalLayout,
    part_id: str,
    kind: str,
    rules: list[CadQueryMateFeatureRule],
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "units": layout.units,
        "feature_count": len(rules),
        "feature_semantics": sorted({rule.semantic for rule in rules}),
        "datum_geometry_applied": _datum_geometry_applied(kind, rules),
    }
    if kind == "joint":
        joint = _joint_layout(layout, part_id)
        metadata["morphology"] = joint.metadata.get("morphology")
        metadata["layout_decision"] = joint.metadata.get("layout_decision")
    if kind == "link":
        link = _link_layout(layout, part_id)
        metadata["morphology"] = link.metadata.get("morphology")
        metadata["primitive_type"] = link.metadata.get("primitive_type")
        metadata["link_primitive"] = link.metadata.get("link_primitive")
        metadata["layout_decision"] = link.metadata.get("layout_decision")
    return metadata


def _datum_geometry_applied(
    kind: str,
    rules: list[CadQueryMateFeatureRule],
) -> list[str]:
    semantics = {rule.semantic for rule in rules}
    applied: list[str] = []
    if kind == "joint" and "joint_axis" in semantics:
        applied.append("joint_axis_pin")
    if kind == "end_effector" and "tool_mount" in semantics:
        applied.append("tool_mount_register")
    if kind == "base" and "mount_face" in semantics:
        applied.append("mount_face_reference_pad")
    return applied


def _joint_layout(layout: MechanicalLayout, joint_id: str) -> JointLayout:
    for joint in layout.joints:
        if joint.id == joint_id:
            return joint
    raise KeyError(f"Unknown joint `{joint_id}`")


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
