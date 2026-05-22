"""SCARA-style arm with two revolute joints and one vertical slide."""

from robot_sdk import (
    Box,
    Cylinder,
    JointDynamics,
    JointLimit,
    Origin,
    RobotModel,
    inertial_from_box,
    inertial_from_cylinder,
)


def build_robot_model() -> RobotModel:
    robot = RobotModel("example_scara_arm")
    material = robot.material("painted_metal", rgba=(0.25, 0.32, 0.42, 1.0))

    base_geom = Cylinder(radius=0.09, length=0.08)
    base = robot.link("base", inertial=inertial_from_cylinder(base_geom, mass=2.0))
    base.visual(base_geom, material=material)
    base.collision(base_geom)

    arm1_geom = Box((0.32, 0.05, 0.045))
    arm1 = robot.link("arm1", inertial=inertial_from_box(arm1_geom, mass=0.8))
    arm1.visual(arm1_geom, origin=Origin(xyz=(0.16, 0.0, 0.0)), material=material)
    arm1.collision(arm1_geom, origin=Origin(xyz=(0.16, 0.0, 0.0)))

    arm2_geom = Box((0.24, 0.045, 0.04))
    arm2 = robot.link("arm2", inertial=inertial_from_box(arm2_geom, mass=0.55))
    arm2.visual(arm2_geom, origin=Origin(xyz=(0.12, 0.0, 0.0)), material=material)
    arm2.collision(arm2_geom, origin=Origin(xyz=(0.12, 0.0, 0.0)))

    tool_slide_geom = Cylinder(radius=0.025, length=0.16)
    tool_slide = robot.link("tool_slide", inertial=inertial_from_cylinder(tool_slide_geom, mass=0.25))
    tool_slide.visual(tool_slide_geom, origin=Origin(xyz=(0.0, 0.0, -0.08)), material=material)
    tool_slide.collision(tool_slide_geom, origin=Origin(xyz=(0.0, 0.0, -0.08)))

    shoulder = robot.joint(
        "shoulder_yaw",
        "revolute",
        parent=base,
        child=arm1,
        origin=Origin(xyz=(0.0, 0.0, 0.08)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-3.14, upper=3.14, effort=35.0, velocity=4.0),
        dynamics=JointDynamics(damping=0.04, friction=0.01, armature=0.001),
    )
    elbow = robot.joint(
        "elbow_yaw",
        "revolute",
        parent=arm1,
        child=arm2,
        origin=Origin(xyz=(0.32, 0.0, 0.0)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-2.8, upper=2.8, effort=25.0, velocity=4.0),
        dynamics=JointDynamics(damping=0.035, friction=0.01, armature=0.001),
    )
    vertical = robot.joint(
        "tool_z",
        "prismatic",
        parent=arm2,
        child=tool_slide,
        origin=Origin(xyz=(0.24, 0.0, 0.0)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-0.12, upper=0.02, effort=80.0, velocity=0.5),
        dynamics=JointDynamics(damping=0.02, friction=0.01, armature=0.0),
    )

    robot.actuator("shoulder_motor", shoulder, ctrl_range=(-1.0, 1.0), force_range=(-35.0, 35.0))
    robot.actuator("elbow_motor", elbow, ctrl_range=(-1.0, 1.0), force_range=(-25.0, 25.0))
    robot.actuator("z_axis_motor", vertical, ctrl_range=(-1.0, 1.0), force_range=(-80.0, 80.0))
    robot.sensor("shoulder_pos", "jointpos", "shoulder_yaw")
    robot.sensor("elbow_pos", "jointpos", "elbow_yaw")
    robot.sensor("tool_z_pos", "jointpos", "tool_z")
    return robot


robot_model = build_robot_model()
