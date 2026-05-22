"""Two-link Robot SDK scaffold.

Edit build_robot_model() to change the design. Do not hand-write MJCF/URDF;
compile_robot_model owns validation and simulator export.
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
    robot = RobotModel("two_link_arm")
    aluminum = robot.material("aluminum", rgba=(0.7, 0.72, 0.75, 1.0), density=2700.0)

    base_geom = Cylinder(radius=0.08, length=0.06)
    base = robot.link("base", inertial=inertial_from_cylinder(base_geom, mass=1.2))
    base.visual(base_geom, material=aluminum, name="base_visual")
    base.collision(base_geom, name="base_collision")

    link1_geom = Box((0.42, 0.05, 0.05))
    link1 = robot.link("link1", inertial=inertial_from_box(link1_geom, mass=0.8))
    link1.visual(link1_geom, origin=Origin(xyz=(0.21, 0.0, 0.0)), material=aluminum, name="link1_visual")
    link1.collision(link1_geom, origin=Origin(xyz=(0.21, 0.0, 0.0)), name="link1_collision")

    link2_geom = Box((0.32, 0.04, 0.04))
    link2 = robot.link("link2", inertial=inertial_from_box(link2_geom, mass=0.5))
    link2.visual(link2_geom, origin=Origin(xyz=(0.16, 0.0, 0.0)), material=aluminum, name="link2_visual")
    link2.collision(link2_geom, origin=Origin(xyz=(0.16, 0.0, 0.0)), name="link2_collision")

    shoulder = robot.joint(
        "shoulder",
        "revolute",
        parent=base,
        child=link1,
        origin=Origin(xyz=(0.0, 0.0, 0.06)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-1.57, upper=1.57, effort=30.0, velocity=4.0),
        dynamics=JointDynamics(damping=0.05, friction=0.01, armature=0.001),
    )
    elbow = robot.joint(
        "elbow",
        "revolute",
        parent=link1,
        child=link2,
        origin=Origin(xyz=(0.42, 0.0, 0.0)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-2.0, upper=2.0, effort=20.0, velocity=4.0),
        dynamics=JointDynamics(damping=0.04, friction=0.01, armature=0.001),
    )

    robot.actuator("shoulder_motor", shoulder, gear=1.0, ctrl_range=(-1.0, 1.0), force_range=(-30.0, 30.0))
    robot.actuator("elbow_motor", elbow, gear=1.0, ctrl_range=(-1.0, 1.0), force_range=(-20.0, 20.0))
    robot.sensor("shoulder_pos", "jointpos", "shoulder")
    robot.sensor("elbow_pos", "jointpos", "elbow")
    return robot


robot_model = build_robot_model()
