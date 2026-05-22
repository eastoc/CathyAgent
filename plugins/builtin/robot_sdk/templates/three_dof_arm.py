"""Three-DoF arm Robot SDK scaffold.

Use this as a starting point for serial manipulators. Edit build_robot_model()
and run compile_robot_model after each coherent change.
"""

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
    robot = RobotModel("three_dof_arm")
    aluminum = robot.material("aluminum", rgba=(0.7, 0.72, 0.75, 1.0), density=2700.0)

    base_geom = Cylinder(radius=0.07, length=0.08)
    base = robot.link("base", inertial=inertial_from_cylinder(base_geom, mass=2.0))
    base.visual(base_geom, material=aluminum, name="base_visual")
    base.collision(base_geom, name="base_collision")

    link1_geom = Box((0.32, 0.05, 0.05))
    link1 = robot.link("link1", inertial=inertial_from_box(link1_geom, mass=1.0))
    link1.visual(link1_geom, origin=Origin(xyz=(0.16, 0.0, 0.0)), material=aluminum, name="link1_visual")
    link1.collision(link1_geom, origin=Origin(xyz=(0.16, 0.0, 0.0)), name="link1_collision")

    link2_geom = Box((0.26, 0.045, 0.045))
    link2 = robot.link("link2", inertial=inertial_from_box(link2_geom, mass=0.7))
    link2.visual(link2_geom, origin=Origin(xyz=(0.13, 0.0, 0.0)), material=aluminum, name="link2_visual")
    link2.collision(link2_geom, origin=Origin(xyz=(0.13, 0.0, 0.0)), name="link2_collision")

    wrist_geom = Box((0.12, 0.035, 0.035))
    wrist = robot.link("wrist", inertial=inertial_from_box(wrist_geom, mass=0.3))
    wrist.visual(wrist_geom, origin=Origin(xyz=(0.06, 0.0, 0.0)), material=aluminum, name="wrist_visual")
    wrist.collision(wrist_geom, origin=Origin(xyz=(0.06, 0.0, 0.0)), name="wrist_collision")

    shoulder = robot.joint(
        "shoulder",
        "revolute",
        parent=base,
        child=link1,
        origin=Origin(xyz=(0.0, 0.0, 0.08)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-3.14, upper=3.14, effort=40.0, velocity=3.0),
        dynamics=JointDynamics(damping=0.05, friction=0.01, armature=0.001),
    )
    elbow = robot.joint(
        "elbow",
        "revolute",
        parent=link1,
        child=link2,
        origin=Origin(xyz=(0.32, 0.0, 0.0)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-2.5, upper=2.5, effort=30.0, velocity=3.0),
        dynamics=JointDynamics(damping=0.04, friction=0.01, armature=0.001),
    )
    wrist_pitch = robot.joint(
        "wrist_pitch",
        "revolute",
        parent=link2,
        child=wrist,
        origin=Origin(xyz=(0.26, 0.0, 0.0)),
        axis=(0.0, 1.0, 0.0),
        limit=JointLimit(lower=-1.57, upper=1.57, effort=15.0, velocity=4.0),
        dynamics=JointDynamics(damping=0.03, friction=0.01, armature=0.001),
    )

    robot.actuator("shoulder_motor", shoulder, ctrl_range=(-1.0, 1.0), force_range=(-40.0, 40.0))
    robot.actuator("elbow_motor", elbow, ctrl_range=(-1.0, 1.0), force_range=(-30.0, 30.0))
    robot.actuator("wrist_motor", wrist_pitch, ctrl_range=(-1.0, 1.0), force_range=(-15.0, 15.0))
    robot.sensor("shoulder_pos", "jointpos", "shoulder")
    robot.sensor("elbow_pos", "jointpos", "elbow")
    robot.sensor("wrist_pos", "jointpos", "wrist_pitch")
    return robot


robot_model = build_robot_model()
