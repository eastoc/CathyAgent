"""Convert RobotStructurePlan into MechanicalLayout.

The structure adapter is the bridge from structure-first planning into the
existing CAD pipeline.  It consumes explicit stations, joint axes, link routes,
and datums instead of deriving every part position from DH `a/d` rows.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from robot_sdk.layout.link_primitives import (
    PRIMITIVE_METADATA_KEY,
    PRIMITIVE_TYPE_METADATA_KEY,
    apply_link_primitive_metadata,
)
from robot_sdk.layout.mechanical_layout import (
    DEFAULT_BASE_THICKNESS_MM,
    DEFAULT_FLANGE_RADIUS_MM,
    DEFAULT_FLANGE_THICKNESS_MM,
    DEFAULT_JOINT_DEPTH_MM,
    DEFAULT_JOINT_RADIUS_MM,
    DEFAULT_LINK_HEIGHT_MM,
    DEFAULT_LINK_WIDTH_MM,
    DEFAULT_TOOL_OFFSET_MM,
    _append_end_effector_frames,
    _base_part_features,
    _base_top_frame_id,
    _dedupe_assembly_constraints,
    _dedupe_envelopes,
    _dedupe_frames,
    _dedupe_interfaces,
    _dedupe_part_features,
    _end_effector_assembly_constraints,
    _end_effector_part_features,
    _flange_envelope,
    _incoming_interface_id,
    _interface_direction_metadata,
    _joint_link_assembly_constraints,
    _joint_link_frames,
    _joint_link_part_features,
    _orthogonal_tangent,
    _outgoing_interface_id,
    _unit_vector,
    _vector_between,
    _vector_length,
)
from robot_sdk.structure.ids import (
    body_frame_id,
    end_effector_frame_id,
    generate_serial_chain_ids,
    interface_frame_id,
    joint_axis_frame_id,
    kinematic_frame_id,
    mount_frame_id,
)
from robot_sdk.structure.robot_structure_plan import (
    DatumPlan,
    JointAxisPlan,
    LinkRoutePlan,
    RobotStructurePlan,
)
from robot_sdk.types import (
    AssemblyConstraint,
    EnvelopeSpec,
    FrameMapping,
    FrameSpec,
    InterfaceSpec,
    JointLayout,
    KinematicModel,
    LinkLayout,
    MechanicalLayout,
    PartFeature,
    Transform,
)


STRUCTURE_PLAN_TEMPLATE = "structure_plan"


def build_mechanical_layout_from_structure_plan(
    model: KinematicModel,
    structure_plan: RobotStructurePlan,
    *,
    link_width: float = DEFAULT_LINK_WIDTH_MM,
    link_height: float = DEFAULT_LINK_HEIGHT_MM,
    joint_radius: float = DEFAULT_JOINT_RADIUS_MM,
    joint_depth: float = DEFAULT_JOINT_DEPTH_MM,
    flange_radius: float = DEFAULT_FLANGE_RADIUS_MM,
    flange_thickness: float = DEFAULT_FLANGE_THICKNESS_MM,
    tool_offset: float = DEFAULT_TOOL_OFFSET_MM,
) -> MechanicalLayout:
    """Build MechanicalLayout from an explicit structure plan.

    The kinematic model still provides joint/link IDs and joint types.  The
    structure plan owns the mechanical station positions, joint-axis
    directions, link routes, and datum intent.
    """

    if not model.joints:
        raise ValueError("KinematicModel.joints cannot be empty")
    if len(model.joints) != len(model.links):
        raise ValueError("KinematicModel joints and links must have the same count")
    if len(structure_plan.joint_axes) < len(model.joints):
        raise ValueError("RobotStructurePlan has fewer joint axes than the model")
    if len(structure_plan.link_routes) < len(model.links):
        raise ValueError("RobotStructurePlan has fewer link routes than the model")

    ids = generate_serial_chain_ids(len(model.joints))
    joint_axes = _joint_axes_by_id(structure_plan)
    link_routes = _link_routes_by_id(structure_plan)
    station_origins = _station_origins(structure_plan)

    base_frame = FrameSpec(
        id=mount_frame_id("base"),
        parent=None,
        transform=Transform(),
        semantic="mount",
        description="Fixed base mounting frame from RobotStructurePlan.",
    )
    frames: list[FrameSpec] = [
        base_frame,
        FrameSpec(
            id=_base_top_frame_id(),
            parent=base_frame.id,
            transform=Transform(
                translation=(0.0, 0.0, DEFAULT_BASE_THICKNESS_MM / 2.0),
            ),
            semantic="interface",
            description="Physical top mounting face of the base.",
        ),
        FrameSpec(
            id="base_kinematic",
            parent=base_frame.id,
            transform=Transform(),
            semantic="kinematic",
            description="Abstract base frame for kinematic references.",
        ),
    ]
    joints: list[JointLayout] = []
    links: list[LinkLayout] = []
    interfaces: list[InterfaceSpec] = []
    envelopes: list[EnvelopeSpec] = []
    part_features: list[PartFeature] = _base_part_features(
        _base_top_frame_id(),
        base_frame.id,
    )
    assembly_constraints: list[AssemblyConstraint] = []
    frame_mappings: list[FrameMapping] = [
        FrameMapping(
            kinematic_frame_id="base_kinematic",
            mechanical_frame_id=base_frame.id,
            rationale=(
                "Structure adapter maps the abstract base kinematic frame to "
                "the fixed base mount; this is not a DH-to-CAD mate transform."
            ),
        )
    ]

    previous_link_id = ids.base_link
    link_vectors: list[tuple[float, float, float]] = []
    for index, joint in enumerate(model.joints):
        joint_id = ids.joints[index]
        link_id = ids.links[index]
        axis_plan = joint_axes.get(joint_id)
        route = link_routes.get(link_id)
        if axis_plan is None:
            raise ValueError(f"Missing JointAxisPlan for `{joint_id}`")
        if route is None:
            raise ValueError(f"Missing LinkRoutePlan for `{link_id}`")

        station_position = _axis_origin(axis_plan, station_origins)
        route_start = station_origins.get(route.from_station_id, station_position)
        route_end = station_origins.get(route.to_station_id)
        if route_end is None:
            raise ValueError(
                f"LinkRoutePlan `{route.link_id}` references missing station `{route.to_station_id}`"
            )
        link_vector = _vector_between(route_start, route_end)
        if _vector_length(link_vector) <= 0:
            link_vector = (50.0, 0.0, 0.0)
        axis_direction = _unit_vector(axis_plan.direction)
        interface_normal = _unit_vector(link_vector)
        interface_tangent = _orthogonal_tangent(interface_normal, axis_direction)
        link_vectors.append(link_vector)

        axis_frame_id = joint_axis_frame_id(joint_id)
        link_body_frame_id = body_frame_id(link_id)
        joint_kinematic_frame_id = kinematic_frame_id(joint_id)
        joint_to_link_interface_id = f"{joint_id}_to_{link_id}_flange"
        incoming_interface_id = _incoming_interface_id(ids, index)
        outgoing_interface_id = _outgoing_interface_id(ids, index)

        frames.extend(
            _joint_link_frames(
                base_frame_id=base_frame.id,
                station_position=station_position,
                link_vector=link_vector,
                axis_direction=axis_direction,
                flange_thickness=flange_thickness,
                joint_id=joint_id,
                link_id=link_id,
                axis_frame_id=axis_frame_id,
                link_body_frame_id=link_body_frame_id,
                joint_kinematic_frame_id=joint_kinematic_frame_id,
                incoming_interface_id=incoming_interface_id,
                joint_to_link_interface_id=joint_to_link_interface_id,
                outgoing_interface_id=outgoing_interface_id,
                base_top_frame_id=_base_top_frame_id(),
                is_first_joint=index == 0,
            )
        )

        joint_envelope = EnvelopeSpec(
            id=f"{joint_id}_joint_housing_envelope",
            shape="cylinder",
            dimensions={"radius": joint_radius, "length": joint_depth},
            frame=axis_frame_id,
            description=f"Simplified joint housing for {joint_id}.",
        )
        link_envelope = EnvelopeSpec(
            id=f"{link_id}_structure_link_envelope",
            shape="box",
            dimensions={
                "length": _vector_length(link_vector),
                "width": link_width,
                "height": link_height,
            },
            frame=link_body_frame_id,
            description=f"Structure-plan link envelope for {link_id}.",
        )
        incoming_envelope = _flange_envelope(
            incoming_interface_id,
            interface_frame_id(incoming_interface_id),
            flange_radius,
            flange_thickness,
        )
        joint_to_link_envelope = _flange_envelope(
            joint_to_link_interface_id,
            interface_frame_id(joint_to_link_interface_id),
            flange_radius,
            flange_thickness,
        )
        outgoing_envelope = _flange_envelope(
            outgoing_interface_id,
            interface_frame_id(outgoing_interface_id),
            flange_radius,
            flange_thickness,
        )
        envelopes.extend(
            [
                joint_envelope,
                link_envelope,
                incoming_envelope,
                joint_to_link_envelope,
                outgoing_envelope,
            ]
        )
        part_features.extend(
            _joint_link_part_features(
                joint_id=joint_id,
                link_id=link_id,
                axis_frame_id=axis_frame_id,
                joint_to_link_interface_id=joint_to_link_interface_id,
                incoming_interface_id=incoming_interface_id,
                outgoing_interface_id=outgoing_interface_id,
                link_body_frame_id=link_body_frame_id,
            )
        )
        assembly_constraints.extend(
            _joint_link_assembly_constraints(
                ids=ids,
                index=index,
                joint_id=joint_id,
                link_id=link_id,
            )
        )
        interfaces.extend(
            [
                InterfaceSpec(
                    id=incoming_interface_id,
                    frame=interface_frame_id(incoming_interface_id),
                    type="flange",
                    mates_to=joint_to_link_interface_id if index == 0 else None,
                    envelope=incoming_envelope,
                    metadata=_interface_direction_metadata(
                        normal=_scale(interface_normal, -1.0),
                        tangent=interface_tangent,
                    ),
                ),
                InterfaceSpec(
                    id=joint_to_link_interface_id,
                    frame=interface_frame_id(joint_to_link_interface_id),
                    type="flange",
                    mates_to=incoming_interface_id,
                    envelope=joint_to_link_envelope,
                    metadata=_interface_direction_metadata(
                        normal=interface_normal,
                        tangent=interface_tangent,
                    ),
                ),
                InterfaceSpec(
                    id=outgoing_interface_id,
                    frame=interface_frame_id(outgoing_interface_id),
                    type="end_effector_mount" if index + 1 == len(model.joints) else "flange",
                    mates_to=None,
                    envelope=outgoing_envelope,
                    metadata=_interface_direction_metadata(
                        normal=interface_normal,
                        tangent=interface_tangent,
                    ),
                ),
            ]
        )
        joints.append(
            JointLayout(
                id=joint_id,
                type=joint.type,
                axis_frame=axis_frame_id,
                parent_link=previous_link_id,
                child_link=link_id,
                actuator_envelope=joint_envelope,
                metadata={
                    "structure_axis_role": axis_plan.role,
                    "structure_station_id": axis_plan.station_id,
                },
            )
        )
        links.append(
            LinkLayout(
                id=link_id,
                body_frame=link_body_frame_id,
                from_interface=joint_to_link_interface_id,
                to_interface=outgoing_interface_id,
                envelope=link_envelope,
                metadata={
                    "structure_route_type": route.route_type,
                    "structure_from_station": route.from_station_id,
                    "structure_to_station": route.to_station_id,
                },
            )
        )
        frame_mappings.extend(
            [
                FrameMapping(
                    kinematic_frame_id=joint_kinematic_frame_id,
                    mechanical_frame_id=axis_frame_id,
                    rationale=(
                        f"{joint_id} axis frame comes from RobotStructurePlan "
                        "JointAxisPlan, not from direct DH/CAD mate conversion."
                    ),
                ),
                FrameMapping(
                    kinematic_frame_id=joint_kinematic_frame_id,
                    mechanical_frame_id=link_body_frame_id,
                    rationale=(
                        f"{link_id} body frame follows RobotStructurePlan "
                        "LinkRoutePlan between mechanical stations."
                    ),
                ),
            ]
        )
        previous_link_id = link_id

    last_link_vector = link_vectors[-1] if link_vectors else (50.0, 0.0, 0.0)
    tool_station = _tool_origin(structure_plan, station_origins)
    _append_end_effector_frames(
        frames=frames,
        frame_mappings=frame_mappings,
        last_link_id=ids.links[-1],
        end_position=tool_station,
        last_link_vector=last_link_vector,
        tool_offset=tool_offset,
    )
    part_features.extend(_end_effector_part_features())
    assembly_constraints.extend(_end_effector_assembly_constraints(ids.links[-1]))

    datum_frames, datum_features = _datum_frames_and_features(
        structure_plan.datums,
        base_frame_id=base_frame.id,
    )
    frames.extend(datum_frames)
    part_features.extend(datum_features)

    layout = MechanicalLayout(
        units=structure_plan.units,
        base_frame=base_frame,
        frames=_dedupe_frames(frames),
        joints=joints,
        links=links,
        interfaces=_dedupe_interfaces(interfaces),
        frame_mappings=frame_mappings,
        part_features=_dedupe_part_features(part_features),
        assembly_constraints=_dedupe_assembly_constraints(assembly_constraints),
        envelopes=_dedupe_envelopes(envelopes),
        assumptions=[
            f"Mechanical layout uses `{STRUCTURE_PLAN_TEMPLATE}` adapter.",
            "RobotStructurePlan stations drive mechanical placement.",
            "Joint axes come from JointAxisPlan; DH frames are not direct CAD mates.",
            *structure_plan.assumptions,
        ],
        warnings=list(structure_plan.warnings),
        metadata={
            "layout_source": structure_plan.source,
            "robot_family": structure_plan.family,
            "layout_template": STRUCTURE_PLAN_TEMPLATE,
            "structure_plan_name": structure_plan.name,
            "structure_plan": structure_plan.to_dict(),
            "local_subassemblies": [
                {
                    "name": item.name,
                    "part_ids": item.part_ids,
                    "anchor_part_id": item.anchor_part_id,
                    "role": item.role,
                    "reason": item.rationale,
                    "confidence": 0.8,
                    "source_signals": ["source=RobotStructurePlan"],
                }
                for item in structure_plan.subassemblies
            ],
        },
    )
    layout = apply_link_primitive_metadata(layout)
    return _apply_structure_route_metadata(layout, structure_plan)


def _joint_axes_by_id(plan: RobotStructurePlan) -> dict[str, JointAxisPlan]:
    return {item.joint_id: item for item in plan.joint_axes}


def _link_routes_by_id(plan: RobotStructurePlan) -> dict[str, LinkRoutePlan]:
    return {item.link_id: item for item in plan.link_routes}


def _station_origins(plan: RobotStructurePlan) -> dict[str, tuple[float, float, float]]:
    return {item.id: item.origin for item in plan.stations}


def _axis_origin(
    axis: JointAxisPlan,
    station_origins: dict[str, tuple[float, float, float]],
) -> tuple[float, float, float]:
    if axis.origin is not None:
        return axis.origin
    if axis.station_id not in station_origins:
        raise ValueError(
            f"JointAxisPlan `{axis.joint_id}` references missing station `{axis.station_id}`"
        )
    return station_origins[axis.station_id]


def _tool_origin(
    plan: RobotStructurePlan,
    station_origins: dict[str, tuple[float, float, float]],
) -> tuple[float, float, float]:
    for station in plan.stations:
        if station.role == "tool":
            return station.origin
    if station_origins:
        return list(station_origins.values())[-1]
    return (0.0, 0.0, 0.0)


def _datum_frames_and_features(
    datums: list[DatumPlan],
    *,
    base_frame_id: str,
) -> tuple[list[FrameSpec], list[PartFeature]]:
    frames: list[FrameSpec] = []
    features: list[PartFeature] = []
    for datum in datums:
        frame_id = f"{datum.id}_frame"
        frames.append(
            FrameSpec(
                id=frame_id,
                parent=base_frame_id,
                transform=Transform(
                    translation=datum.origin,
                    rotation_rpy=_rpy_from_normal(datum.normal),
                ),
                semantic=_datum_frame_semantic(datum),
                description=datum.description or f"Datum frame for {datum.id}.",
            )
        )
        feature = _datum_feature(datum, frame_id)
        if feature is not None:
            features.append(feature)
    return frames, features


def _datum_feature(datum: DatumPlan, frame_id: str) -> PartFeature | None:
    part_id = "end_effector" if datum.owner_id == "tool" else datum.owner_id
    if datum.semantic == "mount_plane":
        return PartFeature(
            id=f"{part_id}.datum.{datum.id}",
            part_id=part_id,
            cad_tag=f"datum_{datum.id}",
            type="plane",
            semantic="mount_face",
            frame=frame_id,
        )
    if datum.semantic == "joint_axis":
        return PartFeature(
            id=f"{part_id}.datum.{datum.id}",
            part_id=part_id,
            cad_tag=f"datum_{datum.id}",
            type="axis",
            semantic="joint_axis",
            frame=frame_id,
        )
    if datum.semantic == "tool_plane":
        return PartFeature(
            id=f"end_effector.datum.{datum.id}",
            part_id="end_effector",
            cad_tag=f"datum_{datum.id}",
            type="plane",
            semantic="tool_mount",
            frame=frame_id,
        )
    if datum.semantic == "interface_plane":
        return PartFeature(
            id=f"{part_id}.datum.{datum.id}",
            part_id=part_id,
            cad_tag=f"datum_{datum.id}",
            type="plane",
            semantic="flange_face",
            frame=frame_id,
        )
    return None


def _datum_frame_semantic(datum: DatumPlan) -> str:
    if datum.semantic == "joint_axis":
        return "joint_axis"
    if datum.semantic == "mount_plane":
        return "mount"
    if datum.semantic == "tool_plane":
        return "ee"
    return "interface"


def _apply_structure_route_metadata(
    layout: MechanicalLayout,
    plan: RobotStructurePlan,
) -> MechanicalLayout:
    route_by_link = _link_routes_by_id(plan)
    links: list[LinkLayout] = []
    for link in layout.links:
        route = route_by_link.get(link.id)
        if route is None:
            links.append(link)
            continue
        primitive_type = _primitive_type_for_route(route.route_type)
        metadata = {
            **link.metadata,
            "structure_route_type": route.route_type,
            PRIMITIVE_TYPE_METADATA_KEY: primitive_type,
            PRIMITIVE_METADATA_KEY: {
                **(
                    link.metadata.get(PRIMITIVE_METADATA_KEY)
                    if isinstance(link.metadata.get(PRIMITIVE_METADATA_KEY), dict)
                    else {}
                ),
                "type": primitive_type,
                "source": "RobotStructurePlan",
                "route_type": route.route_type,
                "from_station_id": route.from_station_id,
                "to_station_id": route.to_station_id,
                "waypoints": [tuple(point) for point in route.waypoints],
                "route_vector": _route_vector_for_metadata(plan, route),
                "route_offset_vector": _route_offset_vector_for_metadata(plan, route),
                "reasons": [route.rationale] if route.rationale else [],
            },
        }
        links.append(replace(link, metadata=metadata))
    return replace(layout, links=links)


def _primitive_type_for_route(route_type: str) -> str:
    if route_type == "offset":
        return "offset_link"
    if route_type == "elbow":
        return "elbow_link"
    if route_type in {"wrist_spacer", "tool_stub"}:
        return "wrist_spacer"
    return "straight_link"


def _route_vector_for_metadata(
    plan: RobotStructurePlan,
    route: LinkRoutePlan,
) -> tuple[float, float, float] | None:
    origins = _station_origins(plan)
    start = origins.get(route.from_station_id)
    end = origins.get(route.to_station_id)
    if start is None or end is None:
        return None
    return _vector_between(start, end)


def _route_offset_vector_for_metadata(
    plan: RobotStructurePlan,
    route: LinkRoutePlan,
) -> tuple[float, float, float] | None:
    origins = _station_origins(plan)
    start = origins.get(route.from_station_id)
    end = origins.get(route.to_station_id)
    if start is None or end is None:
        return None
    if route.waypoints:
        waypoint = route.waypoints[0]
        return _vector_between(start, waypoint)
    vector = _vector_between(start, end)
    return (0.0, vector[1], vector[2])


def _rpy_from_normal(normal: tuple[float, float, float]) -> tuple[float, float, float]:
    # Reuse the same local-Z alignment used by the mechanical layout builder.
    from robot_sdk.layout.mechanical_layout import _rpy_align_z_axis

    return _rpy_align_z_axis(normal)


def _scale(
    vector: tuple[float, float, float],
    scalar: float,
) -> tuple[float, float, float]:
    return (vector[0] * scalar, vector[1] * scalar, vector[2] * scalar)


__all__ = [
    "STRUCTURE_PLAN_TEMPLATE",
    "build_mechanical_layout_from_structure_plan",
]
