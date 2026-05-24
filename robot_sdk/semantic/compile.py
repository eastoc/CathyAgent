"""Compile mathematical kinematic specs into RobotModel semantic structure."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from robot_sdk.inertia import inertial_from_box, inertial_from_cylinder
from robot_sdk.kinematics.checks import check_serial_manipulator_spec
from robot_sdk.kinematics.dh import Matrix4, joint_transform
from robot_sdk.kinematics.model import DHJoint, SerialManipulatorSpec
from robot_sdk.model import Box, Cylinder, JointLimit, Origin, RobotModel

from .naming import actuator_name_for_joint, clean_identifier, link_name_for_joint, sensor_name_for_joint


@dataclass(frozen=True)
class SerialCompileOptions:
    """Options for serial manipulator semantic compilation."""

    add_actuators: bool = True
    add_sensors: bool = True
    add_placeholder_geometry: bool = True
    base_radius: float = 0.05
    base_height: float = 0.04
    link_radius: float = 0.025
    base_mass: float = 1.0
    link_mass: float = 0.25
    default_effort: float = 20.0
    default_velocity: float = 2.0
    default_ctrl_range: tuple[float, float] = (-1.0, 1.0)


def compile_serial_manipulator(
    spec: SerialManipulatorSpec,
    *,
    name: str | None = None,
    options: SerialCompileOptions | None = None,
) -> RobotModel:
    """Compile a serial manipulator kinematic spec into a ``RobotModel``.

    The generated model is intentionally semantic and simulator-neutral. It
    creates a connected link/joint tree, actuator and joint-position sensor
    channels, and simple placeholder geometry so the current SDK validators and
    exporters can consume it before detailed visual generation is added.
    """

    if spec.representation not in {"dh", "modified_dh"}:
        raise ValueError(f"compile_serial_manipulator does not support {spec.representation!r} yet")

    report = check_serial_manipulator_spec(spec)
    report.assert_ok()
    opts = options or SerialCompileOptions()

    robot = RobotModel(
        clean_identifier(name or spec.name, fallback="serial_manipulator"),
        meta={
            "source": "kinematics",
            "kinematic_type": "serial_manipulator",
            "representation": spec.representation,
            "dof": spec.dof,
            "base_frame": spec.base_frame,
            "tool_frame": spec.tool_frame,
            "metadata": dict(spec.metadata),
        },
    )
    material = robot.material("kinematic_gray", rgba=(0.72, 0.74, 0.76, 1.0), density=1200.0)

    base_link = robot.link(
        clean_identifier(spec.base_frame, fallback="base"),
        inertial=inertial_from_cylinder(Cylinder(opts.base_radius, opts.base_height), mass=opts.base_mass),
        meta={"role": "base", "source": "kinematics"},
    )
    if opts.add_placeholder_geometry:
        base_geom = Cylinder(opts.base_radius, opts.base_height)
        base_link.visual(base_geom, material=material, name="base_placeholder_visual")
        base_link.collision(base_geom, name="base_placeholder_collision")

    parent_link = base_link
    for index, dh_joint in enumerate(spec.joints):
        child_link = robot.link(
            link_name_for_joint(dh_joint.name, index=index, tool_frame=spec.tool_frame),
            inertial=_placeholder_inertial(dh_joint, opts),
            meta={
                "role": dh_joint.role or "serial_link",
                "source": "kinematics",
                "dh_index": index,
                "dh_joint": dh_joint.name,
                "metadata": dict(dh_joint.metadata),
            },
        )
        if opts.add_placeholder_geometry:
            _add_placeholder_link_geometry(child_link, dh_joint, opts, material.name)

        joint = robot.joint(
            clean_identifier(dh_joint.name, fallback=f"joint_{index + 1}"),
            _robot_joint_type(dh_joint),
            parent=parent_link,
            child=child_link,
            origin=_origin_from_dh_row(dh_joint, spec.representation),
            axis=_axis_for_joint(dh_joint),
            limit=_joint_limit(dh_joint, opts),
            meta={
                "source": "kinematics",
                "representation": spec.representation,
                "dh_index": index,
                "dh": {
                    "alpha": dh_joint.alpha,
                    "a": dh_joint.a,
                    "d": dh_joint.d,
                    "theta": dh_joint.theta,
                },
                "role": dh_joint.role,
                "metadata": dict(dh_joint.metadata),
            },
        )
        if opts.add_actuators and dh_joint.joint_type in {"revolute", "continuous", "prismatic"}:
            robot.actuator(
                actuator_name_for_joint(joint.name),
                joint,
                ctrl_range=_ctrl_range(dh_joint, opts),
                force_range=(-opts.default_effort, opts.default_effort),
                meta={"source": "kinematics", "dh_index": index},
            )
        if opts.add_sensors and dh_joint.joint_type in {"revolute", "continuous", "prismatic"}:
            robot.sensor(
                sensor_name_for_joint(joint.name),
                "jointpos",
                joint.name,
                meta={"source": "kinematics", "dh_index": index},
            )
        parent_link = child_link

    if spec.joints:
        tool_link = robot.link(
            clean_identifier(spec.tool_frame, fallback="tool0"),
            inertial=inertial_from_box(Box((0.04, 0.04, 0.02)), mass=0.05),
            meta={"role": "tool", "source": "kinematics"},
        )
        if opts.add_placeholder_geometry:
            tool_geom = Box((0.04, 0.04, 0.02))
            tool_link.visual(tool_geom, material=material.name, name="tool_placeholder_visual")
            tool_link.collision(tool_geom, name="tool_placeholder_collision")
        robot.joint(
            clean_identifier(f"{parent_link.name}_to_{tool_link.name}", fallback="tool_fixed"),
            "fixed",
            parent=parent_link,
            child=tool_link,
            origin=Origin(),
            axis=(0.0, 0.0, 1.0),
            meta={"source": "kinematics", "role": "tool_mount"},
        )

    return robot


def _origin_from_dh_row(joint: DHJoint, representation: str) -> Origin:
    transform = joint_transform(
        joint,
        representation=representation,
        joint_values=_zero_values_for_row(joint),
        index=0,
    )
    return Origin(xyz=(transform[0][3], transform[1][3], transform[2][3]), rpy=_rpy_from_matrix(transform))


def _zero_values_for_row(joint: DHJoint) -> Mapping[str, float]:
    values: dict[str, float] = {}
    if isinstance(joint.theta, str):
        values[joint.theta] = 0.0
    if isinstance(joint.d, str):
        values[joint.d] = 0.0
    return values


def _rpy_from_matrix(transform: Matrix4) -> tuple[float, float, float]:
    r00, r01, r02 = transform[0][0], transform[0][1], transform[0][2]
    r10, r11, r12 = transform[1][0], transform[1][1], transform[1][2]
    r20, r21, r22 = transform[2][0], transform[2][1], transform[2][2]
    if abs(r20) < 1.0 - 1e-9:
        pitch = math.asin(-r20)
        roll = math.atan2(r21, r22)
        yaw = math.atan2(r10, r00)
    else:
        pitch = math.pi / 2.0 if r20 <= -1.0 else -math.pi / 2.0
        roll = math.atan2(-r12, r11)
        yaw = 0.0
    return (_clean_float(roll), _clean_float(pitch), _clean_float(yaw))


def _axis_for_joint(joint: DHJoint) -> tuple[float, float, float]:
    if joint.axis_hint is not None:
        return joint.axis_hint
    if joint.joint_type in {"revolute", "continuous", "prismatic"}:
        return (0.0, 0.0, 1.0)
    return (0.0, 0.0, 1.0)


def _robot_joint_type(joint: DHJoint) -> str:
    return "fixed" if joint.joint_type == "fixed" else joint.joint_type


def _joint_limit(joint: DHJoint, opts: SerialCompileOptions) -> JointLimit | None:
    if joint.joint_type == "continuous" or joint.joint_type == "fixed":
        return None
    if joint.limit is None:
        if joint.joint_type == "revolute":
            lower, upper = -math.pi, math.pi
        else:
            lower, upper = 0.0, 0.1
    else:
        lower, upper = joint.limit
    return JointLimit(lower=lower, upper=upper, effort=opts.default_effort, velocity=opts.default_velocity)


def _ctrl_range(joint: DHJoint, opts: SerialCompileOptions) -> tuple[float, float] | None:
    if joint.joint_type in {"revolute", "continuous", "prismatic"}:
        return opts.default_ctrl_range
    return None


def _placeholder_inertial(dh_joint: DHJoint, opts: SerialCompileOptions):
    geom = _placeholder_box(dh_joint, opts)
    return inertial_from_box(geom, mass=opts.link_mass)


def _add_placeholder_link_geometry(link, dh_joint: DHJoint, opts: SerialCompileOptions, material: str) -> None:
    geom = _placeholder_box(dh_joint, opts)
    link.visual(geom, origin=Origin(xyz=(geom.size[0] / 2.0, 0.0, 0.0)), material=material, name=f"{link.name}_placeholder_visual")
    link.collision(geom, origin=Origin(xyz=(geom.size[0] / 2.0, 0.0, 0.0)), name=f"{link.name}_placeholder_collision")


def _placeholder_box(dh_joint: DHJoint, opts: SerialCompileOptions) -> Box:
    # Placeholder geometry is only for early validation/export; the geometry layer
    # should replace it with mesh-backed visuals that follow the real mechanism.
    d_value = dh_joint.d if not isinstance(dh_joint.d, str) else 0.0
    length = max(abs(float(dh_joint.a)), abs(float(d_value)), opts.link_radius * 2.0, 0.04)
    width = max(opts.link_radius * 2.0, 0.02)
    return Box((length, width, width))


def _clean_float(value: float) -> float:
    return 0.0 if abs(value) < 1e-12 else float(value)
