# Robot SDK Agent Guide

本文档是给 CathyAgent / subagent 读取的 Robot SDK 边界说明。修改机器人 CAD、运动学、布局、装配、导出相关代码前，必须先阅读本文档，再进入具体模块。

## 结论

- `robot_sdk` 是确定性 SDK，只接收结构化输入，不直接读取用户自然语言。
- 用户自然语言决策属于 subagent，例如 `kinematics_agent`、`layout_agent`、`robot_design_agent`。
- DH / POE 只描述运动学关系，不等于 CAD 零件 pose，也不等于实体装配约束。
- CAD 只消费 `MechanicalLayout`，不得直接从 DH 表生成最终实体装配。
- 当前默认 production 整机导出使用 `full_semantic_solve`：CadQuery solver 以 full-assembly semantic mate constraints 作为 production assembly source；`fixed_layout_pose` 仍保留为显式回退路径。

## 当前主流程

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

主入口是 `subagents/robot_design_agent/agent.py`。它负责串联 SDK、创建 session 输出目录、导出需求文档、导出零件 STEP、子总成 STEP 和整机 STEP。

## SDK 层级

### `robot_sdk/types.py`

核心数据契约层。这里定义跨 agent / SDK 的稳定对象，例如：

- `RobotRequirement`
- `KinematicModel`
- `DHParam`
- `FrameSpec`
- `MechanicalLayout`
- `PartFeature`
- `AssemblyConstraint`
- joint / link / interface morphology literal

原则：

- 只放轻量数据结构和校验。
- 不引入 CadQuery、LangGraph、LLM runtime。
- 新字段必须能被 `to_dict()` 序列化。

### `robot_sdk/kinematics/`

运动学确定性能力层。

- `profiles.py`：内置可追溯 profile，例如 `ur3e`。
- `scaling.py`：执行 explicit scaling request；只缩放 DH 长度项 `a/d`，不缩放 `alpha/theta`。
- `dh.py`：DH/FK 数学工具。

原则：

- 是否使用 exact profile、scaled profile、profile-like template，由 `kinematics_agent` 决策。
- SDK 不从自然语言猜测用户意图。
- `exact_profile` 只能表示原始/官方/no-scaling profile。
- 用户说 `UR3E-like + reach 500mm` 时，应走 `profile_like_template` 或 `scaled_profile`，不能走 `exact_profile`。

### `robot_sdk/layout/`

运动学到机械语义层。

- `mechanical_layout.py`：从 `KinematicModel` / DH home pose 生成基础 `MechanicalLayout`。
- `frame_mapping.py`：记录 kinematic frame 到 mechanical frame 的映射 rationale。
- `link_primitives.py`：根据 layout decision 写入 link primitive / morphology metadata。
- `decision_adapter.py`：把 `layout_agent` 的 `LayoutDecision` 应用到 `MechanicalLayout`。
- `validation.py`：layout 层校验。

原则：

- `MechanicalLayout` 是 CAD 前置语义层，不能跳过。
- DH station position 可以作为粗骨架参考，但不能直接等同于实体 mate transform。
- link body direction、joint axis direction、interface normal 必须显式区分。

### `robot_sdk/assembly/`

中立装配规则层。

- `features.py`：把 `PartFeature` 整理为 CAD mate feature 规则。
- `constraints.py`：把 `AssemblyConstraint` 整理为中立 constraint rule。
- `mate_frames.py`：把 `PartFeature` 解析成带 origin / normal / tangent 的 `MateFrame`。
- `local_solve.py`：生成局部子总成计划，筛选可安全进入局部 solve 的 semantic constraints。
- `cq_adapter.py`：把中立规则转为 CadQuery `Assembly.constrain(...)` 调用。

原则：

- 这里不生成零件实体。
- 这里不应该根据 DH 重新推导 pose。
- 后续阶段 6 的重点是让语义约束真正成为 solver 主约束。

### `robot_sdk/cad/`

CadQuery 适配层。

- `cq_parts.py`：根据 `MechanicalLayout` 生成粗粒度零件实体。
- `cq_assembly.py`：创建 CadQuery assembly 并执行 solve。
- `cq_local_solve.py`：对局部子总成运行独立 CadQuery solve。
- `cq_export.py` / `export.py`：导出 STEP package。

当前重要装配策略：

- `cq_assembly.py` 的 `assembly_mode` 是 `constraint_solve`。
- 默认 `production_assembly_source` 是 `full_semantic_solve`。
- `semantic_constraints_applied_to_solver` 是枚举值：`none` / `local_subassembly` / `full_assembly`。默认 production 整机通过 gate 时应记录为 `full_assembly`。
- `cq_local_solve.py` 仍会运行 `local_constraint_solve`，用于局部子总成验证和局部 STEP 导出。
- `fixed_layout_pose` 只能作为显式保守回退，不能伪装成 full semantic solve。
- 当前 STEP 正确性依赖 `MechanicalLayout` 的初始 pose、semantic mate constraints、whole graph gate 和 production residual gate 共同成立。

修改原则：

- 不要把错误隐藏成“约束驱动已完成”。
- 如果改变装配方式，必须同步更新 validation 报告和 requirement doc metadata。
- 不要在 `cq_parts.py` 里硬编码会话目录、文件命名或 agent 决策。

### `robot_sdk/structure/`

结构规划层，位于 `kinematics` 和 `layout` 之间。

- `robot_structure_plan.py`：定义 `RobotStructurePlan`、station、joint axis、link route、datum、subassembly plan。
- `build_generic_6axis_cobot_structure_plan(...)`：生成非平面 6 轴协作臂结构拓扑模板。

原则：

- `RobotStructurePlan` 描述“机器人应该长成什么三维拓扑”，不是 CAD 实体，也不是 DH 表。
- 不把完整结构计划塞进 `MechanicalLayout` 主字段；由 `structure_adapter.py` 转换。
- generic structure plan 不声称是官方 DH 或厂家几何，只能作为 concept blocking 的结构来源。

### `robot_sdk/validation/`

报告级校验层。

- `basic.py`：检查 reach、DOF、layout、constraint、CAD solve、导出文件是否存在。
- `structure.py`：检查高自由度机械臂结构拓扑，拒绝 all-zero `alpha/d` 的 planar template fallback 进入 production CAD。

原则：

- validation 要指出当前模式的真实风险。
- 如果语义约束未进入 solver，报告里必须保留 warning。
- `6DOF/high-DOF + planar template_fallback` 必须输出 `six_axis_planar_template_rejected`，不能继续导出 production CAD。

## Subagent 边界

### `kinematics_agent`

职责：

- 根据用户需求选择 `exact_profile` / `scaled_profile` / `profile_like_template` / `search_verified` / `template_fallback`。
- 输出 `KinematicsAgentResult` 和 `KinematicModel`。

禁止：

- 输出手写 DH rows 代替 SDK profile/scaling。
- 对 `UR3E-like + custom reach` 选择 `exact_profile`。

### `layout_agent`

职责：

- 根据 robot family、DH pattern、link/joint 上下文决策 morphology、link primitive、interface 语义。
- 决策 local subassembly solve candidates，例如 terminal tool、joint-link-joint、UR-style wrist group。
- 高自由度机器人还应输出 `RobotStructurePlan`，明确 station、joint axis、link route、datum。
- 输出 `LayoutDecision` 和可选 `RobotStructurePlan`；前者由 `decision_adapter` 应用 morphology，后者由 `structure_adapter.py` 转成 `MechanicalLayout`。

禁止：

- 直接生成 CadQuery 实体。
- 把 DH frame 当作 CAD mate frame。

### `robot_design_agent`

职责：

- 固定主流程编排。
- 负责 session 输出目录、需求文档、STEP package、validation summary。
- 在 CAD 生成前运行 structure validation；结构错误时应停止导出。

禁止：

- 绕过 `kinematics_agent` / `layout_agent` 直接从用户文本生成 CAD。
- 把 SDK 内部目录命名规则写死为某个项目路径。

## 修改入口速查

- 改数据结构：先改 `robot_sdk/types.py`，再补 adapter / validation / tests。
- 改结构规划：改 `robot_sdk/structure/robot_structure_plan.py`、`robot_sdk/validation/structure.py`、`robot_sdk/layout/structure_adapter.py`。
- 改 UR3E/profile 运动学：改 `robot_sdk/kinematics/profiles.py` 或 `scaling.py`，agent 决策改 `subagents/kinematics_agent/`。
- 改 link primitive/morphology 选择：改 `subagents/layout_agent/` 和 `robot_sdk/layout/decision_adapter.py`。
- 改零件几何：改 `robot_sdk/cad/cq_parts.py`。
- 改装配约束求解：改 `robot_sdk/assembly/`、`robot_sdk/cad/cq_assembly.py`、`robot_sdk/validation/basic.py`。
- 改导出目录和文件命名：优先改 `subagents/robot_design_agent/agent.py`，不要写进 SDK 几何层。

## 当前禁忌

- 不要新增“DH 直接到 CAD assembly pose”的路径。
- 不要在 `robot_sdk` 内调用 LLM。
- 不要在 `robot_sdk` 内读取 `config.yaml` 或 workspace。
- 不要让 `exact_profile` 承担缩放或 like-template 语义。
- 不要用固定位置装配结果伪装成完整语义约束求解结果。
- 不要让 6DOF planar template fallback 直接导出 production CAD。

## 阶段 7 扩展点

阶段 7 当前已完成第一批结构底线能力：

- `RobotStructurePlan` schema。
- `build_generic_6axis_cobot_structure_plan(...)`。
- `validate_robot_structure(...)`。
- `validate_robot_design(...)` 接入 structure validation。
- `robot_design_agent` 对 `six_axis_planar_template_rejected` 做 CAD 前置早停。
- `layout_agent` 已支持输出 `RobotStructurePlan`，LLM 输出可解析，fallback 可为 6DOF/high-DOF 生成结构计划。
- `robot_sdk/layout/structure_adapter.py` 已实现 `build_mechanical_layout_from_structure_plan(...)`，可把 structure plan 转成 `MechanicalLayout`。
- `robot_design_agent` 已接入 `layout_agent.structure_plan -> build_mechanical_layout_from_structure_plan(...) -> CAD/export/validation`。
- `robot_design_agent` 成功路径已导出 `structure_plan.json`、`mechanical_layout.json`、`robot_model.py`。

下一步：

1. CAD builder 消费 datum/link route，而不是只消费旧 tabletop serial placement。
2. 增加视觉/STEP snapshot 复核，拒绝明显水平串珠结构。

## 阶段 6 扩展点

阶段 6 的目标是约束驱动装配升级：

1. 为每个 part 生成稳定、可查询的 mate feature。
2. 建立 `AssemblyConstraint` 到 CadQuery selector / mate plane / axis 的可靠映射。
3. 先在最小两零件 fixture 中验证约束，再扩展到子总成。
4. 引入 constraint graph 校验，检查欠约束、过约束、循环和 feature 缺失。
5. 当语义约束真正进入生产整机 STEP solver 后，再把 `semantic_constraints_applied_to_solver` 从 `local_subassembly` 升级为 `full_assembly`。

当前阶段 6 已完成局部约束驱动闭环：

- `build_mate_frame_catalog(layout)` 可生成 MateFrame。
- `build_assembly_mate_constraint_plan(layout)` 可生成 mate-frame constraint plan。
- `validate_assembly_semantics(layout)` 可报告 mate frame 完整性、constraint 可解析性、危险旧轴约束。
- `solve_local_subassemblies(layout)` 可对 `J6-L6-end_effector`、`J5-L5-J6`、`J4-L4-J5`、`J3-L3-J4` 和 wrist group 等局部子总成运行 local constraint solve。
- local subassembly candidates 优先来自 `layout.metadata["local_subassemblies"]`，该 metadata 由 `layout_agent` 的 `LayoutDecision.subassemblies` 写入；SDK index fallback 只用于缺少 agent 决策的 generic layout。
- `MechanicalLayout` 已为接口面生成 `*_tangent` axis feature；local solve 使用 interface tangent axis 约束，不再使用 `link.body_axis -> joint.axis`。
- `build_cadquery_assembly(layout)` 会把 local solve 结果写入 `local_subassemblies` metadata，validation report 会读取这些结果。
- `export_robot_step_package(...)` 可按 `CadQuerySubassemblyExportSpec.source_local_subassembly_name` 导出对应的 local solved assembly；`robot_design_agent` 会把 solved local subassemblies 追加进 STEP package。
- STEP export result 会尽量记录导出对象 bbox；validation 会检查整机与子总成 bbox 是否非退化，并对 package bbox 空间一致性给出 pass/warning/error。
- `build_constraint_graph_report(...)` 可对 local subassembly plans 做 constraint graph 校验；传入 `layout=layout` 时还会生成 whole-machine constraint graph。validation 会报告 disconnected/isolated parts、cycles 和 dense part-pair constraints。
- whole-machine graph 会检查 production parts 是否整体连通，以及 `base -> end_effector` 是否存在完整约束路径。
- `evaluate_full_assembly_solve_gate(...)` 会把 local graph 和 whole graph warning 汇总为 full-assembly solve fixture 前置门禁：
  - `clear_for_fixture`：允许开始最小 full-assembly solve fixture 试验。
  - `blocked`：存在 underconstrained / cyclic / dense pair 等图风险，先不要进入 full solve。
- `validate_assembly_semantics(layout)` 会输出 `full_assembly_solve_gate_clear_for_fixture` 或 `full_assembly_solve_gate_blocked`。
- `robot_design_agent` 会把 constraint graph summary 和 full assembly solve gate 写入 `需求文档.md` 和最终回答。
- `solve_full_assembly_fixture(layout)` 会在 gate 通过时创建独立 CadQuery Assembly，以 `base` 为 fixed seed 并应用全部 semantic mate constraints；结果会记录 `pose_delta_report` 和全量 semantic mate `residual_reports`。
- `build_cadquery_assembly(layout)` 默认使用 `full_semantic_solve` 作为 production assembly source；只有 gate、fixture solve 和 production residual gate 通过时，整机 STEP 才使用 full semantic assembly。`fixed_layout_pose` 仍可显式请求为回退。
- validation 已对 full fixture 的 translation pose delta、origin residual 和 normal/tangent angle residual 设置 promotion 阈值：
  - 全部在阈值内输出 `full_assembly_fixture_ready_for_promotion`。
  - 超过 warning 阈值输出 `full_assembly_fixture_threshold_warning`。
  - 超过 error 阈值输出 `full_assembly_fixture_threshold_exceeded`。
- `base.top -> J1.bottom` 的 fixture residual 热点已通过显式 `base_top_mount_face` frame 修复；首关节输入 mate frame 与基座顶面 mate frame 对齐。
- `base_J1_mount` 已作为 promoted production local subassembly 加入 local solve candidates：
  - part ids: `base`, `J1`。
  - fixed seed: `base`。
  - semantic constraints: `base_top_to_J1_bottom_plane`、`base_axis_to_J1_axis`。
  - 该子总成会随 STEP package 导出为 local solved subassembly；整机 STEP 仍不切换到 full constraint solve。
- local solve 结果包含 `residual_reports` 和 `pose_delta_report`；validation report detail 会汇总 `max_local_pose_delta_mm`、`max_local_origin_residual_mm`、`max_local_normal_residual_deg` 和 `max_local_tangent_residual_deg`。
- local solve 结果会把 `LocalSubassemblySpec.metadata` 透传到总装 metadata；validation 会按 role/source/name 判断哪些局部子总成已经达到 promotion 阈值：
  - 至少一个局部子总成满足阈值时输出 `local_subassembly_ready_for_promotion`。
  - 至少一个 `joint_link_joint` 子总成满足阈值时输出 `joint_link_joint_promotion_ready`。
- STEP package export 会根据 promotion 阈值区分 local solved subassembly：
  - `production_local_solve`：可替代 fixed-pose 子总成导出的局部语义约束解。
  - `experimental_local_solve`：仍只作为实验报告/对照导出的局部语义约束解。
- end-effector mount frame 已按最后一段 link 输出方向定向，terminal tool mate frame 不再产生 90 度 normal/tangent residual。
- validation 已对 local solve 的 translation pose delta、origin residual 和 normal/tangent angle residual 设置 warning/error 阈值；超过 error 阈值时 report 会 fail。
- 整机导出仍保留 fixed layout pose；不要把 `local_subassembly` 说成整机 `full_assembly` constraint solve。

## 阶段 7 完成门

`validate_robot_design(...)` 会在高自由度 / structure-plan 路径上汇总最终 gate：

- 全部满足时输出 `stage7_robot_cad_ready`。
- 缺少真实 STEP snapshot、source-first structure/layout、local promotion、full fixture promotion、export package 或 visual cue 时输出 `stage7_robot_cad_not_ready`。
- `visual_snapshot_fallback_only` 不能升级为 ready；必须有 renderer-backed STEP SVG snapshot。
- `stage7_robot_cad_ready` 只表示阶段 7 MVP source-first CAD 流程可接受；生产整机 STEP 仍是 fixed layout pose，full semantic solve 仍是 promotion fixture。

## 阶段 8：Production Full Semantic Solve

`build_cadquery_assembly(...)` 支持显式选择 production assembly source：

```python
build_cadquery_assembly(
    layout,
    production_assembly_source="full_semantic_solve",    # 默认
)

build_cadquery_assembly(
    layout,
    production_assembly_source="fixed_layout_pose",      # 显式回退
)
```

规则：

- 默认是 `full_semantic_solve`，整机 production STEP 走 full semantic assembly gate。
- `fixed_layout_pose` 是显式保守回退路径，不能伪装成 full semantic solve。
- `full_semantic_solve` 会复用 gated full-assembly semantic fixture；只有 gate clear 且 solve 成功时，才会成为 production assembly。
- 请求 `full_semantic_solve` 但未成功时，result 会失败并记录 `production_full_semantic_solve_error`，不能静默退回 fixed pose。
- STEP package 会记录 `whole_machine_assembly_source`。
- validation 会输出：
  - `production_full_semantic_solve_used`
  - `production_full_semantic_solve_failed`
  - `production_full_semantic_solve_ready`
  - `production_full_semantic_solve_residual_warning`
  - `production_full_semantic_solve_residual_exceeded`
- full semantic solve gate 已同时检查 local subassembly graph 和 whole-machine graph；整机约束链断开时不会进入伪 full solve。
- full semantic solve 的 production gate 会独立检查 pose delta / mate residual；超过 error 阈值时会在 `contradictory_residual_candidates` 中列出疑似冲突 mate。

## 提交前检查

- 新增/修改 SDK 能力是否有单元测试。
- `docs/plan.md` 是否同步阶段状态。
- `skills/robot_cad_design/SKILL.md` 是否仍正确描述当前能力。
- 需求文档是否能记录 source mode、DH 表、layout decision、CAD/assembly metadata。
- UR3E-like custom reach case 是否不会退回 `exact_profile`。
