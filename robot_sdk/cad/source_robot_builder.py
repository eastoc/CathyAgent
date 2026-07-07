"""Build lightweight source-level robot assemblies with build123d joints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from robot_sdk.cad.source_assembly import SourceAssemblyHelper, SourceMateRelation
from robot_sdk.cad.source_parts import (
    DEFAULT_LINK_LENGTH_MM,
    SourcePart,
    SourcePartCatalog,
    build_source_base_part,
    build_source_joint_part,
    build_source_link_part,
    build_source_routed_link_part,
    build_source_structure_joint_part,
    build_source_tool_flange_part,
)
from robot_sdk.types import LinkLayout, MechanicalLayout


@dataclass(frozen=True)
class SourceRobotAssemblyResult:
    """Resolved source-level robot assembly and its source mate metadata."""

    name: str
    assembly: Any
    part_catalog: SourcePartCatalog
    relations: list[SourceMateRelation]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def part_ids(self) -> list[str]:
        return self.part_catalog.part_ids()

    def source_mates(self) -> list[dict[str, Any]]:
        return [relation.to_dict() for relation in self.relations]


def build_simple_source_serial_robot(
    *,
    joint_count: int = 2,
    link_lengths: Sequence[float] | None = None,
    name: str = "source_serial_robot",
) -> SourceRobotAssemblyResult:
    """Build a simple serial robot with part-local source joints.

    This is the phase-10 smoke path: source parts own their local joints and the
    helper resolves the chain through native build123d ``connect_to`` calls.
    It intentionally does not consume DH transforms as CAD part poses.
    """

    if joint_count <= 0:
        raise ValueError("joint_count must be positive")
    lengths = _normalized_link_lengths(joint_count, link_lengths)
    parts = _build_serial_source_parts(joint_count, lengths)
    catalog = SourcePartCatalog(by_part_id={part.part_id: part for part in parts})
    helper = SourceAssemblyHelper(name)

    base = helper.add(catalog.require("base").solid, "base")
    joints = [
        helper.add(catalog.require(f"J{index}").solid, f"J{index}")
        for index in range(1, joint_count + 1)
    ]
    links = [
        helper.add(catalog.require(f"L{index}").solid, f"L{index}")
        for index in range(1, joint_count + 1)
    ]
    tool = helper.add(catalog.require("end_effector").solid, "end_effector")

    helper.face_to_face(
        (base, "output"),
        (joints[0], "input"),
        label="base_to_J1",
    )
    for index in range(joint_count):
        joint = joints[index]
        link = links[index]
        helper.face_to_face(
            (joint, "output"),
            (link, "input"),
            label=f"J{index + 1}_to_L{index + 1}",
        )
        if index + 1 < joint_count:
            helper.face_to_face(
                (link, "output"),
                (joints[index + 1], "input"),
                label=f"L{index + 1}_to_J{index + 2}",
            )
        else:
            helper.face_to_face(
                (link, "output"),
                (tool, "input"),
                label=f"L{index + 1}_to_end_effector",
            )

    assembly = helper.build()
    return SourceRobotAssemblyResult(
        name=name,
        assembly=assembly,
        part_catalog=catalog,
        relations=list(helper.relations),
        metadata={
            "source": "source_robot_builder",
            "production_assembly_source": "source_joint",
            "assembly_backend": "build123d",
            "assembly_mode": "source_joint",
            "placement_mode": "source_joint_connect_to",
            "solver_constraint_mode": "build123d_native_joints",
            "semantic_constraints_applied_to_solver": "source_joint",
            "semantic_constraint_application": "source_joint",
            "semantic_constraint_count": len(helper.relations),
            "production_full_semantic_solve_requested": False,
            "production_full_semantic_solve_used": False,
            "full_assembly_fixture_requested": False,
            "full_assembly_fixture_ran": False,
            "full_assembly_fixture_solved": False,
            "local_subassembly_count": 0,
            "local_subassembly_solved_count": 0,
            "local_subassembly_failed_count": 0,
            "joint_count": joint_count,
            "link_lengths": list(lengths),
            "relation_count": len(helper.relations),
            "part_count": len(catalog.part_ids()),
            "source_mates": [relation.to_dict() for relation in helper.relations],
        },
    )


def build_source_robot_from_layout(
    layout: MechanicalLayout,
    *,
    name: str = "source_layout_robot",
) -> SourceRobotAssemblyResult:
    """Build a source-joint robot from MechanicalLayout routing metadata.

    This is the production-facing phase-10 path. MechanicalLayout supplies the
    structure route for each link; source parts turn those routes into
    part-local input/output datums; build123d resolves the static assembly via
    native ``connect_to()`` calls.
    """

    if not layout.joints:
        raise ValueError("MechanicalLayout.joints cannot be empty")
    if len(layout.joints) != len(layout.links):
        raise ValueError("MechanicalLayout joints and links must have the same count")

    parts = _build_layout_source_parts(layout)
    catalog = SourcePartCatalog(by_part_id={part.part_id: part for part in parts})
    helper = SourceAssemblyHelper(name)

    base = helper.add(catalog.require("base").solid, "base")
    joints = [helper.add(catalog.require(joint.id).solid, joint.id) for joint in layout.joints]
    links = [helper.add(catalog.require(link.id).solid, link.id) for link in layout.links]
    tool = helper.add(catalog.require("end_effector").solid, "end_effector")

    helper.face_to_face(
        (base, "output"),
        (joints[0], "input"),
        label=f"base_to_{layout.joints[0].id}",
    )
    for index, joint_layout in enumerate(layout.joints):
        link_layout = layout.links[index]
        joint = joints[index]
        link = links[index]
        helper.face_to_face(
            (joint, "output"),
            (link, "input"),
            label=f"{joint_layout.id}_to_{link_layout.id}",
        )
        if index + 1 < len(layout.joints):
            next_joint_layout = layout.joints[index + 1]
            helper.face_to_face(
                (link, "output"),
                (joints[index + 1], "input"),
                label=f"{link_layout.id}_to_{next_joint_layout.id}",
            )
        else:
            helper.face_to_face(
                (link, "output"),
                (tool, "input"),
                label=f"{link_layout.id}_to_end_effector",
            )

    assembly = helper.build()
    link_routes = [_source_link_route_metadata(link) for link in layout.links]
    return SourceRobotAssemblyResult(
        name=name,
        assembly=assembly,
        part_catalog=catalog,
        relations=list(helper.relations),
        metadata={
            **_source_robot_metadata(helper, catalog),
            "source": "source_robot_builder_from_layout",
            "layout_source": layout.metadata.get("layout_source", "unknown"),
            "robot_family": layout.metadata.get("robot_family", "unknown"),
            "layout_template": layout.metadata.get("layout_template"),
            "structure_plan_name": layout.metadata.get("structure_plan_name"),
            "joint_count": len(layout.joints),
            "link_routes": link_routes,
            "link_lengths": [_route_length(item["route_vector"]) for item in link_routes],
            "part_count": len(catalog.part_ids()),
        },
    )


def _build_serial_source_parts(
    joint_count: int,
    link_lengths: Sequence[float],
) -> list[SourcePart]:
    parts: list[SourcePart] = [build_source_base_part()]
    for index in range(1, joint_count + 1):
        parts.append(build_source_joint_part(f"J{index}"))
        parts.append(build_source_link_part(f"L{index}", length=link_lengths[index - 1]))
    parts.append(build_source_tool_flange_part())
    return parts


def _build_layout_source_parts(layout: MechanicalLayout) -> list[SourcePart]:
    parts: list[SourcePart] = [build_source_base_part()]
    for joint in layout.joints:
        parts.append(
            build_source_structure_joint_part(
                joint.id,
                axis_role=str(joint.metadata.get("structure_axis_role") or ""),
                morphology=_metadata_text(joint.metadata.get("morphology")),
            )
        )
    for link in layout.links:
        route = _source_link_route_metadata(link)
        parts.append(
            build_source_routed_link_part(
                link.id,
                route_vector=route["route_vector"],
                route_type=route["route_type"],
                morphology=route["morphology"],
                width=float(link.envelope.dimensions.get("width", 24.0)),
                height=float(link.envelope.dimensions.get("height", 20.0)),
            )
        )
    parts.append(build_source_tool_flange_part())
    return _layout_source_parts_in_chain_order(layout, parts)


def _layout_source_parts_in_chain_order(
    layout: MechanicalLayout,
    parts: list[SourcePart],
) -> list[SourcePart]:
    by_id = {part.part_id: part for part in parts}
    ordered: list[SourcePart] = [by_id["base"]]
    for joint, link in zip(layout.joints, layout.links):
        ordered.append(by_id[joint.id])
        ordered.append(by_id[link.id])
    ordered.append(by_id["end_effector"])
    return ordered


def _source_link_route_metadata(link: LinkLayout) -> dict[str, Any]:
    primitive = link.metadata.get("link_primitive")
    if not isinstance(primitive, dict):
        primitive = {}
    route_vector = _vec3_or_none(primitive.get("route_vector"))
    if route_vector is None:
        route_vector = (
            float(link.envelope.dimensions.get("length", DEFAULT_LINK_LENGTH_MM)),
            0.0,
            0.0,
        )
    route_type = str(
        link.metadata.get("structure_route_type")
        or primitive.get("route_type")
        or link.metadata.get("primitive_type")
        or "straight"
    )
    if route_type in {"straight_link", "generic_straight_link"}:
        route_type = "straight"
    if route_type in {"offset_link", "generic_offset_link"}:
        route_type = "offset"
    if route_type in {"elbow_link", "generic_elbow_link"}:
        route_type = "elbow"
    if route_type in {"wrist_spacer", "generic_wrist_spacer", "tool_stub"}:
        route_type = "wrist_spacer"
    return {
        "link_id": link.id,
        "route_vector": route_vector,
        "route_type": route_type,
        "morphology": _metadata_text(link.metadata.get("morphology")),
        "primitive_type": _metadata_text(link.metadata.get("primitive_type")),
    }


def _source_robot_metadata(
    helper: SourceAssemblyHelper,
    catalog: SourcePartCatalog,
) -> dict[str, Any]:
    return {
        "source": "source_robot_builder",
        "production_assembly_source": "source_joint",
        "assembly_backend": "build123d",
        "assembly_mode": "source_joint",
        "placement_mode": "source_joint_connect_to",
        "solver_constraint_mode": "build123d_native_joints",
        "semantic_constraints_applied_to_solver": "source_joint",
        "semantic_constraint_application": "source_joint",
        "semantic_constraint_count": len(helper.relations),
        "production_full_semantic_solve_requested": False,
        "production_full_semantic_solve_used": False,
        "full_assembly_fixture_requested": False,
        "full_assembly_fixture_ran": False,
        "full_assembly_fixture_solved": False,
        "local_subassembly_count": 0,
        "local_subassembly_solved_count": 0,
        "local_subassembly_failed_count": 0,
        "relation_count": len(helper.relations),
        "part_count": len(catalog.part_ids()),
        "source_mates": [relation.to_dict() for relation in helper.relations],
    }


def _normalized_link_lengths(
    joint_count: int,
    link_lengths: Sequence[float] | None,
) -> list[float]:
    if link_lengths is None:
        return [DEFAULT_LINK_LENGTH_MM for _ in range(joint_count)]
    if len(link_lengths) != joint_count:
        raise ValueError("link_lengths length must match joint_count")
    lengths = [float(length) for length in link_lengths]
    if any(length <= 0 for length in lengths):
        raise ValueError("link_lengths must be positive")
    return lengths


def _vec3_or_none(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _metadata_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _route_length(vector: tuple[float, float, float]) -> float:
    return sum(component * component for component in vector) ** 0.5


__all__ = [
    "SourceRobotAssemblyResult",
    "build_source_robot_from_layout",
    "build_simple_source_serial_robot",
]
