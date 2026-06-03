"""Deterministic ID generation for the Robot CAD workflow.

This module is deliberately boring: it only decides names. Keeping the naming
rules here prevents every agent from inventing its own IDs.

Naming layers:
- `J1`, `J2`, ... are logical joint IDs.
- `L1`, `L2`, ... are logical moving-link IDs.
- `base` is the fixed base link.
- `end_effector` is the tool/output side of the last moving link.

Frame suffixes describe what a frame means:
- `*_kinematic`: abstract DH/kinematic frame. This is math, not CAD assembly.
- `*_axis`: mechanical joint-axis frame. CAD can use this to orient a joint.
- `*_body`: mechanical link-body frame. CAD can use this to place a link body.
- `*_mount`: mounting/interface frame.
- `end_effector_tool`: tool/TCP-side frame for the end effector.

Interface IDs describe a mechanical connection intent:
- `base_to_J1_flange`
- `J1_to_L1_flange`
- `L1_to_J2_flange`
- `L4_to_end_effector_mount`

These IDs are not globally unique object UUIDs. They are stable, readable names
for a single generated robot design artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FrameIdSuffix = Literal["axis", "body", "kinematic", "mount", "tool", "interface"]
InterfaceKind = Literal[
    "flange",
    "shaft",
    "bearing_seat",
    "bolt_pattern",
    "end_effector_mount",
    "open_end",
]


@dataclass(frozen=True)
class SerialChainIds:
    """Grouped ID set for a simple serial robot chain.

    A 4DOF tabletop arm produces:
    - joints: `J1`..`J4`
    - links: `L1`..`L4`
    - kinematic frames: `base_kinematic`, `J1_kinematic`.., `end_effector_kinematic`
    - mechanical frames: `J1_axis`.. and `L1_body`..
    - interfaces: flange/mount names describing connection intent

    This object is useful because later modules usually need the whole naming
    bundle, not one ID at a time.
    """

    joints: list[str]
    links: list[str]
    base_link: str = "base"
    end_effector_link: str = "end_effector"
    joint_axis_frames: list[str] | None = None
    link_body_frames: list[str] | None = None
    kinematic_frames: list[str] | None = None
    interfaces: list[str] | None = None

    def __post_init__(self) -> None:
        if not self.joints:
            raise ValueError("SerialChainIds.joints cannot be empty")
        if not self.links:
            raise ValueError("SerialChainIds.links cannot be empty")
        if len(set(self.joints)) != len(self.joints):
            raise ValueError("SerialChainIds.joints must be unique")
        if len(set(self.links)) != len(self.links):
            raise ValueError("SerialChainIds.links must be unique")


def generate_indexed_ids(prefix: str, count: int, *, start: int = 1, width: int = 0) -> list[str]:
    """Generate plain indexed IDs.

    Examples:
    - `generate_indexed_ids("J", 3)` -> `["J1", "J2", "J3"]`
    - `generate_indexed_ids("L", 2, width=2)` -> `["L01", "L02"]`
    """

    prefix = _require_token(prefix, "prefix")
    if count < 0:
        raise ValueError("count cannot be negative")
    if start < 0:
        raise ValueError("start cannot be negative")
    if width < 0:
        raise ValueError("width cannot be negative")
    return [f"{prefix}{index:0{width}d}" for index in range(start, start + count)]


def generate_joint_ids(dof: int, *, prefix: str = "J") -> list[str]:
    """Generate canonical logical joint IDs.

    `dof` is used here because the MVP assumes one actuated joint per degree of
    freedom. If later a mechanism has passive joints, it should use lower-level
    ID helpers explicitly.
    """

    if dof <= 0:
        raise ValueError("dof must be positive")
    return generate_indexed_ids(prefix, dof)


def generate_link_ids(count: int, *, prefix: str = "L") -> list[str]:
    """Generate canonical moving-link IDs.

    The fixed base is named `base` and is not included in this list.
    """

    if count <= 0:
        raise ValueError("count must be positive")
    return generate_indexed_ids(prefix, count)


def generate_interface_ids(count: int, *, prefix: str = "I") -> list[str]:
    """Generate generic interface IDs.

    Prefer semantic IDs from `interface_id()` when the connected objects are
    known. Use this generic helper only for temporary or imported interfaces.
    """

    if count <= 0:
        raise ValueError("count must be positive")
    return generate_indexed_ids(prefix, count)


def generate_serial_chain_ids(
    dof: int,
    *,
    include_base_interface: bool = True,
    include_end_effector_interface: bool = True,
) -> SerialChainIds:
    """Generate canonical IDs for a simple serial-chain robot.

    Convention:
    - joints: J1..Jn
    - moving links: L1..Ln
    - base link: base
    - end effector link: end_effector
    - kinematic frames: base_kinematic, J1_kinematic..Jn_kinematic, end_effector_kinematic
    - interfaces alternate through the chain:
      base -> J1 -> L1 -> J2 -> L2 -> ... -> end_effector

    This is the default ID set for the first MVP path. It intentionally does not
    encode geometry, dimensions, motor choices, or CAD mating transforms.
    """

    joints = generate_joint_ids(dof)
    links = generate_link_ids(dof)
    interfaces = _serial_chain_interfaces(
        joints,
        links,
        include_base_interface=include_base_interface,
        include_end_effector_interface=include_end_effector_interface,
    )
    return SerialChainIds(
        joints=joints,
        links=links,
        joint_axis_frames=[joint_axis_frame_id(joint_id) for joint_id in joints],
        link_body_frames=[body_frame_id(link_id) for link_id in links],
        kinematic_frames=["base_kinematic"]
        + [kinematic_frame_id(joint_id) for joint_id in joints]
        + [end_effector_frame_id("kinematic")],
        interfaces=interfaces,
    )


def joint_axis_frame_id(joint_id: str) -> str:
    """Return a mechanical joint-axis frame ID, e.g. `J1_axis`.

    This is the frame that later CAD/layout code can treat as the physical
    rotation or translation axis for the joint.
    """

    return _frame_id(joint_id, "axis")


def body_frame_id(link_id: str) -> str:
    """Return a mechanical link-body frame ID, e.g. `L1_body`.

    This frame represents where the solid body/envelope of a link is placed. It
    is intentionally separate from the DH frame.
    """

    return _frame_id(link_id, "body")


def kinematic_frame_id(owner_id: str) -> str:
    """Return an abstract kinematic frame ID, e.g. `J1_kinematic`.

    Kinematic frames belong to DH/FK math. They must be mapped to mechanical
    frames before CAD generation.
    """

    return _frame_id(owner_id, "kinematic")


def mount_frame_id(owner_id: str = "base") -> str:
    """Return a mount frame ID, e.g. `base_mount`.

    Mount frames describe mechanical attachment surfaces, not motion variables.
    """

    return _frame_id(owner_id, "mount")


def end_effector_frame_id(kind: FrameIdSuffix = "tool") -> str:
    """Return an end-effector frame ID.

    Defaults to `end_effector_tool`, the tool/TCP-side frame. Other useful
    variants are `end_effector_kinematic` and `end_effector_mount`.
    """

    if kind == "tool":
        return "end_effector_tool"
    if kind == "kinematic":
        return "end_effector_kinematic"
    if kind == "mount":
        return "end_effector_mount"
    return _frame_id("end_effector", kind)


def interface_id(first_id: str, second_id: str | None, kind: InterfaceKind) -> str:
    """Return a semantic mechanical interface ID.

    The ID names the two sides and the interface intent:
    - `J1_to_L1_flange`
    - `L1_to_J2_flange`
    - `L4_to_end_effector_mount`

    `second_id=None` means an intentionally open interface, for example an
    unused mounting face.
    """

    first = _require_token(first_id, "first_id")
    second = _sanitize_token(second_id) if second_id else "open"
    suffix = _require_token(kind, "kind")
    if second == "end_effector" and suffix == "end_effector_mount":
        return f"{first}_to_end_effector_mount"
    return f"{first}_to_{second}_{suffix}"


def interface_frame_id(interface_id_value: str) -> str:
    """Return the frame ID that represents a mechanical interface.

    Example:
    - `interface_frame_id("J1_to_L1_flange")`
      -> `J1_to_L1_flange_interface`

    The interface object (`InterfaceSpec`) describes the connection intent; the
    interface frame is the actual coordinate frame that CAD/layout code can use.
    """

    return _frame_id(interface_id_value, "interface")


def _serial_chain_interfaces(
    joints: list[str],
    links: list[str],
    *,
    include_base_interface: bool,
    include_end_effector_interface: bool,
) -> list[str]:
    """Build the default serial-chain interface list.

    This deliberately creates the full alternating chain:

        base -> J1 -> L1 -> J2 -> L2 -> ... -> end_effector

    More detailed bearing/shaft/flange pairs can be added later by the
    mechanical layout layer.
    """
    interfaces: list[str] = []
    if include_base_interface:
        interfaces.append(interface_id("base", joints[0], "flange"))
    for index, (joint_id, link_id) in enumerate(zip(joints, links, strict=True)):
        interfaces.append(interface_id(joint_id, link_id, "flange"))
        if index + 1 < len(joints):
            interfaces.append(interface_id(link_id, joints[index + 1], "flange"))
    if include_end_effector_interface:
        interfaces.append(interface_id(links[-1], "end_effector", "end_effector_mount"))
    return interfaces


def _frame_id(owner_id: str, kind: FrameIdSuffix) -> str:
    """Join an owner ID and a frame suffix after light token cleanup."""

    owner = _require_token(owner_id, "owner_id")
    suffix = _require_token(kind, "kind")
    return f"{owner}_{suffix}"


def _require_token(value: str, name: str) -> str:
    """Return a sanitized token or fail with a field-specific message."""

    token = _sanitize_token(value)
    if not token:
        raise ValueError(f"{name} is required")
    return token


def _sanitize_token(value: str | None) -> str:
    """Normalize simple user-readable tokens for ID construction."""

    if value is None:
        return ""
    return str(value).strip().replace(" ", "_")
