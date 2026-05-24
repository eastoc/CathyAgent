# 机器人建模三层架构方案与 TODO

## 结论

当前 `robot_sdk` 已经具备类 URDF 的语义结构层和基础几何层。下一步不建议重写现有系统，而是补上一层数学/运动学模型层，再建立从数学模型到 `RobotModel` 的编译链路，最后用程序化几何零件库生成更真实的外观。

目标架构：

```text
用户需求 / Prompt
  -> 数学模型层 KinematicModel
  -> 语义结构层 RobotModel
  -> 几何外观层 Geometry / Assets
  -> 校验 / 导出 / 渲染 / 仿真
```

这会让 agent 从“直接堆圆柱和长方体”，升级为“先确定机器人如何运动，再确定它由哪些 link/joint 组成，最后生成具体 3D 外观”。

## 当前代码现状

项目目前已有较好的第二层和第三层基础：

- `robot_sdk/model.py`：已有 `RobotModel`、`Link`、`Joint`、`Origin`、`Visual`、`Collision`、`Inertial`、`Actuator`、`Sensor`。
- `robot_sdk/checks.py`：已有 `RobotModel` 结构校验。
- `robot_sdk/urdf_export.py`：已有 URDF 导出。
- `robot_sdk/mjcf_export.py`：已有 MJCF 导出。
- `robot_sdk/assets.py`：已有 OBJ mesh asset 管理能力，包括 `AssetSession`、`mesh_from_vertices`、`mesh_from_cadquery`。
- `plugins/builtin/robot_sdk/main.py`：已有 robot_sdk 插件，支持创建模板、编译模型、探测模型。
- `plugins/builtin/robot_sdk/templates/three_dof_arm.py`：当前模板仍是直接手写 link、joint、box、cylinder。

当前链路更接近：

```text
Prompt -> RobotModel + Geometry
```

建议升级为：

```text
Prompt -> KinematicModel -> RobotModel -> Geometry
```

## 三层架构设计

### 1. 数学模型层：KinematicModel

这一层表达机器人运动学，不直接关心外观。

它回答：

- 机器人有几个自由度？
- 每个关节是什么类型？
- 每个关节轴在哪里？
- 连杆长度、偏距、扭角是多少？
- 末端位姿如何由关节变量推出？
- 四足机器人每条腿的挂载点、关节顺序、镜像关系是什么？

建议新增目录：

```text
robot_sdk/kinematics/
├─ __init__.py
├─ model.py
├─ dh.py
├─ screw.py
├─ builders.py
└─ checks.py
```

建议核心数据结构：

```python
@dataclass
class DHJoint:
    name: str
    alpha: float
    a: float
    d: float
    theta: str | float
    joint_type: str = "revolute"
    limit: tuple[float, float] | None = None
    role: str | None = None


@dataclass
class SerialManipulatorSpec:
    name: str
    dof: int
    representation: str  # "dh", "modified_dh", "poe"
    joints: list[DHJoint]
    base_frame: str = "base"
    tool_frame: str = "tool0"
    metadata: dict = field(default_factory=dict)
```

四足机器人建议使用独立抽象：

```python
@dataclass
class LegChainSpec:
    name: str
    mount_xyz: tuple[float, float, float]
    joints: list[DHJoint]
    segment_lengths: tuple[float, ...]
    mirror_of: str | None = None


@dataclass
class QuadrupedSpec:
    name: str
    body_size: tuple[float, float, float]
    legs: list[LegChainSpec]
```

### 2. 语义结构层：RobotModel

这一层继续使用现有 `RobotModel`，不要推翻。

它回答：

- 有哪些 link？
- 有哪些 joint？
- 每个 joint 的 parent/child 是谁？
- joint origin、axis、limit 是什么？
- 每个 link 的 visual、collision、inertial 插槽是什么？
- actuator 和 sensor 绑定到哪里？

建议新增目录：

```text
robot_sdk/semantic/
├─ __init__.py
├─ compile.py
└─ naming.py
```

核心函数：

```python
def compile_serial_manipulator(spec: SerialManipulatorSpec) -> RobotModel:
    ...
```

职责：

- 将数学模型中的关节转为 `RobotModel.joint(...)`。
- 根据 DH / modified DH / POE 参数推导 `Origin` 和 `axis`。
- 自动创建 link chain。
- 自动添加 actuator 和 joint position sensor。
- 在 `meta` 中保留数学来源，便于后续校验和调试。

示例：

```python
robot.joint(
    "shoulder_lift_joint",
    "revolute",
    parent=prev_link,
    child=next_link,
    origin=Origin(...),
    axis=(0.0, 1.0, 0.0),
    limit=JointLimit(...),
    meta={
        "source": "modified_dh",
        "dh_index": 2,
        "role": "shoulder_pitch",
    },
)
```

### 3. 几何外观层：Geometry / Assets

这一层负责把语义结构变成具体外观。

它回答：

- 底座长什么样？
- 肩关节壳体多大？
- 上臂是圆角胶囊外壳还是方形梁？
- 法兰孔怎么排？
- 线缆、端盖、螺栓、传感器如何生成？
- visual mesh 和 collision primitive 如何分离？

建议新增目录：

```text
robot_sdk/geometry/
├─ __init__.py
├─ parts.py
├─ arm_visuals.py
├─ quadruped_visuals.py
└─ primitives.py
```

建议 API：

```python
def add_ur_style_arm_visuals(
    robot: RobotModel,
    spec: SerialManipulatorSpec,
    assets: AssetSession,
) -> None:
    ...
```

几何映射示例：

```text
base_link        -> 分层圆柱底座 + 法兰
shoulder_link    -> 大圆柱电机壳 + 侧盖
upper_arm_link   -> 圆角胶囊外壳
forearm_link     -> 细长圆角外壳
wrist_*          -> 紧凑串联圆柱
tool0            -> 末端法兰 + 孔阵列
```

## 推荐最终调用方式

未来模板应从“直接手写几何”改为“三层流水线”：

```python
from robot_sdk import AssetSession
from robot_sdk.kinematics.builders import ur3e_like_spec
from robot_sdk.semantic.compile import compile_serial_manipulator
from robot_sdk.geometry.arm_visuals import add_ur_style_arm_visuals


def build_robot_model():
    spec = ur3e_like_spec(scale=1.0)
    robot = compile_serial_manipulator(spec)

    assets = AssetSession("build/robot")
    add_ur_style_arm_visuals(robot, spec, assets)

    return robot
```

这样可以得到清晰边界：

```text
ur3e_like_spec()
  -> compile_serial_manipulator()
  -> add_ur_style_arm_visuals()
  -> compile_robot_model
```

## TODO List

### Phase 1：补数学模型层

- [x] 新增 `robot_sdk/kinematics/` 包。
- [x] 新增 `DHJoint`。
- [x] 新增 `SerialManipulatorSpec`。
- [x] 新增 `LegChainSpec`。
- [x] 新增 `QuadrupedSpec`。
- [x] 实现标准 DH transform。
- [x] 实现 modified DH transform。
- [x] 实现简单 FK 计算。
- [x] 新增 `check_kinematic_spec(...)`。
- [x] 为 2DoF / 3DoF / 6DoF serial manipulator 写单测。

### Phase 2：数学模型编译到 RobotModel

- [x] 新增 `robot_sdk/semantic/` 包。
- [x] 实现 `compile_serial_manipulator(...)`。
- [x] 自动创建 link chain。
- [x] 自动创建 revolute / prismatic joint。
- [x] 自动写入 `JointLimit`。
- [x] 自动添加 actuator。
- [x] 自动添加 jointpos sensor。
- [x] 在 link / joint `meta` 里保留数学来源。
- [x] 确保输出模型通过现有 `check_robot_model(...)`。

### Phase 3：迁移现有模板

- [x] 保留旧 primitive 版 3DoF 模板作为备份：`plugins/builtin/robot_sdk/templates/_backup/three_dof_arm_primitive.py`。
- [x] 将正式 `three_dof_arm.py` 保持为 kinematics-first 模板。
- [x] 使用 `SerialManipulatorSpec` 生成 3DoF 机械臂。
- [x] 对比新旧模板的 link / joint 拓扑。
- [x] 确保 URDF / MJCF 导出不破坏。
- [x] 更新 `skills/robot_sdk/SKILL.md`，要求新建机械臂优先走数学模型层。

备注：当前实现选择让内置 `three_dof_arm.py` 成为正式 kinematics-first 模板；旧 primitive 手写版本仅作为备份/reference 保留在 `_backup` 目录，不注册到 `create_robot_template` 的可选模板里，避免工作链路回退到 primitive-first。

### Phase 4：新增 UR3e-like 6DOF 模板

- [x] 在 `robot_sdk/kinematics/builders.py` 中新增 `ur3e_like_spec(...)`。
- [x] 生成 6 个 revolute joints。
- [x] 定义 `base_link`、`shoulder_link`、`upper_arm_link`、`forearm_link`、`wrist_1_link`、`wrist_2_link`、`wrist_3_link`、`tool0`。
- [x] 设置合理 joint axis。
- [x] 设置合理 joint limits。
- [x] 设置近似 link length / offset。
- [x] 添加 UR3e-like 专用校验：必须有 6 个 revolute joints。
- [x] 添加 UR3e-like 专用校验：必须有 wrist 三轴。
- [x] 添加 UR3e-like 专用校验：必须有 tool flange。

备注：UR3e-like 的 link 命名由 semantic compiler 根据 joint 名派生（例如 `wrist_1` -> `wrist_1_link`），所以 builder 通过稳定的 joint 名和 `tool_frame="tool0"` 达成上述 link 约定。

### Phase 5：建设几何零件库

- [ ] 新增 `robot_sdk/geometry/parts.py`。
- [ ] 实现 `make_flange(...)`。
- [ ] 实现 `make_motor_housing(...)`。
- [ ] 实现 `make_rounded_arm_shell(...)`。
- [ ] 实现 `make_wrist_stack(...)`。
- [ ] 实现 `make_tool_flange(...)`。
- [ ] 实现 `make_bolt_circle(...)`。
- [ ] 实现 `make_cable_route(...)`。
- [ ] visual 使用 mesh-backed OBJ。
- [ ] collision 保持简化 primitive。
- [ ] inertial 使用简化几何估算。

### Phase 6：UR-style 外观生成器

- [ ] 新增 `robot_sdk/geometry/arm_visuals.py`。
- [ ] 实现 `add_ur_style_arm_visuals(...)`。
- [ ] 为 base 添加分层圆柱底座。
- [ ] 为 shoulder 添加大电机壳和侧盖。
- [ ] 为 upper arm 添加圆角外壳。
- [ ] 为 forearm 添加圆角外壳。
- [ ] 为 wrist 三轴添加紧凑串联圆柱。
- [ ] 为 tool0 添加末端法兰和孔阵列。
- [ ] 确保 `compile_robot_model` 返回非空 `obj_paths`。

### Phase 7：扩展校验

- [ ] 新增 `robot_sdk/kinematics/checks.py`。
- [ ] 新增 `robot_sdk/geometry/checks.py`。
- [ ] 检查数学模型 DOF 与 joint 数一致。
- [ ] 检查 joint limit 合理性。
- [ ] 检查 serial manipulator link chain 连续。
- [ ] 检查四足机器人四条腿镜像关系。
- [ ] 检查 visual 不能全部是 primitive。
- [ ] 检查主要 link 是否缺少 visual mesh。
- [ ] 检查 wrist 不能全部塌成一个 link。

### Phase 8：扩展 robot_sdk 插件

- [ ] 更新 `plugins/builtin/robot_sdk/main.py`。
- [ ] 新增模板名 `ur3e_like`。
- [ ] 新增模板名 `quadruped_basic`。
- [ ] 新增 `probe_kinematic_model` 工具，返回数学层摘要。
- [ ] 新增 `compile_kinematic_model` 工具，执行数学层到 RobotModel 的编译。
- [ ] 保持 `compile_robot_model` 兼容现有 `build_robot_model() -> RobotModel`。

### Phase 9：机械狗建模

- [ ] 新增 `quadruped_basic_spec(...)`。
- [ ] 建立 torso body frame。
- [ ] 建立四条 leg chain。
- [ ] 每条腿至少包含 hip_roll、hip_pitch、knee_pitch。
- [ ] 支持左右/前后镜像。
- [ ] 新增 `add_quadruped_visuals(...)`。
- [ ] 添加脚掌、髋部电机包、膝盖壳体、躯干外壳。
- [ ] 添加机械狗专用校验：必须有四条腿。
- [ ] 添加机械狗专用校验：每条腿至少 3 个 joints。

## 验收标准

### UR3e-like 机械臂验收

- [ ] 能通过 `compile_robot_model`。
- [ ] 能导出 MJCF。
- [ ] 能导出 URDF。
- [ ] 能导出 OBJ visual assets。
- [ ] 有 6 个 revolute joints。
- [ ] link tree 只有一个 root。
- [ ] wrist 三轴存在且没有合并成单个 link。
- [ ] tool flange 存在。
- [ ] 主要臂段不是简单长方体。
- [ ] 关节附近有可见 motor housing / gearbox / bearing cap。

### 机械狗验收

- [ ] 能通过 `compile_robot_model`。
- [ ] 有 torso。
- [ ] 有四条腿。
- [ ] 每条腿至少 3 个 joints。
- [ ] 左右腿具备镜像关系。
- [ ] 脚掌接近地面。
- [ ] visual mesh 不为空。
- [ ] collision 使用简化 primitive。

## 参考链接

- Articraft 项目：https://github.com/mattzh72/articraft
- Articraft 论文：https://arxiv.org/abs/2605.15187
- ROS URDF 文档：https://docs.ros.org/en/rolling/Tutorials/Intermediate/URDF/URDF-Main.html
- 当前 Robot SDK 模型层：../robot_sdk/model.py
- 当前 Robot SDK 校验层：../robot_sdk/checks.py
- 当前 URDF 导出：../robot_sdk/urdf_export.py
- 当前 MJCF 导出：../robot_sdk/mjcf_export.py
- 当前 mesh asset 管理：../robot_sdk/assets.py
- 当前 robot_sdk 插件入口：../plugins/builtin/robot_sdk/main.py

## 总结

三层架构的关键不是增加复杂度，而是把复杂度放到正确的位置：

```text
数学模型层：保证它动得对
语义结构层：保证它连得对
几何外观层：保证它长得对
```

当前项目已经有语义结构、导出、校验和 asset 基础。最优路线是补 `kinematics` 数学层，新增 `semantic compiler`，再用 `geometry` 零件库提升外观质量。这样后续做 UR3e-like 机械臂、机械狗、夹爪或其他 articulated robot，都会更稳定、更可校验、更容易扩展。
