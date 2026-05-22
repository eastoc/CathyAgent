"""Parallel gripper example with two prismatic finger joints."""

from robot_sdk import Box, JointDynamics, JointLimit, Origin, RobotModel, inertial_from_box


def build_robot_model() -> RobotModel:
    robot = RobotModel("example_parallel_gripper")
    dark = robot.material("dark_anodized", rgba=(0.1, 0.1, 0.12, 1.0))
    pad = robot.material("rubber_pad", rgba=(0.02, 0.02, 0.02, 1.0))

    palm_geom = Box((0.16, 0.06, 0.05))
    palm = robot.link("palm", inertial=inertial_from_box(palm_geom, mass=0.5))
    palm.visual(palm_geom, material=dark)
    palm.collision(palm_geom)

    finger_geom = Box((0.035, 0.025, 0.12))
    left = robot.link("left_finger", inertial=inertial_from_box(finger_geom, mass=0.08))
    left.visual(finger_geom, origin=Origin(xyz=(0.0, 0.0, -0.06)), material=pad)
    left.collision(finger_geom, origin=Origin(xyz=(0.0, 0.0, -0.06)))

    right = robot.link("right_finger", inertial=inertial_from_box(finger_geom, mass=0.08))
    right.visual(finger_geom, origin=Origin(xyz=(0.0, 0.0, -0.06)), material=pad)
    right.collision(finger_geom, origin=Origin(xyz=(0.0, 0.0, -0.06)))

    left_slide = robot.joint(
        "left_slide",
        "prismatic",
        parent=palm,
        child=left,
        origin=Origin(xyz=(0.04, 0.02, -0.025)),
        axis=(0.0, 1.0, 0.0),
        limit=JointLimit(lower=0.0, upper=0.035, effort=40.0, velocity=0.3),
        dynamics=JointDynamics(damping=0.02, friction=0.02),
    )
    right_slide = robot.joint(
        "right_slide",
        "prismatic",
        parent=palm,
        child=right,
        origin=Origin(xyz=(0.04, -0.02, -0.025)),
        axis=(0.0, -1.0, 0.0),
        limit=JointLimit(lower=0.0, upper=0.035, effort=40.0, velocity=0.3),
        dynamics=JointDynamics(damping=0.02, friction=0.02),
    )

    robot.actuator("left_finger_motor", left_slide, ctrl_range=(0.0, 1.0), force_range=(-40.0, 40.0))
    robot.actuator("right_finger_motor", right_slide, ctrl_range=(0.0, 1.0), force_range=(-40.0, 40.0))
    robot.sensor("left_finger_pos", "jointpos", "left_slide")
    robot.sensor("right_finger_pos", "jointpos", "right_slide")
    return robot


robot_model = build_robot_model()
