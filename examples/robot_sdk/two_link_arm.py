"""Two-link arm example for robot_sdk."""

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
    robot = RobotModel("example_two_link_arm")
    aluminum = robot.material("aluminum", rgba=(0.7, 0.72, 0.75, 1.0), density=2700.0)

    base_geom = Cylinder(radius=0.08, length=0.06)
    base = robot.link("base", inertial=inertial_from_cylinder(base_geom, mass=1.2))
    base.visual(base_geom, material=aluminum)
    base.collision(base_geom)

    upper_geom = Box((0.4, 0.05, 0.05))
    upper = robot.link("upper_arm", inertial=inertial_from_box(upper_geom, mass=0.8))
    upper.visual(upper_geom, origin=Origin(xyz=(0.2, 0.0, 0.0)), material=aluminum)
    upper.collision(upper_geom, origin=Origin(xyz=(0.2, 0.0, 0.0)))

    forearm_geom = Box((0.3, 0.04, 0.04))
    forearm = robot.link("forearm", inertial=inertial_from_box(forearm_geom, mass=0.5))
    forearm.visual(forearm_geom, origin=Origin(xyz=(0.15, 0.0, 0.0)), material=aluminum)
    forearm.collision(forearm_geom, origin=Origin(xyz=(0.15, 0.0, 0.0)))

    shoulder = robot.joint(
        "shoulder",
        "revolute",
        parent=base,
        child=upper,
        origin=Origin(xyz=(0.0, 0.0, 0.06)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-1.57, upper=1.57, effort=30.0, velocity=4.0),
        dynamics=JointDynamics(damping=0.05, friction=0.01, armature=0.001),
    )
    elbow = robot.joint(
        "elbow",
        "revolute",
        parent=upper,
        child=forearm,
        origin=Origin(xyz=(0.4, 0.0, 0.0)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-2.2, upper=2.2, effort=20.0, velocity=4.0),
        dynamics=JointDynamics(damping=0.04, friction=0.01, armature=0.001),
    )

    robot.actuator("shoulder_motor", shoulder, ctrl_range=(-1.0, 1.0), force_range=(-30.0, 30.0))
    robot.actuator("elbow_motor", elbow, ctrl_range=(-1.0, 1.0), force_range=(-20.0, 20.0))
    robot.sensor("shoulder_pos", "jointpos", "shoulder")
    robot.sensor("elbow_pos", "jointpos", "elbow")
    return robot


robot_model = build_robot_model()
