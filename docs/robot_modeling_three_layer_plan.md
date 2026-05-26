# 机器人建模三层架构方案与 TODO

## 结论

当前 `robot_sdk` 已经具备类 URDF 的 `RobotModel`、导出、校验和 mesh asset 基础。下一步不建议围绕具体机械臂型号继续堆模板，而是把建模流程重构为：先得到数学/运动学模型，再把运动链编译为以 `base` 为根节点的语义图谱，最后从根节点出发沿装配树生成几何外观。

目标架构：

```text
用户需求 / Prompt
  -> 数学模型层 KinematicModel
  -> 语义图谱层 SemanticGraph（base-rooted）
  -> 根驱动几何层 Geometry / Assets
  -> 校验 / 导出 / 渲染 / 仿真
```

这会让 agent 从“直接堆圆柱和长方体”，升级为“先确定机器人如何运动，再理解机器人装配树和每个节点的语义角色，最后从 `base` 开始逐级生成底座、关节壳、连杆壳、腕部和末端法兰”。

## 当前代码现状

项目目前已有较好的 `RobotModel` 语义结构和 asset 基础：

- `robot_sdk/model.py`：已有 `RobotModel`、`Link`、`Joint`、`Origin`、`Visual`、`Collision`、`Inertial`、`Actuator`、`Sensor`。
- `robot_sdk/checks.py`：已有 `RobotModel` 结构校验。
- `robot_sdk/urdf_export.py`：已有 URDF 导出。
- `robot_sdk/mjcf_export.py`：已有 MJCF 导出。
- `robot_sdk/assets.py`：已有 OBJ mesh asset 管理能力，包括 `AssetSession`、`mesh_from_vertices`、`mesh_from_cadquery`。
- `plugins/builtin/robot_sdk/main.py`：已有 robot_sdk 插件，支持创建模板、编译模型、探测模型。
- `plugins/builtin/robot_sdk/templates/three_dof_arm.py`：已迁移为 kinematics-first 模板。

当前链路更接近：

```text
Prompt -> RobotModel + Geometry
```

建议升级为：

```text
Prompt -> KinematicModel -> SemanticGraph(base-rooted) -> Geometry
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

### 2. 语义图谱层：SemanticGraph（base 为根）

这一层不推翻现有 `RobotModel`，而是在 `RobotModel` 之上或旁边构建一个以 `base` 为 root 的机器人语义图谱。`RobotModel` 继续作为 URDF / MJCF 导出的事实模型；`SemanticGraph` 是 SDK 的稳定事实层，负责让 agent 理解“机器人是一棵从底座生长到末端的装配树”。

边界原则：

- `SemanticGraph`、`build_semantic_graph(...)`、图谱校验和基础节点分类必须由 SDK 提供，不能让 agent 每次临时推断。
- Agent / skill 只负责调用图谱 API、选择 style / catalog / 局部参数 override，并根据校验结果迭代。
- 几何层和标准件库只能消费 `SemanticGraph`，不应靠 link name 写死某个机械臂型号。

它回答：

- 根节点是谁，通常是 `base` / `base_link`？
- 每个节点是 link、joint、frame 还是 assembly region？
- 每个节点的 role 是 base、shoulder、upper_arm、elbow、forearm、wrist 还是 tool？
- 每个 joint 的 parent/child、origin、axis、limit 是什么？
- 从当前节点到子节点的 span、距离、方向和相邻轴夹角是什么？
- 哪些 link/joint 组合应被理解为同一个机械装配区域，例如 T 形关节、腕部三轴堆叠或末端法兰？

建议新增目录：

```text
robot_sdk/semantic/
├─ __init__.py
├─ compile.py
├─ graph.py
└─ naming.py
```

核心数据结构和函数：

```python
def compile_serial_manipulator(spec: SerialManipulatorSpec) -> RobotModel:
    ...


@dataclass
class SemanticNode:
    name: str
    kind: Literal["link"]
    role: str | None
    parent: str | None
    children: list[str]
    frame: Origin  # root 到当前 link frame 的全局位姿
    axis: tuple[float, float, float] | None = None
    link_name: str | None = None
    joint_name: str | None = None
    incoming_joint: str | None = None
    dh_index: int | None = None
    span_to_children: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    axis_angle_to_children: dict[str, float] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    visual_intent: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticEdge:
    parent: str
    child: str
    kind: Literal["joint", "fixed"]
    joint_name: str | None
    origin: Origin
    axis: tuple[float, float, float] | None
    span: tuple[float, float, float]
    span_length: float
    metadata: dict = field(default_factory=dict)


def build_semantic_graph(robot: RobotModel, *, spec: SerialManipulatorSpec | None = None) -> SemanticGraph:
    ...
```

图谱以 link node 为主，joint 信息同时存在于 `SemanticEdge` 和 child node 的 `incoming_joint` 字符串上：edge 表示 parent -> child 的连接事实，`incoming_joint` 让从某个节点出发时能快速知道它由哪个 joint 挂到父节点。

职责：

- 将数学模型中的关节转为 `RobotModel.joint(...)`。
- 根据 DH / modified DH / POE 参数推导 `Origin` 和 `axis`。
- 自动创建 link chain。
- 自动添加 actuator 和 joint position sensor。
- 在 `meta` 中保留数学来源，便于后续校验和调试。
- 从 `RobotModel` 和 `SerialManipulatorSpec` 派生 base-rooted graph。
- 为几何层提供稳定的节点上下文，而不是让几何层直接猜 link 名或按 DH 行硬编码。

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

### 3. 根驱动几何层：Geometry / Assets

这一层负责从 `SemanticGraph.root` 开始遍历机器人装配树，根据节点角色、父子关系、span 方向和相邻 joint axis 生成具体外观。几何层不应该变成“某型号机械臂模板集合”，而应该是一套可复用的节点形态推断和 CadQuery 原子零件组合规则。

核心原则：

1. **从根节点生成**：几何从 `base` 出发 DFS/BFS 生长，而不是孤立地按 DH row 循环。
2. **按局部图谱判断形态**：根据当前节点、父节点、子节点、span 和 axis 夹角判断 base、T-joint、inline joint、elbow、wrist stack、tool flange。
3. **CadQuery 只做外观**：关节仍由 `RobotModel.joint(...)` 表达，CadQuery 生成 mesh-backed visual。
4. **visual / collision / inertial 分离**：visual 可以复杂，collision 和 inertial 继续使用简化 primitive。

它回答：

- 从 base 到 tool 的装配树如何逐级生成外观？
- 当前节点应生成 base_turntable、T-joint housing、inline housing、rounded link shell、wrist stack 还是 tool flange？
- 哪些零件由 SDK 按尺寸参数化生成，哪些来自 GB/ISO 标准件库或 custom 导入？
- visual mesh 和 collision primitive 如何分离？

建议新增目录：

```text
robot_sdk/geometry/
├─ __init__.py
├─ style.py              # 外观风格参数和 preset，不绑定具体机械臂型号
├─ context.py            # 从 SemanticGraph 提取几何上下文
├─ classify.py           # 判断 base / T-joint / inline / wrist / tool 等形态
├─ parts.py              # CadQuery 参数化原子零件
├─ synthesize.py         # 从 root 遍历并挂载 mesh visual
├─ library.py            # 可选：GB/ISO 标准件库 + custom 导入
├─ quadruped_visuals.py  # 可选：四足图谱驱动外观
└─ catalogs/
   └─ standard/          # GB/ISO 标准件 catalog
```

建议 API：

```python
# 从语义图谱出发生成外观（默认推荐）
graph = build_semantic_graph(robot, spec=spec)
add_geometry_from_root(robot, graph, assets=assets, style=IndustrialArmStyle())

# 底层仍保留 CadQuery 原子零件，供合成器或用户直接调用
make_t_joint_housing(...)
make_rounded_link_shell(...)
make_tool_flange(...)

# 可选：GB/ISO 标准件
load_standard_part("ISO4762", spec="M6x16", assets=assets)

# 可选：非标文件导入
load_custom_part("shoulder_housing_v1", path="assets/custom/housing.stl", assets=assets)
```

`IndustrialArmStyle`、`URLikeStyle`、`MinimalStyle` 只作为风格 preset，控制半径比例、圆角、端盖颜色、螺钉数量等外观倾向；它们不应该包含某个型号机械臂的固定 link 名和固定尺寸。

节点形态映射示例：

```text
base/root 节点                         -> make_base_turntable(...)
joint axis 与 child span 近似垂直       -> make_t_joint_housing(...)
joint axis 与 child span 近似平行       -> make_inline_joint_housing(...)
长 span link 节点                       -> make_rounded_link_shell(...)
连续短 span + 多个 wrist role joint     -> make_wrist_stack(...)
叶子 tool 节点                          -> make_tool_flange(...)
```

## 推荐最终调用方式

未来模板应从“直接手写几何”改为“数学模型 + 语义图谱 + 根驱动几何”的流水线：

```python
from robot_sdk import AssetSession
from robot_sdk.geometry.style import IndustrialArmStyle
from robot_sdk.geometry.synthesize import add_geometry_from_root
from robot_sdk.kinematics.builders import ur3e_like_spec
from robot_sdk.semantic.compile import compile_serial_manipulator
from robot_sdk.semantic.graph import build_semantic_graph


def build_robot_model():
    spec = ur3e_like_spec(scale=1.0)
    robot = compile_serial_manipulator(spec)

    graph = build_semantic_graph(robot, spec=spec)
    assets = AssetSession("build/robot")
    add_geometry_from_root(robot, graph, assets=assets, style=IndustrialArmStyle())

    return robot
```

这样可以得到清晰边界：

```text
ur3e_like_spec()
  -> compile_serial_manipulator()     # 运动学 + RobotModel 事实模型 + placeholder 几何
  -> build_semantic_graph()           # base-rooted 语义图谱
  -> add_geometry_from_root()         # 从 base 遍历并合成 CadQuery visual
  -> compile_robot_model             # 校验 + 导出 MJCF/URDF/OBJ
```

其中 `IndustrialArmStyle` 是风格 preset，不是 UR3e 专用模板。换成其他 6DoF 或 3DoF 机械臂时，主逻辑仍是重新构建语义图谱并从 root 生成几何。

## CathyAgent Skill 控制流程

上述流水线是 CathyAgent 的默认建模工作流，不应该硬编码成某个插件命令或某个机械臂模板的固定逻辑。代码层负责提供稳定 API 和校验器；`skills/robot_sdk/SKILL.md` 负责告诉 agent 何时、如何组合这些 API。

建议边界：

```text
代码层：
  - DHJoint / SerialManipulatorSpec
  - compile_serial_manipulator(...)
  - build_semantic_graph(...)
  - classify_geometry_kind(...)
  - make_* CadQuery 原子零件
  - add_geometry_from_root(...)
  - checks / exporters

Skill 层：
  - 用户给 DH 参数时先建数学模型
  - 编译 RobotModel 后构建 base-rooted SemanticGraph
  - 从 root 生成几何，而不是找某个型号模板
  - visual 使用 mesh-backed OBJ
  - collision / inertial 保持简化 primitive
  - 编译失败时按 report 和 checks 修复
```

在 Phase 5 的 API 尚未全部实现前，skill 仍可约束 agent 采用同一思路手动补外观：先分析 base/root、joint axis、child span 和节点 role，再用 `mesh_from_cadquery(...)` 把 CadQuery visual 挂到正确 link。等 `SemanticGraph` 和 `add_geometry_from_root(...)` 实现后，skill 必须优先调用正式 SDK API，而不是继续让 agent 自行构图。

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
- [x] 更新 `skills/robot_sdk/SKILL.md`，明确 CathyAgent 默认流程由 skill 控制，不在插件或模板中 hardcode 某个机械臂 workflow。

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

### Phase 5：SDK 语义图谱驱动的几何生成

Phase 5 从“建设零件库 + 某型号装配 helper”调整为“先在 SDK 中构建 base-rooted 语义图谱，再从根节点遍历生成几何”。CadQuery 原子零件仍然重要，但它们只是合成器使用的形状词汇；标准件库后置为可选增强。

责任边界：

```text
SDK 负责：
  - SemanticGraph 数据结构
  - build_semantic_graph(...)
  - 图谱校验
  - GeometryContext / classify_geometry_kind(...)
  - CadQuery 原子零件
  - add_geometry_from_root(...)

Agent / skill 负责：
  - 选择 spec / style / standard part catalog
  - 调用 SDK API
  - 根据 graph / checks / render 结果迭代参数
  - 只做局部 override，不临时重建图谱规则
```

#### Phase 5A：构建 base-rooted SemanticGraph

- [x] 新增 `robot_sdk/semantic/graph.py`。
- [x] 实现 `SemanticNode` 和 `SemanticGraph`。
- [x] 实现 `build_semantic_graph(robot, *, spec=None)`。
- [x] 将 `RobotModel` 的 link / joint tree 转为以 base/root 为根的图谱。
- [x] 为节点写入 `kind`、`role`、`frame`、`axis`、`link_name`、`joint_name`。
- [x] 计算 parent / children、span vector、span length、相邻 joint axis 夹角。
- [x] 将 `DHJoint.role`、`metadata` 和 compiler 写入的 `meta` 合并为几何可用的语义提示。
- [x] 确保图谱能处理 serial manipulator 的 base -> joints -> tool 链路。
- [x] 验收：graph 只有一个 root。
- [x] 验收：root 是 base / base_link 或显式指定的 root link。
- [x] 验收：每个非 root node 有唯一 parent。
- [x] 验收：每条 edge 绑定一个 incoming joint 或 fixed frame relation。
- [x] 验收：serial manipulator 的 path 顺序与 `RobotModel.joints` 的 parent / child 链一致。
- [x] 验收：node 能读取 role、dh_index、span、joint_axis 和 metadata。
- [x] 验收：图谱构建失败时返回可读错误，不让几何层继续猜。

#### Phase 5B：节点几何上下文与形态分类

- [ ] 新增 `robot_sdk/geometry/context.py`。
- [ ] 新增 `robot_sdk/geometry/classify.py`。
- [ ] 定义 `GeometryContext`，描述当前节点、父节点、子节点、span、axis 和 role。
- [ ] 定义第一版 `GeometryKind`：`base_turntable`、`inline_link`、`offset_link`、`generic_joint`、`elbow_joint`、`wrist_stack`、`tool_flange`。
- [ ] 实现 `classify_geometry_kind(ctx)`。
- [ ] 规则：base/root 节点生成 `base_turntable`。
- [ ] 规则：joint axis 与 child span 近似垂直时生成 `generic_joint` 或 `elbow_joint`。
- [ ] 规则：joint axis 与 child span 近似平行时生成 `inline_link`。
- [ ] 规则：存在明显 offset span 时生成 `offset_link`。
- [ ] 规则：连续短 span 且多个 wrist role joint 生成 `wrist_stack`。
- [ ] 规则：叶子 tool 节点生成 `tool_flange`。
- [ ] 保持规则保守；无法判断时生成简洁 placeholder mesh，不伪装成精确工业设计。

#### Phase 5C：CadQuery 参数化原子零件

- [ ] 新增 `robot_sdk/geometry/parts.py`。
- [ ] 基于 CadQuery / 现有 `mesh_from_cadquery` 封装参数化生成。
- [ ] 实现 `make_base_turntable(...)`。
- [ ] 实现 `make_t_joint_housing(...)`。
- [ ] 实现 `make_inline_joint_housing(...)`。
- [ ] 实现 `make_elbow_housing(...)`。
- [ ] 实现 `make_rounded_link_shell(...)`。
- [ ] 实现 `make_wrist_stack(...)`。
- [ ] 实现 `make_tool_flange(...)`。
- [ ] 实现 `make_bolt_circle(...)`。
- [ ] 实现 `make_separator_band(...)`。
- [ ] 实现 `make_end_cap(...)`。
- [ ] 实现 `make_cable_route(...)`。
- [ ] 复杂外观拆成多个 mesh-backed visual，便于分别设置灰色主体、蓝色端盖、黑色分缝环等材质。
- [ ] collision 保持简化 primitive。
- [ ] inertial 使用简化几何估算。

#### Phase 5D：从 root 遍历合成并挂载几何

- [ ] 新增 `robot_sdk/geometry/style.py`。
- [ ] 新增 `robot_sdk/geometry/synthesize.py`。
- [ ] 定义 `ArmVisualStyle` / `IndustrialArmStyle`，控制半径比例、圆角、端盖颜色、螺钉数量、最小壳体尺寸。
- [ ] 实现 `add_geometry_from_root(robot, graph, assets, style)`。
- [ ] 从 `SemanticGraph.root` 出发 DFS/BFS 遍历节点。
- [ ] 为每个节点构建 `GeometryContext` 并调用 `classify_geometry_kind(...)`。
- [ ] 根据分类调用 `parts.py` 中的 CadQuery builder。
- [ ] 使用 `mesh_from_cadquery(...)` 生成 OBJ 并挂到正确的 `RobotModel` link。
- [ ] 确保 visual 不跨越会相对运动的 joint；需要跨区域外观时拆到 parent/child link。
- [ ] 确保调用后 `compile_robot_model` 能返回非空 `obj_paths`。
- [ ] 在 `ur3e_like` 示例中演示“spec -> RobotModel -> SemanticGraph -> root geometry”的推荐顺序。

#### Phase 5E：图谱 / 几何校验

- [ ] 新增 `robot_sdk/geometry/checks.py`。
- [ ] 检查 `SemanticGraph` 只有一个 base/root。
- [ ] 检查 `SemanticGraph` 中每个非 root 节点都有 parent。
- [ ] 检查主要 joint 节点都有 axis、frame 和可用于几何生成的 span 信息。
- [ ] 检查 visual 不能全部是 primitive。
- [ ] 检查主要 link 是否缺少 visual mesh。
- [ ] 检查主要 revolute joint 附近是否生成 mesh-backed visual。
- [ ] 检查 mesh visual 不跨越会相对运动的 joint。
- [ ] 检查 wrist 不能全部塌成一个 link。

#### Phase 5F：GB/ISO 标准件库导入（可选增强）

- [ ] 新增 `robot_sdk/geometry/library.py`。
- [ ] 定义标准件目录结构，例如 `robot_sdk/geometry/catalogs/standard/` 与外部资产路径。
- [ ] 实现 `StandardPartSpec`，描述标准号（GB/T、ISO）、类别、规格和安装姿态。
- [ ] 实现 `load_standard_part(...)`，支持按标准号 + 规格加载或生成。
- [ ] 实现 `load_custom_part(...)`，导入用户/厂商提供的非标 OBJ/STL/STEP。
- [ ] 首批类目：轴承、螺纹紧固件、非螺纹紧固件、齿轮、链轮、键、销。
- [ ] 第二批类目：带轮、链条、联轴器、密封件、弹簧。
- [ ] 标准件统一返回 `MeshExport` 或可直接用于 `Visual` 的 `Mesh`。
- [ ] 保证 materialize 到当前 `AssetSession`，便于 MJCF / URDF 引用。

### Phase 7：扩展校验

- [ ] 新增 `robot_sdk/kinematics/checks.py`。
- [ ] 检查数学模型 DOF 与 joint 数一致。
- [ ] 检查 joint limit 合理性。
- [ ] 检查 serial manipulator link chain 连续。
- [ ] 检查四足机器人四条腿镜像关系。
- [ ] 检查 T-joint 的 branch 方向是否接近对应 child span。
- [ ] 整合 Phase 5 的 graph / geometry checks 到 `compile_robot_model` 或新增 probe 工具报告。

### Phase 8：扩展 robot_sdk 插件

- [ ] 更新 `plugins/builtin/robot_sdk/main.py`。
- [ ] 新增模板名 `ur3e_like`。
- [ ] 新增模板名 `quadruped_basic`。
- [ ] 新增 `probe_kinematic_model` 工具，返回数学层摘要。
- [ ] 新增 `probe_semantic_graph` 工具，返回 base-rooted 图谱摘要。
- [ ] 新增 `compile_kinematic_model` 工具，执行数学层到 RobotModel 的编译。
- [ ] 保持 `compile_robot_model` 兼容现有 `build_robot_model() -> RobotModel`。
- [x] 更新 `skills/robot_sdk/SKILL.md`，将“数学模型 -> 语义图谱 -> 根驱动几何”的 agent 默认流程写入 skill，而不是在插件里 hardcode 某个机械臂型号 workflow。

### Phase 9：机械狗建模

- [ ] 新增 `quadruped_basic_spec(...)`。
- [ ] 建立 torso body frame。
- [ ] 建立四条 leg chain。
- [ ] 每条腿至少包含 hip_roll、hip_pitch、knee_pitch。
- [ ] 支持左右/前后镜像。
- [ ] 基于 torso/root 构建四足 `SemanticGraph`。
- [ ] 新增图谱驱动的 `add_quadruped_visuals(...)`。
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
- [ ] `SemanticGraph` 以 base/root 为根，能从 root 遍历到 tool0。
- [ ] 几何由 `add_geometry_from_root(...)` 生成，而不是依赖 UR3e 专用 link-name 模板。
- [ ] wrist 三轴存在且没有合并成单个 link。
- [ ] tool flange 存在。
- [ ] 主要臂段不是简单长方体。
- [ ] 关节附近有可见 T-joint / inline housing / gearbox / bearing cap。

### 机械狗验收

- [ ] 能通过 `compile_robot_model`。
- [ ] 有 torso。
- [ ] 有四条腿。
- [ ] 每条腿至少 3 个 joints。
- [ ] 左右腿具备镜像关系。
- [ ] 四足 `SemanticGraph` 能从 torso/root 遍历到四条腿和脚掌。
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
语义图谱层：保证它从 base/root 连得对、角色分得清
根驱动几何层：保证它沿装配树长得对
```

当前项目已经有 `RobotModel`、导出、校验和 asset 基础，并且已经补上 `kinematics` 与 `semantic compiler` 的主要链路。下一步应把几何能力建立在 base-rooted `SemanticGraph` 上：先理解装配树，再从根节点遍历生成 CadQuery mesh visual。这样后续做 UR3e-like 机械臂、机械狗、夹爪或其他 articulated robot 时，agent 不需要为每个型号新增模板，而是复用“数学模型 -> 语义图谱 -> 根驱动几何”的通用流程。
