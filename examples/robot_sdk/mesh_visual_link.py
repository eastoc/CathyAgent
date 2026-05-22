"""Mesh-backed visual example using robot_sdk.assets."""

from robot_sdk import AssetSession, Box, Origin, RobotModel, inertial_from_box, mesh_from_vertices


def build_robot_model() -> RobotModel:
    robot = RobotModel("example_mesh_visual_link")

    assets = AssetSession("build/robot")
    mesh_export = mesh_from_vertices(
        vertices=[
            (0.0, -0.03, -0.02),
            (0.24, -0.03, -0.02),
            (0.24, 0.03, -0.02),
            (0.0, 0.03, -0.02),
            (0.02, -0.035, 0.02),
            (0.22, -0.035, 0.02),
            (0.22, 0.035, 0.02),
            (0.02, 0.035, 0.02),
        ],
        faces=[
            (0, 1, 2),
            (0, 2, 3),
            (4, 6, 5),
            (4, 7, 6),
            (0, 4, 5),
            (0, 5, 1),
            (1, 5, 6),
            (1, 6, 2),
            (2, 6, 7),
            (2, 7, 3),
            (3, 7, 4),
            (3, 4, 0),
        ],
        name="tapered_link_visual",
        assets=assets,
    )

    collision_geom = Box((0.24, 0.07, 0.04))
    base = robot.link("base", inertial=inertial_from_box(collision_geom, mass=0.4))
    base.visual(mesh_export.mesh)
    base.collision(collision_geom, origin=Origin(xyz=(0.12, 0.0, 0.0)))
    return robot


robot_model = build_robot_model()
