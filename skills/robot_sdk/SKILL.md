---
name: robot_sdk
description: Build and revise robot_model.py files with CathyAgent's Robot SDK, then compile to MJCF/URDF/OBJ.
triggers:
  - robot_sdk
  - RobotModel
  - mechanical arm
  - 机械臂
  - robot
version: 0.1.0
---

# Robot SDK 建模工作流

当用户要求创建或修改机器人、机械臂、关节链、MJCF/URDF/OBJ 模型时，使用这个工作流。

## 核心原则

- 直接编辑 `robot_model.py`，在 Python 中调用 `robot_sdk` API。不要手写最终 MJCF/URDF。
- 如果没有模型文件，先调用 `create_robot_template` 生成 scaffold。
- 修改后调用 `compile_robot_model`，以返回的 `summary`、`checks.summary` 和 `checks.issues` 作为修复依据。
- 如果已有模型较复杂，先调用 `probe_robot_model` 理解 link、joint、actuator、sensor 和 root link。
- 用户要求“建模机器人/机械臂”时，默认同时产出可查看的 OBJ visual assets；不要等用户额外说“导出 OBJ”。

## 文档索引

需要细节时读取这些项目文档，而不是把所有知识塞进 skill：

- `robot_sdk/docs/concepts.md`：核心 API、坐标系、`Origin`、`Link`、`Joint`、`Visual`、`Collision`、`Inertial`。
- `robot_sdk/docs/robot_arms.md`：机械臂拓扑、3DoF/6DoF、SCARA、夹爪、joint origin/axis 约定。
- `robot_sdk/docs/assets.md`：OBJ 导出、`AssetSession`、`mesh_from_vertices`、`mesh_from_cadquery`、visual mesh + simplified collision。
- `robot_sdk/docs/export.md`：MJCF/URDF 导出差异和字段映射。
- `robot_sdk/docs/troubleshooting.md`：常见校验错误、warning 和修复策略。

## 标准闭环

1. 读取当前 `robot_model.py`。
2. 小步修改 `build_robot_model() -> RobotModel`；新建机械臂时默认创建 `assets = AssetSession("build/robot")`。
3. 调用 `compile_robot_model`。
4. 如果 `ok=false`，根据 `summary.suggestions` 和具体 issue 修复。
5. 机械臂主要可见部件默认用 `mesh_from_vertices(...)` 或 `mesh_from_cadquery(...)` 创建 mesh-backed visual，并保留 primitive collision/inertial；primitive `Box`/`Cylinder`/`Sphere` 不会自动生成 OBJ。
6. 编译通过后返回 `mjcf_path`、`urdf_path`、`obj_paths`、`report_path`；OBJ 通常在 `build/robot/assets/meshes/*.obj`。

## 建模约定

- 每个刚体用 `robot.link(...)` 创建。
- 每个可动连接用 `robot.joint(...)` 创建，`parent` 指父 link，`child` 指子 link。
- `revolute` 和 `prismatic` 必须提供 `JointLimit`。
- 被驱动的 joint 通常要配一个 `robot.actuator(...)`。
- 被驱动的机械臂 joint 也应该有可见电机/减速器外观：在关节附近添加 `Cylinder`/`Box` visual（如 motor housing、gearbox、bearing cap）。`Actuator` 是控制通道，不会自动生成可见电机几何。
- 机械臂的主要外观 visual 默认应使用 mesh-backed visual 生成 OBJ；collision 和 inertial 继续使用简化 primitive。
- 需要观测关节状态时添加 `robot.sensor("name", "jointpos", "joint_name")` 或 `jointvel`。
- 每个 link 尽量都有 visual、collision、inertial；缺少时 compile 会 warning。

## 修复策略

- 优先修复 `summary.errors` 中的 blocking error。
- 使用 `summary.suggestions` 和 `summary.next_actions` 作为下一步。
- 结构不清楚时先 `probe_robot_model`，不要盲改 parent/child。
- 编译通过后返回 `mjcf_path`、`urdf_path`、`obj_paths`、`report_path`；如果 `obj_paths` 为空，说明当前模型没有 mesh-backed visual，需要补 `AssetSession` + mesh asset。
- 更具体的排错表见 `robot_sdk/docs/troubleshooting.md`。
