"""Three-DoF arm Robot SDK scaffold backed by the kinematics-first pipeline.

Keep this template as the default 3DoF workflow. The primitive-only historical
version is preserved under templates/_backup as a migration reference.
"""

from robot_sdk import RobotModel, compile_serial_manipulator, demo_three_dof_spec


def build_robot_model() -> RobotModel:
    spec = demo_three_dof_spec()
    return compile_serial_manipulator(spec, name="three_dof_arm")


robot_model = build_robot_model()
