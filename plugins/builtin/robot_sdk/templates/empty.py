"""Robot SDK scaffold.

Edit build_robot_model() only. Do not hand-write MJCF/URDF files; run
compile_robot_model after edits to validate and export artifacts.
"""

from robot_sdk import RobotModel


def build_robot_model() -> RobotModel:
    """Build and return a RobotModel.

    Add links, joints, actuators, sensors, and materials here. Use
    compile_robot_model after edits to validate and export MJCF/URDF.
    """

    robot = RobotModel("draft_robot")
    robot.link("base")
    return robot


robot_model = build_robot_model()
