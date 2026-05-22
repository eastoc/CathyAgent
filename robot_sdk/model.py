"""Core robot design protocol for CathyAgent's robot SDK.

The model layer is intentionally small and simulator-neutral. Exporters map
these dataclasses to concrete targets such as MuJoCo MJCF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Sequence, Tuple, Union

# 表示 三维向量，三个 float 组成一个元组，常见用途：位置(Origin.xyz)、姿态(Origin.rpy)、关节轴(Joint.axis)、盒子尺寸(Box.size)
Vec3 = Tuple[float, float, float] 
# 关节类型,[旋转关节,连续关节,平动关节,固定关节,球关节,自由关节]
JointKind = Literal["revolute", "continuous", "prismatic", "fixed", "ball", "free"]
# 执行器类型,[电机,位置,速度]
ActuatorKind = Literal["motor", "position", "velocity"]
# 传感器类型,[关节位置,关节速度,惯性测量单元,力,触摸,相机]
SensorKind = Literal["jointpos", "jointvel", "imu", "force", "touch", "camera"]


def _vec3(values: Sequence[float], *, name: str) -> Vec3:
    if len(values) != 3:
        raise ValueError(f"{name} must have 3 values")
    return (float(values[0]), float(values[1]), float(values[2]))


def _positive(value: float, *, name: str) -> float:
    value = float(value)
    if value <= 0.0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Origin:
    """Pose of an item relative to its parent frame.

    ``xyz`` is translation in meters. ``rpy`` is roll/pitch/yaw in radians.
    """

    xyz: Vec3 = (0.0, 0.0, 0.0)
    rpy: Vec3 = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "xyz", _vec3(self.xyz, name="origin.xyz"))
        object.__setattr__(self, "rpy", _vec3(self.rpy, name="origin.rpy"))


@dataclass(frozen=True)
class Box:
    """Box geometry with full extents in meters."""

    size: Vec3

    def __post_init__(self) -> None:
        size = _vec3(self.size, name="box.size")
        if any(v <= 0.0 for v in size):
            raise ValueError("box.size values must be positive")
        object.__setattr__(self, "size", size)


@dataclass(frozen=True)
class Cylinder:
    """Cylinder geometry aligned to the local z-axis."""

    radius: float
    length: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "radius", _positive(self.radius, name="cylinder.radius"))
        object.__setattr__(self, "length", _positive(self.length, name="cylinder.length"))


@dataclass(frozen=True)
class Sphere:
    """Sphere geometry defined by radius in meters."""

    radius: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "radius", _positive(self.radius, name="sphere.radius"))


@dataclass(frozen=True)
class Mesh:
    """External or generated mesh geometry.

    ``filename`` is the simulator-facing asset reference. ``materialized_path``
    records the local generated path when the mesh was produced by robot_sdk.
    """

    filename: str
    name: str | None = None
    scale: Vec3 | None = None
    materialized_path: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        filename = str(self.filename).strip()
        if not filename:
            raise ValueError("mesh.filename is required")
        object.__setattr__(self, "filename", filename)
        if self.name is not None:
            name = str(self.name).strip()
            if not name:
                raise ValueError("mesh.name must be non-empty when provided")
            object.__setattr__(self, "name", name)
        if self.scale is not None:
            scale = _vec3(self.scale, name="mesh.scale")
            if any(v <= 0.0 for v in scale):
                raise ValueError("mesh.scale values must be positive")
            object.__setattr__(self, "scale", scale)


Geometry = Union[Box, Cylinder, Sphere, Mesh]


@dataclass(frozen=True)
class Material:
    """Reusable visual material and optional density metadata."""

    name: str
    rgba: tuple[float, float, float, float] | None = None
    density: float | None = None

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("material.name is required")
        object.__setattr__(self, "name", name)
        if self.rgba is not None:
            rgba = tuple(float(v) for v in self.rgba)
            if len(rgba) == 3:
                rgba = rgba + (1.0,)
            if len(rgba) != 4:
                raise ValueError("material.rgba must have 3 or 4 values")
            object.__setattr__(self, "rgba", rgba)
        if self.density is not None:
            object.__setattr__(self, "density", _positive(self.density, name="material.density"))


@dataclass
class Visual:
    """Visual-only geometry attached to a link.
    渲染、预览、给人看；不参与物理碰撞。
    """

    geometry: Geometry # Box / Cylinder / Sphere / Mesh
    origin: Origin = field(default_factory=Origin) # 相对 link 坐标系的位姿
    material: str | Material | None = None # 颜色、材质
    name: str | None = None


@dataclass
class Collision:
    """Collision geometry attached to a link.
    仿真里的接触、碰撞检测（MuJoCo 导出成 geom，带碰撞属性
    物理碰撞、计算碰撞响应；不参与渲染、预览。
    """

    geometry: Geometry
    origin: Origin = field(default_factory=Origin)
    name: str | None = None


@dataclass(frozen=True)
class Inertia:
    """Rotational inertia tensor components around a link frame.
    惯性张量，描述物体绕某个轴的旋转惯性。
    """
    ixx: float
    ixy: float
    ixz: float
    iyy: float
    iyz: float
    izz: float


@dataclass(frozen=True)
class Inertial:
    """Mass properties for a link."""

    mass: float
    inertia: Inertia | None = None
    origin: Origin = field(default_factory=Origin)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mass", _positive(self.mass, name="inertial.mass"))


@dataclass
class Link:
    """Rigid body in the robot kinematic tree."""

    name: str
    visuals: list[Visual] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)
    inertial: Inertial | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _clean_name(self.name, field_name="link.name")

    def visual(
        self,
        geometry: Geometry,
        *,
        origin: Origin | None = None,
        material: str | Material | None = None,
        name: str | None = None,
    ) -> Visual:
        """Add a visual geometry to this link and return it."""

        item = Visual(geometry=geometry, origin=origin or Origin(), material=material, name=name)
        self.visuals.append(item)
        return item

    def collision(
        self,
        geometry: Geometry,
        *,
        origin: Origin | None = None,
        name: str | None = None,
    ) -> Collision:
        """Add collision geometry to this link and return it."""

        item = Collision(geometry=geometry, origin=origin or Origin(), name=name)
        self.collisions.append(item)
        return item


@dataclass(frozen=True)
class JointLimit:
    """Motion limits for revolute and prismatic joints.
    运动学约束
    """

    lower: float | None = None
    upper: float | None = None
    effort: float = 1.0
    velocity: float = 1.0

    def __post_init__(self) -> None:
        if self.lower is not None:
            object.__setattr__(self, "lower", float(self.lower))
        if self.upper is not None:
            object.__setattr__(self, "upper", float(self.upper))
        object.__setattr__(self, "effort", _positive(self.effort, name="joint_limit.effort"))
        object.__setattr__(self, "velocity", _positive(self.velocity, name="joint_limit.velocity"))
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("joint_limit.lower cannot exceed joint_limit.upper")


@dataclass(frozen=True)
class JointDynamics:
    """Passive joint dynamics used by simulators such as MuJoCo.
    动力学约束，阻尼、摩擦、关节上的等效转动惯量等
    """

    damping: float = 0.0
    friction: float = 0.0
    armature: float = 0.0 

    def __post_init__(self) -> None:
        object.__setattr__(self, "damping", max(0.0, float(self.damping)))
        object.__setattr__(self, "friction", max(0.0, float(self.friction)))
        object.__setattr__(self, "armature", max(0.0, float(self.armature)))


@dataclass
class Joint:
    """Kinematic relation from a parent link to a child link."""

    name: str
    joint_type: JointKind
    parent: str
    child: str
    origin: Origin = field(default_factory=Origin)
    axis: Vec3 = (0.0, 0.0, 1.0)
    limit: JointLimit | None = None
    dynamics: JointDynamics | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _clean_name(self.name, field_name="joint.name")
        self.parent = _clean_name(self.parent, field_name="joint.parent")
        self.child = _clean_name(self.child, field_name="joint.child")
        self.axis = _vec3(self.axis, name="joint.axis")
        if self.parent == self.child:
            raise ValueError("joint parent and child cannot be the same")


@dataclass
class Actuator:
    """Actuator command channel bound to one joint.
    Actuator（执行器） 表示 怎么驱动某个关节：
    仿真或控制里，你发一条控制指令，由它转换成作用在该 joint 上的力/力矩或位置/速度目标
    """

    name: str
    joint: str
    actuator_type: ActuatorKind = "motor"
    gear: float = 1.0
    ctrl_range: tuple[float, float] | None = None
    force_range: tuple[float, float] | None = None
    kp: float | None = None
    kv: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _clean_name(self.name, field_name="actuator.name")
        self.joint = _clean_name(self.joint, field_name="actuator.joint")
        self.gear = float(self.gear)
        if self.ctrl_range is not None:
            self.ctrl_range = _range_pair(self.ctrl_range, name="actuator.ctrl_range")
        if self.force_range is not None:
            self.force_range = _range_pair(self.force_range, name="actuator.force_range")
        if self.kp is not None:
            self.kp = max(0.0, float(self.kp))
        if self.kv is not None:
            self.kv = max(0.0, float(self.kv))


@dataclass
class Sensor:
    """Sensor channel bound to a joint, link, or simulator site."""

    name: str
    sensor_type: SensorKind
    target: str
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _clean_name(self.name, field_name="sensor.name")
        self.target = _clean_name(self.target, field_name="sensor.target")


@dataclass
class RobotModel:
    """Top-level robot definition.

    A valid robot is a set of links connected by joints, optionally enriched
    with actuators, sensors, materials, and simulator-specific metadata.
    """

    name: str
    links: list[Link] = field(default_factory=list)
    joints: list[Joint] = field(default_factory=list)
    actuators: list[Actuator] = field(default_factory=list)
    sensors: list[Sensor] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _clean_name(self.name, field_name="robot.name")

    def link(self, name: str, *, inertial: Inertial | None = None, meta: dict[str, Any] | None = None) -> Link:
        """Create and register a link."""

        item = Link(name=name, inertial=inertial, meta=dict(meta or {}))
        self.links.append(item)
        return item

    def joint(
        self,
        name: str,
        joint_type: JointKind,
        parent: str | Link,
        child: str | Link,
        *,
        origin: Origin | None = None,
        axis: Vec3 = (0.0, 0.0, 1.0),
        limit: JointLimit | None = None,
        dynamics: JointDynamics | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Joint:
        """Create and register a joint between two links."""

        item = Joint(
            name=name,
            joint_type=joint_type,
            parent=_link_ref(parent),
            child=_link_ref(child),
            origin=origin or Origin(),
            axis=axis,
            limit=limit,
            dynamics=dynamics,
            meta=dict(meta or {}),
        )
        self.joints.append(item)
        return item

    def actuator(
        self,
        name: str,
        joint: str | Joint,
        *,
        actuator_type: ActuatorKind = "motor",
        gear: float = 1.0,
        ctrl_range: tuple[float, float] | None = None,
        force_range: tuple[float, float] | None = None,
        kp: float | None = None,
        kv: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Actuator:
        """Create and register an actuator for a joint."""

        item = Actuator(
            name=name,
            joint=_joint_ref(joint),
            actuator_type=actuator_type,
            gear=gear,
            ctrl_range=ctrl_range,
            force_range=force_range,
            kp=kp,
            kv=kv,
            meta=dict(meta or {}),
        )
        self.actuators.append(item)
        return item

    def sensor(self, name: str, sensor_type: SensorKind, target: str, *, meta: dict[str, Any] | None = None) -> Sensor:
        """Create and register a sensor."""

        item = Sensor(name=name, sensor_type=sensor_type, target=target, meta=dict(meta or {}))
        self.sensors.append(item)
        return item

    def material(
        self,
        name: str,
        *,
        rgba: tuple[float, float, float] | tuple[float, float, float, float] | None = None,
        density: float | None = None,
    ) -> Material:
        """Create and register a reusable material."""

        item = Material(name=name, rgba=rgba, density=density)
        self.materials.append(item)
        return item

    def get_link(self, name: str) -> Link:
        key = _clean_name(name, field_name="link.name")
        for link in self.links:
            if link.name == key:
                return link
        raise KeyError(f"unknown link: {name!r}")

    def get_joint(self, name: str) -> Joint:
        key = _clean_name(name, field_name="joint.name")
        for joint in self.joints:
            if joint.name == key:
                return joint
        raise KeyError(f"unknown joint: {name!r}")


def _clean_name(value: str, *, field_name: str) -> str:
    name = str(value).strip()
    if not name:
        raise ValueError(f"{field_name} is required")
    return name


def _link_ref(value: str | Link) -> str:
    return value.name if isinstance(value, Link) else _clean_name(value, field_name="link ref")


def _joint_ref(value: str | Joint) -> str:
    return value.name if isinstance(value, Joint) else _clean_name(value, field_name="joint ref")


def _range_pair(values: tuple[float, float], *, name: str) -> tuple[float, float]:
    if len(values) != 2:
        raise ValueError(f"{name} must have 2 values")
    lo, hi = float(values[0]), float(values[1])
    if lo > hi:
        raise ValueError(f"{name} lower value cannot exceed upper value")
    return (lo, hi)
