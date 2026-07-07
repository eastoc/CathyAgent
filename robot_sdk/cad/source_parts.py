"""Minimal build123d source parts with part-local joints.

These builders are intentionally small. They create coarse robot primitives and
register named joints directly on each part so source-level assembly can call
native build123d ``connect_to`` without an intermediate pose compiler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from robot_sdk.cad.source_assembly import add_axis_frame, add_rigid_frame, label_shape


DEFAULT_BASE_LENGTH_MM = 140.0
DEFAULT_BASE_WIDTH_MM = 120.0
DEFAULT_BASE_THICKNESS_MM = 10.0
DEFAULT_JOINT_RADIUS_MM = 25.0
DEFAULT_JOINT_LENGTH_MM = 35.0
DEFAULT_LINK_WIDTH_MM = 24.0
DEFAULT_LINK_HEIGHT_MM = 20.0
DEFAULT_LINK_LENGTH_MM = 100.0
DEFAULT_TOOL_FLANGE_RADIUS_MM = 20.0
DEFAULT_TOOL_FLANGE_THICKNESS_MM = 12.0
MIN_ROUTE_SEGMENT_MM = 1.0


@dataclass(frozen=True)
class SourcePart:
    """One build123d part plus standard source joint metadata."""

    part_id: str
    kind: str
    solid: Any
    frame_names: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourcePartCatalog:
    """Indexed build123d source parts."""

    by_part_id: dict[str, SourcePart]

    def require(self, part_id: str) -> SourcePart:
        try:
            return self.by_part_id[part_id]
        except KeyError as exc:
            raise KeyError(f"Unknown source part `{part_id}`") from exc

    def part_ids(self) -> list[str]:
        return list(self.by_part_id)


def build_source_base_part(
    part_id: str = "base",
    *,
    length: float = DEFAULT_BASE_LENGTH_MM,
    width: float = DEFAULT_BASE_WIDTH_MM,
    thickness: float = DEFAULT_BASE_THICKNESS_MM,
) -> SourcePart:
    """Build a rectangular base plate with mount/output joints on its top face."""

    b = _import_build123d()
    part = label_shape(b.Box(length, width, thickness), part_id)
    top_z = thickness / 2.0
    add_rigid_frame(part, "mount", b.Location((0.0, 0.0, top_z)))
    add_rigid_frame(part, "output", b.Location((0.0, 0.0, top_z)))
    add_axis_frame(
        part,
        "axis",
        b.Axis((0.0, 0.0, top_z), (0.0, 0.0, 1.0)),
        joint_type="RevoluteJoint",
    )
    return SourcePart(
        part_id=part_id,
        kind="base",
        solid=part,
        frame_names=["mount", "output", "axis"],
        metadata={"length": length, "width": width, "thickness": thickness},
    )


def build_source_joint_part(
    part_id: str,
    *,
    radius: float = DEFAULT_JOINT_RADIUS_MM,
    length: float = DEFAULT_JOINT_LENGTH_MM,
) -> SourcePart:
    """Build a simple inline joint housing with input/output/axis joints."""

    b = _import_build123d()
    part = label_shape(
        b.Cylinder(radius, length, rotation=(0.0, 90.0, 0.0)),
        part_id,
    )
    input_x = -length / 2.0
    output_x = length / 2.0
    add_rigid_frame(part, "input", b.Location((input_x, 0.0, 0.0)))
    add_rigid_frame(part, "output", b.Location((output_x, 0.0, 0.0)))
    add_axis_frame(
        part,
        "axis",
        b.Axis((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        joint_type="RevoluteJoint",
    )
    return SourcePart(
        part_id=part_id,
        kind="joint",
        solid=part,
        frame_names=["input", "output", "axis"],
        metadata={"radius": radius, "length": length},
    )


def build_source_structure_joint_part(
    part_id: str,
    *,
    axis_role: str | None = None,
    morphology: str | None = None,
    radius: float = DEFAULT_JOINT_RADIUS_MM,
    length: float = DEFAULT_JOINT_LENGTH_MM,
) -> SourcePart:
    """Build a station joint whose source datums live at the station origin.

    Unlike ``build_source_joint_part()``, this is not an inline spacer. It is
    meant for structure-first assembly: the adjacent link route owns the
    station-to-station displacement, while the joint housing owns only local
    input/output/axis datums.
    """

    b = _import_build123d()
    axis_direction = _axis_direction_for_role(axis_role)
    primitive_family = _joint_primitive_family(axis_role, morphology)
    visual_features = _joint_visual_features(primitive_family)
    part = label_shape(
        _structure_joint_shape(
            b,
            radius=radius,
            length=length,
            axis_direction=axis_direction,
            primitive_family=primitive_family,
        ),
        part_id,
    )
    add_rigid_frame(part, "input", b.Location((0.0, 0.0, 0.0)))
    add_rigid_frame(part, "output", b.Location((0.0, 0.0, 0.0)))
    add_axis_frame(
        part,
        "axis",
        b.Axis((0.0, 0.0, 0.0), axis_direction),
        joint_type="RevoluteJoint",
    )
    return SourcePart(
        part_id=part_id,
        kind="structure_joint",
        solid=part,
        frame_names=["input", "output", "axis"],
        metadata={
            "axis_role": axis_role,
            "morphology": morphology,
            "radius": radius,
            "length": length,
            "primitive_family": primitive_family,
            "visual_features": visual_features,
            "source_joint_mode": "station_datum",
        },
    )


def build_source_link_part(
    part_id: str,
    *,
    length: float = DEFAULT_LINK_LENGTH_MM,
    width: float = DEFAULT_LINK_WIDTH_MM,
    height: float = DEFAULT_LINK_HEIGHT_MM,
) -> SourcePart:
    """Build a box beam with input/output joints on its local X ends."""

    b = _import_build123d()
    part = label_shape(b.Box(length, width, height), part_id)
    input_x = -length / 2.0
    output_x = length / 2.0
    add_rigid_frame(part, "input", b.Location((input_x, 0.0, 0.0)))
    add_rigid_frame(part, "output", b.Location((output_x, 0.0, 0.0)))
    return SourcePart(
        part_id=part_id,
        kind="link",
        solid=part,
        frame_names=["input", "output"],
        metadata={"length": length, "width": width, "height": height},
    )


def build_source_routed_link_part(
    part_id: str,
    *,
    route_vector: tuple[float, float, float],
    route_type: str = "straight",
    morphology: str | None = None,
    width: float = DEFAULT_LINK_WIDTH_MM,
    height: float = DEFAULT_LINK_HEIGHT_MM,
) -> SourcePart:
    """Build a link whose local output datum follows a structure route.

    The part-local ``input`` datum is at the upstream station. The ``output``
    datum is at ``route_vector`` in the part's local source coordinates. This
    lets build123d ``connect_to()`` propagate the robot chain directly from
    source-level datums instead of from an external pose compiler.
    """

    b = _import_build123d()
    vector = _valid_route_vector(route_vector)
    primitive_family = _link_primitive_family(route_type, morphology, vector)
    visual_features = _link_visual_features(primitive_family)
    part = label_shape(
        _route_shape(
            b,
            vector=vector,
            route_type=route_type,
            primitive_family=primitive_family,
            width=width,
            height=height,
        ),
        part_id,
    )
    add_rigid_frame(part, "input", b.Location((0.0, 0.0, 0.0)))
    add_rigid_frame(part, "output", b.Location(vector))
    return SourcePart(
        part_id=part_id,
        kind="routed_link",
        solid=part,
        frame_names=["input", "output"],
        metadata={
            "route_vector": vector,
            "route_type": route_type,
            "morphology": morphology,
            "primitive_family": primitive_family,
            "visual_features": visual_features,
            "width": width,
            "height": height,
            "source_joint_mode": "routed_endpoint",
        },
    )


def build_source_tool_flange_part(
    part_id: str = "end_effector",
    *,
    radius: float = DEFAULT_TOOL_FLANGE_RADIUS_MM,
    thickness: float = DEFAULT_TOOL_FLANGE_THICKNESS_MM,
) -> SourcePart:
    """Build a short tool flange with input and tool mount joints."""

    b = _import_build123d()
    part = label_shape(
        b.Cylinder(radius, thickness, rotation=(0.0, 90.0, 0.0)),
        part_id,
    )
    input_x = -thickness / 2.0
    tool_x = thickness / 2.0
    add_rigid_frame(part, "input", b.Location((input_x, 0.0, 0.0)))
    add_rigid_frame(part, "tool", b.Location((tool_x, 0.0, 0.0)))
    add_axis_frame(
        part,
        "axis",
        b.Axis((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        joint_type="RevoluteJoint",
    )
    return SourcePart(
        part_id=part_id,
        kind="tool_flange",
        solid=part,
        frame_names=["input", "tool", "axis"],
        metadata={"radius": radius, "thickness": thickness},
    )


def build_minimal_source_part_catalog() -> SourcePartCatalog:
    """Build a small hand-authored catalog for source assembly smoke tests."""

    parts = [
        build_source_base_part(),
        build_source_joint_part("J1"),
        build_source_link_part("L1"),
        build_source_tool_flange_part(),
    ]
    return SourcePartCatalog(by_part_id={part.part_id: part for part in parts})


def _import_build123d() -> Any:
    try:
        import build123d
    except ModuleNotFoundError as exc:
        raise RuntimeError("source_parts requires build123d at runtime") from exc
    return build123d


def _axis_direction_for_role(role: str | None) -> tuple[float, float, float]:
    if role in {"base_yaw"}:
        return (0.0, 0.0, 1.0)
    if role in {"shoulder_pitch", "elbow_pitch", "wrist_pitch"}:
        return (0.0, 1.0, 0.0)
    if role in {"wrist_roll", "tool_roll"}:
        return (1.0, 0.0, 0.0)
    return (1.0, 0.0, 0.0)


def _joint_shape_for_axis(
    b: Any,
    *,
    radius: float,
    length: float,
    axis_direction: tuple[float, float, float],
) -> Any:
    if abs(axis_direction[2]) >= 0.9:
        return b.Cylinder(radius, length)
    if abs(axis_direction[1]) >= 0.9:
        return b.Cylinder(radius, length, rotation=(90.0, 0.0, 0.0))
    return b.Cylinder(radius, length, rotation=(0.0, 90.0, 0.0))


def _structure_joint_shape(
    b: Any,
    *,
    radius: float,
    length: float,
    axis_direction: tuple[float, float, float],
    primitive_family: str,
) -> Any:
    """Return a coarse role-specific joint housing.

    The station input/output datums remain at the local origin. Extra housings
    are visual massing cues only; they do not change source mate semantics.
    """

    core = _joint_shape_for_axis(
        b,
        radius=radius,
        length=length,
        axis_direction=axis_direction,
    )
    children: list[Any] = [core]
    if primitive_family == "base_yaw_pedestal":
        children.append(
            b.Location((0.0, 0.0, -length * 0.45))
            * b.Box(radius * 2.4, radius * 2.4, length * 0.45)
        )
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.55,
                length * 0.18,
                0.0,
            )
        )
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.35,
                length * 0.15,
                length * 0.45,
            )
        )
    elif primitive_family == "shoulder_block":
        children.append(
            b.Location((0.0, 0.0, -radius * 0.35))
            * b.Box(length * 1.25, radius * 2.2, radius * 1.35)
        )
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.2,
                length * 0.16,
                -length * 0.45,
            )
        )
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.2,
                length * 0.16,
                length * 0.45,
            )
        )
        children.append(
            b.Location((length * 0.18, 0.0, -radius * 1.05))
            * b.Box(length * 0.72, radius * 1.45, radius * 0.55)
        )
    elif primitive_family == "elbow_block":
        children.append(
            b.Location((0.0, 0.0, -radius * 0.25))
            * b.Box(length, radius * 1.75, radius)
        )
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.12,
                length * 0.14,
                -length * 0.42,
            )
        )
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.12,
                length * 0.14,
                length * 0.42,
            )
        )
    elif primitive_family == "wrist_compact":
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.12,
                length * 0.18,
                -length * 0.48,
            )
        )
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.12,
                length * 0.18,
                length * 0.48,
            )
        )
        children.append(_wrist_sensor_boss(b, axis_direction, radius, length))
    elif primitive_family == "tool_flange_joint":
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 1.35,
                length * 0.25,
                length * 0.35,
            )
        )
        children.append(
            _flange_disk_for_axis(
                b,
                axis_direction,
                radius * 0.55,
                length * 0.22,
                length * 0.62,
            )
        )
    if len(children) == 1:
        return core
    return b.Compound(label=f"{primitive_family}_joint", children=children)


def _flange_disk_for_axis(
    b: Any,
    axis_direction: tuple[float, float, float],
    radius: float,
    thickness: float,
    offset: float,
) -> Any:
    if abs(axis_direction[2]) >= 0.9:
        return b.Location((0.0, 0.0, offset)) * b.Cylinder(radius, thickness)
    if abs(axis_direction[1]) >= 0.9:
        return (
            b.Location((0.0, offset, 0.0))
            * b.Cylinder(radius, thickness, rotation=(90.0, 0.0, 0.0))
        )
    return (
        b.Location((offset, 0.0, 0.0))
        * b.Cylinder(radius, thickness, rotation=(0.0, 90.0, 0.0))
    )


def _valid_route_vector(
    vector: tuple[float, float, float],
) -> tuple[float, float, float]:
    if len(vector) != 3:
        raise ValueError("route_vector must contain 3 values")
    result = (float(vector[0]), float(vector[1]), float(vector[2]))
    if _route_length(result) <= 0:
        return (DEFAULT_LINK_LENGTH_MM, 0.0, 0.0)
    return result


def _route_shape(
    b: Any,
    *,
    vector: tuple[float, float, float],
    route_type: str,
    primitive_family: str,
    width: float,
    height: float,
) -> Any:
    children: list[Any] = []
    points = _route_points(vector, route_type)
    for start, end in zip(points, points[1:]):
        children.extend(
            _axis_segment_shapes(
                b,
                start=start,
                end=end,
                width=width,
                height=height,
                primitive_family=primitive_family,
            )
        )
    for point in points[1:-1]:
        children.append(_route_corner_shape(b, point, width, height, primitive_family))
    children.extend(
        _route_fitting_shapes(
            b,
            points=points,
            width=width,
            height=height,
            primitive_family=primitive_family,
        )
    )
    if not children:
        children.append(
            b.Location((vector[0] / 2.0, 0.0, 0.0))
            * b.Box(max(abs(vector[0]), MIN_ROUTE_SEGMENT_MM), width, height)
        )
    return b.Compound(label=f"{primitive_family}_route", children=children)


def _route_points(
    vector: tuple[float, float, float],
    route_type: str,
) -> list[tuple[float, float, float]]:
    x, y, z = vector
    if route_type == "straight" and abs(y) <= 1e-9 and abs(z) <= 1e-9:
        return [(0.0, 0.0, 0.0), vector]
    points = [(0.0, 0.0, 0.0)]
    if abs(x) > 1e-9:
        points.append((x, 0.0, 0.0))
    if abs(y) > 1e-9:
        points.append((x, y, 0.0))
    if abs(z) > 1e-9:
        points.append((x, y, z))
    if points[-1] != vector:
        points.append(vector)
    return _dedupe_points(points)


def _axis_segment_shapes(
    b: Any,
    *,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    width: float,
    height: float,
    primitive_family: str,
) -> list[Any]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    style = _segment_style(primitive_family)
    if abs(dx) > 1e-9:
        length = max(abs(dx), MIN_ROUTE_SEGMENT_MM)
        center = ((start[0] + end[0]) / 2.0, start[1], start[2])
        return _segment_shapes_for_axis(
            b,
            axis="x",
            center=center,
            length=length,
            width=width,
            height=height,
            style=style,
        )
    if abs(dy) > 1e-9:
        length = max(abs(dy), MIN_ROUTE_SEGMENT_MM)
        center = (start[0], (start[1] + end[1]) / 2.0, start[2])
        return _segment_shapes_for_axis(
            b,
            axis="y",
            center=center,
            length=length,
            width=width,
            height=height,
            style=style,
        )
    if abs(dz) > 1e-9:
        length = max(abs(dz), MIN_ROUTE_SEGMENT_MM)
        center = (start[0], start[1], (start[2] + end[2]) / 2.0)
        return _segment_shapes_for_axis(
            b,
            axis="z",
            center=center,
            length=length,
            width=width,
            height=height,
            style=style,
        )
    return []


def _segment_shapes_for_axis(
    b: Any,
    *,
    axis: str,
    center: tuple[float, float, float],
    length: float,
    width: float,
    height: float,
    style: str,
) -> list[Any]:
    if style == "cylinder":
        radius = max(width, height) * 0.36
        return [
            _cylinder_segment(
                b,
                axis=axis,
                center=center,
                length=length,
                radius=radius,
            )
        ]
    if style == "dual_rail":
        offset = max(width, height) * 0.42
        rail_width = max(width * 0.34, MIN_ROUTE_SEGMENT_MM)
        rail_height = max(height * 0.55, MIN_ROUTE_SEGMENT_MM)
        return [
            _box_segment(
                b,
                axis=axis,
                center=_offset_center(center, axis, offset),
                length=length,
                width=rail_width,
                height=rail_height,
            ),
            _box_segment(
                b,
                axis=axis,
                center=_offset_center(center, axis, -offset),
                length=length,
                width=rail_width,
                height=rail_height,
            ),
        ]
    if style == "plate":
        return [
            _box_segment(
                b,
                axis=axis,
                center=center,
                length=length,
                width=width * 1.18,
                height=height * 0.72,
            )
        ]
    return [
        _box_segment(
            b,
            axis=axis,
            center=center,
            length=length,
            width=width,
            height=height,
        )
    ]


def _box_segment(
    b: Any,
    *,
    axis: str,
    center: tuple[float, float, float],
    length: float,
    width: float,
    height: float,
) -> Any:
    if axis == "x":
        return b.Location(center) * b.Box(length, width, height)
    if axis == "y":
        return b.Location(center) * b.Box(width, length, height)
    return b.Location(center) * b.Box(width, height, length)


def _cylinder_segment(
    b: Any,
    *,
    axis: str,
    center: tuple[float, float, float],
    length: float,
    radius: float,
) -> Any:
    if axis == "z":
        return b.Location(center) * b.Cylinder(radius, length)
    if axis == "y":
        return (
            b.Location(center)
            * b.Cylinder(radius, length, rotation=(90.0, 0.0, 0.0))
        )
    return b.Location(center) * b.Cylinder(radius, length, rotation=(0.0, 90.0, 0.0))


def _route_corner_shape(
    b: Any,
    point: tuple[float, float, float],
    width: float,
    height: float,
    primitive_family: str,
) -> Any:
    if primitive_family == "wrist_elbow_cylinder":
        return b.Location(point) * b.Sphere(max(width, height) * 0.58)
    if _segment_style(primitive_family) == "cylinder":
        return b.Location(point) * b.Cylinder(
            max(width, height) * 0.42,
            max(width, height) * 0.5,
        )
    return b.Location(point) * b.Box(width, width, height)


def _route_fitting_shapes(
    b: Any,
    *,
    points: list[tuple[float, float, float]],
    width: float,
    height: float,
    primitive_family: str,
) -> list[Any]:
    if len(points) < 2:
        return []
    first_axis = _segment_axis(points[0], points[1])
    last_axis = _segment_axis(points[-2], points[-1])
    fittings: list[Any] = []
    if primitive_family in {"upper_arm_dual_rail", "forearm_dual_rail"}:
        fittings.append(_end_block(b, points[0], first_axis, width * 1.1, height * 1.25))
        fittings.append(
            _end_block(b, points[-1], last_axis, width * 1.1, height * 1.25)
        )
        fittings.extend(_dual_rail_cross_braces(b, points, width, height))
    elif primitive_family == "wrist_offset_plate":
        fittings.append(
            _end_block(b, points[0], first_axis, width * 1.25, height * 0.9)
        )
        fittings.append(
            _end_block(b, points[-1], last_axis, width * 1.25, height * 0.9)
        )
    elif primitive_family == "wrist_elbow_cylinder":
        fittings.append(
            _route_flange_disk(b, points[0], first_axis, width * 0.78, height * 0.35)
        )
        fittings.append(
            _route_flange_disk(b, points[-1], last_axis, width * 0.78, height * 0.35)
        )
        fittings.append(
            _route_flange_disk(b, points[-1], last_axis, width * 0.52, height * 0.58)
        )
    elif primitive_family == "wrist_tool_flange":
        fittings.append(
            _route_flange_disk(b, points[0], first_axis, width * 0.82, height * 0.4)
        )
        fittings.append(
            _route_flange_disk(b, points[-1], last_axis, width * 1.0, height * 0.48)
        )
        fittings.append(
            _route_flange_disk(b, points[-1], last_axis, width * 0.5, height * 0.75)
        )
    elif primitive_family == "terminal_tool_spacer":
        fittings.append(
            _route_flange_disk(b, points[0], first_axis, width * 0.7, height * 0.38)
        )
        fittings.append(
            _route_flange_disk(b, points[-1], last_axis, width * 0.82, height * 0.42)
        )
    return fittings


def _segment_axis(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> str:
    dx = abs(end[0] - start[0])
    dy = abs(end[1] - start[1])
    dz = abs(end[2] - start[2])
    if dx >= dy and dx >= dz:
        return "x"
    if dy >= dx and dy >= dz:
        return "y"
    return "z"


def _route_flange_disk(
    b: Any,
    point: tuple[float, float, float],
    axis: str,
    radius: float,
    thickness: float,
) -> Any:
    if axis == "z":
        return b.Location(point) * b.Cylinder(
            radius,
            max(thickness, MIN_ROUTE_SEGMENT_MM),
        )
    if axis == "y":
        return (
            b.Location(point)
            * b.Cylinder(
                radius,
                max(thickness, MIN_ROUTE_SEGMENT_MM),
                rotation=(90.0, 0.0, 0.0),
            )
        )
    return (
        b.Location(point)
        * b.Cylinder(
            radius,
            max(thickness, MIN_ROUTE_SEGMENT_MM),
            rotation=(0.0, 90.0, 0.0),
        )
    )


def _end_block(
    b: Any,
    point: tuple[float, float, float],
    axis: str,
    width: float,
    height: float,
) -> Any:
    length = max(width * 0.45, MIN_ROUTE_SEGMENT_MM)
    if axis == "x":
        return b.Location(point) * b.Box(length, width, height)
    if axis == "y":
        return b.Location(point) * b.Box(width, length, height)
    return b.Location(point) * b.Box(width, height, length)


def _dual_rail_cross_braces(
    b: Any,
    points: list[tuple[float, float, float]],
    width: float,
    height: float,
) -> list[Any]:
    if len(points) < 2:
        return []
    start = points[0]
    end = points[-1]
    braces: list[Any] = []
    for ratio in (0.28, 0.5, 0.72):
        point = (
            start[0] + (end[0] - start[0]) * ratio,
            start[1] + (end[1] - start[1]) * ratio,
            start[2] + (end[2] - start[2]) * ratio,
        )
        braces.append(
            b.Location(point) * b.Box(width * 0.18, width * 1.25, height * 0.34)
        )
    return braces


def _segment_style(primitive_family: str) -> str:
    if primitive_family in {"wrist_elbow_cylinder", "wrist_tool_flange", "terminal_tool_spacer"}:
        return "cylinder"
    if primitive_family in {"upper_arm_dual_rail", "forearm_dual_rail"}:
        return "dual_rail"
    if primitive_family in {"wrist_offset_plate"}:
        return "plate"
    return "box"


def _wrist_sensor_boss(
    b: Any,
    axis_direction: tuple[float, float, float],
    radius: float,
    length: float,
) -> Any:
    boss_size = radius * 0.55
    if abs(axis_direction[1]) >= 0.9:
        return b.Location((0.0, 0.0, radius * 0.9)) * b.Box(boss_size, length * 0.3, boss_size)
    if abs(axis_direction[2]) >= 0.9:
        return b.Location((radius * 0.9, 0.0, 0.0)) * b.Box(boss_size, boss_size, length * 0.3)
    return b.Location((0.0, radius * 0.9, 0.0)) * b.Box(length * 0.3, boss_size, boss_size)


def _joint_visual_features(primitive_family: str) -> list[str]:
    features = {
        "base_yaw_pedestal": ["pedestal_block", "base_flange", "top_flange"],
        "shoulder_block": ["side_flanges", "shoulder_support_block", "motor_housing"],
        "elbow_block": ["side_flanges", "compact_elbow_housing"],
        "wrist_compact": ["dual_flanges", "sensor_boss", "compact_barrel"],
        "tool_flange_joint": ["tool_flange", "pilot_hub"],
        "generic_joint": ["generic_barrel"],
    }
    return list(features.get(primitive_family, features["generic_joint"]))


def _link_visual_features(primitive_family: str) -> list[str]:
    features = {
        "upper_arm_dual_rail": ["dual_rail", "end_blocks", "cross_brace"],
        "forearm_dual_rail": ["dual_rail", "end_blocks", "cross_brace"],
        "wrist_offset_plate": ["offset_plate", "compact_end_blocks"],
        "wrist_elbow_cylinder": ["bent_cylindrical_housing", "elbow_ball", "dual_flanges", "pilot_hub"],
        "wrist_tool_flange": ["terminal_flange", "pilot_hub", "short_barrel"],
        "terminal_tool_spacer": ["tool_spacer", "dual_flanges"],
        "generic_elbow_box": ["box_elbow"],
        "generic_offset_box": ["offset_box"],
        "generic_straight_box": ["straight_box"],
    }
    return list(features.get(primitive_family, ["generic_link"]))


def _offset_center(
    center: tuple[float, float, float],
    axis: str,
    offset: float,
) -> tuple[float, float, float]:
    if axis == "x":
        return (center[0], center[1] + offset, center[2])
    return (center[0] + offset, center[1], center[2])


def _dedupe_points(
    points: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    result: list[tuple[float, float, float]] = []
    for point in points:
        if not result or point != result[-1]:
            result.append(point)
    return result


def _route_length(vector: tuple[float, float, float]) -> float:
    return sum(component * component for component in vector) ** 0.5


def _joint_primitive_family(axis_role: str | None, morphology: str | None) -> str:
    value = _specific_morphology(morphology) or (axis_role or "").strip()
    if value in {"base_yaw_joint", "base_yaw"}:
        return "base_yaw_pedestal"
    if value in {"shoulder_joint", "shoulder_pitch"}:
        return "shoulder_block"
    if value in {"elbow_joint", "elbow_pitch"}:
        return "elbow_block"
    if value in {"wrist_pitch_joint", "wrist_roll_joint", "wrist_pitch", "wrist_roll"}:
        return "wrist_compact"
    if value in {"tool_flange_joint", "tool_roll"}:
        return "tool_flange_joint"
    return "generic_joint"


def _link_primitive_family(
    route_type: str,
    morphology: str | None,
    route_vector: tuple[float, float, float],
) -> str:
    value = _specific_morphology(morphology)
    if value in {"upper_arm_link"}:
        return "upper_arm_dual_rail"
    if value in {"forearm_link"}:
        return "forearm_dual_rail"
    if value in {"wrist1_offset_housing"}:
        return "wrist_offset_plate"
    if value in {"wrist2_elbow_cylinder"}:
        return "wrist_elbow_cylinder"
    if value in {"wrist3_tool_flange"}:
        return "wrist_tool_flange"
    if value in {"terminal_tool_spacer"} or route_type == "tool_stub":
        return "terminal_tool_spacer"
    if morphology == "generic_wrist_spacer":
        return "terminal_tool_spacer"
    if route_type == "wrist_spacer":
        return "wrist_tool_flange"
    if route_type == "elbow":
        if _route_length(route_vector) <= 80.0:
            return "wrist_elbow_cylinder"
        return "generic_elbow_box"
    if route_type == "offset":
        return "generic_offset_box"
    return "generic_straight_box"


def _specific_morphology(morphology: str | None) -> str | None:
    value = (morphology or "").strip()
    if not value or value.startswith("generic_"):
        return None
    return value


__all__ = [
    "SourcePart",
    "SourcePartCatalog",
    "build_minimal_source_part_catalog",
    "build_source_base_part",
    "build_source_joint_part",
    "build_source_link_part",
    "build_source_routed_link_part",
    "build_source_structure_joint_part",
    "build_source_tool_flange_part",
]
