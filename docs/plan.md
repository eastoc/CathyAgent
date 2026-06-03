# Robot CAD Agent MVP Plan

## 结论

MVP 先做一个串联机械臂 CAD 概念建模闭环：从用户自然语言需求出发，生成需求拆解、构型/DH 参数、机械布局/装配语义模型、ID 表、CadQuery 参数化概念 CAD 和轻量验证报告。

目标不是一步做到真实工业级机器人设计，而是先跑通一条可解释、可测试、可迭代的最小链路。

重要原则：**DH / POE 是运动学坐标系模型，不等于 CAD 实体零件之间的装配变换**。CAD 建模必须经过机械布局语义层，把 joint axis、link body、interface frame、mount frame、mate feature、assembly constraint 等机械概念显式化后，再交给 CadQuery 生成实体和求解装配。

CAD 和装配底层统一使用 CadQuery：SDK 不自研通用 mate solver，而是生成 CadQuery geometry、tag/mate features、Assembly constraints，并调用 `Assembly.solve()`。

## MVP 输入与输出

### 示例输入

```text
设计一个桌面 4 自由度机械臂，用于搬运 500g 物体，工作半径 400mm，输出 CAD 初版。
```

### 期望输出

- 需求拆解表
- 初步构型方案
- DH 参数表
- 机械布局/装配语义模型
- 连杆/关节 ID 表
- CadQuery 参数化概念 CAD 文件
- 轻量验证报告

## 范围

### MVP 内

- 串联机械臂概念设计
- 基础工况分析
- 需求字段结构化
- 简化构型选择
- DH 参数生成
- FK / reach 基础校验
- 机械布局模型生成
- 装配语义 frame 定义
- CadQuery mate feature / constraint 生成
- 关节与连杆 ID 生成
- CadQuery Assembly.solve() 装配求解
- 简化 CadQuery 概念 CAD 建模和导出
- 基础验证报告

### MVP 外

- 复杂动力学
- 电机/减速器真实选型
- 碰撞检测全量仿真
- 有限元分析
- 工业级装配细节
- 完整控制器设计
- 真实制造图纸

## 分层设计

### Skills

Skills 放方法论、模板和检查清单，不直接执行工程计算。

建议新增：

```text
skills/
  robot_requirement_analysis/SKILL.md
  robot_cad_design_review/SKILL.md
```

职责：

- 工况分析模板
- 需求指标表格式
- 缺失信息处理策略
- CAD 设计检查清单
- 设计报告输出格式

### Subagents

Subagents 放需要推理、权衡和阶段性编排的设计角色。

建议新增：

```text
subagents/
  robot_design_agent/
    __init__.py
    agent.py
  requirement_agent/
    __init__.py
    agent.py
  kinematics_agent/
    __init__.py
    agent.py
  layout_agent/
    __init__.py
    agent.py
  cad_agent/
    __init__.py
    agent.py
  verification_agent/
    __init__.py
    agent.py
```

职责：

- `robot_design_agent`：MVP 总编排入口，主 agent 优先调用它完成机器人 CAD 任务。
- `requirement_agent`：工况分析、需求拆解、指标量化、默认值补全。
- `kinematics_agent`：构型/DH 或 POE 参数生成，调用 SDK 做 FK 或 reach 校验。
- `layout_agent`：把运动学模型映射为机械布局/装配语义模型，定义 joint axis、link body、interface frame、mount frame。
- `cad_agent`：消费机械布局模型，而不是直接消费 DH 表，调用 SDK 生成 CadQuery 参数化概念 CAD，并通过约束求解完成装配。
- `verification_agent`：做基础一致性检查并输出验证报告。

### SDK

SDK 放确定性、可测试、可复用的工程能力。

建议新增：

```text
robot_sdk/
  __init__.py
  types.py
  kinematics/
    __init__.py
    dh.py
  layout/
    __init__.py
    mechanical_layout.py
    frame_mapping.py
  assembly/
    __init__.py
    features.py
    constraints.py
    cq_adapter.py
  structure/
    __init__.py
    ids.py
  cad/
    __init__.py
    cq_parts.py
    cq_assembly.py
    export.py
  validation/
    __init__.py
    basic.py
```

职责：

- `types.py`：定义 `RobotSpec`、`JointSpec`、`LinkSpec`、`DHParam`、`MechanicalLayout`、`InterfaceSpec`、`PartFeature`、`AssemblyConstraint` 等结构。
- `kinematics/dh.py`：DH 参数、齐次变换、FK。
- `layout/mechanical_layout.py`：定义机械布局模型，包括 joint axis、link body、interface frame、mount frame、end-effector mount、mate features、assembly constraints 等。
- `layout/frame_mapping.py`：把 DH / POE 运动学 frame 映射为机械装配语义 frame；记录偏置、接口面和实体参考坐标。
- `assembly/features.py`：定义和生成 CadQuery 可 tag 的 mate features，例如 `base.top`、`J1.bottom`、`J1.axis`。
- `assembly/constraints.py`：定义中立装配约束，例如 `Plane`、`Axis`、`Point`、`Fixed`。
- `assembly/cq_adapter.py`：把中立 `AssemblyConstraint` 转换成 CadQuery `Assembly.constrain(...)` 调用。
- `structure/ids.py`：生成 `J1/J2/...`、`L1/L2/...`、`end_effector` 等 ID。
- `cad/cq_parts.py`：基于 `MechanicalLayout` 生成 CadQuery Workplane 零件，并给关键面/边/轴打 tag。
- `cad/cq_assembly.py`：创建 CadQuery Assembly，添加零件，应用约束并调用 `solve()`。
- `cad/export.py`：导出 STEP / STL 等文件。
- `validation/basic.py`：检查 DOF、link 长度、reach、必需字段、layout frame 完整性、constraint 完整性、CadQuery solve 结果等。

## 推荐工作流

```text
用户提问
  -> robot_design_agent
      -> requirement_agent
      -> kinematics_agent
      -> layout_agent
      -> cad_agent
      -> verification_agent
  -> 主 agent 整合最终回答
```

阶段说明：

1. `requirement_agent`
   - 输入：用户需求文本
   - 输出：结构化需求对象
   - 处理：缺失字段用默认值补全，并标注假设

2. `kinematics_agent`
   - 输入：结构化需求对象
   - 输出：构型建议、DH 参数表、初步连杆长度
   - 处理：调用 `robot_sdk.kinematics.dh` 做 FK / reach 基础校验

3. `layout_agent`
   - 输入：需求、构型、DH/POE 运动学模型、初步 link/joint spec
   - 输出：`MechanicalLayout`
   - 处理：定义 CAD 可消费的机械语义 frame、mate features 和 assembly constraints，包括 joint axis frame、link body frame、mount interface frame、end-effector mount frame
   - 注意：这一层负责说明“数学连杆坐标系”和“实体零件装配坐标系”之间的映射关系

4. `cad_agent`
   - 输入：`MechanicalLayout`、ID 表、基础尺寸参数、assembly constraints
   - 输出：CadQuery CAD 文件路径、STEP 导出路径、参数摘要
   - 处理：调用 `robot_sdk.cad.cq_parts` 生成几何，调用 `robot_sdk.cad.cq_assembly` 用 CadQuery `Assembly.solve()` 求解装配

5. `verification_agent`
   - 输入：需求、DH/POE、MechanicalLayout、CAD 参数、生成文件
   - 输出：验证报告
   - 处理：调用 `robot_sdk.validation.basic`

## `layout_agent` 详细设计

### 定位

`layout_agent` 不是“把 DH 翻译成 CAD”的 agent，而是**机械装配语义生成器**。

它的核心职责是把运动学模型中的抽象 frame 转换成 CAD 可消费的机械布局模型：

```text
KinematicModel
  -> MechanicalLayout
  -> CadQuery constrained assembly
```

必须避免以下错误假设：

- DH frame 不等于 joint 实体轴线 frame。
- DH link length 不等于 link body 实体长度。
- DH transform 不等于 CAD mate transform。
- joint spec 不等于 actuator / bearing / flange layout。
- 同一组 DH 参数可以对应多种机械实体结构。

### 输入

`layout_agent` 的输入应包含：

```text
RobotRequirement
KinematicModel
LinkSpec / JointSpec 初稿
DesignAssumptions
```

输入不应只有 DH 表。DH / POE 只能说明运动学坐标系关系，不能完整说明 CAD 装配关系。

### 输出

`layout_agent` 输出 `MechanicalLayout`。MVP 建议结构如下：

```python
MechanicalLayout:
  units: "mm"
  base_frame: FrameSpec
  frames: list[FrameSpec]
  joints: list[JointLayout]
  links: list[LinkLayout]
  interfaces: list[InterfaceSpec]
  part_features: list[PartFeature]
  assembly_constraints: list[AssemblyConstraint]
  frame_mappings: list[FrameMapping]
  envelopes: list[EnvelopeSpec]
  assumptions: list[str]
  warnings: list[str]
```

关键对象：

```python
FrameSpec:
  id: str
  parent: str | None
  transform: Transform
  semantic: "kinematic" | "joint_axis" | "link_body" | "interface" | "mount" | "ee"
```

```python
FrameMapping:
  kinematic_frame_id: str
  mechanical_frame_id: str
  transform_offset: Transform
  rationale: str
```

```python
JointLayout:
  id: str
  type: "revolute" | "prismatic"
  axis_frame: str
  parent_link: str
  child_link: str
  range: tuple[float, float]
  actuator_envelope: EnvelopeSpec | None
```

```python
LinkLayout:
  id: str
  body_frame: str
  from_interface: str
  to_interface: str
  envelope: EnvelopeSpec
```

```python
InterfaceSpec:
  id: str
  frame: str
  type: "flange" | "shaft" | "bearing_seat" | "bolt_pattern" | "end_effector_mount"
  mates_to: str | None
```

```python
PartFeature:
  id: str                  # e.g. "base.top", "J1.bottom", "J1.axis"
  part_id: str             # e.g. "base", "J1"
  cad_tag: str             # CadQuery tag name, e.g. "top"
  type: "plane" | "axis" | "point" | "edge" | "face"
  semantic: "mount_face" | "joint_axis" | "flange_face" | "link_end"
  frame: str | None
```

```python
AssemblyConstraint:
  id: str
  fixed: str               # feature ref, e.g. "base.top"
  moving: str              # feature ref, e.g. "J1.bottom"
  kind: "Fixed" | "Plane" | "Axis" | "Point"
  offset: float | None
  flipped: bool
  rationale: str
```

### LangGraph 流程

`layout_agent` 建议实现为 LangGraph：

```text
START
  -> normalize_inputs
  -> choose_layout_template
  -> instantiate_frames
  -> map_kinematic_to_mechanical_frames
  -> synthesize_interfaces
  -> synthesize_mate_features
  -> synthesize_assembly_constraints
  -> assign_link_envelopes
  -> validate_layout
  -> emit_layout
  -> END
```

节点职责：

1. `normalize_inputs`
   - 统一单位：mm、rad、kg。
   - 检查 DOF、joint 数量、link 数量。
   - 补默认值并记录 `assumptions`。

2. `choose_layout_template`
   - MVP 不允许 LLM 完全自由生成机械结构。
   - 先支持少数模板：
     - tabletop serial arm
     - SCARA-like arm
     - simple 4DOF educational arm
   - LLM 只负责选择模板和解释原因。

3. `instantiate_frames`
   - 根据模板生成基础 frame：
     - `base_frame`
     - `J1_axis_frame`
     - `L1_body_frame`
     - `J2_axis_frame`
     - `L2_body_frame`
     - `end_effector_mount_frame`

4. `map_kinematic_to_mechanical_frames`
   - 显式建立：
     - DH frame 到 joint axis frame 的偏置。
     - DH link frame 到 link body frame 的偏置。
     - end effector frame 到工具安装面的偏置。
   - 每个映射必须写入 `FrameMapping.rationale`。
   - 这是整个 `layout_agent` 最关键的节点。

5. `synthesize_interfaces`
   - 生成法兰、轴承座、安装面、末端接口。
   - MVP 先支持简化接口：
     - circular flange
     - box link mount
     - revolute joint shaft axis

6. `synthesize_mate_features`
   - 给 CAD 零件生成可约束的 mate feature。
   - 例如：
     - `base.top`
     - `base.axis`
     - `J1.bottom`
     - `J1.axis`
     - `J1.output_flange`
     - `L1.input_face`
   - 每个 feature 必须能映射到 CadQuery tag 或明确的几何选择。

7. `synthesize_assembly_constraints`
   - 根据装配语义生成中立 `AssemblyConstraint`。
   - 例如：
     - `base.top` 与 `J1.bottom` 使用 `Plane` 约束。
     - `base.axis` 与 `J1.axis` 使用轴向/同轴约束。
     - `J1.output_flange` 与 `L1.input_face` 使用 `Plane` 约束。
   - Agent 可以在 Skill 指导下推理约束关系，但不能直接手算最终 part pose。

8. `assign_link_envelopes`
   - 给每个 link 分配 CAD envelope。
   - envelope 是 CAD 几何包络，不是 DH 参数。
   - MVP 可支持：
     - box beam
     - cylinder joint housing
     - flange disk

9. `validate_layout`
   - 检查 frame graph 是否连通。
   - 检查每个 joint 是否有 axis frame。
   - 检查每个 link 是否有 body frame。
   - 检查 interface 是否成对。
   - 检查每个 CAD part 是否有必需 mate feature。
   - 检查每个装配 pattern 是否有必需 constraints。
   - 检查 CAD 必需字段是否齐全。
   - 检查所有 kinematic frame 到 mechanical frame 的映射是否有 rationale。

10. `emit_layout`
   - 输出结构化 `MechanicalLayout`。
   - 附带 `assumptions` 和 `warnings`。
   - 交给 `cad_agent`。

### 关键原则

- `layout_agent` 不直接生成 CAD 实体，只生成 CAD 语义输入。
- `layout_agent` 不做 FK/IK 矩阵计算，这些属于 SDK。
- `layout_agent` 不自由发挥复杂机械结构，MVP 必须用模板约束。
- 每一个 CAD frame 都必须有 `semantic`，不能只有匿名矩阵。
- 每一个从 DH / POE 到 CAD 语义 frame 的转换都必须记录 `FrameMapping.rationale`。
- 装配关系必须优先表达为 constraint，而不是直接表达为绝对 pose。
- 最终 CAD part pose 由 CadQuery `Assembly.solve()` 求解或由 CadQuery 装配上下文确定。
- `cad_agent` 只能消费 `MechanicalLayout`，不应该直接消费 DH 表。

### SDK 支撑

`layout_agent` 应调用 `robot_sdk/layout/`，而不是自己实现所有细节。

建议 SDK 模块：

```text
robot_sdk/layout/
  mechanical_layout.py
  frame_mapping.py
  templates.py
  validation.py
```

职责：

- 定义 `MechanicalLayout`、`FrameSpec`、`FrameMapping`、`JointLayout`、`LinkLayout`、`InterfaceSpec`、`EnvelopeSpec`。
- 提供标准 layout template。
- 做 frame graph 连通性校验。
- 做 interface 完整性校验。
- 做单位和 transform 校验。
- 输出 cad_agent 可直接消费的结构。

### 失败教训

之前 `robot_agent` / `robot_sdk` 分支失败的关键原因之一，是把运动学 frame 和机械实体 frame 混在一起，导致：

- 数学上可以 FK，但 CAD 装配关系不成立。
- link 几何体和 joint 轴线缺少明确接口关系。
- CAD 只知道矩阵，不知道 mount、flange、bearing seat、bolt pattern 等机械语义。
- CAD 只有最终 pose，没有可追踪的 mate feature 和 assembly constraint，后续无法解释装配为什么成立或失败。
- 后续验证无法判断失败来自运动学、布局还是 CAD 几何。

因此 MVP 必须强制保留：

```text
KinematicModel -> MechanicalLayout -> CadQuery constrained assembly
```

不能走：

```text
DH table -> CAD code / absolute part poses
```

## MVP 验收标准

- 用户输入一段中文机器人需求后，系统能生成结构化设计报告。
- 报告包含需求表、构型说明、DH 参数表、机械布局说明、ID 表、CAD 输出路径和验证结论。
- 自动生成关节/连杆 ID，例如 `J1/J2/...`、`L1/L2/...`、`end_effector`。
- 生成 `MechanicalLayout`，至少包含 joint axis、link body、interface frame、mount frame。
- 生成 `PartFeature` 和 `AssemblyConstraint`，并能追踪每条约束的来源。
- CadQuery 零件由 `MechanicalLayout` 生成，而不是直接由 DH 表生成。
- CadQuery 零件包含可被约束引用的 tag / mate feature。
- CadQuery `Assembly.solve()` 能成功完成基础装配。
- CAD 文件可以导出为 STEP，且可由脚本复现。
- 验证报告包含通过、警告或失败项。
- 每个阶段有 trace，能追踪中间产物。
- 不要求真实动力学、电机选型、完整碰撞仿真或工业级制造细节。

## 推荐实现顺序

实现顺序原则：**先稳定 SDK 数据契约和确定性能力，再做一个写死流程的 `robot_design_agent` 跑通闭环，最后再拆分多个子 agent 和补 Skills。**

不要一开始就写很多 agent。否则在 `RobotRequirement`、`KinematicModel`、`MechanicalLayout` 这些数据契约还没稳定时，会反复返工。

### 1. `robot_sdk/types.py`

先定义整个系统的数据契约：

- `RobotRequirement`
- `KinematicModel`
- `DHParam`
- `JointSpec`
- `LinkSpec`
- `MechanicalLayout`
- `FrameSpec`
- `FrameMapping`
- `InterfaceSpec`
- `EnvelopeSpec`

这是优先级最高的一步。后续所有 subagent 和 SDK 模块都围绕这些类型通信。

### 2. `robot_sdk/structure/ids.py`

实现 ID 生成：

- `J1/J2/...`
- `L1/L2/...`
- `end_effector`
- interface ID
- frame ID

这部分简单、稳定、容易测试，应尽早完成。

### 3. `robot_sdk/kinematics/dh.py`

实现基础运动学：

- DH 参数结构
- 单段 DH transform
- 串联链 FK
- 基础 reach 估算

MVP 先只支持串联 revolute/prismatic 基础场景。

### 4. `robot_sdk/layout/mechanical_layout.py`

实现 `MechanicalLayout` 构造工具。

第一阶段只支持一个模板：

```text
tabletop serial arm
```

不要让 LLM 自由生成复杂机械布局。

### 5. `robot_sdk/layout/frame_mapping.py`

实现 DH / POE frame 到 mechanical frame 的显式映射。

要求：

- 每个 `FrameMapping` 必须有 `rationale`。
- 明确区分：
  - kinematic frame
  - joint axis frame
  - link body frame
  - interface frame
  - mount frame
- 禁止把 DH transform 直接当 CAD mate transform。

这是 MVP 最关键的 SDK 模块。

### 6. `robot_sdk/layout/validation.py`

校验 `MechanicalLayout`：

- frame graph 是否连通
- 每个 joint 是否有 axis frame
- 每个 link 是否有 body frame
- 每个 frame 是否有 semantic
- 每个 interface 是否成对或有明确开放端
- 每个 CAD part 是否有必要的 mate feature
- 每个装配关系是否表达为 constraint
- 每个 mapping 是否有 rationale

### 7. `robot_sdk/assembly/features.py`

定义 CAD 可约束的 mate feature。

要求：

- feature ID 稳定，例如 `base.top`、`base.axis`、`J1.bottom`、`J1.axis`。
- feature 能映射到 CadQuery tag 或明确的几何选择。
- feature 带机械语义，例如 `mount_face`、`joint_axis`、`flange_face`。

### 8. `robot_sdk/assembly/constraints.py`

定义中立装配约束。

MVP 先支持：

- `Fixed`
- `Plane`
- `Axis`
- `Point`

约束应引用 feature，而不是直接引用绝对 pose。

### 9. `robot_sdk/cad/cq_parts.py`

生成粗粒度 CadQuery 零件。

要求：

- 只消费 `MechanicalLayout` 和基础尺寸参数。
- 不接收 DH 表作为直接输入。
- 给关键面、轴、边打 tag。
- MVP 几何可以很粗：
  - cylinder joint housing
  - box link body
  - flange disk
  - simple mount plate

### 10. `robot_sdk/assembly/cq_adapter.py`

把中立 `AssemblyConstraint` 转成 CadQuery `Assembly.constrain(...)` 调用。

要求：

- 统一处理 feature ref 到 CadQuery selector 的转换。
- 保留 constraint trace，便于验证报告说明失败来源。
- 不在这里手算完整装配 pose。

### 11. `robot_sdk/cad/cq_assembly.py`

创建 CadQuery Assembly 并求解。

要求：

- `Assembly.add(...)` 添加所有 part。
- 应用 `Fixed`、`Plane`、`Axis` 等约束。
- 调用 `Assembly.solve()`。
- 返回 solve 状态、part pose 摘要和错误信息。

### 12. `robot_sdk/cad/export.py`

导出 CAD 文件。

MVP 至少支持：

- STEP
- 可选 STL

### 13. `robot_sdk/validation/basic.py`

做整体基础校验：

- DOF 是否匹配
- reach 是否覆盖需求
- link 长度是否为正
- mate feature 是否完整
- assembly constraint 是否完整
- CadQuery solve 是否成功
- CAD 输出是否存在
- `MechanicalLayout` 是否通过 layout validation
- 必需字段是否完整

### 14. `subagents/robot_design_agent/agent.py`

先实现一个写死 MVP 流程的总入口。

第一版不要拆多个子 agent，直接串 SDK：

```text
parse requirement
-> generate simple DH
-> map DH frames to MechanicalLayout
-> synthesize CadQuery mate features and constraints
-> generate IDs
-> build CadQuery parts
-> solve CadQuery assembly
-> export CAD
-> validate
```

目标是先让主 agent 可以调用一个 `robot_design_agent`，生成完整 MVP 产物。

### 15. 补 SDK 和 robot_design_agent 测试

建议测试文件：

- `tests/test_robot_sdk_types.py`
- `tests/test_robot_sdk_ids.py`
- `tests/test_robot_sdk_dh.py`
- `tests/test_robot_sdk_layout.py`
- `tests/test_robot_sdk_assembly_features.py`
- `tests/test_robot_sdk_assembly_constraints.py`
- `tests/test_robot_sdk_cq_parts.py`
- `tests/test_robot_sdk_cq_assembly.py`
- `tests/test_robot_design_agent.py`

先测 SDK，再测 agent。SDK 测试应该不依赖 LLM。

### 16. 再拆分子 agent

等 `robot_design_agent` 跑通闭环后，再拆：

- `requirement_agent`
- `kinematics_agent`
- `layout_agent`
- `cad_agent`
- `verification_agent`

拆分时保持数据契约不变，不要让各子 agent 私自定义新结构。

### 17. 最后补 robot 相关 Skills

等数据结构和流程稳定后，再新增 Skills：

- `robot_requirement_analysis`
- `robot_cad_design_review`
- `robot_kinematics_modeling`

Skills 是方法论和输出格式提示，过早写会跟着数据结构反复改。

## 第一阶段建议

第一阶段只支持一个最小场景：

```text
桌面 4DOF 串联机械臂
负载：默认 500g
工作半径：默认 400mm
关节：revolute
CAD：box link + cylinder joint + simple flange
```

第一阶段流程：

```text
robot_design_agent
  -> parse requirement
  -> generate simple DH
  -> map DH frames to MechanicalLayout
  -> synthesize CadQuery mate features and constraints
  -> generate IDs
  -> build CadQuery parts
  -> solve CadQuery assembly
  -> export STEP
  -> validate
```

等最小闭环跑通后，再把内部步骤拆成独立 subagents。

### 第一阶段不要做

- 不要先做多构型选择。
- 不要先做真实动力学。
- 不要先做电机/减速器选型。
- 不要先做复杂 CAD 外形。
- 不要自研通用 CAD mate solver，MVP 使用 CadQuery `Assembly.solve()`。
- 不要让 LLM 自由生成 `MechanicalLayout`。
- 不要让 LLM 直接输出最终 CadQuery code 或绝对装配 pose。
- 不要让 `cad_agent` 直接消费 DH 表。

## 总结

MVP 的重点是跑通“需求 -> 运动学 -> 机械布局语义 -> CadQuery 约束装配 -> CAD 导出”的闭环，而不是追求完整工业机器人设计能力。  
Skill 提供方法论，Subagent 负责阶段推理和编排，SDK 承担确定性工程计算与 CadQuery 建模。DH/POE 只作为运动学层输入，不能直接替代机械装配语义层。
