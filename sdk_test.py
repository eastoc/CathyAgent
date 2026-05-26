"""robot_sdk 教学文档测试。

运行方式：

    python sdk_test.py

这个文件不是正式单元测试，而是一个可运行的 SDK 教学脚本，演示：

1. 如何手写最小 RobotModel；
2. 如何用 DH/SerialManipulatorSpec 编译机械臂；
3. 如何用 SemanticGraph 从 base/root 遍历 link tree；
4. 如何生成 mesh-backed OBJ visual；
5. 如何校验并导出 MJCF / URDF。

约定：

- RobotModel 表达 link / joint / actuator / sensor 等语义结构。
- Visual 可以复杂并使用 Mesh；Collision / Inertial 默认保持简化 primitive。
- 不手写 MJCF / URDF，始终从 RobotModel 导出。
"""

from __future__ import annotations

from pathlib import Path

from robot_sdk import (
    AssetSession,
    Box,
    Cylinder,
    JointLimit,
    Origin,
    RobotModel,
    build_semantic_graph,
    check_robot_model,
    compile_serial_manipulator,
    demo_three_dof_spec,
    export_mjcf,
    export_urdf,
    inertial_from_box,
    inertial_from_cylinder,
    mesh_from_vertices,
)


BUILD_DIR = Path("build/sdk_test")


def print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def assert_valid(robot: RobotModel) -> None:
    """校验 RobotModel；有 error 时直接抛异常。"""

    report = check_robot_model(robot)
    print(f"check ok: {report.ok}")
    if report.issues:
        for issue in report.issues:
            print(f"- {issue.severity}: {issue.code}: {issue.message}")
    report.assert_ok()


def build_manual_two_link_robot() -> RobotModel:
    """示例 1：手写一个两连杆机械臂。

    适合学习 RobotModel 的基本组成：

    - link 是刚体；
    - joint 连接 parent link 和 child link；
    - visual 用于显示；
    - collision 用于碰撞；
    - inertial 用于质量/惯性；
    - actuator/sensor 是控制和观测通道，不会自动生成可见电机。
    """

    robot = RobotModel("manual_two_link")
    robot.material("arm_gray", rgba=(0.65, 0.68, 0.72, 1.0))

    base_geom = Cylinder(radius=0.05, length=0.04)
    base = robot.link("base", inertial=inertial_from_cylinder(base_geom, mass=1.0))
    base.visual(base_geom, material="arm_gray")
    base.collision(base_geom)

    link_geom = Box((0.3, 0.04, 0.04))
    link1 = robot.link("link1", inertial=inertial_from_box(link_geom, mass=0.4))
    link1.visual(link_geom, origin=Origin(xyz=(0.15, 0.0, 0.0)), material="arm_gray")
    link1.collision(link_geom, origin=Origin(xyz=(0.15, 0.0, 0.0)))

    shoulder = robot.joint(
        "shoulder",
        "revolute",
        parent=base,
        child=link1,
        origin=Origin(xyz=(0.0, 0.0, 0.04)),
        axis=(0.0, 0.0, 1.0),
        limit=JointLimit(lower=-1.57, upper=1.57, effort=10.0, velocity=2.0),
    )
    robot.actuator("shoulder_motor", shoulder, ctrl_range=(-1.0, 1.0), force_range=(-10.0, 10.0))
    robot.sensor("shoulder_pos", "jointpos", shoulder.name)
    return robot


def build_dh_three_dof_robot() -> RobotModel:
    """示例 2：用内置 DH spec 编译一个 3DoF 机械臂。"""

    spec = demo_three_dof_spec()
    robot = compile_serial_manipulator(spec, name="dh_three_dof_demo")

    # SemanticGraph 是 RobotModel 的只读派生视图，便于后续从 base/root 生成几何。
    graph = build_semantic_graph(robot, spec=spec)
    print(f"semantic root: {graph.root}")
    print("semantic walk:", " -> ".join(graph.order))
    print("path to tool0:", " -> ".join(graph.path_to(spec.tool_frame)))

    first_child_name = graph.children(graph.root)[0]
    first_child = graph.get(first_child_name)
    print(f"first child link: {first_child.name}")
    print(f"incoming joint: {first_child.incoming_joint}")
    return robot


def build_mesh_visual_robot() -> RobotModel:
    """示例 3：用 mesh_from_vertices 生成 OBJ visual。

    primitive visual 会直接写入 MJCF/URDF，不会产生 OBJ。
    如果需要 OBJ，需要使用 AssetSession + mesh_from_vertices 或 mesh_from_cadquery。
    """

    assets = AssetSession(BUILD_DIR)
    mesh_export = mesh_from_vertices(
        vertices=[
            (0.0, -0.03, -0.02),
            (0.24, -0.03, -0.02),
            (0.24, 0.03, -0.02),
            (0.0, 0.03, -0.02),
            (0.02, -0.04, 0.02),
            (0.22, -0.04, 0.02),
            (0.22, 0.04, 0.02),
            (0.02, 0.04, 0.02),
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

    robot = RobotModel("mesh_visual_demo")
    collision_geom = Box((0.24, 0.08, 0.04))
    base = robot.link("base", inertial=inertial_from_box(collision_geom, mass=0.3))
    base.visual(mesh_export.mesh)
    base.collision(collision_geom, origin=Origin(xyz=(0.12, 0.0, 0.0)))

    print(f"obj path: {mesh_export.mesh.materialized_path}")
    print(f"mesh filename in MJCF/URDF: {mesh_export.mesh.filename}")
    print(f"local aabb: {mesh_export.local_aabb}")
    return robot


def export_robot(robot: RobotModel, stem: str) -> None:
    """校验并导出 MJCF / URDF 文件。"""

    assert_valid(robot)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    mjcf_path = BUILD_DIR / f"{stem}.xml"
    urdf_path = BUILD_DIR / f"{stem}.urdf"
    mjcf = export_mjcf(robot, mjcf_path)
    urdf = export_urdf(robot, urdf_path)
    print(f"mjcf: {mjcf_path} ({len(mjcf)} chars)")
    print(f"urdf: {urdf_path} ({len(urdf)} chars)")


def main() -> None:
    print_section("1. 手写 RobotModel")
    manual_robot = build_manual_two_link_robot()
    export_robot(manual_robot, "manual_two_link")

    print_section("2. DH spec -> RobotModel -> SemanticGraph")
    dh_robot = build_dh_three_dof_robot()
    export_robot(dh_robot, "dh_three_dof_demo")

    print_section("3. 生成 mesh-backed OBJ visual")
    mesh_robot = build_mesh_visual_robot()
    export_robot(mesh_robot, "mesh_visual_demo")

    print_section("完成")
    print(f"生成文件目录: {BUILD_DIR.resolve()}")


if __name__ == "__main__":
    main()
