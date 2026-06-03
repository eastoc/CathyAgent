"""Mechanical layout construction for the first Robot CAD MVP.

This module is the bridge between kinematics and CAD skeleton generation.

Important boundary:
- DH/FK frames describe mathematical motion relationships.
- CAD frames describe physical things: joint axes, link bodies, flanges,
  mounting faces, and tool frames.

So this module never says "the DH transform is the CAD mate transform". Instead
it creates explicit `FrameMapping` records that explain how an abstract
kinematic frame is mapped onto a mechanical frame for the selected template.

MVP scope:
- one template: `tabletop_serial_arm`
- serial chains only
- simple box links, cylinder joint housings, and flange/mount interfaces
- neutral mate features and assembly constraints for CadQuery adapters
- deterministic defaults, no LLM-generated free-form mechanical structures
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from robot_sdk.layout.frame_mapping import (
    map_base_kinematic_to_mount,
    map_end_effector_kinematic_to_mount,
    map_joint_kinematic_to_axis,
    map_joint_kinematic_to_link_body,
)
from robot_sdk.structure.ids import (
    SerialChainIds,
    body_frame_id,
    end_effector_frame_id,
    generate_serial_chain_ids,
    interface_frame_id,
    interface_id,
    joint_axis_frame_id,
    kinematic_frame_id,
    mount_frame_id,
)
from robot_sdk.types import (
    AssemblyConstraint,
    DHParam,
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


TABLETOP_SERIAL_ARM_TEMPLATE = "tabletop_serial_arm"
DEFAULT_LINK_LENGTH_MM = 50.0
DEFAULT_LINK_WIDTH_MM = 40.0
DEFAULT_LINK_HEIGHT_MM = 30.0
DEFAULT_JOINT_RADIUS_MM = 25.0
DEFAULT_JOINT_DEPTH_MM = 35.0
DEFAULT_FLANGE_RADIUS_MM = 28.0
DEFAULT_FLANGE_THICKNESS_MM = 8.0
DEFAULT_TOOL_OFFSET_MM = 20.0


def build_tabletop_serial_mechanical_layout_from_model(
    model: KinematicModel,
    *,
    chain_ids: SerialChainIds | None = None,
) -> MechanicalLayout:
    """Build a tabletop serial-arm layout from a `KinematicModel`.

    The kinematic model supplies DH rows and joint/link counts; this module
    supplies mechanical semantics. It does not infer CAD geometry directly from
    arbitrary matrices.
    """

    if model.convention not in {"dh", "modified_dh"}:
        raise ValueError("tabletop serial layout requires a DH-based kinematic model")
    return build_tabletop_serial_mechanical_layout(
        model.dh_params,
        chain_ids=chain_ids,
        convention=model.convention,
    )


def build_tabletop_serial_mechanical_layout(
    dh_params: Sequence[DHParam],
    *,
    chain_ids: SerialChainIds | None = None,
    convention: str = "dh",
    link_width: float = DEFAULT_LINK_WIDTH_MM,
    link_height: float = DEFAULT_LINK_HEIGHT_MM,
    joint_radius: float = DEFAULT_JOINT_RADIUS_MM,
    joint_depth: float = DEFAULT_JOINT_DEPTH_MM,
    flange_radius: float = DEFAULT_FLANGE_RADIUS_MM,
    flange_thickness: float = DEFAULT_FLANGE_THICKNESS_MM,
    tool_offset: float = DEFAULT_TOOL_OFFSET_MM,
) -> MechanicalLayout:
    """Build a deterministic `MechanicalLayout` for a tabletop serial arm.

    `dh_params` are used for rough station spacing and kinematic-frame names.
    Physical CAD semantics are created by the template:
    - each joint receives a `*_axis` frame and a cylinder envelope
    - each link receives a `*_body` frame and a box envelope
    - each connection receives an explicit `*_interface` frame
    - every kinematic-to-mechanical mapping gets a rationale

    The template is a starting skeleton for CAD, not a finished manufacturable
    design.
    """

    if not dh_params:
        raise ValueError("dh_params cannot be empty")
    if convention not in {"dh", "modified_dh"}:
        raise ValueError("tabletop serial layout only supports DH conventions")

    ids = chain_ids or generate_serial_chain_ids(len(dh_params))
    _validate_chain_ids(ids, len(dh_params))

    assumptions = [
        f"Mechanical layout uses the fixed `{TABLETOP_SERIAL_ARM_TEMPLATE}` template.",
        "DH rows are used for station spacing only; CAD assembly semantics are generated separately.",
        "Joint housings are simplified cylinders and links are simplified box beams.",
    ]
    warnings: list[str] = []

    base_frame = FrameSpec(
        id=mount_frame_id("base"),
        parent=None,
        transform=Transform(),
        semantic="mount",
        description="Fixed tabletop base mounting frame.",
    )
    frames: list[FrameSpec] = [
        base_frame,
        FrameSpec(
            id="base_kinematic",
            parent=base_frame.id,
            transform=Transform(),
            semantic="kinematic",
            description="Abstract base frame for DH/FK math.",
        ),
    ]
    joints: list[JointLayout] = []
    links: list[LinkLayout] = []
    interfaces: list[InterfaceSpec] = []
    part_features: list[PartFeature] = _base_part_features(base_frame.id)
    assembly_constraints: list[AssemblyConstraint] = []
    frame_mappings: list[FrameMapping] = [
        map_base_kinematic_to_mount(mount_frame_id=base_frame.id)
    ]
    envelopes: list[EnvelopeSpec] = []

    station_x = 0.0
    previous_link_id = ids.base_link

    for index, param in enumerate(dh_params):
        joint_id = ids.joints[index]
        link_id = ids.links[index]
        span = _link_span(param)
        if span == DEFAULT_LINK_LENGTH_MM and math.isclose(param.a, 0.0) and math.isclose(param.d, 0.0):
            warnings.append(
                f"{joint_id} has zero DH a/d offsets; using {DEFAULT_LINK_LENGTH_MM:g} mm "
                "as the minimum CAD skeleton span."
            )

        axis_frame_id = joint_axis_frame_id(joint_id)
        link_body_frame_id = body_frame_id(link_id)
        joint_kinematic_frame_id = kinematic_frame_id(joint_id)
        joint_to_link_interface_id = interface_id(joint_id, link_id, "flange")
        incoming_interface_id = _incoming_interface_id(ids, index)
        outgoing_interface_id = _outgoing_interface_id(ids, index)

        frames.extend(
            _joint_link_frames(
                base_frame_id=base_frame.id,
                station_x=station_x,
                span=span,
                flange_thickness=flange_thickness,
                joint_id=joint_id,
                link_id=link_id,
                axis_frame_id=axis_frame_id,
                link_body_frame_id=link_body_frame_id,
                joint_kinematic_frame_id=joint_kinematic_frame_id,
                incoming_interface_id=incoming_interface_id,
                joint_to_link_interface_id=joint_to_link_interface_id,
                outgoing_interface_id=outgoing_interface_id,
            )
        )

        joint_envelope = EnvelopeSpec(
            id=f"{joint_id}_joint_housing_envelope",
            shape="cylinder",
            dimensions={"radius": joint_radius, "length": joint_depth},
            frame=axis_frame_id,
            description=f"Simplified cylindrical joint housing for {joint_id}.",
        )
        link_envelope = EnvelopeSpec(
            id=f"{link_id}_box_link_envelope",
            shape="box",
            dimensions={"length": span, "width": link_width, "height": link_height},
            frame=link_body_frame_id,
            description=f"Simplified box-beam envelope for {link_id}.",
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
                ),
                InterfaceSpec(
                    id=joint_to_link_interface_id,
                    frame=interface_frame_id(joint_to_link_interface_id),
                    type="flange",
                    mates_to=incoming_interface_id,
                    envelope=joint_to_link_envelope,
                ),
                InterfaceSpec(
                    id=outgoing_interface_id,
                    frame=interface_frame_id(outgoing_interface_id),
                    type="end_effector_mount" if index + 1 == len(dh_params) else "flange",
                    mates_to=None,
                    envelope=outgoing_envelope,
                ),
            ]
        )

        joints.append(
            JointLayout(
                id=joint_id,
                type=param.joint_type,
                axis_frame=axis_frame_id,
                parent_link=previous_link_id,
                child_link=link_id,
                actuator_envelope=joint_envelope,
            )
        )
        links.append(
            LinkLayout(
                id=link_id,
                body_frame=link_body_frame_id,
                from_interface=joint_to_link_interface_id,
                to_interface=outgoing_interface_id,
                envelope=link_envelope,
            )
        )
        frame_mappings.extend(
            [
                map_joint_kinematic_to_axis(
                    template=TABLETOP_SERIAL_ARM_TEMPLATE,
                    joint_id=joint_id,
                    kinematic_frame_id=joint_kinematic_frame_id,
                    axis_frame_id=axis_frame_id,
                ),
                map_joint_kinematic_to_link_body(
                    joint_id=joint_id,
                    link_id=link_id,
                    kinematic_frame_id=joint_kinematic_frame_id,
                    link_body_frame_id=link_body_frame_id,
                    link_span=span,
                ),
            ]
        )

        station_x += span
        previous_link_id = link_id

    _append_end_effector_frames(
        frames=frames,
        frame_mappings=frame_mappings,
        last_link_id=ids.links[-1],
        last_span=_link_span(dh_params[-1]),
        station_x=station_x,
        tool_offset=tool_offset,
    )
    part_features.extend(_end_effector_part_features())
    assembly_constraints.extend(_end_effector_assembly_constraints(ids.links[-1]))

    return MechanicalLayout(
        units="mm",
        base_frame=base_frame,
        frames=_dedupe_frames(frames),
        joints=joints,
        links=links,
        interfaces=_dedupe_interfaces(interfaces),
        frame_mappings=frame_mappings,
        part_features=_dedupe_part_features(part_features),
        assembly_constraints=_dedupe_assembly_constraints(assembly_constraints),
        envelopes=_dedupe_envelopes(envelopes),
        assumptions=assumptions,
        warnings=warnings,
    )


def _joint_link_frames(
    *,
    base_frame_id: str,
    station_x: float,
    span: float,
    flange_thickness: float,
    joint_id: str,
    link_id: str,
    axis_frame_id: str,
    link_body_frame_id: str,
    joint_kinematic_frame_id: str,
    incoming_interface_id: str,
    joint_to_link_interface_id: str,
    outgoing_interface_id: str,
) -> list[FrameSpec]:
    """Create the mechanical and kinematic frames for one joint-link station."""

    return [
        FrameSpec(
            id=joint_kinematic_frame_id,
            parent="base_kinematic",
            transform=Transform(translation=(station_x, 0.0, 0.0)),
            semantic="kinematic",
            description=f"Abstract kinematic frame for {joint_id}.",
        ),
        FrameSpec(
            id=axis_frame_id,
            parent=base_frame_id,
            transform=Transform(translation=(station_x, 0.0, 0.0)),
            semantic="joint_axis",
            description=f"Mechanical axis frame for {joint_id}.",
        ),
        FrameSpec(
            id=interface_frame_id(incoming_interface_id),
            parent=axis_frame_id,
            transform=Transform(translation=(-flange_thickness / 2.0, 0.0, 0.0)),
            semantic="interface",
            description=f"Incoming mechanical interface for {joint_id}.",
        ),
        FrameSpec(
            id=interface_frame_id(joint_to_link_interface_id),
            parent=axis_frame_id,
            transform=Transform(translation=(flange_thickness / 2.0, 0.0, 0.0)),
            semantic="interface",
            description=f"Outgoing mechanical interface from {joint_id} to {link_id}.",
        ),
        FrameSpec(
            id=link_body_frame_id,
            parent=axis_frame_id,
            transform=Transform(translation=(span / 2.0, 0.0, 0.0)),
            semantic="link_body",
            description=f"Simplified CAD body frame for {link_id}.",
        ),
        FrameSpec(
            id=interface_frame_id(outgoing_interface_id),
            parent=link_body_frame_id,
            transform=Transform(translation=(span / 2.0, 0.0, 0.0)),
            semantic="interface",
            description=f"Outgoing mechanical interface from {link_id}.",
        ),
    ]


def _base_part_features(base_frame_id: str) -> list[PartFeature]:
    """Create neutral CadQuery-selectable features for the base part.

    `cad_tag` is the future CadQuery tag name. The feature ID is the stable
    cross-module reference used by assembly constraints.
    """

    return [
        PartFeature(
            id="base.top",
            part_id="base",
            cad_tag="top",
            type="plane",
            semantic="mount_face",
            frame=base_frame_id,
        ),
        PartFeature(
            id="base.axis",
            part_id="base",
            cad_tag="axis",
            type="axis",
            semantic="joint_axis",
            frame=base_frame_id,
        ),
    ]


def _joint_link_part_features(
    *,
    joint_id: str,
    link_id: str,
    axis_frame_id: str,
    joint_to_link_interface_id: str,
    incoming_interface_id: str,
    outgoing_interface_id: str,
    link_body_frame_id: str,
) -> list[PartFeature]:
    """Create mate features for one joint part and one link part.

    These are not constraints yet. They are named feature references that the
    next step can connect with `AssemblyConstraint`.
    """

    return [
        PartFeature(
            id=f"{joint_id}.bottom",
            part_id=joint_id,
            cad_tag="bottom",
            type="plane",
            semantic="flange_face",
            frame=interface_frame_id(incoming_interface_id),
        ),
        PartFeature(
            id=f"{joint_id}.axis",
            part_id=joint_id,
            cad_tag="axis",
            type="axis",
            semantic="joint_axis",
            frame=axis_frame_id,
        ),
        PartFeature(
            id=f"{joint_id}.output_flange",
            part_id=joint_id,
            cad_tag="output_flange",
            type="plane",
            semantic="flange_face",
            frame=interface_frame_id(joint_to_link_interface_id),
        ),
        PartFeature(
            id=f"{link_id}.input_face",
            part_id=link_id,
            cad_tag="input_face",
            type="plane",
            semantic="link_end",
            frame=interface_frame_id(joint_to_link_interface_id),
        ),
        PartFeature(
            id=f"{link_id}.output_face",
            part_id=link_id,
            cad_tag="output_face",
            type="plane",
            semantic="link_end",
            frame=interface_frame_id(outgoing_interface_id),
        ),
        PartFeature(
            id=f"{link_id}.body_axis",
            part_id=link_id,
            cad_tag="body_axis",
            type="axis",
            semantic="custom",
            frame=link_body_frame_id,
        ),
    ]


def _joint_link_assembly_constraints(
    *,
    ids: SerialChainIds,
    index: int,
    joint_id: str,
    link_id: str,
) -> list[AssemblyConstraint]:
    """Create neutral constraints for one joint-link station.

    Constraints reference `PartFeature.id`, not CAD faces directly. The CadQuery
    adapter will later translate these neutral constraints into
    `Assembly.constrain(...)` calls.
    """

    constraints: list[AssemblyConstraint] = []
    if index == 0:
        constraints.extend(
            [
                AssemblyConstraint(
                    id=f"base_top_to_{joint_id}_bottom_plane",
                    fixed="base.top",
                    moving=f"{joint_id}.bottom",
                    kind="Plane",
                    rationale=f"Mount {joint_id} on the fixed base top face.",
                ),
                AssemblyConstraint(
                    id=f"base_axis_to_{joint_id}_axis",
                    fixed="base.axis",
                    moving=f"{joint_id}.axis",
                    kind="Axis",
                    rationale=f"Align {joint_id}'s mechanical axis with the base axis.",
                ),
            ]
        )
    else:
        previous_link_id = ids.links[index - 1]
        constraints.extend(
            [
                AssemblyConstraint(
                    id=f"{previous_link_id}_output_to_{joint_id}_bottom_plane",
                    fixed=f"{previous_link_id}.output_face",
                    moving=f"{joint_id}.bottom",
                    kind="Plane",
                    rationale=(
                        f"Attach {joint_id}'s input flange to the output face of "
                        f"{previous_link_id}."
                    ),
                ),
                AssemblyConstraint(
                    id=f"{previous_link_id}_axis_to_{joint_id}_axis",
                    fixed=f"{previous_link_id}.body_axis",
                    moving=f"{joint_id}.axis",
                    kind="Axis",
                    rationale=(
                        f"Align {joint_id}'s joint axis to the simplified body axis "
                        f"of {previous_link_id} in the tabletop template."
                    ),
                ),
            ]
        )

    constraints.extend(
        [
            AssemblyConstraint(
                id=f"{joint_id}_output_to_{link_id}_input_plane",
                fixed=f"{joint_id}.output_flange",
                moving=f"{link_id}.input_face",
                kind="Plane",
                rationale=f"Attach {link_id}'s input face to {joint_id}'s output flange.",
            ),
            AssemblyConstraint(
                id=f"{joint_id}_axis_to_{link_id}_body_axis",
                fixed=f"{joint_id}.axis",
                moving=f"{link_id}.body_axis",
                kind="Axis",
                rationale=(
                    f"Align {link_id}'s simplified body axis with {joint_id}'s "
                    "mechanical axis for the concept skeleton."
                ),
            ),
        ]
    )
    return constraints


def _end_effector_part_features() -> list[PartFeature]:
    """Create mate features for the tool/output side of the assembly."""

    return [
        PartFeature(
            id="end_effector.mount",
            part_id="end_effector",
            cad_tag="mount",
            type="plane",
            semantic="tool_mount",
            frame=end_effector_frame_id("mount"),
        ),
        PartFeature(
            id="end_effector.tool",
            part_id="end_effector",
            cad_tag="tool",
            type="point",
            semantic="tool_mount",
            frame=end_effector_frame_id("tool"),
        ),
    ]


def _end_effector_assembly_constraints(last_link_id: str) -> list[AssemblyConstraint]:
    """Create neutral constraints from the last link to the tool mount."""

    return [
        AssemblyConstraint(
            id=f"{last_link_id}_output_to_end_effector_mount_plane",
            fixed=f"{last_link_id}.output_face",
            moving="end_effector.mount",
            kind="Plane",
            rationale=(
                "Attach the end-effector mounting face to the output face of "
                f"{last_link_id}."
            ),
        )
    ]


def _append_end_effector_frames(
    *,
    frames: list[FrameSpec],
    frame_mappings: list[FrameMapping],
    last_link_id: str,
    last_span: float,
    station_x: float,
    tool_offset: float,
) -> None:
    """Append end-effector kinematic, mount, and tool frames."""

    end_effector_mount_frame_id = end_effector_frame_id("mount")
    end_effector_tool_frame_id = end_effector_frame_id("tool")
    end_effector_kinematic_frame_id = end_effector_frame_id("kinematic")
    frames.extend(
        [
            FrameSpec(
                id=end_effector_kinematic_frame_id,
                parent="base_kinematic",
                transform=Transform(translation=(station_x, 0.0, 0.0)),
                semantic="kinematic",
                description="Abstract end-effector kinematic frame.",
            ),
            FrameSpec(
                id=end_effector_mount_frame_id,
                parent=body_frame_id(last_link_id),
                transform=Transform(translation=(last_span / 2.0, 0.0, 0.0)),
                semantic="mount",
                description="Physical tool mounting face at the end of the last link.",
            ),
            FrameSpec(
                id=end_effector_tool_frame_id,
                parent=end_effector_mount_frame_id,
                transform=Transform(translation=(tool_offset, 0.0, 0.0)),
                semantic="ee",
                description="Tool/TCP-side frame for downstream tool geometry.",
            ),
        ]
    )
    frame_mappings.append(
        map_end_effector_kinematic_to_mount(
            kinematic_frame_id=end_effector_kinematic_frame_id,
            mount_frame_id=end_effector_mount_frame_id,
        )
    )


def _incoming_interface_id(ids: SerialChainIds, index: int) -> str:
    """Return the interface that enters joint `index` from base or previous link."""

    if index == 0:
        return interface_id("base", ids.joints[index], "flange")
    return interface_id(ids.links[index - 1], ids.joints[index], "flange")


def _outgoing_interface_id(ids: SerialChainIds, index: int) -> str:
    """Return the interface that leaves link `index` toward the next joint or tool."""

    link_id = ids.links[index]
    if index + 1 < len(ids.joints):
        return interface_id(link_id, ids.joints[index + 1], "flange")
    return interface_id(link_id, "end_effector", "end_effector_mount")


def _validate_chain_ids(ids: SerialChainIds, expected_dof: int) -> None:
    """Ensure externally supplied IDs match the DH chain length."""

    if len(ids.joints) != expected_dof:
        raise ValueError("SerialChainIds.joints length must match dh_params")
    if len(ids.links) != expected_dof:
        raise ValueError("SerialChainIds.links length must match dh_params")


def _link_span(param: DHParam) -> float:
    """Return the template span for one link station.

    DH `a` and `d` are mathematical offsets, not actual solid-body dimensions.
    The template uses their geometric magnitude as a rough default span and
    falls back to a minimum length for zero-offset rows.
    """

    span = math.hypot(param.a, param.d)
    if span <= 0:
        return DEFAULT_LINK_LENGTH_MM
    return span


def _flange_envelope(
    interface_id_value: str,
    frame_id: str,
    radius: float,
    thickness: float,
) -> EnvelopeSpec:
    """Create a simple flange disk envelope for an interface frame."""

    return EnvelopeSpec(
        id=f"{interface_id_value}_flange_envelope",
        shape="flange_disk",
        dimensions={"radius": radius, "thickness": thickness},
        frame=frame_id,
        description=f"Simplified flange envelope for {interface_id_value}.",
    )


def _dedupe_frames(frames: Sequence[FrameSpec]) -> list[FrameSpec]:
    """Keep the first frame with each ID.

    The layout builder may reference the same interface from adjacent objects.
    Keeping first occurrence makes generation deterministic while preserving the
    semantic frame graph.
    """

    seen: set[str] = set()
    result: list[FrameSpec] = []
    for frame in frames:
        if frame.id in seen:
            continue
        seen.add(frame.id)
        result.append(frame)
    return result


def _dedupe_interfaces(interfaces: Sequence[InterfaceSpec]) -> list[InterfaceSpec]:
    """Keep the first interface with each ID."""

    seen: set[str] = set()
    result: list[InterfaceSpec] = []
    for interface in interfaces:
        if interface.id in seen:
            continue
        seen.add(interface.id)
        result.append(interface)
    return result


def _dedupe_part_features(part_features: Sequence[PartFeature]) -> list[PartFeature]:
    """Keep the first feature with each stable feature ID."""

    seen: set[str] = set()
    result: list[PartFeature] = []
    for feature in part_features:
        if feature.id in seen:
            continue
        seen.add(feature.id)
        result.append(feature)
    return result


def _dedupe_assembly_constraints(
    assembly_constraints: Sequence[AssemblyConstraint],
) -> list[AssemblyConstraint]:
    """Keep the first constraint with each stable constraint ID."""

    seen: set[str] = set()
    result: list[AssemblyConstraint] = []
    for constraint in assembly_constraints:
        if constraint.id in seen:
            continue
        seen.add(constraint.id)
        result.append(constraint)
    return result


def _dedupe_envelopes(envelopes: Sequence[EnvelopeSpec]) -> list[EnvelopeSpec]:
    """Keep the first envelope with each ID."""

    seen: set[str] = set()
    result: list[EnvelopeSpec] = []
    for envelope in envelopes:
        if envelope.id in seen:
            continue
        seen.add(envelope.id)
        result.append(envelope)
    return result
