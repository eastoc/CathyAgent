"""Base-rooted semantic graph derived from RobotModel link/joint trees."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Tuple

from robot_sdk.kinematics.model import SerialManipulatorSpec
from robot_sdk.model import Joint, Link, Origin, RobotModel

GraphNodeKind = Literal["link"]
GraphEdgeKind = Literal["joint", "fixed"]
Vec3 = Tuple[float, float, float]
Matrix4 = Tuple[
    Tuple[float, float, float, float],
    Tuple[float, float, float, float],
    Tuple[float, float, float, float],
    Tuple[float, float, float, float],
]


@dataclass
class SemanticNode:
    """One link node in the base-rooted robot semantic graph."""

    name: str
    kind: GraphNodeKind
    role: str | None
    parent: str | None
    children: list[str]
    frame: Origin
    axis: Vec3 | None = None
    link_name: str | None = None
    joint_name: str | None = None
    incoming_joint: str | None = None
    dh_index: int | None = None
    span_to_children: dict[str, Vec3] = field(default_factory=dict)
    axis_angle_to_children: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    visual_intent: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticEdge:
    """Parent-child relation in the semantic graph."""

    parent: str
    child: str
    kind: GraphEdgeKind
    joint_name: str | None
    origin: Origin
    axis: Vec3 | None
    span: Vec3
    span_length: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticGraph:
    """Base-rooted graph over RobotModel links."""

    root: str
    nodes: dict[str, SemanticNode]
    edges: dict[tuple[str, str], SemanticEdge]
    order: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> SemanticNode:
        return self.nodes[name]

    def children(self, name: str) -> list[str]:
        return list(self.nodes[name].children)

    def edge(self, parent: str, child: str) -> SemanticEdge:
        return self.edges[(parent, child)]

    def path_to(self, link_name: str) -> list[str]:
        """Return the root-to-link path for a link node."""

        if link_name not in self.nodes:
            raise KeyError(f"unknown graph node: {link_name!r}")
        path = [link_name]
        current = self.nodes[link_name]
        while current.parent is not None:
            path.append(current.parent)
            current = self.nodes[current.parent]
        path.reverse()
        return path


class SemanticGraphBuildError(ValueError):
    """Raised when RobotModel cannot be converted into one rooted graph."""


def build_semantic_graph(robot: RobotModel, *, spec: SerialManipulatorSpec | None = None) -> SemanticGraph:
    """Build a base-rooted semantic graph from ``RobotModel``.

    ``RobotModel`` remains the exporter-facing truth. The graph is a derived
    SDK fact layer for geometry synthesis and inspection.
    """

    link_by_name = {link.name: link for link in robot.links}
    joint_by_child = _joint_by_child(robot.joints)
    children_by_parent = _children_by_parent(robot.joints, link_by_name)
    root = _select_root(spec, link_by_name, joint_by_child)
    spec_joint_by_name = {joint.name: joint for joint in spec.joints} if spec is not None else {}

    nodes: dict[str, SemanticNode] = {}
    edges: dict[tuple[str, str], SemanticEdge] = {}
    order: list[str] = []

    def visit(link_name: str, parent_name: str | None, parent_transform: Matrix4) -> None:
        link = link_by_name[link_name]
        incoming_joint = joint_by_child.get(link_name)
        frame_transform = parent_transform
        if incoming_joint is not None:
            frame_transform = _matmul(parent_transform, _origin_to_matrix(incoming_joint.origin))

        metadata = _node_metadata(link, incoming_joint, spec_joint_by_name)
        node = SemanticNode(
            name=link.name,
            kind="link",
            role=_node_role(link, incoming_joint, spec_joint_by_name),
            parent=parent_name,
            children=[],
            frame=_origin_from_matrix(frame_transform),
            axis=incoming_joint.axis if incoming_joint is not None else None,
            link_name=link.name,
            joint_name=incoming_joint.name if incoming_joint is not None else None,
            incoming_joint=incoming_joint.name if incoming_joint is not None else None,
            dh_index=_dh_index(link, incoming_joint),
            metadata=metadata,
        )
        nodes[link.name] = node
        order.append(link.name)

        if parent_name is not None and incoming_joint is not None:
            parent_frame = nodes[parent_name].frame
            span = _sub_vec3(node.frame.xyz, parent_frame.xyz)
            edge = SemanticEdge(
                parent=parent_name,
                child=link.name,
                kind="fixed" if incoming_joint.joint_type == "fixed" else "joint",
                joint_name=incoming_joint.name,
                origin=incoming_joint.origin,
                axis=incoming_joint.axis,
                span=span,
                span_length=_norm(span),
                metadata=dict(incoming_joint.meta),
            )
            edges[(parent_name, link.name)] = edge
            nodes[parent_name].children.append(link.name)
            nodes[parent_name].span_to_children[link.name] = span
            axis_angle = _angle_between(nodes[parent_name].axis, node.axis)
            if axis_angle is not None:
                nodes[parent_name].axis_angle_to_children[link.name] = axis_angle

        for child_name in children_by_parent.get(link.name, []):
            visit(child_name, link.name, frame_transform)

    visit(root, None, _identity_matrix())
    _assert_all_links_reachable(link_by_name, nodes, root)

    return SemanticGraph(
        root=root,
        nodes=nodes,
        edges=edges,
        order=order,
        metadata={
            "robot": robot.name,
            "source": "robot_model",
            "kinematic_spec": spec.name if spec is not None else None,
            "base_frame": spec.base_frame if spec is not None else root,
            "tool_frame": spec.tool_frame if spec is not None else None,
        },
    )


def _joint_by_child(joints: list[Joint]) -> dict[str, Joint]:
    result: dict[str, Joint] = {}
    for joint in joints:
        if joint.child in result:
            raise SemanticGraphBuildError(f"link {joint.child!r} has multiple incoming joints")
        result[joint.child] = joint
    return result


def _children_by_parent(joints: list[Joint], links: dict[str, Link]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {name: [] for name in links}
    for joint in joints:
        if joint.parent not in links:
            raise SemanticGraphBuildError(f"joint {joint.name!r} references missing parent link {joint.parent!r}")
        if joint.child not in links:
            raise SemanticGraphBuildError(f"joint {joint.name!r} references missing child link {joint.child!r}")
        result[joint.parent].append(joint.child)
    return result


def _select_root(
    spec: SerialManipulatorSpec | None,
    links: dict[str, Link],
    joint_by_child: dict[str, Joint],
) -> str:
    roots = sorted(set(links) - set(joint_by_child))
    if spec is not None and spec.base_frame in links and spec.base_frame in roots:
        return spec.base_frame
    if len(roots) != 1:
        raise SemanticGraphBuildError(f"semantic graph requires exactly one root link; found {roots}")
    return roots[0]


def _assert_all_links_reachable(links: dict[str, Link], nodes: dict[str, SemanticNode], root: str) -> None:
    missing = sorted(set(links) - set(nodes))
    if missing:
        raise SemanticGraphBuildError(f"links are unreachable from root {root!r}: {missing}")


def _node_role(
    link: Link,
    incoming_joint: Joint | None,
    spec_joint_by_name: dict[str, Any],
) -> str | None:
    if link.meta.get("role"):
        return str(link.meta["role"])
    if incoming_joint is not None and incoming_joint.meta.get("role"):
        return str(incoming_joint.meta["role"])
    if incoming_joint is not None and incoming_joint.name in spec_joint_by_name:
        return spec_joint_by_name[incoming_joint.name].role
    return None


def _node_metadata(
    link: Link,
    incoming_joint: Joint | None,
    spec_joint_by_name: dict[str, Any],
) -> dict[str, Any]:
    metadata = {"link": dict(link.meta)}
    if incoming_joint is not None:
        metadata["joint"] = dict(incoming_joint.meta)
        spec_joint = spec_joint_by_name.get(incoming_joint.name)
        if spec_joint is not None:
            metadata["kinematic_joint"] = {
                "name": spec_joint.name,
                "role": spec_joint.role,
                "metadata": dict(spec_joint.metadata),
            }
    return metadata


def _dh_index(link: Link, incoming_joint: Joint | None) -> int | None:
    value = link.meta.get("dh_index")
    if value is None and incoming_joint is not None:
        value = incoming_joint.meta.get("dh_index")
    return int(value) if value is not None else None


def _identity_matrix() -> Matrix4:
    return (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _matmul(a: Matrix4, b: Matrix4) -> Matrix4:
    rows = []
    for i in range(4):
        row = []
        for j in range(4):
            row.append(sum(a[i][k] * b[k][j] for k in range(4)))
        rows.append(tuple(row))
    return tuple(rows)  # type: ignore[return-value]


def _origin_to_matrix(origin: Origin) -> Matrix4:
    roll, pitch, yaw = origin.rpy
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, origin.xyz[0]),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, origin.xyz[1]),
        (-sp, cp * sr, cp * cr, origin.xyz[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def _origin_from_matrix(transform: Matrix4) -> Origin:
    return Origin(
        xyz=(transform[0][3], transform[1][3], transform[2][3]),
        rpy=_rpy_from_matrix(transform),
    )


def _rpy_from_matrix(transform: Matrix4) -> Vec3:
    r00, r10 = transform[0][0], transform[1][0]
    r20, r21, r22 = transform[2][0], transform[2][1], transform[2][2]
    r11, r12 = transform[1][1], transform[1][2]
    if abs(r20) < 1.0 - 1e-9:
        pitch = math.asin(-r20)
        roll = math.atan2(r21, r22)
        yaw = math.atan2(r10, r00)
    else:
        pitch = math.pi / 2.0 if r20 <= -1.0 else -math.pi / 2.0
        roll = math.atan2(-r12, r11)
        yaw = 0.0
    return (_clean_float(roll), _clean_float(pitch), _clean_float(yaw))


def _sub_vec3(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(value: Vec3) -> float:
    return math.sqrt(sum(component * component for component in value))


def _angle_between(a: Vec3 | None, b: Vec3 | None) -> float | None:
    if a is None or b is None:
        return None
    norm_a = _norm(a)
    norm_b = _norm(b)
    if norm_a <= 0.0 or norm_b <= 0.0:
        return None
    dot = sum(a[i] * b[i] for i in range(3)) / (norm_a * norm_b)
    dot = min(1.0, max(-1.0, dot))
    return math.acos(dot)


def _clean_float(value: float) -> float:
    return 0.0 if abs(value) < 1e-12 else float(value)
