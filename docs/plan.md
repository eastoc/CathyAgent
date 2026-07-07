# Robot CAD Agent Plan

## 结论

第一阶段已经完成：CathyAgent 现在可以把一段机器人 CAD 需求跑成可复现的 MVP 产物，包括 `需求文档.md`、粗粒度 CadQuery 零件、语义命名的 STEP package、整机 STEP 和基础验证报告。

当前最重要的问题不是继续堆 CAD 外形，也不是继续微调局部装配约束，而是补上 **structure-first / source-first** 的机器人结构规划层：现有 `layout_agent` 已能输出 morphology / primitive 决策，但不能真正改变 6DOF 机械臂的三维轴系、站位和结构骨架，导致 6DOF + 500mm 这类请求仍可能退化为平面 template fallback。

核心原则保持不变：**DH / POE 是运动学模型，不等于 CAD 实体装配变换**。CAD 仍必须经过结构计划与 `MechanicalLayout` 语义层；`layout_agent` 应输出可落地的 `RobotStructurePlan`，SDK 负责把结构计划转换成机械 frame、datum、features、constraints 和可复核 CAD source。

## 当前状态

### 已完成

- `robot_sdk/types.py`：核心数据契约，包括 `RobotRequirement`、`KinematicModel`、`DHParam`、`MechanicalLayout`、`PartFeature`、`AssemblyConstraint`。
- `robot_sdk/structure/ids.py`：串联链 ID、frame ID、interface ID 生成。
- `robot_sdk/kinematics/dh.py`：DH transform、FK、reach 估算。
- `robot_sdk/layout/`：`MechanicalLayout`、frame mapping、layout validation。
- `robot_sdk/assembly/`：mate feature、neutral constraint、CadQuery adapter。
- `robot_sdk/cad/`：粗粒度零件、constraint_solve 装配、STEP package 导出；当前使用 fixed layout pose 约束避免 solver 把不成熟的 mate 集合拉歪。
- `robot_sdk/validation/basic.py`：报告级校验。
- `subagents/robot_design_agent/`：固定 MVP 总入口，串起需求解析、DH、layout、CAD、导出、验证。
- `subagents/robot_design_agent/rules.py`：CAD 命名规则，例如 `J1 -> joint1`、`L1 -> link1`。
- `skills/robot_cad_design/SKILL.md`：机器人 CAD 方法论、调用边界和审查清单。

### 当前输出结构

```text
session_id/
  需求文档.md
  base/
    base_body.step
    base.step
  joint1/
    joint1_housing.step
    joint1.step
  link1/
    link1_body.step
    link1.step
  ...
  整机.step
```

### 当前主要限制

- `kinematics_agent` / profile / scaling 已经能避免 UR3E 请求落回占位 DH，但 search-assisted DH 仍未完成。
- `MechanicalLayout` 目前只有 `tabletop_serial_arm` 简化模板。
- `layout_agent` 目前主要做 morphology label，不会真正重写 6DOF 机械臂三维站位和轴系。
- J4/L3 暴露出当前 layout 语义不完整：`link.body_axis` 不能等同于相邻 `joint.axis`，UR wrist 区域尤其明显。
- 当前 `constraint_solve` 采用 fixed layout pose 约束，能防止 CadQuery solver 漂移，但不能修正 layout 本身的机械错位。
- 当前 `link_primitives.py` 是 deterministic rule，不是 agent 自主推理；候选类型过粗，无法完整表达 UR-style morphology。
- CAD 几何仍是概念级：box/offset/wrist link、morphology-aware joint housing、simple flange。
- `UR3E-like + reach 500mm` 误选 `exact_profile` 的 decision 回归已在阶段 5 加 guard；search-assisted DH 仍未完成。
- `6DOF + reach 500mm + 未指定型号` 当前会走 `template_fallback`，产生全零 `alpha/d` 的平面串联链；这种结果必须从 warning 升级为 structure validation failure。

## 阶段 2 回顾：运动学可信度

阶段 2 的目标是把 DH 生成从“占位模板”升级为“资料/机型/profile 驱动”。当前 profile / scaling / `kinematics_agent` 基础能力已经形成，search-assisted DH 仍是待补项。

用户输入：

```text
6DoF 机械臂，工作空间 500mm，DH 模型参考 UR3E。
```

期望系统行为：

```text
识别 UR3E 参考
-> 调 kinematics_agent 生成可信 KinematicModel
    -> 优先查本地 robot profile
    -> 本地没有时调用 search_agent 搜资料
    -> 提取/确认 DH 参数、单位、standard/modified convention
    -> 必要时按目标 workspace 缩放或标注不可缩放
    -> 调 robot_sdk.kinematics.dh 做 FK/reach 校验
-> 写入 需求文档.md
-> 再进入 MechanicalLayout 和 CAD 导出
```

## 阶段 2 实现内容与待补项

### 1. `robot_sdk/kinematics/profiles.py`

新增本地机型 profile 模块。

建议数据结构：

```python
RobotKinematicProfile:
  name: str
  aliases: list[str]
  manufacturer: str | None
  convention: "dh" | "modified_dh"
  units: "mm"
  dh_params: list[DHParam]
  source: str
  notes: list[str]
```

先内置少量 profile：

- `ur3e`
- 后续再加 `ur5e`、`ur10e`、`puma560`

要求：

- profile 必须声明 DH convention。
- profile 必须声明单位。
- profile 必须能转成当前 `KinematicModel`。
- profile 参数来源要写入 `source` 和需求文档。

### 2. `robot_sdk/kinematics/scaling.py`

处理“参考某机型，但目标 workspace 不同”的情况。

需要区分：

- `exact_profile`：使用原始机型 DH，不缩放。
- `scaled_profile`：按目标 reach 缩放 `a/d`，明确标注“不再是官方 DH”。
- `profile_like_template`：只参考结构，不声称参数来自原机型。

要求：

- 缩放只作用于长度项 `a/d`，不缩放角度项 `alpha/theta`。
- 输出必须记录 scale factor。
- 需求文档必须写清楚是否为官方参数。

### 3. `subagents/kinematics_agent/agent.py`

阶段 2 正式拆出 `kinematics_agent`，负责从需求生成可信 `KinematicModel`。

输入：

```text
request: 用户原始需求
dof: 可选
reach_mm: 可选
payload_g: 可选
mode: "auto" | "exact_profile" | "scaled_profile" | "template"
```

输出：

```text
KinematicModel
RobotRequirement fields relevant to kinematics
source summary
profile name
model mode
scale factor
FK / reach validation summary
warnings
```

内部流程：

```text
normalize requirement
  -> detect profile reference
  -> load local profile if available
  -> request search only if profile is missing and source is required
  -> optionally scale profile
  -> build KinematicModel
  -> run FK/reach validation
  -> fallback to MVP template
```

首版优先做确定性 profile + template fallback；搜索能力可以先保留接口，再接 `search_agent`。

识别规则：

- `ur3e` / `UR3E` / `UR3 e`
- `ur5e`
- `puma560`
- “参考 XXX”
- “类似 XXX”

fallback 规则：

- 如果没有 profile reference，继续用当前 MVP simple DH。
- 如果有 reference 但 profile 不存在，返回 warning，不要静默使用 simple DH。
- 如果用户明确要求“必须按 UR3E DH”，profile 缺失时应失败或要求搜索，不允许 fallback。

### 4. `robot_design_agent` 集成 `kinematics_agent`

`robot_design_agent` 不再直接调用 `_build_simple_kinematic_model()` 作为唯一入口。

新的主流程：

```text
robot_design_agent
  -> parse lightweight request / output paths
  -> kinematics_agent
  -> MechanicalLayout
  -> requirement document
  -> CadQuery assembly
  -> STEP package
  -> validation
```

要求：

- `robot_design_agent` 接收 `kinematics_agent` 返回的 `KinematicModel`。
- `_build_simple_kinematic_model()` 只保留为 template fallback helper。
- trace 中必须出现 `kinematics_agent` 或 `kinematic_model_source`。
- 需求文档必须显示 kinematics source，而不是只显示 DH 表。

### 5. Search-Assisted DH

当本地 profile 没有命中时，再接搜索能力。

优先由 `kinematics_agent` 内部编排：

```text
kinematics_agent
  -> detect missing profile
  -> search_agent 搜索官方/可信来源
  -> extract DH candidate
  -> normalize units/convention
  -> validate with SDK
  -> emit KinematicModel + source summary
```

搜索结果要求：

- 优先官方文档、厂商参数页、可信 robotics 文档。
- 必须记录来源标题/URL/摘要。
- 不确定 convention 时不能静默使用，必须 warning。
- 搜索提取的 DH 必须经过 SDK FK/reach 基础校验。

### 6. `需求文档.md` 增强

需求文档新增：

- `运动学来源`
- `profile 名称`
- `source`
- `exact_profile / scaled_profile / template_dh`
- `scale factor`
- `FK/reach 校验结果`
- `warnings`

如果用户要求 UR3E，但系统使用了 fallback template，文档必须显式警告。

### 7. Tests

新增测试：

- `tests/test_robot_sdk_kinematic_profiles.py`
- `tests/test_robot_sdk_kinematic_scaling.py`
- `tests/test_kinematics_agent.py`
- `tests/test_robot_design_agent_kinematics_integration.py`

关键断言：

- `UR3E` 文本能命中 `ur3e` profile。
- profile DH 不会被 simple template 覆盖。
- scaled profile 会记录 scale factor。
- fallback template 会产生 warning。
- `robot_design_agent` 会消费 `kinematics_agent` 的 `KinematicModel`。
- `需求文档.md` 能展示 profile/source/scale/reach check。

## 阶段 2 验收标准

- 用户说“DH 模型参考 UR3E”时，不再生成 `150, 70, 70, ...` 这种占位 DH。
- 输出能说明 DH 来源是 `ur3e profile`、搜索资料，还是 fallback template。
- `kinematics_agent` 成为阶段 2 的正式子 agent，并被 `robot_design_agent` 集成。
- `需求文档.md` 中包含完整 DH 表、单位、convention、来源和 warnings。
- SDK 能对 profile DH 做 FK/reach 基础校验。
- fallback 行为必须可见，不能把 template DH 伪装成参考机型 DH。

## 阶段 3：MechanicalLayout 语义修复

阶段 3 的目标：修复 J4/L3 这类装配错位，把问题从“看起来能出 STEP”提升到“机械布局语义可检查、可解释、可迭代”。

### 问题判断

当前错位不应继续从 CadQuery constraint 层硬修。原因：

- DH / FK 只给运动学 frame，不等于实体零件装配 frame。
- `link.body_axis` 是实体外形主方向，不一定等于下一关节的 `joint.axis`。
- UR 类机械臂的 wrist 区域常有交错轴、短 offset、法兰转接，不能用单根 box link 强行表达。
- fixed layout pose solve 只能防止 solver 漂移，不能证明 layout 的 interface 语义正确。

### 3.1 `robot_sdk/layout/debug.py`

新增 layout 诊断模块，先把 frame chain 打清楚。

输出对象建议：

```text
LayoutDebugReport
  joints:
    joint_id
    axis_frame
    axis_origin
    axis_direction
    input_interface
    output_interface
  links:
    link_id
    body_frame
    body_origin
    body_direction
    input_interface
    output_interface
    connects_joint_from
    connects_joint_to
  warnings
```

必须能单独打印局部链路：

```text
J3 -> L3 -> J4
J4 -> L4 -> J5
J5 -> L5 -> J6
```

用于 review 的最小输出：

- joint axis 的位置和方向。
- link body 的起点、终点、方向。
- link input/output interface frame 的位置和方向。
- interface gap：例如 `L3.output_interface` 到 `J4.input_interface` 的距离。
- axis mismatch：例如 `L3.body_axis` 与 `J4.axis` 的夹角。

### 3.2 `robot_sdk/layout/validation.py` 增强

新增 MechanicalLayout 级错误检查，不依赖 CadQuery：

- `interface_gap_error`：应该连接的两个 interface 距离超过容差。
- `interface_direction_error`：连接面法向不相对或不一致。
- `invalid_axis_assumption`：link body axis 被错误用于约束 joint axis。
- `wrist_layout_warning`：连续短 offset / alpha 变化区域需要 wrist spacer 或 elbow connector。
- `zero_or_degenerate_link_warning`：DH 中 `a/d` 接近 0 时不能强行生成长 box link。

这些检查要在 CAD 生成前运行，避免错误 layout 进入 STEP。

### 3.3 重写 frame 语义规则

`MechanicalLayout` 生成时需要明确分离三类 frame：

- `kinematic_station`：DH/FK 得到的运动学站点。
- `joint_axis_frame`：由对应 DH frame 的 z-axis 或 convention 明确推导。
- `mechanical_interface_frame`：实体装配接口，不能直接等同于 DH station。

规则要求：

- joint axis 只来自运动学 frame 的轴向，不从 link body direction 推导。
- link body 只表达两个 mechanical interface 之间的实体连接。
- input/output interface 必须独立保存 position + normal + tangent。
- 不再用 `link_vector` 简化所有 joint/link 关系。
- DH 的 `alpha` 变化必须影响 axis frame，而不是只影响 station spacing。

### 3.4 link 几何从单 box 升级为布局类型

根据两个 interface 的相对关系选择 link primitive：

```text
straight_link:
  两个 interface 近似共线，用 box/cylinder 连接。

offset_link:
  两个 interface 平行但有横向 offset，用偏置 box 或双段 box。

elbow_link:
  两个 interface 有明显夹角，用 L-shape / elbow connector。

wrist_spacer:
  wrist 区域短距离法兰/圆柱转接件，不再强行生成长 box。
```

J4/L3 优先用这个规则验证：如果 `L3.output_interface` 和 `J4.input_interface` 不共线，不能再生成一根直 box 去碰它。

当前实现状态：

- `robot_sdk/layout/link_primitives.py` 已根据 layout debug 信息生成 link primitive 分类。
- `LinkLayout.metadata.primitive_type` 已写入 `straight_link / offset_link / elbow_link / wrist_spacer`。
- `robot_sdk/cad/cq_parts.py` 已按 primitive 选择概念级几何：
  - `straight_link`：单段 box beam + 两端 flange。
  - `offset_link`：双段 offset box + bridge + 两端 flange。
  - `elbow_link`：轴向主梁 + 输出端朝下一关节轴方向的腕部弯折壳体 + 两端 flange。
  - `wrist_spacer`：末端短圆柱 spacer + 两端 flange。
- UR3E scaled 500mm 当前分类：`L1/L2 = straight_link`，`L3/L4/L5 = elbow_link`，`L6 = wrist_spacer`。
- L5/J6 已作为回归用例：短腕部段如果存在 90 度 body-to-axis mismatch，不能被 `wrist_spacer` 规则吞掉，必须进入 `elbow_link`。

### 3.5 mate feature / constraint 回归策略

在 3.1 到 3.4 完成前，不要把语义 mate 约束直接喂给 CadQuery solver 重排整机。

当前策略：

- CadQuery solver 使用 fixed layout pose 约束，保证 STEP 不被 solver 拉歪。
- `AssemblyConstraint` 继续生成并进入 metadata / validation。
- `semantic_constraints_applied_to_solver` 必须用 `none/local_subassembly/full_assembly` 明确展示，不能把 fixed pose solve 伪装成整机 full semantic solve。
- 3.5 尚未完成整机 constraint-driven assembly；等 interface frame 语义正确后，再逐步启用局部 constraint solve。

恢复真正 constraint-driven assembly 的顺序：

1. 先只对单个子总成启用 constraint solve，例如 `J3-L3-J4`。
2. 验证 solver 后 frame 与 layout frame 的偏差小于容差。
3. 再扩展到 wrist 子总成。
4. 最后扩展到整机。

### 3.6 Tests

新增测试：

- `tests/test_robot_sdk_layout_debug.py`
- `tests/test_robot_sdk_layout_validation.py`
- `tests/test_robot_sdk_layout_wrist.py`
- `tests/test_robot_sdk_cq_assembly_pose_stability.py`

关键断言：

- debug report 能输出 `J3 -> L3 -> J4`。
- `L3.output_interface` 与 `J4.input_interface` 的 gap 可计算。
- UR3E scaled profile 下 J4/L3 不允许用错误的 `link.body_axis == joint.axis` 假设通过校验。
- fixed layout pose solve 后 part location 不应偏离 layout pose。
- 如果未来启用局部 semantic constraint solve，solver 后 pose 偏差必须小于容差。

## 阶段 3 验收标准

- 能用 report 解释 J4/L3 错位来自哪个 frame 或 interface。
- `MechanicalLayout` 明确区分 kinematic station、joint axis、mechanical interface、link body。
- UR3E wrist 区域不再强行用单根直 box 表达所有连接。
- CAD 导出的整机 STEP 不再出现 solver 漂移。
- 语义约束是否进入 solver 必须可追踪，不能静默切换。

## 阶段 4：layout_agent 结构语义决策

阶段 4 的目标：把当前 `link_primitives.py` 的硬规则升级为 **agent 结构语义推理 + SDK 校验落地**。`layout_agent` 不直接生成 CadQuery，不直接输出 STEP，也不直接摆绝对 part pose；它只输出可审查、可校验的 morphology decision。

### 问题判断

当前错误不是单纯 CAD 几何问题：

- `L6` 在 exact UR3E profile 中因为长度超过固定阈值，被误判为 `straight_link`。
- `L3/L4/L5` 都被粗暴归入 `elbow_link`，但 UR-style 结构里它们分别更接近 forearm / wrist offset / wrist elbow。
- `joint1~joint6` 仍使用同一种 cylinder housing，没有区分 shoulder、elbow、wrist joint。
- `link primitive` 当前由 SDK 阈值规则决定，不理解 robot family、profile 语义和 UR-style morphology。

因此下一阶段不要继续堆阈值，而是引入：

```text
KinematicModel / DH / profile
  -> layout_agent 输出 LayoutDecision
  -> SDK adapter 生成/覆盖 MechanicalLayout metadata
  -> SDK validation
  -> CAD builder 消费 morphology
```

### 4.1 数据边界

`layout_agent` 输入：

```text
RobotRequirement
KinematicModel / DHParam
kinematics metadata，例如 source_mode/profile_name/scale_factor
MechanicalLayout debug report
candidate morphology catalog
用户额外约束，例如“UR3E-like”“概念粗模”“更像协作臂”
```

`layout_agent` 输出：

```text
LayoutDecision
```

禁止输出：

- CadQuery 代码。
- STEP 文件。
- 绝对 part pose。
- 绕过 SDK validation 的 MechanicalLayout。
- 把 DH transform 当 CAD mate transform。

### 4.2 `subagents/layout_agent/schema.py`

新增结构化 schema：

```python
LayoutDecision:
  robot_family: str
  confidence: float
  layout_source: str
  joint_decisions: list[JointMorphologyDecision]
  link_decisions: list[LinkMorphologyDecision]
  interface_decisions: list[InterfaceMorphologyDecision]
  assumptions: list[str]
  warnings: list[str]

JointMorphologyDecision:
  joint_id: str
  morphology: JointMorphologyType
  reason: str
  confidence: float
  source_signals: list[str]

LinkMorphologyDecision:
  link_id: str
  morphology: LinkMorphologyType
  reason: str
  confidence: float
  source_signals: list[str]
  cad_intent: dict

InterfaceMorphologyDecision:
  interface_id: str
  morphology: InterfaceMorphologyType
  reason: str
  confidence: float
```

首版候选 morphology：

```text
JointMorphologyType:
  base_yaw_joint
  shoulder_joint
  elbow_joint
  wrist_pitch_joint
  wrist_roll_joint
  tool_flange_joint
  generic_revolute_joint

LinkMorphologyType:
  base_column
  upper_arm_link
  forearm_link
  wrist1_offset_housing
  wrist2_elbow_cylinder
  wrist3_tool_flange
  terminal_tool_spacer
  generic_straight_link
  generic_offset_link
  generic_elbow_link
  generic_wrist_spacer

InterfaceMorphologyType:
  base_mount_face
  actuator_flange
  arm_flange
  wrist_cross_axis_interface
  tool_mount_flange
  generic_flange
```

### 4.3 `subagents/layout_agent/prompt.py`

Prompt 要求：

- 只做 morphology / interface / constraint intent 决策。
- 明确说明 DH/FK 只提供运动学 frame。
- 识别 UR-style 6 轴协作臂结构：
  - `L1`：base/shoulder region。
  - `L2`：upper arm。
  - `L3`：forearm。
  - `L4`：wrist1 offset housing。
  - `L5`：wrist2 elbow/cross-axis cylinder。
  - `L6`：wrist3/tool flange。
- 如果用户给出 `UR3E-like + reach 500mm`，layout_agent 要把“像 UR3E 的结构”和“是否缩放 DH”分开记录。
- 不确定时必须输出 warning，而不是强行自信分类。

### 4.4 `subagents/layout_agent/agent.py`

首版用 LangGraph 固定流程：

```text
parse_input_node
  -> build_initial_layout_debug_node
  -> decision_node
  -> validate_decision_node
  -> finalize_node
```

节点职责：

- `parse_input_node`：读取 requirement、kinematic_model、metadata。
- `build_initial_layout_debug_node`：调用当前 SDK layout builder + debug report，为 agent 提供 frame/axis/interface 事实。
- `decision_node`：LLM structured output，生成 `LayoutDecision`。
- `validate_decision_node`：检查 ID 是否存在、morphology 是否在 catalog、confidence/reason 是否完整。
- `finalize_node`：返回 `LayoutAgentResult`，包含 `LayoutDecision` 和 trace。

首版不联网，不调用 search_agent。资料来源先只用本地 profile metadata、DH、debug report。

### 4.5 `robot_sdk/layout/decision_adapter.py`

新增 SDK adapter：

```text
LayoutDecision + KinematicModel
  -> build_tabletop_serial_mechanical_layout(...)
  -> apply_layout_decision(...)
  -> MechanicalLayout
```

职责：

- 保留现有 deterministic frame/interface/envelope 生成。
- 用 `LayoutDecision` 覆盖或补充 metadata：
  - `layout.metadata.layout_source = layout_agent`
  - `link.metadata.morphology`
  - `joint.metadata.morphology`
  - `interface.metadata.morphology`
  - decision reason/confidence/source_signals。
- 对缺失或非法 decision 生成 validation warning。
- 如果 `layout_agent` 失败，允许 fallback 到当前 `link_primitives.py`，但必须标记：

```text
layout_source = rule_fallback
```

### 4.6 扩展 SDK types

短期不强改 `MechanicalLayout` 主字段，先把 morphology 放在 metadata，降低破坏面。

需要新增 Literal：

```text
JointMorphologyType
LinkMorphologyType
InterfaceMorphologyType
LayoutSource
```

后续如果稳定，再把 `metadata["morphology"]` 提升为正式字段。

### 4.7 接入 `robot_design_agent`

当前流程：

```text
KinematicModel
  -> build_tabletop_serial_mechanical_layout()
```

阶段 4 改成：

```text
KinematicModel
  -> layout_agent.build_result()
  -> apply_layout_decision()
  -> MechanicalLayout
```

要求：

- `robot_design_agent` 构造函数接收 `layout_agent`。
- final_answer 必须输出：
  - `kinematics_source`
  - `layout_source`
  - `robot_family`
  - key morphology decisions
  - `semantic_constraints_applied_to_solver`
- 如果 layout_agent 不存在或失败，继续 fallback，但 final_answer 必须清楚写明。

### 4.8 更新 CAD builder（已完成首版）

`robot_sdk/cad/cq_parts.py` 优先读取：

```text
link.metadata["morphology"]
joint.metadata["morphology"]
```

再 fallback 到：

```text
link.metadata["primitive_type"]
```

首版 CAD morphology 映射：

```text
base_column -> vertical/base transition housing
upper_arm_link -> long arm body, box/capsule hybrid
forearm_link -> forearm body, slimmer long link
wrist1_offset_housing -> short offset housing
wrist2_elbow_cylinder -> bent cylindrical wrist housing
wrist3_tool_flange -> short terminal flange
terminal_tool_spacer -> short cylindrical spacer
```

这一步的目标不是工业级真实外形，而是避免“所有 link 都是 box/cylinder”的错误语义。

当前状态：

- `cq_parts.py` 已优先消费 `link.metadata["morphology"]`，再 fallback 到 `primitive_type`。
- `cq_parts.py` 已消费 `joint.metadata["morphology"]`，能区分 `base_yaw_joint`、`shoulder_joint`、`elbow_joint`、`wrist_pitch_joint`、`wrist_roll_joint`、`tool_flange_joint`。
- `wrist2_elbow_cylinder` 已不再走普通 box elbow。

### 4.9 修复 kinematics decision 回归

阶段 4 同时补一个必要回归：

```text
用户请求包含 UR3E-like / reference UR3E + target reach 500mm
```

应优先：

```text
scaled_profile
```

而不是：

```text
exact_profile
```

除非用户明确说“使用官方原始 UR3E 尺寸，不要缩放”。

验收样例：

```text
Build a UR3e-like 6DOF robot arm CAD MVP, workspace/reach 500mm.
```

期望：

```text
kinematics_source = scaled_profile
profile = ur3e
target_reach = 500mm
scale_factor != 1
```

### 4.10 Tests

新增测试：

```text
tests/test_layout_agent_schema.py
tests/test_layout_agent_ur3e_decision.py
tests/test_robot_sdk_layout_decision_adapter.py
tests/test_robot_design_agent_layout_integration.py
tests/test_robot_sdk_cq_parts_morphology.py
tests/test_kinematics_agent_ur3e_scaled_decision.py
```

关键断言：

- UR3E exact/scaled profile 下：

```text
L2 -> upper_arm_link
L3 -> forearm_link
L4 -> wrist1_offset_housing
L5 -> wrist2_elbow_cylinder
L6 -> wrist3_tool_flange
```

- `layout_agent` 输出必须包含 reason/confidence/source_signals。
- `robot_design_agent` final_answer 必须显示 `layout_source = layout_agent`。
- fallback 时 final_answer 必须显示 `layout_source = rule_fallback`。
- `cq_parts.py` 对 `wrist2_elbow_cylinder` 不能生成普通直圆柱。
- `semantic_constraints_applied_to_solver` 仍必须可见，并明确区分 `local_subassembly` 与 `full_assembly`。

### 4.11 阶段 4 验收标准

- `layout_agent` 能输出结构化 `LayoutDecision`。
- `robot_design_agent` 能消费 `LayoutDecision` 并生成 `MechanicalLayout`。
- UR3E 请求不再只靠 `link_primitives.py` 的几何阈值硬判。
- L5/J6 不再被普通 link / 普通 cylinder 表达。
- `需求文档.md` 或 final_answer 能说明：
  - 运动学来源。
  - layout 决策来源。
  - robot family。
  - link/joint morphology。
  - 哪些假设仍然是 MVP 简化。
- 所有 robot 相关测试通过。

当前已补齐：

- `需求文档.md` 已包含 `MechanicalLayout 决策` 章节。
- 文档会记录 `layout_source`、`robot_family`、joint morphology、link morphology、layout assumptions 和 layout warnings。
- fallback 路径会显示 `layout_source = rule_fallback`，layout_agent 正常路径会显示 `layout_source = layout_agent`。

阶段 4 状态：

- layout / morphology / CAD builder / requirement document 主线已完成。
- `UR3E-like + reach 500mm` 误选 `exact_profile` 已在阶段 5 修复。
- 当前不应继续堆零件外形；下一步进入阶段 6 装配约束。

## 阶段 5：SDK 文档与 Agent 读入边界

阶段 5 的目标：在进入 constraint-driven assembly 前，先写清楚 `robot_sdk` 的分层、边界、禁忌和扩展点，让后续 agent 修改代码时不会再次混淆 DH、MechanicalLayout、CAD pose 和 assembly constraints。

### 5.1 新增 `docs/robot_sdk.md`

文档面向人和 agent 双读，必须覆盖：

```text
robot_sdk/types.py
  -> 数据契约

robot_sdk/kinematics/
  -> DH/profile/scaling/FK，只负责运动学

robot_sdk/layout/
  -> MechanicalLayout、frames、interfaces、morphology metadata

robot_sdk/assembly/
  -> PartFeature、AssemblyConstraint、未来 MateFrame / ConstraintRule

robot_sdk/cad/
  -> CadQuery parts / assembly / export

robot_sdk/validation/
  -> report-level validation
```

必须写清楚的原则：

- `DH/FK transform != CAD part placement`。
- `Kinematic frame != mechanical interface frame`。
- `link.body_axis != joint.axis`。
- `layout_agent` 输出 morphology / layout intent，不输出 CAD、STEP、绝对 pose。
- CAD builder 消费 `MechanicalLayout.metadata`，不自行推理 robot family。
- semantic constraints 未进入 solver 时必须显式标记，不能伪装成真正约束装配。

### 5.2 `robot_cad_design` skill 引用 SDK 文档

更新：

```text
skills/robot_cad_design/SKILL.md
```

规则：

- 任何涉及 `robot_sdk` 改造的任务，必须先阅读 `docs/robot_sdk.md`。
- 修改运动学时只进入 `robot_sdk/kinematics`。
- 修改 layout/morphology 时先看 `robot_sdk/layout` 和 `layout_agent`。
- 修改装配时先看 `robot_sdk/assembly` 和 `robot_sdk/cad/cq_assembly.py`。
- 禁止绕过 `MechanicalLayout` 直接从 DH 生成 CadQuery。

### 5.3 记录当前 SDK 工作流

文档必须包含当前真实主流程：

```text
RobotRequirement
  -> kinematics_agent
  -> KinematicModel
  -> layout_agent
  -> LayoutDecision
  -> decision_adapter
  -> MechanicalLayout
  -> cq_parts
  -> cq_assembly
  -> export
  -> validation
```

并明确当前装配状态：

```text
semantic constraints are resolved/reported
but solver currently uses fixed_layout_pose constraints
semantic_constraints_applied_to_solver = none/local_subassembly/full_assembly
```

### 5.4 阶段 5 前置回归

修复运动学 decision 回归：

```text
UR3E-like + reach 500mm
```

应优先：

```text
profile_like_template or scaled_profile
```

不应默认：

```text
exact_profile
```

除非用户明确要求“使用官方原始 UR3E 尺寸，不要缩放”。

### 5.5 阶段 5 验收标准

- `docs/robot_sdk.md` 存在，并能作为 agent 修改 SDK 前的必读文档。
- `robot_cad_design` skill 明确引用该 SDK 文档。
- 文档覆盖 SDK 分层、当前主流程、禁忌、已知限制和阶段 6 扩展点。
- `UR3E-like + reach 500mm` decision 不再误选 `exact_profile`。

阶段 5 状态：已完成。

当前已补齐：

- 新增 `docs/robot_sdk.md`，记录 SDK 分层、主流程、当前装配真实状态、禁忌和阶段 6 扩展点。
- 更新 `skills/robot_cad_design/SKILL.md`，要求涉及 SDK / CAD / 装配 / STEP package 的任务先读 `docs/robot_sdk.md`。
- `kinematics_agent` 增加 deterministic guard：当 LLM 把 `UR3E-like + custom reach` 误判成 `exact_profile` 时，自动改为 `profile_like_template` 或 `scaled_profile`。
- 新增回归测试覆盖 `UR3E-like 6DOF reach 500mm`。

## 阶段 6：Constraint-driven Assembly 语义装配约束驱动

阶段 6 的目标：把当前 “按 layout pose 摆好再 Fixed 锁死” 的装配方式，逐步升级为 “由 mate frame + assembly constraint 驱动局部子总成装配”。

当前问题依据：

- `cq_assembly.py` 已生成 semantic constraints，但实际 solver 使用的是 `_fixed_layout_pose_constraint_calls(...)`。
- `semantic_constraints_applied_to_solver` 必须从 bool 升级为 `none/local_subassembly/full_assembly`，当前阶段目标是让它真实表达 `local_subassembly`。
- `link.body_axis == joint.axis` 这类默认约束不适合 UR wrist 区域。

### 6.1 MateFrame 数据结构

新增：

```text
robot_sdk/assembly/mate_frames.py
```

核心结构：

```python
MateFrame:
  id
  part_id
  feature_id
  origin
  normal
  tangent / x_dir
  semantic
  source_frame
  metadata
```

要求：

- 不只依赖 CadQuery selector，例如 `<X`、`>X`。
- 每个 mate feature 必须有可验证的局部坐标系。
- `PartFeature` 继续保留，`MateFrame` 负责真正装配语义。

当前已补齐：

- 新增 `robot_sdk/assembly/mate_frames.py`。
- `MateFrame` 包含 `origin / normal / tangent / semantic / source_frame`。
- `build_mate_frame_catalog(layout)` 可从 `MechanicalLayout.part_features` 生成一一对应的 mate frame。
- mate frame 会基于 layout frame graph 计算全局 origin 和方向，后续不再只依赖 CadQuery selector 字符串。

### 6.2 Constraint Rule 升级

扩展：

```text
robot_sdk/assembly/constraints.py
robot_sdk/assembly/cq_adapter.py
```

新增或规范约束类型：

```text
MatePlane
MateAxis
MateFrame
Coaxial
Flush
Offset
FixedSeed
```

重点替换：

```text
link.body_axis -> joint.axis
```

改成：

```text
link.output_mate -> next_joint.input_mate
joint.output_mate -> link.input_mate
```

UR wrist 区域增加专门规则：

```text
wrist_cross_axis_interface
tool_mount_flange
wrist_elbow_output
```

当前已补齐：

- `AssemblyConstraintKind` 新增 `MatePlane / MateAxis / MateFrame / Coaxial / Flush / Offset / FixedSeed`。
- `robot_sdk/assembly/constraints.py` 新增 `AssemblyMateConstraintRule` 和 `AssemblyMateConstraintPlan`。
- 旧 `Plane / Axis / Fixed / Point` 会规范化为 `MatePlane / MateAxis / FixedSeed / MateFrame`。
- 现阶段仍保留旧 CadQuery adapter 路径，避免直接打断 STEP 导出。
- 新 constraint plan 会标记 `link.body_axis -> joint.axis` 这类危险旧约束，供阶段 6 后续替换。

### 6.3 局部子总成优先

不要一上来整机 constraint solve。

实现顺序：

1. `J6-L6-end_effector`
2. `J5-L5-J6`
3. `J4-L4-J5`
4. `J3-L3-J4`
5. wrist group
6. full assembly

每个子总成输出：

```text
subassembly solve status
constraint count
residual report
pose delta report
```

当前已补齐：

- 新增 `robot_sdk/assembly/local_solve.py`。
- `default_local_subassembly_specs(layout)` 会优先生成：
  - `J6-L6-end_effector`
  - `J5-L5-J6`
  - `J4-L4-J5`
  - `J3-L3-J4`
  - `wrist_group_J4_to_end_effector`
- 这些 candidate 已迁移为 `layout_agent` 的 `LayoutDecision.subassemblies` 决策输出。
- `robot_sdk/layout/decision_adapter.py` 会把 `LayoutDecision.subassemblies` 写入 `layout.metadata["local_subassemblies"]`。
- `default_local_subassembly_specs(layout)` 会优先消费 `layout.metadata["local_subassemblies"]`，只有缺少 agent 决策时才使用 SDK deterministic fallback。
- `build_local_subassembly_plans(layout)` 会筛选局部子总成内部约束。
- 安全策略：
  - 保留 `joint.output_flange -> link.input_face` 这类 plane mate。
  - 保留 `link.output_face -> next joint / end_effector mount` 这类 plane mate。
  - 已用 interface tangent mate 替换 `link.body_axis -> joint.axis` 和 `joint.axis -> link.body_axis` 这类危险旧轴约束。
- 局部 plan 会记录 `applied_constraint_ids / skipped_constraint_ids / skipped_reasons`。
- `MechanicalLayout` 现在为接口面生成 `*_tangent` axis feature，例如：
  - `joint.output_tangent -> link.input_tangent`
  - `link.output_tangent -> joint.bottom_tangent`
  - `link.output_tangent -> end_effector.mount_tangent`
- local subassembly solve 现在会应用这些 interface tangent axis 约束，而不是跳过旧 body-axis 约束。
- local subassembly plan 会记录每条已应用约束的 mate-frame residual：
  - `origin_delta_mm`
  - `normal_angle_deg`
  - `tangent_angle_deg`

### 6.4 Assembly Mode 设计

显式区分：

```text
assembly_mode:
  fixed_layout_pose
  local_constraint_solve
  full_constraint_solve
```

首版默认：

```text
local_constraint_solve
```

但只启用 wrist 局部子总成，整机可继续使用 fixed seed 保底。

当前已补齐：

- 新增 `robot_sdk/cad/cq_local_solve.py`。
- `solve_local_subassemblies(layout)` 会为每个局部子总成创建独立 CadQuery `Assembly`。
- 每个局部子总成使用：
  - anchor part 的 `FixedSeed`。
  - 局部安全 semantic constraints。
- 输出 `CadQueryLocalSubassemblySolveResult`，包括：
  - `solved`
  - `solve_error`
  - `constraint_calls`
  - `applied_constraint_ids`
  - `skipped_constraint_ids`
  - `residual_reports`
  - `pose_delta_report`
  - `part_locations`
  - `semantic_constraint_application = local_subassembly`
- 当前整机 `cq_assembly.py` 仍保持 fixed layout pose，不切换为 full constraint solve；局部语义约束解通过 local solved subassembly 导出。
- `cq_assembly.py` 已默认运行 local subassembly solve，并把结果写入总装 metadata：
  - `local_subassembly_solve_requested`
  - `local_subassembly_count`
  - `local_subassembly_solved_count`
  - `local_subassembly_failed_count`
  - `local_subassembly_names`
  - `local_subassemblies`
- `export_robot_step_package(...)` 已支持按 `source_local_subassembly_name` 直接导出 local solved assembly。
- `robot_design_agent/rules.py` 会把 solved local subassemblies 追加进 STEP package，并使用 CAD 语义命名，例如 `joint2_link2_end_effector`。
- STEP export result 已记录 best-effort bbox。
- validation 已检查：
  - 单个 export bbox 是否可获取、非退化。
  - STEP package 中整机 bbox 是否非退化。
  - 子总成 bbox 是否非退化。
  - 子总成 bbox 是否落在整机 bbox 范围内；当前越界先 warning，不直接 fail。
- `robot_sdk/assembly/constraint_graph.py` 已提供 local subassembly constraint graph 校验：
  - disconnected / isolated parts 作为 underconstrained warning。
  - graph cycle 作为 cycle warning。
  - 单个 part-pair 超过 2 条 semantic constraints 作为 dense pair / potential overconstraint warning。
- `evaluate_full_assembly_solve_gate(...)` 已把 graph warning 汇总成 full-assembly solve fixture 前置门禁：
  - `clear_for_fixture`：局部子总成图检查通过，可以开始最小 full-assembly fixture 试验。
  - `blocked`：存在 underconstrained / cycle / dense pair 风险，不应进入 full solve。
- `validate_assembly_semantics(layout)` 已接入 constraint graph report。
- `robot_design_agent` 已把 constraint graph summary 写入：
  - `需求文档.md` 的 `Assembly Constraint Graph` 小节。
  - 最终回答里的 `Constraint graph` 和 `Full assembly solve gate` 行。
- validation 已输出 `full_assembly_solve_gate_clear_for_fixture` / `full_assembly_solve_gate_blocked`。
- `Full assembly solve gate` 是 fixture 前置门禁，不代表整机 full constraint solve 已启用。
- `robot_sdk/cad/cq_full_solve.py` 已新增 `solve_full_assembly_fixture(layout)`：
  - gate 通过时，创建独立 CadQuery Assembly。
  - 以 `base` 作为 fixed seed。
  - 应用全部 semantic mate constraints。
  - 记录 `pose_delta_report`、全量 semantic mate `residual_reports`、constraint 数量、solve error 和 gate metadata。
- `build_cadquery_assembly(layout)` 已默认运行 full fixture，并把 `full_assembly_fixture_*` 写入总装 metadata。
- validation 已输出 `full_assembly_fixture_solve_passed` / `full_assembly_fixture_blocked` / `full_assembly_fixture_solve_failed`。
- validation 已对 full fixture 的 translation pose delta / origin residual / normal-tangent angle residual 设置 promotion 阈值：
  - 指标全部在阈值内会输出 `full_assembly_fixture_ready_for_promotion`。
  - 超过 warning 阈值会输出 `full_assembly_fixture_threshold_warning`。
  - 超过 error 阈值会输出 `full_assembly_fixture_threshold_exceeded`；当前仍是 warning，只阻止升级，不让 fixed-pose 生产导出失败。
- `base.top -> J1.bottom` residual 热点已通过 `base_top_mount_face` 显式 frame 修复：
  - `base.top` 挂到基座顶面 frame。
  - 首关节 `J1.bottom` 的 incoming interface 与该顶面 frame 对齐。
  - 默认 2DOF tabletop layout 的 full fixture residual 已进入 `full_assembly_fixture_ready_for_promotion`。
- `base_J1_mount` 已从 promotion fixture 升级为 production local semantic subassembly：
  - `part_ids = ["base", "J1"]`。
  - `anchor_part_id = "base"`。
  - 应用 `base_top_to_J1_bottom_plane` 与 `base_axis_to_J1_axis`。
  - local solve residual / pose delta 为 0，并会随 STEP package 作为 local solved subassembly 导出。
- local solve metadata 已透传 `LocalSubassemblySpec.metadata`：
  - 总装 metadata 的 `local_subassemblies[]` 会记录 `role`、`source` 和 `spec_metadata`。
  - validation 会输出 `local_subassembly_ready_for_promotion`，列出全部达到 promotion 阈值的局部子总成。
  - validation 会输出 `joint_link_joint_promotion_ready`，确认至少一个 `joint-link-joint` 子总成已经达到 promotion 阈值。
  - 默认 2DOF tabletop layout 中 `J1_L1_J2` 已作为首个 `joint-link-joint` promotion-ready 回归样例。
- STEP package export 已接入 promotion-ready 判定：
  - `production_local_solve`：达到 promotion 阈值的 local solved subassembly，可作为生产局部子总成导出。
  - `experimental_local_solve`：未达到 promotion 阈值但 solve 成功的 local solved subassembly，只作为实验/对照导出。
  - `robot_design_agent` final answer 会显示 production / experimental local solved export 数量。
- full fixture assembly 只用于实验报告，不用于生产整机 STEP 导出。
- 如果至少一个局部子总成 solve 成功，总装 metadata 会记录 `semantic_constraint_application = local_subassembly`。
- `semantic_constraints_applied_to_solver` 已升级为枚举值；当前 local solve 成功时记录为 `local_subassembly`，表示语义约束已进入局部 solver，但整机 full assembly solver 仍未启用。

### 6.5 Assembly Validation

新增：

```text
robot_sdk/validation/assembly.py
```

检查：

- mate frame 是否缺失。
- constraint 是否欠约束。
- constraint 是否过约束。
- solver 后 mate residual。
- solver 后 part pose delta。
- STEP 导出后 bbox 是否异常。
- semantic constraints 是否真的 applied to solver。

`semantic_constraints_applied_to_solver` 已从 bool 升级为：

```text
none
local_subassembly
full_assembly
```

当前已补齐：

- 新增 `robot_sdk/validation/assembly.py`。
- `validate_assembly_semantics(layout)` 会检查 mate frame 完整性、constraint plan 可解析性和危险旧轴约束。
- `robot_sdk/validation/basic.py` 已接入 assembly semantic validation。
- `robot_sdk/validation/basic.py` 已读取 `cad_result.metadata["semantic_constraint_application"]`。
- validation report 已能输出：
  - `local_subassembly_solve_passed`
  - `local_subassembly_solve_failed`
  - `local_subassembly_solve_not_run`
- validation report detail 已包含：
  - `max_local_pose_delta_mm`
  - `max_local_origin_residual_mm`
  - `max_local_normal_residual_deg`
  - `max_local_tangent_residual_deg`
- end-effector mount frame 已按最后一段 link 输出方向定向，terminal tool mate frame 不再产生 90 度 normal/tangent residual。
- validation report 已对 local solve 的 translation pose delta / origin residual / normal-tangent angle residual 设置阈值：
  - 超过 warning 阈值会输出 `local_subassembly_residual_threshold_warning`。
  - 超过 error 阈值会输出 `local_subassembly_residual_threshold_exceeded`，并让报告 fail。
- `robot_sdk/cad/cq_assembly.py` metadata 新增：
  - `mate_frame_count`
  - `mate_constraint_rule_count`
  - `unsafe_mate_constraint_count`
  - `semantic_constraint_application = none/local_subassembly`
  - `semantic_constraints_applied_to_solver = none/local_subassembly/full_assembly`
- 当前生产整机仍不是 `full_assembly`，不伪装成 full constraint solve。

### 6.6 Tests

新增测试：

```text
tests/test_robot_sdk_mate_frames.py
tests/test_robot_sdk_constraint_rules.py
tests/test_robot_sdk_local_constraint_solve.py
tests/test_robot_sdk_wrist_subassembly_solve.py
tests/test_robot_design_agent_constraint_assembly.py
```

关键断言：

- `J5-L5-J6` 子总成能生成 mate constraints。
- `L5.output_mate` 与 `J6.input_mate` 被真正用于 solver。
- `semantic_constraints_applied_to_solver = local_subassembly`。
- fixed layout pose 仍可作为 fallback，但不能伪装成语义装配。
- solver 后 translation pose delta / origin residual / normal-tangent angle residual 超过阈值时 validation fail。
- UR3e-like wrist 不再只靠绝对 placement 拼起来。

### 6.7 暂不做

- 不做工业级真实零件结构。
- 不做碰撞/强度/电机选型。
- 不直接启用整机 full constraint solve。
- 不让 `layout_agent` 直接输出 CadQuery constraint。
- 不继续用 `link.body_axis == joint.axis` 修 wrist 装配。

## 阶段 7：Structure-first / Source-first Robot CAD

阶段 7 的目标：修复 `6DOF + reach 500mm` 这类请求生成“一串水平零件”的根因。问题不在 STEP 导出，而在结构规划没有真正落到三维骨架。参考 `text-to-cad` 的思路，CathyAgent 后续应把 CAD 视为 **可复核源代码生成物**：先生成结构计划和 CAD source，再导出 STEP，并强制做几何 / 装配 / 视觉复核。

### 问题判断

当前失败样例：

```text
用户：建模 6DOF 机械臂，工作空间/工作半径 500mm
实际：
  kinematics source = template_fallback
  DH = J1 150mm + J2-J6 70mm
  alpha = 0, d = 0 for all joints
  layout_agent 输出 shoulder/elbow/wrist 标签
  MechanicalLayout 仍是平面串联 skeleton
  STEP 外观变成水平串珠结构
```

因此后续不能只修 `cq_parts.py` 外形，也不能只增加 local constraint solve。必须先让结构计划能改变：

- joint axis sequence。
- station / frame position。
- shoulder / elbow / wrist 的三维相对布局。
- link route 与 envelope。
- datum / mating frame graph。

### 7.1 禁止 6DOF planar fallback 直接出 CAD

新增 validation gate：

```text
robot_sdk/validation/structure.py
```

规则：

- `dof >= 5` 且所有 `alpha ~= 0`、`d ~= 0`、joint axes parallel 时，不能通过 CAD production validation。
- `6DOF + robot arm + CAD` 如果没有型号 profile，必须使用 `generic_6axis_cobot_profile` 或明确失败。
- `template_fallback` 只能用于 2-4DOF early MVP 或用户明确要求“平面串联示意”。
- validation code 建议：
  - `six_axis_planar_template_rejected`
  - `joint_axis_topology_invalid`
  - `structure_plan_missing`
  - `robot_family_structure_mismatch`

这一步的验收标准：

- 截图中那种全水平串珠 STEP 不能再报告 `validation ok`。
- `需求文档.md` 必须明确写出 structure validation failure。

当前实现状态：

- 已新增 `robot_sdk/validation/structure.py`。
- 已新增 `six_axis_planar_template_rejected`、`structure_plan_missing`、`axis_topology_valid` 等 structure validation code。
- `robot_sdk/validation/basic.py` 已接入 structure validation。
- `robot_design_agent` 已在 CAD 生成前运行 structure validation；当 `6DOF/high-DOF + planar template_fallback` 被识别时，会停止 CAD 导出并返回结构规划失败。
- 低自由度 2-4DOF MVP 仍允许使用 planar template fallback。

### 7.2 新增 `RobotStructurePlan`

新增数据层：

```text
robot_sdk/structure/robot_structure_plan.py
```

建议 schema：

```python
RobotStructurePlan:
  id: str
  robot_family: str
  source: "layout_agent" | "sdk_profile" | "rule_fallback"
  confidence: float
  base_frame: StructureFrame
  joint_axes: list[JointAxisPlan]
  stations: list[StationPlan]
  links: list[LinkRoutePlan]
  subassemblies: list[StructureSubassemblyPlan]
  datum_plan: list[DatumPlan]
  assumptions: list[str]
  warnings: list[str]

JointAxisPlan:
  joint_id: str
  role: base_yaw / shoulder_pitch / elbow_pitch / wrist_pitch / wrist_roll / tool_roll
  origin: tuple[float, float, float]
  axis: tuple[float, float, float]
  parent_station: str
  child_station: str

StationPlan:
  id: str
  role: base / shoulder / elbow / wrist1 / wrist2 / wrist3 / tool
  origin: tuple[float, float, float]
  x_dir: tuple[float, float, float]
  z_dir: tuple[float, float, float]

LinkRoutePlan:
  link_id: str
  role: base_column / upper_arm / forearm / wrist_offset / wrist_spacer / tool_stub
  from_station: str
  to_station: str
  route_kind: straight / offset / elbow / coaxial_spacer
  envelope: dict

DatumPlan:
  part_id: str
  datum_id: str
  kind: mount_face / input_flange / output_flange / joint_axis / bolt_circle
  local_frame: Transform
```

边界：

- `RobotStructurePlan` 是 `MechanicalLayout` 之前的独立中间层。
- 不把完整结构决策塞进 `MechanicalLayout` 主字段。
- `MechanicalLayout.metadata["structure_plan_id"]` 可以记录来源。

当前实现状态：

- 已新增 `robot_sdk/structure/robot_structure_plan.py`。
- 已实现 `StructureStation`、`JointAxisPlan`、`LinkRoutePlan`、`DatumPlan`、`StructureSubassemblyPlan`、`RobotStructurePlan`。
- 已实现 `build_generic_6axis_cobot_structure_plan(reach_mm=...)`，生成 base / shoulder / elbow / wrist1 / wrist2 / wrist3 / tool stations，以及非平行 6 轴拓扑。
- `RobotStructurePlan` 已进入 `MechanicalLayout` adapter，并由 `robot_design_agent` 主流程优先消费。

### 7.3 `layout_agent` 升级为结构计划 agent

当前 `layout_agent` 主要输出 morphology label。阶段 7 要求它输出 `RobotStructurePlan`。

新的工作流：

```text
layout_agent
  -> read KinematicModel / requirement / profile metadata
  -> build candidate axis topology
  -> build station plan
  -> build link route plan
  -> build datum plan
  -> validate structure plan
  -> return RobotStructurePlan + trace
```

Prompt 必须新增要求：

- 不能只输出 label。
- 不能把 all-zero planar DH 当成合理 6DOF manipulator。
- 对 generic 6DOF 必须生成非平行轴系：
  - J1: vertical base yaw。
  - J2/J3: shoulder/elbow pitch。
  - J4/J5/J6: wrist orientation group，至少包含 cross-axis wrist。
- 如果无法生成可信结构计划，必须失败或降级为“示意图”，不能进入 production CAD。

当前实现状态：

- `subagents/layout_agent/schema.py` 已允许输出可选 `structure_plan`。
- `LayoutAgentResult` 已新增 `structure_plan: RobotStructurePlan | None`。
- `layout_agent` 的 LLM JSON 如果携带 `structure_plan`，会解析成 `RobotStructurePlan`。
- 当 LLM 未提供结构计划时，6DOF/high-DOF fallback 会由 SDK 生成 `RobotStructurePlan`，避免继续依赖平面 skeleton。
- `layout_agent` trace 已记录 `structure_plan` 和 `structure_validation`。
- prompt 已明确要求：高自由度机械臂不能只输出 morphology label，必须输出非平面 axis/station/link route/datum 结构计划。
- `RobotStructurePlan` 到 `MechanicalLayout` 的转换已由 `structure_adapter.py` 首版实现，并已接入 `robot_design_agent` 主流程。

### 7.4 `generic_6axis_cobot_profile`

新增 SDK 内置 generic profile：

```text
robot_sdk/kinematics/profiles.py
```

用途：

- 用户说“6DOF 机械臂，工作空间 500mm”但未指定 UR/PUMA/厂家时使用。
- 不声称官方 DH。
- 生成一个合理的 6 轴协作臂结构比例。

建议结构比例：

```text
base height: reach * 0.16
upper arm:   reach * 0.34
forearm:     reach * 0.30
wrist stack: reach * 0.12
tool stub:   reach * 0.08
```

注意：

- profile 可以提供 kinematic sizing，但不应直接作为 CAD mate frame。
- profile 必须输出 source summary：`generic_6axis_cobot_profile, not manufacturer DH`。

当前实现状态：

- 先落在 `robot_sdk/structure/robot_structure_plan.py` 的 `build_generic_6axis_cobot_structure_plan()`，作为结构拓扑 profile。
- 尚未把 generic profile 接入 `kinematics_agent` 的 route decision；在 adapter 完成前，不让它直接绕过 `MechanicalLayout` 输出 production CAD。

### 7.5 `structure_adapter.py`

新增 adapter：

```text
robot_sdk/layout/structure_adapter.py
```

职责：

```text
RobotStructurePlan
  -> MechanicalLayout.frames
  -> JointLayout.axis_frame
  -> LinkLayout.body_frame / input_interface / output_interface
  -> PartFeature datum
  -> AssemblyConstraint
```

规则：

- `MechanicalLayout` 不再从 DH `a/d` 线性排布 6DOF part。
- joint axis 来自 `JointAxisPlan.axis`。
- link body route 来自 `LinkRoutePlan`。
- datum feature 来自 `DatumPlan`。
- 如果 structure plan 缺失 datum，layout validation fail。

当前实现状态：

- 已新增 `robot_sdk/layout/structure_adapter.py`。
- 已实现 `build_mechanical_layout_from_structure_plan(model, structure_plan)`。
- adapter 会从 `RobotStructurePlan` 生成：
  - `MechanicalLayout.frames`
  - `JointLayout.axis_frame`
  - `LinkLayout.body_frame / input_interface / output_interface`
  - datum `PartFeature`
  - `AssemblyConstraint`
- joint axis 已来自 `JointAxisPlan.direction`。
- link route 已写入 `LinkLayout.metadata["structure_route_type"]`，并映射到 CAD 可消费的 `primitive_type`。
- `RobotStructurePlan` 会写入 `MechanicalLayout.metadata["structure_plan"]`。
- `robot_design_agent` 已接入 `layout_agent.structure_plan -> structure_adapter -> CAD` 主流程。
- 当 `layout_agent` 返回 `structure_plan` 时，主流程优先调用 `build_mechanical_layout_from_structure_plan(...)`，再应用 morphology `LayoutDecision`。
- `validate_robot_design(...)` 已接收并校验主流程中的 `structure_plan`。
- `需求文档.md` 和最终回答会显示 `Template: structure_plan` / `Layout template: structure_plan`。

### 7.6 CAD Source-first

参考 `text-to-cad` 的方向，每个 session 不只导出 STEP，还要导出 CAD source：

```text
session_id/
  robot_model.py
  structure_plan.json
  mechanical_layout.json
  需求文档.md
  整机.step
  ...
```

原则：

- CAD source 是 primary artifact。
- STEP 是 derived artifact。
- 后续修结构必须改 `robot_model.py` 或 structure/layout source，不 patch STEP。

首版可以继续用 CadQuery source；不强制立刻迁移 build123d。但 source 里必须有：

- part-local datum 定义。
- named joint/link/body。
- assembly constraints / placements 的来源注释。
- export manifest。

当前实现状态：

- `robot_design_agent` 成功路径已在 session/output 目录导出 source-first artifacts：
  - `structure_plan.json`
  - `mechanical_layout.json`
  - `robot_model.py`
- `structure_plan.json` 在没有 `RobotStructurePlan` 的低自由度 legacy 路径中也会稳定存在，并写入 `structure_plan: null` 与原因。
- `mechanical_layout.json` 来自当前实际用于 CAD 的 `MechanicalLayout.to_dict()`。
- `robot_model.py` 是 session source manifest，可加载同级 `structure_plan.json` 与 `mechanical_layout.json`，并打印当前设计摘要。
- final answer 会显示 `Source artifacts: ...`，trace 会记录 `source_artifacts`。

### 7.7 Datum / Joint Driven Assembly

新增等价于 `AssemblyHelper` 的本地层：

```text
robot_sdk/assembly/datum_assembly.py
```

概念：

```text
PartDatum
  part_id
  datum_id
  local_origin
  local_x_dir
  local_z_dir

AssemblyMate
  fixed_part
  fixed_datum
  moving_part
  moving_datum
  relation: face_to_face / coaxial / revolute_axis / offset
```

导出前必须能报告：

- mate residual。
- axis residual。
- unconnected part。
- overconstrained pair。

这一步替换当前“全局 fixed pose 直接摆”的主逻辑，先用于子总成，再用于整机。

当前实现状态：

- `MechanicalLayout` 已输出 datum / interface `PartFeature` 与中立 `AssemblyConstraint`。
- `robot_sdk/assembly/mate_frames.py`、`constraints.py`、`cq_adapter.py` 已把这些 datum / constraint 规则整理成 CadQuery adapter 可消费的 mate rule。
- 生产整机 STEP 当前仍使用 deterministic fixed placement，避免把尚未完全验证的 full semantic solve 误当成生产装配结果。
- 局部子总成已使用 semantic constraints 求解，并记录 residual / pose delta。
- full-assembly semantic solve 已作为独立 fixture 运行，只用于升级评估，不直接替代生产整机导出。
- validation 已汇总：
  - `local_subassembly_ready_for_promotion`
  - `joint_link_joint_promotion_ready`
  - `full_assembly_fixture_ready_for_promotion`
  - `stage7_robot_cad_ready`
  - `stage7_robot_cad_not_ready`

阶段 7 的完成口径：不要求此阶段把生产整机切换为 `full_assembly` 求解；要求 source-first 结构、局部 semantic solve、full fixture、STEP package、真实 renderer-backed snapshot、structure/visual gate 同时通过，才报告 `stage7_robot_cad_ready`。

### 7.8 强制复核：Structure / Geometry / Visual

新增：

```text
robot_sdk/validation/structure.py
robot_sdk/validation/visual.py
tools/inspect_step.py
```

复核项：

- DOF 是否等于需求。
- reach 是否覆盖需求。
- joint axis topology 是否合理。
- station sequence 是否像对应 robot family。
- link route 是否与 datum graph 一致。
- STEP BBox 是否异常。
- 每个子总成 bbox 是否在整机 bbox 内。
- 是否存在明显水平串珠结构。
- 自动生成至少一个 snapshot/SVG/PNG 预览，供 agent 视觉复核。

新增 validation code：

```text
structure_plan_valid
axis_topology_valid
axis_topology_invalid
visual_snapshot_generated
visual_review_required
planar_serial_chain_rejected
```

当前实现状态：

- 已新增 `robot_sdk/validation/visual.py`。
- 已在 `robot_sdk/validation/basic.py` 接入 visual geometry screening。
- 当前 visual check 是 renderer-free proxy：根据 `MechanicalLayout` debug report、structure intent、joint station extents、axis family count、STEP package bbox 判断外观拓扑风险。
- `dof >= 5`、无 `RobotStructurePlan`、关节站位呈水平细长串联、轴系高度平行时，会报错 `planar_serial_chain_visual_rejected`。
- `structure_plan` 路径不会被旧 tabletop visual rule 误伤。
- 已新增 `robot_sdk/cad/snapshot.py`，支持对 STEP package 生成 `front/top/isometric` SVG snapshots。
- snapshot 首选 `CadQuery importStep -> SVG export`，失败时生成 bbox SVG fallback，并记录 `renderer` / `fallback_used` / `error`。
- 已新增 `tools/inspect_step.py`，可单独对 STEP 文件生成 SVG 复核图。
- `robot_design_agent` 已在 STEP package 导出后生成 snapshot，并把 snapshot trace、final answer summary、`需求文档.md` 复核段写入 session 输出。
- validation 已新增：
  - `visual_snapshot_generated`
  - `visual_snapshot_fallback_only`
  - `visual_snapshot_fallback_used`
  - `visual_snapshot_missing`
  - `visual_snapshot_generation_failed`
- 已新增 SVG silhouette 自动判据：
  - 解析 renderer-backed SVG 中的 path 线段投影范围。
  - 计算 `aspect_ratio` / `height_ratio` / `point_count`。
  - 对高自由度机器人，如果 front/isometric snapshot 呈极端细长扁平形态，会报错：
    - `snapshot_horizontal_silhouette_rejected`
    - `snapshot_structure_silhouette_rejected`
  - 正常比例会记录 `visual_snapshot_silhouette_screened`。
- 已新增 structure visual cues 自动判据：
  - 读取 `RobotStructurePlan` 或 `MechanicalLayout.metadata["structure_plan"]` 中的 stations。
  - 检查 base / shoulder / elbow / wrist1 / wrist2 / wrist3 / tool 角色是否齐全。
  - 计算 station 的水平展开、高度展开、`station_z_to_horizontal_ratio` 和 elevated station 数量。
  - 关键角色缺失时报错 `structure_visual_roles_missing`。
  - 高自由度结构计划被压平成近似水平结构时报错 `structure_body_elevation_rejected`。
  - 正常结构记录 `structure_visual_cues_screened`。
- 已将 snapshot 从 whole-machine 扩展到重点子总成：
  - `robot_sdk/cad/snapshot.py` 支持 `subassembly_names`，可以只生成选定子总成 SVG。
  - `robot_design_agent.rules` 统一选择 snapshot 关注对象，优先 local-solved 多零件子总成、terminal tool、wrist/forearm/end-effector，跳过 base-only mount。
  - `需求文档.md` 的 `CAD Visual Snapshot` 小节已拆成 `Whole Machine` 和 `Focus Subassemblies` 两张表。
  - final answer 的 `Visual snapshots` 会显示 whole/subassembly snapshot 计数。
- 已新增重点子总成局部 silhouette 判据：
  - SVG metrics 已带 `target_name`，可区分整机和局部子总成。
  - 对 wrist / forearm / tool / end_effector / 多零件 local subassembly 的 front/isometric snapshot 做局部比例检查。
  - 局部子总成过度细长或退化时报错 `focus_subassembly_silhouette_rejected`。
  - 正常局部子总成记录 `focus_subassembly_snapshots_screened`。
- CAD builder 已开始更深入消费 `LinkRoutePlan`：
  - `structure_adapter.py` 会把 `route_vector`、`route_offset_vector`、`waypoints` 写入 `LinkLayout.metadata["link_primitive"]`。
  - `cq_parts.py` 的 offset/elbow link 会优先读取 route offset，决定局部桥接/分支朝 Y 还是 Z，而不是固定朝一个方向。
  - 这一步先解决“结构计划已有 route/waypoint，但 CAD 外形仍按固定模板弯折”的问题。
- CAD builder 已开始消费 datum/interface 语义生成可见外形：
  - `cq_parts.py` 会统计每个 part 的 `feature_semantics` 与 `datum_geometry_applied`。
  - joint 的 `joint_axis` datum 会生成可见轴芯 reference。
  - end-effector 的 `tool_mount` datum 会生成 tool register / pilot。
  - base 的 `mount_face` datum 会生成 mount reference pad。

验收标准：

- 截图中的错误结构必须 fail。
- 6DOF generic profile 至少生成 base/shoulder/elbow/wrist/tool 的三维结构。
- final answer 不能只说“STEP 已生成”，必须给出 structure validation 结论。

下一步：

- 阶段 7 MVP 验收门已完成：`validate_robot_design(...)` 会在高自由度 / structure-plan 路径上汇总 source-first、structure、layout、constraint、local solve、full fixture、export package、renderer-backed snapshot 和 visual cue 的通过状态。
- `robot_design_agent` 最终回答已显示 `Stage 7 gate: ready / not ready / not applicable`。
- `visual_snapshot_fallback_only` 仍保持 warning；没有真实 STEP renderer-backed snapshot 时，不会报告 `stage7_robot_cad_ready`。
- 后续阶段再决定是否把生产整机从 fixed placement 推进到 full semantic assembly solve。

### 7.9 Tests

新增测试：

```text
tests/test_robot_sdk_structure_plan.py
tests/test_layout_agent_structure_plan.py
tests/test_robot_sdk_structure_adapter.py
tests/test_robot_sdk_structure_validation.py
tests/test_robot_design_agent_source_first.py
tests/test_robot_design_agent_rejects_planar_6dof.py
```

关键断言：

- `6DOF + reach 500mm` 不再生成 all-zero planar DH production CAD。
- `RobotStructurePlan` 包含 base、shoulder、elbow、wrist、tool stations。
- J1 轴与 J2/J3 轴不全平行。
- L2/L3 不再只是水平串珠 link。
- session 输出包含 `robot_model.py`、`structure_plan.json`、`mechanical_layout.json`。
- 结构错误时 `robot_design_agent.finished == False`。

### 7.10 暂不做

- 不做真实工业外壳。
- 不做电机/减速器选型。
- 不做动力学力矩闭环。
- 不做运动规划和碰撞检测。
- 不让 LLM 直接输出最终 CadQuery 代码并绕过 SDK schema。

## 阶段 8：Production Full Semantic Assembly Solve

阶段 8 的目标：把当前“production 整机 fixed layout pose + local semantic solve + full fixture 验证”推进为可选的 production full semantic assembly solve。核心不是继续美化零件，而是让整机 STEP 的装配来源可声明、可验证、可失败。

### 8.1 Assembly Source 显式化

当前已完成：

- `build_cadquery_assembly(...)` 新增 `production_assembly_source`：
  - `full_semantic_solve`：默认，整机 production STEP 走 full semantic solve gate。
  - `fixed_layout_pose`：显式保守回退路径。
- `full_semantic_solve` 只有在 full-assembly semantic fixture gate clear 且 solve 成功时，才会成为 production assembly。
- 请求 `full_semantic_solve` 但 gate blocked / solve failed 时，result 会失败并记录 `production_full_semantic_solve_error`，不会静默退回 fixed pose。
- `CadQueryAssemblyResult.metadata` 已新增：
  - `requested_production_assembly_source`
  - `production_assembly_source`
  - `production_full_semantic_solve_requested`
  - `production_full_semantic_solve_used`
  - `production_full_semantic_solve_error`
  - `production_full_semantic_solve_gate_status`
  - `production_full_semantic_solve_pose_delta_report`
  - `production_full_semantic_solve_residual_reports`
- `export_robot_step_package(...)` 已记录 `whole_machine_assembly_source`。
- `robot_design_agent` input schema 已允许传入 `production_assembly_source`，默认 `full_semantic_solve`，最终回答会显示 `Production assembly source` 和 `Production full semantic solve`。

验收：

- 默认流程输出 full semantic solve production STEP。
- 显式请求 full semantic solve 且 fixture 通过时，`semantic_constraints_applied_to_solver = full_assembly`。
- 显式请求 full semantic solve 但 fixture blocked 时，validation fail，不导出伪 full solve。

### 8.2 Validation Gate

当前已完成：

- `validate_robot_design(...)` 已新增：
  - `production_full_semantic_solve_used`
  - `production_full_semantic_solve_failed`
  - `production_full_semantic_solve_ready`
  - `production_full_semantic_solve_residual_warning`
  - `production_full_semantic_solve_residual_exceeded`
- full semantic solve 被用于 production 时不再触发 `semantic_constraints_not_applied` warning。
- full semantic solve residual / pose delta 已独立成 production gate，不再只复用 fixture 阈值。
- residual 超过 warning 阈值时 report 仍 ok，但不能标记为 ready。
- residual 超过 error 阈值时 report fail，并在 `contradictory_residual_candidates` 中列出疑似冲突 mate。
- `stage7_robot_cad_ready` 与 `production_full_semantic_solve_ready` 已分开，避免把 MVP ready 误读成 production full solve ready。

### 8.3 Whole-Machine Constraint Graph

当前已完成：

- `robot_sdk/assembly/constraint_graph.py` 已新增 `WholeAssemblyConstraintGraph`。
- `build_constraint_graph_report(..., layout=layout)` 会同时输出：
  - local subassembly graphs。
  - whole-machine graph。
- whole-machine graph 会检查：
  - 全部 production parts 是否连通。
  - base 到 end_effector 是否存在完整约束路径。
  - 是否有 isolated part。
  - 是否有 dense pair / cycle。
- `evaluate_full_assembly_solve_gate(...)` 已同时读取 local graph 和 whole graph；任一 underconstrained / cyclic / dense pair 都会 block full fixture 和 production full semantic solve。
- `validate_assembly_semantics(...)` 已新增：
  - `whole_assembly_graph_valid`
  - `whole_assembly_graph_underconstrained`
  - `whole_assembly_graph_cycle`
  - `whole_assembly_graph_dense_pairs`
- `robot_design_agent` 的 constraint graph summary / 需求文档表格已包含 `whole_assembly` 行。

当前完成边界：

- graph gate 负责检查整机装配链是否连通。
- production residual gate 负责检查 full solve 后的 pose delta / mate residual 是否可接受。
- 疑似 contradictory mate 先按 residual error candidate 报告；更复杂的语义冲突分类留到后续工业化阶段。

### 8.4 默认策略（历史状态，阶段 10 将替换）

阶段 8 曾把默认策略切为：

```text
production_assembly_source = full_semantic_solve
```

保留显式回退：

```text
production_assembly_source = fixed_layout_pose
```

如果后续真实 CadQuery / 复杂 6DOF 回归出现 solver 漂移，可临时显式回退 fixed pose，但不能把 fixed pose 伪装成 full semantic solve。

切换默认值的当前依据：

- 2DOF tabletop 回归通过。
- generic 6DOF structure-plan 回归通过。
- wrist / forearm / terminal tool 重点子总成通过。
- full semantic solve 的 residual / pose delta 在 production 阈值内。
- STEP snapshot 视觉复核通过。

阶段 8 完成口径仍保留为历史记录，但不再作为下一阶段实现方向：

- 默认 production 是 `full_semantic_solve`。
- 用户或上层 agent 可显式请求 `fixed_layout_pose` 作为保守回退。
- full solve 必须同时通过 local graph、whole graph、fixture solve 和 production residual gate。
- 未通过时 validation fail，不能导出伪 full solve。
- 通过时 validation 输出 `production_full_semantic_solve_ready`。

阶段 10 开始后，新的目标是：

```text
production_assembly_source = source_joint
```

`full_semantic_solve` 降级为 experimental / regression 对照路径。

## 阶段 9：Semantic Assembly Compiler / 语义装配编译器（冻结）

阶段 9 原目标是修正阶段 8 暴露出的根因：`full_semantic_solve` 不能只依赖 CadQuery 全局 solver 自由求解，也不能只靠 graph connected 和静态 residual 判定 ready。

复盘后冻结该方向：`AssemblyPlan -> AssemblyCompiler -> compiled pose -> solver seed` 已经开始变成自研装配求解器，和 text-to-cad 的轻量 source-level positioning 思路相反。阶段 9 的产物保留为诊断/回归资产，但不再继续增强为 production 主线。

### 9.1 首批已落地

新增：

```text
robot_sdk/assembly/plan.py
robot_sdk/validation/assembly_geometry.py
tests/test_robot_sdk_assembly_plan.py
tests/test_robot_sdk_assembly_geometry_validation.py
```

当前完成：

- `AssemblyPlan` 中间层已建立：
  - `AssemblyOccurrence`
  - `PartDatum`
  - `AssemblyMate`
  - `AssemblyChain`
  - `AssemblyPlan`
- `build_assembly_plan(layout)` 会从 `MechanicalLayout` 的 mate frames / assembly constraints 生成 CAD-neutral 装配计划。
- 每条 mate 已显式记录：
  - fixed part / moving part。
  - fixed datum / moving datum。
  - fixed feature / moving feature。
  - expected origin / normal / tangent。
  - normalized constraint kind。
- `build_assembly_geometry_report(...)` 已能基于 initial placement 和 solved placement 计算：
  - `origin_delta_mm`
  - `normal_angle_deg`
  - `tangent_angle_deg`
  - `component_cluster_report`
  - `base_to_terminal_connected`
  - `failing_edges`
- `cq_full_solve.py` 已改为使用 `assembly_geometry_report.residual_reports`，不再只用静态 layout residual 作为 full semantic residual。
- `CadQueryAssemblyResult.metadata` 已新增：
  - `production_full_semantic_solve_component_cluster_report`
  - `production_full_semantic_solve_assembly_plan`
  - `production_full_semantic_solve_geometry_report`
  - 对应 full fixture metadata。
- `validate_robot_design(...)` 已新增错误：
  - `production_full_semantic_assembly_geometry_failed`
  - `production_full_semantic_assembly_compiler_failed`
- 如果 full semantic solve 后出现多簇、base 到 end_effector 不连通、或 mate gap 超阈值，validation 会失败。
- 如果 `AssemblyCompiler` 输出 conflict、unresolved part，或 `ok=false`，当前旧 production validation 会失败。
- 阶段 10 后，这类 compiler gate 只能作为 experimental 对照，不作为 source_joint production 的必要 gate。

### 9.2 已完成但冻结：AssemblyCompiler

新增：

```text
robot_sdk/assembly/compiler.py
```

当前已完成首版：

- 新增 `AssemblyCompilerResult`。
- 新增 `compile_assembly_plan(layout, initial_locations=...)`。
- 编译模式为 `translation_origin_propagation`。
- datum 模式为 `part_local_origin_normal_tangent`。
- 编译器会沿 fixed->moving mate 链传播 part-local datum offset。
- 编译器会基于 datum normal/tangent 传播 part pose rotation。
- 输出：
  - `compiled_part_deltas`
  - `compiled_part_orientations`
  - `compiled_locations`
  - `unresolved_part_ids`
  - `conflicts`
  - `assembly_compiler` metadata
- `compiled_locations` 对 dict placement 已能写回：
  - `loc`
  - `xDir`
  - `normal`
- `cq_full_solve.py` 已使用 compiled placement 作为 full semantic fixture 的 solver seed。
- `full_assembly_fixture` / `production_full_semantic_solve` metadata 已带出 compiler report。
- `validate_robot_design(...)` 已把 compiler conflict / unresolved 接入 production gate。
- compiler gate 失败错误码：
  - `production_full_semantic_assembly_compiler_failed`

冻结原因：

- 继续补 CadQuery `Location` adapter、signed axis、offset、normal/tangent 规则，会把 SDK 推向自研 pose graph / assembly compiler。
- 机器人 CAD MVP 更需要 source-level part-local joint，而不是后处理 compiled pose。
- production STEP 应由源码中的 joint/connect 关系解析成 resolved static assembly，而不是先生成错误布局再由 compiler 修。

冻结后的处理：

- 不继续做 CadQuery `Location` adapter。
- 不继续扩展 `compiled_part_orientations` 的 production 消费。
- 不继续把 compiler conflict 作为新 production 主线的 gate。
- 保留现有测试，防止旧 experimental path 静默坏掉。
- 后续如需要诊断，可从 `AssemblyPlan` 读取 mate graph 和 metadata。

### 9.3 Datum Catalog 迁移方向

当前 `PartFeature` / `MateFrame` 已经能表达 datum，但还需要更强规则：

- production mate 禁止只有 CadQuery selector，没有 part-local joint/datum。
- 每个 joint/link/flange/end_effector 必须有稳定命名 datum：
  - `input_interface`
  - `output_interface`
  - `joint_axis`
  - `mount_face`
  - `tool_face`
  - `body_center`
- CadQuery selector 只是旧路径实现细节，不是装配语义源。
- 阶段 10 应把这些 datum 迁移为 CAD source 中真实注册的 part-local joints。

### 9.4 Post-Export Geometry Validation（保留）

当前首版验证基于 initial/solved placement delta，能抓 solver 漂移和断链。阶段 10 仍需要导出后的真实几何检查，但检查对象应从 compiled pose graph 改为 source mates / resolved STEP：

- 从 STEP / snapshot / bbox 中读回 occurrence 几何。
- 对每条 source mate 计算导出后实际接口距离。
- 检查相邻 part 是否形成连续 chain。
- 检查 wrist group 是否被拆成多个空间簇。
- 将截图问题映射为确定性错误分类。

错误分类：

```text
selector_fragility
datum_missing
fixed_moving_order_error
joint_axis_sign_error
part_local_origin_error
world_local_frame_mixed
underconstrained_mate
multi_cluster_assembly
```

### 9.5 阶段 9 冻结口径

- 不再新增 `AssemblyCompiler` production 功能。
- 不再把 `full_semantic_solve` 作为下一阶段默认 production 目标。
- 已有 `AssemblyPlan` / `AssemblyGeometryReport` 可继续服务 validation 和文档。
- 已有 compiler tests 保留，用于 experimental path 回归。
- 截图中的“机械臂断成两段 / 右侧串珠漂走”后续由阶段 10 的 source mates + resolved STEP validation 拦截。

## 阶段 10：Source-Level Joint Assembly / 轻量源码级装配

阶段 10 的判断：阶段 9 的 `AssemblyCompiler` 已经开始过度工程化。下一阶段不继续把 CathyAgent 做成自研装配求解器，而是参考 text-to-cad 的轻量模式：**零件在 CAD source 中定义 part-local joints/datum，装配 helper 只调用 CAD 内核原生 joint 的 `connect_to()`，最后导出已经 resolved 的静态 STEP**。

### 10.1 架构收敛

新的 production assembly 路径：

```text
MechanicalLayout / RobotStructurePlan
  -> CAD part builders
      -> 每个 part 生成 solid
      -> 每个 part 同时注册 part-local joint/datum
  -> SourceAssemblyHelper.connect(fixed, moving)
      -> 调 CAD 内核原生 joint.connect_to()
      -> 记录 source mate metadata
  -> build resolved compound / assembly
  -> export static STEP
  -> snapshot / bbox / simple mate metadata validation
```

明确不做：

- 不继续扩大 `AssemblyCompiler`。
- 不自研全局 pose graph solver。
- 不让 `AssemblyPlan -> compiled_part_orientations -> CadQuery Location adapter` 成为主线。
- 不把 CadQuery selector constraints 当 production 装配来源。
- 不把 DH transform 直接当实体零件 pose。

### 10.2 `AssemblyCompiler` 的新定位

`robot_sdk/assembly/compiler.py` 暂时保留，但降级为实验 / debug 工具：

- 可用于解释当前 mate graph 是否连通。
- 可用于生成诊断报告。
- 不再作为 production STEP 的必要前置。
- 不再继续补复杂 orientation / signed axis / offset 编译能力。

如果后续 source-level joint path 稳定，可以删除或只保留 `AssemblyPlan` / geometry report，不保留 compiler。

### 10.3 新增轻量 helper

建议新增：

```text
robot_sdk/cad/source_assembly.py
```

首版 API：

```python
SourceMateTarget(part, frame)
SourceMateRelation(label, relation, fixed, moving, parameters, endpoints)

class SourceAssemblyHelper:
    add(shape, name, color=None)
    rigid_frame(part, name, location)
    revolute_frame(part, name, axis)
    connect(fixed, moving, relation="rigid", **options)
    face_to_face(fixed, moving, offset=None)
    coaxial(fixed, moving, offset=None)
    build()
```

实现原则：

- frame/joint 必须是 part-local。
- `connect()` 只做一件事：查到 fixed/moving 的 native joint，然后调用 `fixed_joint.connect_to(moving_joint, **options)`。
- helper 只记录 source mate metadata，不做 pose 编译。
- 输出 compound/assembly 时附带 `assembly_mates`，供文档和 validation 使用。

### 10.4 CadQuery / build123d 技术选择

text-to-cad 的核心轻量性来自 build123d 原生 joint。CathyAgent 当前 CAD 后端主要是 CadQuery。下一阶段需要做一个小 spike：

```text
Option A: 在 robot_sdk/cad/source_assembly.py 中引入 build123d backend
  优点：可以直接使用 RigidJoint/RevoluteJoint/connect_to()
  缺点：现有 cq_parts 需要迁移或做桥接

Option B: 保留 CadQuery solid，但实现极薄的 part-local frame placement helper
  优点：改动小
  缺点：仍然没有 build123d 原生 joint 语义，容易又滑回自研 pose solver
```

阶段 10 首选 Option A 做 spike：只做 2DOF tabletop 和 6DOF generic 的少量 primitive，不迁移全部 CAD builder。若 build123d 在 `agent` 环境不可用，再临时用 Option B 验证 API 形状。

### 10.5 Robot CAD 流程重排

新的 `robot_design_agent` CAD 主流程：

```text
requirement
  -> kinematics_agent
  -> layout_agent / RobotStructurePlan
  -> source_part_builder
  -> source_assembly_helper.connect()
  -> export resolved static STEP
  -> visual snapshot + basic report
```

`layout_agent` 的职责保持不变：

- 决定结构 morphology。
- 输出 station / link route / joint role。
- 不输出 CAD 代码。
- 不输出最终 pose。

CAD builder 的职责变为：

- 根据 structure/layout 生成零件。
- 在每个零件上注册 `input`, `output`, `axis`, `mount`, `tool` 等 part-local joints。
- 用 helper 连接这些 joints。

### 10.6 阶段 10 实现顺序

1. Spike build123d 可用性：确认 `RigidJoint / RevoluteJoint / Compound / export_step` 是否可用。已完成：`agent` 环境中 build123d 0.10.0 可用。
2. 新建 `robot_sdk/cad/source_assembly.py`，实现轻量 helper。已完成首版：
   - `SourceAssemblyHelper`
   - `SourceMateTarget`
   - `SourceMateRelation`
   - `export_source_step`
   - part-local rigid/revolute frame registration
   - `connect()` / `face_to_face()` / `coaxial()` 调 native `connect_to()`
   - `assembly_mates` source metadata
3. 新建最小 build123d part builder：base、joint cylinder、link beam、tool flange，并注册 part-local joints。已完成首版：
   - `robot_sdk/cad/source_parts.py`
   - `SourcePart`
   - `SourcePartCatalog`
   - `build_source_base_part()`
   - `build_source_joint_part()`
   - `build_source_link_part()`
   - `build_source_tool_flange_part()`
   - `build_minimal_source_part_catalog()`
   - `build_source_structure_joint_part()`：joint input/output/axis datum 位于结构 station。
   - `build_source_routed_link_part()`：link output datum 直接表达 `MechanicalLayout` route vector。
4. 新建 `robot_sdk/cad/source_robot_builder.py`，只串 2DOF tabletop / simple serial chain。已完成首版：
   - `SourceRobotAssemblyResult`
   - `build_simple_source_serial_robot(joint_count, link_lengths, name)`
   - `build_source_robot_from_layout(layout, name)`
   - 输出 `production_assembly_source = source_joint`
   - 使用 `SourceAssemblyHelper.face_to_face()` 串 `base -> J/L... -> end_effector`
   - layout-driven 路径消费 `LinkLayout.metadata.link_primitive.route_vector`，不再只按 X 轴长度串直线 chain。
5. 让 `robot_design_agent` 支持 `cad_backend="source_joint"`，先不删除旧 CadQuery 路径。已完成首版：
   - input schema 新增 `cad_backend = cadquery | source_joint`
   - 默认仍为 `cadquery`
   - `source_joint` 分支调用 `build_source_robot_from_layout(layout)`
   - source_joint 分支导出 resolved static whole-machine STEP
   - source_joint 分支 trace 记录 source mates
   - source_joint 分支 trace 记录 `source_builder / layout_source / robot_family / link_routes`
   - source_joint 分支导出 build123d source STEP package：整机、零件、单零件子总成目录
   - source_joint 分支从 STEP package 生成 renderer-backed SVG snapshot
6. 跑 2DOF 和 6DOF 500mm，比较 snapshot / STEP。已完成首轮：
   - 2DOF source_joint：可导出 resolved static STEP，validation 通过。
   - 6DOF 500mm source_joint：可导出 resolved static STEP，生成 3 张 renderer-backed STEP SVG snapshot。
   - 6DOF 当前已通过 `visual_geometry.visual_snapshot_silhouette_screened`，说明 source-level route vector 已进入 resolved STEP。
   - 6DOF 当前仍有 `stage7_robot_cad_not_ready` warning，原因是 source_joint 路径尚未补齐 local promotion / full fixture promotion 等阶段 7 历史 gate。
7. 如果 source_joint 路径稳定，把 production 默认切过去；旧 `full_semantic_solve` 降为 experimental。

当前阶段 10 进度：

- 已新增 `tests/test_robot_sdk_source_assembly.py`，覆盖 source-level joint connect 和 resolved static STEP export。
- 已新增 `tests/test_robot_sdk_source_parts.py`，覆盖 source primitive 标准 joints、structure station joint、routed link endpoint datum、角色化 joint/link blockout、最小 source-level chain 和 STEP export。
- 已新增 `tests/test_robot_sdk_source_robot_builder.py`，覆盖 2DOF source robot chain、MechanicalLayout-driven source robot、source STEP package、输入校验和 STEP export。
- 已扩展 `tests/test_robot_design_agent.py`，覆盖 `cad_backend="source_joint"` 的 agent 级 STEP 导出 smoke。
- 已扩展 `tests/test_robot_design_agent.py`，覆盖 `cad_backend="source_joint"` 的 6DOF 500mm smoke：确认 STEP package / snapshot 产物存在，确认 `source_builder=source_robot_builder_from_layout`，确认 L1/L2/L3 route vector 带 Z 分量，确认 visual silhouette gate 通过。
- 已新增 `robot_sdk/cad/source_export.py`，导出 `SourceStepPackageExportResult` / `SourceSubassemblyExportResult`，接口与旧 package result 同形，供 final answer、snapshot、validation 复用。
- 已扩展 `robot_sdk/cad/bbox.py`，支持 build123d `bounding_box()`；source STEP / source package export 会写入 `CadBoundingBox`。
- 已确认两个 build123d part 通过 named local joints 连接后，moving part 位置由 `connect_to()` 解析，而不是 CathyAgent 自己编译 pose。
- 已确认最小 catalog 可以串 `base -> J1 -> L1 -> end_effector`，并导出 resolved static STEP。
- 已确认 `source_robot_builder` 可以串 `base -> J1 -> L1 -> J2 -> L2 -> end_effector`，并导出 resolved static STEP。
- 已确认 `robot_design_agent` 可选 source_joint 后端可以导出整机 STEP 与零件级 STEP package，final answer 显示 `Production assembly source: source_joint` 和 `STEP package`。
- 已确认 source_joint validation 不再误报 “CadQuery fixed layout pose”；`source_joint_constraints_applied` 作为 source-level joint 通过项记录。
- 已确认 source_joint validation 能通过 `export_bbox_valid` 和 `step_package_bboxes_consistent`。
- 已确认 source_joint 已解决首版“装配链 source-level connect_to() + layout route vector 进入 resolved STEP”问题。
- 已确认 source_parts 首版角色化 blockout 已落地：
  - `base_yaw / shoulder / elbow / wrist / tool` joint 使用不同 `primitive_family`。
  - `upper_arm / forearm` 使用 dual-rail blockout。
  - `wrist1_offset / wrist2_elbow / wrist3_tool / terminal_tool` 使用 plate 或 cylindrical blockout。
  - UR-style layout morphology 会进入 source builder，而不只停留在文档 metadata。
- 已继续细化 source_parts 外观语义：
  - joint metadata 新增 `visual_features`，能记录 `pedestal_block / side_flanges / compact_barrel / pilot_hub` 等角色化外观特征。
  - link metadata 新增 `visual_features`，能记录 `dual_rail / end_blocks / cross_brace / bent_cylindrical_housing / dual_flanges / pilot_hub`。
  - `upper_arm / forearm` 不再只是两条光杆，增加端块和 cross brace。
  - `wrist2_elbow_cylinder` 增加 elbow ball、端部法兰和 pilot hub，用作 L5-J6 腕部回归特征。
  - `wrist3_tool_flange / terminal_tool_spacer` 增加短法兰/工具 spacer 特征。
- 已新增 source_joint 真实 STEP/snapshot review subassembly 回归：
  - `base_shoulder = base + J1 + L1 + J2`
  - `upper_arm = J2 + L2 + J3`
  - `forearm = J3 + L3 + J4`
  - `wrist_l5_j6 = J5 + L5 + J6`
  - `tool_end = J6 + L6 + end_effector`
  - 这些 review subassembly 只在 `joint_count >= 5` 的 source_joint 路径启用，避免污染 2DOF smoke。
  - `robot_design_agent` 6DOF source_joint smoke 已断言上述 STEP 目录存在，并断言这些对象会进入 `subassembly_snapshots`。
- 已新增 source_joint review subassembly bbox 比例 gate：
  - `source_joint_review_bbox_ratios_ready`：高自由度 source_joint 的 review subassembly bbox 比例通过。
  - `source_joint_review_bbox_ratio_failed`：比例异常时 validation error。
  - `base_shoulder` 要有足够垂直高度和横向支撑。
  - `upper_arm / forearm` 要满足最小长宽比，generic 6DOF 阈值较保守，避免误杀短粗概念臂。
  - `wrist_l5_j6 / tool_end` 要保持紧凑，不能变成细长串珠或扁平片。
- 已把 review bbox metrics 写入输出层：
  - `robot_design_agent` final answer 增加 `Source review bbox: ready/failed (...)` 摘要。
  - `需求文档.md` 追加 `CAD Review Metrics` 段落。
  - 文档表格列出 `x/cross`、`z/x`、`y/x`、`minor/major` 和 `bbox x/y/z(mm)`。
  - 若 bbox gate 失败，文档会追加 `Review Failures` 表格，列出失败对象和原因。
- 已新增 source_joint 专属 validation gate：
  - `robot_sdk/validation/source_joint.py`
  - `source_joint_mates_complete`：每条 source mate 都必须有 fixed/moving part、frame、local location。
  - `source_joint_chain_connected`：source mate graph 必须形成 `base -> ... -> end_effector` 的单连通链。
  - `source_joint_promotion_gate_ready`：source-level connect_to() production gate 通过，不再依赖 CadQuery local/full solver promotion。
  - `source_joint_step_package_ready`：source_joint STEP package 必须全部存在且 bbox 非退化。
- 已把 source_joint gate 接入 `validate_robot_design(...)`，并让高自由度 source_joint 路径用 source gate 替代旧的 `local_subassembly_ready_for_promotion / full_assembly_fixture_ready_for_promotion` 阶段 7 历史要求。
- 已修复 generic 6DOF 的 reach 传递：
  - `robot_design_agent` 需求解析现在识别“工作空间 500mm”。
  - `robot_design_agent -> layout_agent` 会传递 `reach_mm`。
  - `layout_agent` 生成 `RobotStructurePlan` 时优先使用显式 `reach_mm` / 用户文本 reach，再退回 DH 估算。
  - `structure_plan.metadata.reach_source` 会记录来源，避免 500mm 请求被 planar DH 模板估算成 480mm 或默认 400mm。
- 已优化 generic 6DOF 的首版比例：
  - `build_generic_6axis_cobot_structure_plan()` 将 elbow station 高度从 `0.34 * reach` 调整为 `0.30 * reach`，降低不自然的陡上臂。
  - 500mm generic source_joint 导出中，`upper_arm x/cross` 从约 `2.00` 提升到约 `2.36`，`forearm x/cross` 从约 `2.15` 提升到约 `2.69`。
  - `source_parts` 的 dual-rail link 增加多段 cross brace，增强结构件视觉，而不改变 part-local source joint 语义。
- 已优化 generic wrist/tool source parts 语义：
  - 当 layout_agent 输出 `generic_revolute_joint` 时，source part builder 不再让 generic morphology 覆盖 `structure_axis_role`；J5/J6 会分别回退到 `wrist_compact` / `tool_flange_joint`。
  - 短 `elbow` route 会映射为 `wrist_elbow_cylinder`，避免 L5 退成方盒。
  - `wrist_spacer` 会映射为 `wrist_tool_flange`，`generic_wrist_spacer` 保持 `terminal_tool_spacer`。
  - 500mm generic source_joint 导出中，`wrist_l5_j6 x/cross` 约 `1.39`，`tool_end x/cross` 约 `1.03`，仍通过紧凑腕部 / 工具端 bbox gate。
- 已补齐 source_joint renderer-backed review snapshot gate：
  - 高自由度 `source_joint` 必须生成 `base_shoulder / upper_arm / forearm / wrist_l5_j6 / tool_end` 的 renderer-backed review snapshots。
  - 缺少 review snapshot 时 validation 报 `source_joint_review_snapshots_missing` error。
  - 通过时 validation 记录 `visual_geometry.source_joint_review_snapshots_ready`。
- 已把 `robot_design_agent` 默认 CAD 后端切为 `source_joint`：
  - 不传 `cad_backend` 时默认走 build123d part-local joints + `connect_to()`。
  - 旧 CadQuery / `full_semantic_solve` 路径保留为显式 `cad_backend="cadquery"` 的 experimental / regression 对照。
  - 旧 CadQuery 回归测试已显式指定 `cad_backend="cadquery"`，避免被默认 production 切换污染。
- 已确认 6DOF source_joint 当前外观仍是概念级 blockout，不是最终机械外壳；工业级外壳、执行器、减速器和线缆细节进入下一阶段。

阶段 10 完成口径：

- production 默认是 `source_joint`。
- 6DOF generic 500mm 可以导出整机 STEP、零件 STEP、review subassembly STEP 和 renderer-backed snapshots。
- source mate chain、STEP package bbox、review bbox、review snapshot gate 都纳入 validation。
- `AssemblyCompiler` / CadQuery full semantic solve 不再是 production 主线，只作为 experimental / regression 对照保留。

### 10.7 阶段 10 验收标准

- 装配由 source-level `connect()` 产生，不由 `AssemblyCompiler` 或 CadQuery 全局 solver 产生。
- 每个零件导出前都有 part-local joints。
- STEP 是 resolved static assembly，不要求保留可运动关节。
- 需求文档记录 source mates，而不是 compiled pose graph。
- 6DOF generic 不能再出现“右侧串珠漂走 / 子链断开”。
- 如果 joint 缺失，构建应失败，而不是尝试用 fallback pose 猜测。
- 高自由度 source_joint 输出必须有 renderer-backed review snapshots。

## 暂不做

- 电机/减速器真实选型。
- 动力学/力矩闭环。
- 碰撞检测和运动规划。
- 工业级制造图纸。
- 复杂外形建模。
- 让 LLM 直接手写 CAD pose、CadQuery 代码或替代 SDK validation。
- 让 layout_agent 直接输出最终 STEP。

## 总结

第一阶段已经证明“需求 -> DH -> MechanicalLayout -> CadQuery -> STEP package”的闭环能跑通。第二阶段补上了 profile/scaling/kinematics_agent 的基础能力。第三阶段让 layout debug、validation、interface normal/tangent、primitive metadata 和 fixed pose solve 变得可追踪。第四阶段完成了 layout_agent morphology 决策、CAD builder 消费 morphology、需求文档记录 layout 决策。

阶段 5 已完成：`docs/robot_sdk.md`、skill 读入规则和 `UR3E-like + reach 500mm` decision guard 已补齐。阶段 6 已完成局部约束驱动装配闭环：MateFrame、ConstraintRule、Assembly Validation、LocalSubassemblySolve、constraint graph、full-assembly fixture gate、实验 full fixture、promotion 阈值和 STEP package local solved export 策略都已落地。`link.body_axis -> joint.axis` 危险旧约束已替换为 interface tangent mate rule。局部子总成覆盖已扩展到 `J4-L4-J5`、`J3-L3-J4` 和 wrist group，并记录 residual / pose delta。LocalSubassembly candidates 已从 SDK hardcode 迁入 `layout_agent` decision schema，SDK fallback 只作为缺省兜底。end-effector mount 朝向已与最后 link 输出接口对齐。STEP package 已能导出 local solved subassembly，且通过 `production_local_solve` / `experimental_local_solve` 区分可生产局部导出与实验对照导出。constraint graph、full assembly solve gate、full fixture、local promotion 和 local solved export summary 已写入 validation / 需求文档 / 最终回答。`base.top -> J1.bottom` residual 热点已修复，`base_J1_mount` 已升级为 production local semantic subassembly；默认 2DOF tabletop layout 中 `J1_L1_J2` 已成为首个 joint-link-joint promotion-ready 回归样例。`semantic_constraints_applied_to_solver` 已从 bool 升级为 `none/local_subassembly/full_assembly`，当前成功局部约束解记录为 `local_subassembly`。

阶段 7 已完成 MVP 验收闭环：`RobotStructurePlan` schema、generic 6-axis cobot structure plan、structure validation、structure adapter、`robot_design_agent` CAD 前置早停、source-first artifacts、renderer-free visual geometry gate、STEP SVG snapshot 复核产物、局部 semantic solve promotion、full-assembly fixture promotion，以及最终 `stage7_robot_cad_ready / stage7_robot_cad_not_ready` 完成门都已落地。现在 `6DOF/high-DOF + all-zero alpha/d planar template_fallback` 不会再导出 production CAD，也不会报告 validation ok；即使旧路径给 link 打了 elbow/wrist_spacer primitive 标签，只要关节站位和轴系仍是水平串珠，也会被 visual gate 拦截。

阶段 8 已完成 MVP 验收闭环，并作为历史路径保留：production assembly source 已显式化，`full_semantic_solve` 曾作为默认 production 方案，且只有 local graph、whole-machine graph、full fixture solve、production residual gate 全部通过时才会成为 ready 的 production full semantic assembly；失败时 validation 会报错，不会伪装为 fixed pose 成功。阶段 10 开始后，`full_semantic_solve` 降级为 experimental / regression 对照路径，新的 production 目标是 `source_joint`。

阶段 9 已冻结：`AssemblyPlan`、`AssemblyGeometryReport`、`AssemblyCompiler`、production full semantic compiler gate、cluster gate 和断链回归已首批落地，可继续作为诊断/回归资产。但阶段 9 也暴露出过度工程化风险：继续增强 `AssemblyCompiler` 会把 CathyAgent 推向自研装配内核。

阶段 10 调整方向：参考 text-to-cad 的 source-level positioning 思路，把 production 装配收敛到 part-local joints + `SourceAssemblyHelper.connect()` + resolved static STEP。`AssemblyCompiler` 降级为实验/诊断工具，不再作为 production 主线。
