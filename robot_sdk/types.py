"""Core data contracts for the Robot CAD MVP.

These types intentionally contain no CAD or LLM runtime dependencies. They are
the stable boundary between robot subagents and deterministic SDK modules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


LengthUnit = Literal["mm", "m"]
MassUnit = Literal["g", "kg"]
AngleUnit = Literal["rad", "deg"]
JointType = Literal["revolute", "prismatic", "fixed"]
KinematicConvention = Literal["dh", "modified_dh", "poe"]
FrameSemantic = Literal[
    "kinematic",
    "joint_axis",
    "link_body",
    "interface",
    "mount",
    "ee",
]
InterfaceType = Literal[
    "flange",
    "shaft",
    "bearing_seat",
    "bolt_pattern",
    "end_effector_mount",
    "open_end",
]
EnvelopeShape = Literal[
    "box",
    "cylinder",
    "flange_disk",
    "mount_plate",
    "custom",
]
PartFeatureType = Literal["plane", "axis", "point", "edge", "face"]
PartFeatureSemantic = Literal[
    "mount_face",
    "joint_axis",
    "flange_face",
    "link_end",
    "tool_mount",
    "custom",
]
AssemblyConstraintKind = Literal["Fixed", "Plane", "Axis", "Point"]


@dataclass
class SerializableMixin:
    """Small helper for JSON-friendly handoff between agents and SDK code."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Transform(SerializableMixin):
    """Pose transform stored in MVP units.

    Translation is expressed in `length_unit`; rotation is roll-pitch-yaw in
    `angle_unit`. A full 4x4 matrix can be added later without changing the
    higher-level object graph.
    """

    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)
    length_unit: LengthUnit = "mm"
    angle_unit: AngleUnit = "rad"

    def __post_init__(self) -> None:
        if len(self.translation) != 3:
            raise ValueError("Transform.translation must contain 3 values")
        if len(self.rotation_rpy) != 3:
            raise ValueError("Transform.rotation_rpy must contain 3 values")


@dataclass
class RobotRequirement(SerializableMixin):
    """Structured user requirement after requirement analysis."""

    task: str
    dof: int
    payload: float | None = None
    payload_unit: MassUnit = "g"
    reach: float | None = None
    reach_unit: LengthUnit = "mm"
    workspace: str | None = None
    environment: str | None = None
    mounting: str | None = None
    preferred_architecture: str | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.dof <= 0:
            raise ValueError("RobotRequirement.dof must be positive")
        if self.payload is not None and self.payload < 0:
            raise ValueError("RobotRequirement.payload cannot be negative")
        if self.reach is not None and self.reach <= 0:
            raise ValueError("RobotRequirement.reach must be positive")


@dataclass
class DHParam(SerializableMixin):
    """One row of a standard or modified DH table."""

    joint_id: str
    a: float
    alpha: float
    d: float
    theta: float
    joint_type: JointType = "revolute"
    variable: str | None = None
    length_unit: LengthUnit = "mm"
    angle_unit: AngleUnit = "rad"

    def __post_init__(self) -> None:
        if not self.joint_id:
            raise ValueError("DHParam.joint_id is required")
        if self.joint_type == "fixed" and self.variable:
            raise ValueError("Fixed DHParam cannot have a variable")


@dataclass
class JointSpec(SerializableMixin):
    """Logical joint specification before mechanical layout expansion."""

    id: str
    type: JointType
    parent_link: str
    child_link: str
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    limit: tuple[float, float] | None = None
    home: float = 0.0
    effort_limit: float | None = None
    velocity_limit: float | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("JointSpec.id is required")
        if not self.parent_link or not self.child_link:
            raise ValueError("JointSpec parent_link and child_link are required")
        if len(self.axis) != 3:
            raise ValueError("JointSpec.axis must contain 3 values")
        if self.limit and self.limit[0] > self.limit[1]:
            raise ValueError("JointSpec.limit lower bound cannot exceed upper bound")


@dataclass
class LinkSpec(SerializableMixin):
    """Logical link specification before CAD envelope assignment."""

    id: str
    length: float | None = None
    length_unit: LengthUnit = "mm"
    mass: float | None = None
    mass_unit: MassUnit = "kg"
    parent_joint: str | None = None
    child_joint: str | None = None
    material: str | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("LinkSpec.id is required")
        if self.length is not None and self.length <= 0:
            raise ValueError("LinkSpec.length must be positive")
        if self.mass is not None and self.mass < 0:
            raise ValueError("LinkSpec.mass cannot be negative")


@dataclass
class KinematicModel(SerializableMixin):
    """Kinematic layer, separate from mechanical layout and CAD assembly."""

    convention: KinematicConvention
    joints: list[JointSpec]
    links: list[LinkSpec]
    dh_params: list[DHParam] = field(default_factory=list)
    base_frame_id: str = "base_kinematic"
    end_effector_frame_id: str = "end_effector_kinematic"
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.joints:
            raise ValueError("KinematicModel.joints cannot be empty")
        if self.convention in {"dh", "modified_dh"} and not self.dh_params:
            raise ValueError("DH-based KinematicModel requires dh_params")


@dataclass
class RobotSpec(SerializableMixin):
    """Top-level design spec passed through the robot CAD workflow."""

    requirement: RobotRequirement
    kinematic_model: KinematicModel | None = None
    joints: list[JointSpec] = field(default_factory=list)
    links: list[LinkSpec] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class FrameSpec(SerializableMixin):
    """A named frame with explicit mechanical or kinematic semantics."""

    id: str
    parent: str | None
    transform: Transform = field(default_factory=Transform)
    semantic: FrameSemantic = "kinematic"
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("FrameSpec.id is required")


@dataclass
class FrameMapping(SerializableMixin):
    """Explicit mapping between a kinematic frame and a mechanical frame."""

    kinematic_frame_id: str
    mechanical_frame_id: str
    transform_offset: Transform = field(default_factory=Transform)
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.kinematic_frame_id:
            raise ValueError("FrameMapping.kinematic_frame_id is required")
        if not self.mechanical_frame_id:
            raise ValueError("FrameMapping.mechanical_frame_id is required")
        if not self.rationale.strip():
            raise ValueError("FrameMapping.rationale is required")


@dataclass
class EnvelopeSpec(SerializableMixin):
    """CAD envelope hint consumed by skeleton generation."""

    id: str
    shape: EnvelopeShape
    dimensions: dict[str, float]
    frame: str | None = None
    units: LengthUnit = "mm"
    description: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("EnvelopeSpec.id is required")
        if not self.dimensions:
            raise ValueError("EnvelopeSpec.dimensions cannot be empty")
        invalid = [name for name, value in self.dimensions.items() if value <= 0]
        if invalid:
            raise ValueError(f"EnvelopeSpec dimensions must be positive: {invalid}")


@dataclass
class InterfaceSpec(SerializableMixin):
    """Mechanical interface such as a flange, shaft, or end-effector mount."""

    id: str
    frame: str
    type: InterfaceType
    mates_to: str | None = None
    envelope: EnvelopeSpec | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("InterfaceSpec.id is required")
        if not self.frame:
            raise ValueError("InterfaceSpec.frame is required")


@dataclass
class JointLayout(SerializableMixin):
    """Mechanical joint layout consumed by CAD generation."""

    id: str
    type: JointType
    axis_frame: str
    parent_link: str
    child_link: str
    range: tuple[float, float] | None = None
    actuator_envelope: EnvelopeSpec | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("JointLayout.id is required")
        if not self.axis_frame:
            raise ValueError("JointLayout.axis_frame is required")
        if not self.parent_link or not self.child_link:
            raise ValueError("JointLayout parent_link and child_link are required")
        if self.range and self.range[0] > self.range[1]:
            raise ValueError("JointLayout.range lower bound cannot exceed upper bound")


@dataclass
class LinkLayout(SerializableMixin):
    """Mechanical link layout with explicit CAD envelope and interfaces."""

    id: str
    body_frame: str
    from_interface: str | None
    to_interface: str | None
    envelope: EnvelopeSpec

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("LinkLayout.id is required")
        if not self.body_frame:
            raise ValueError("LinkLayout.body_frame is required")


@dataclass
class PartFeature(SerializableMixin):
    """A named CAD-selectable feature on a generated part.

    This is a neutral feature reference, not a CadQuery object. Later CadQuery
    adapters can map `cad_tag` to a tagged face/edge/axis on a Workplane.
    """

    id: str
    part_id: str
    cad_tag: str
    type: PartFeatureType
    semantic: PartFeatureSemantic
    frame: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("PartFeature.id is required")
        if not self.part_id:
            raise ValueError("PartFeature.part_id is required")
        if not self.cad_tag:
            raise ValueError("PartFeature.cad_tag is required")


@dataclass
class AssemblyConstraint(SerializableMixin):
    """Neutral assembly constraint between two part features.

    The constraint references `PartFeature.id` values. It intentionally avoids
    storing final absolute part poses; CadQuery or another adapter should solve
    the actual placement.
    """

    id: str
    fixed: str
    moving: str
    kind: AssemblyConstraintKind
    offset: float | None = None
    flipped: bool = False
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("AssemblyConstraint.id is required")
        if not self.fixed:
            raise ValueError("AssemblyConstraint.fixed feature ref is required")
        if not self.moving:
            raise ValueError("AssemblyConstraint.moving feature ref is required")
        if self.fixed == self.moving:
            raise ValueError("AssemblyConstraint fixed and moving refs must differ")
        if not self.rationale.strip():
            raise ValueError("AssemblyConstraint.rationale is required")


@dataclass
class MechanicalLayout(SerializableMixin):
    """Mechanical assembly semantics between kinematics and CAD skeleton."""

    units: LengthUnit
    base_frame: FrameSpec
    frames: list[FrameSpec]
    joints: list[JointLayout]
    links: list[LinkLayout]
    interfaces: list[InterfaceSpec]
    frame_mappings: list[FrameMapping]
    part_features: list[PartFeature] = field(default_factory=list)
    assembly_constraints: list[AssemblyConstraint] = field(default_factory=list)
    envelopes: list[EnvelopeSpec] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.base_frame.semantic not in {"mount", "kinematic"}:
            raise ValueError("MechanicalLayout.base_frame must be mount or kinematic")
        if not self.frames:
            raise ValueError("MechanicalLayout.frames cannot be empty")
        if self.base_frame.id not in {frame.id for frame in self.frames}:
            raise ValueError("MechanicalLayout.base_frame must be included in frames")
        if not self.frame_mappings:
            raise ValueError("MechanicalLayout.frame_mappings cannot be empty")
