"""UR3e-like 6DoF arm scaffold backed by the kinematics-first pipeline."""

from robot_sdk import RobotModel, compile_serial_manipulator, ur3e_like_spec


def build_robot_model() -> RobotModel:
    spec = ur3e_like_spec()
    return compile_serial_manipulator(spec, name="ur3e_like")


robot_model = build_robot_model()
