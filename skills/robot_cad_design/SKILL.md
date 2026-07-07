---
name: robot_cad_design
description: 机器人 CAD 设计方法论：需求拆解、运动学到机械布局的边界、CAD MVP 调用策略和审查清单
triggers: [机器人, 机械臂, CAD, 建模, DH, 运动学, 装配, STEP]
version: "0.1.0"
---

加载本 skill 后，处理机器人 CAD / 机械臂设计 / DH 到 CAD 建模相关任务时必须遵守以下边界。

## SDK 文档必读

如果任务涉及修改或调用 `robot_sdk`、`subagents/robot_design_agent`、`subagents/kinematics_agent`、`subagents/layout_agent`、CAD 导出、装配约束或 STEP package，必须先阅读 `docs/robot_sdk.md`。

`docs/robot_sdk.md` 是 agent 读入边界文档，记录 SDK 层级、当前主流程、禁忌、当前装配限制和下一阶段扩展点。不要仅凭本 skill 的摘要改 SDK 代码。

## 结论先行

- 如果用户要“生成 CAD / 导出 STEP / 跑 MVP 建模”，主 agent 应调用 `robot_design_agent`。
- 如果用户说“机器人建模 / 机械臂建模”，且没有明确限定“只要 DH/运动学”，也应调用 `robot_design_agent`，不要只调用 `kinematics_agent`。
- 如果用户只是问设计流程、审查方法或概念解释，主 agent 可直接按本 skill 回答。
- Skill 只放方法论、模板和检查清单；Subagent 才执行 SDK、生成 CAD、导出文件。
- 不要把 DH transform 当成 CAD 实体装配 transform。

## 推荐工作流

1. 工况与需求拆解
   - 明确任务、负载、工作半径、自由度、安装方式、环境约束。
   - 缺失字段可以给默认值，但必须写入假设。

2. 运动学建模
   - 生成 `KinematicModel`、`JointSpec`、`LinkSpec`、`DHParam`。
   - DH / POE 只描述运动学坐标关系，不直接代表实体零件的装配关系。

3. 机械布局语义层
   - 必须经过 `MechanicalLayout`。
   - 显式区分 joint axis、link body、mount frame、interface frame、end effector tool frame。
   - 每个从运动学 frame 到机械 frame 的映射都应说明 rationale。

4. CAD 与装配
   - CAD 只消费 `MechanicalLayout`，不直接消费 DH 表。
   - 零件应有可追踪的 `PartFeature`，例如 mount face、joint axis、flange face、link end。
   - 装配关系应表达为 `AssemblyConstraint`，再由 CadQuery adapter 执行 `constraint_solve`。
   - 当前生产整机 STEP 仍是 `constraint_solve_fixed_layout_pose`：solver 使用固定布局姿态约束。语义装配约束已可进入 local subassembly solve 和 experimental full-assembly fixture，但尚未作为生产整机 STEP 的主约束。回答和文档中必须如实说明。
   - STEP package 的目录和文件命名由 `robot_design_agent` 的 CAD naming rule 决定，不应硬编码在 `robot_sdk`。
   - 命名应采用 CAD 语义名，例如 `base`、`joint1`、`link1`、`left_frontleg`；零件文件名应避免和同目录总成 STEP 冲突，例如 `joint1/joint1_housing.step` 与 `joint1/joint1.step`。

5. 验证
   - 检查 reach、DOF、layout frame 完整性、feature 完整性、constraint 完整性、CAD 导出文件是否存在。
   - 失败时先判断问题属于需求、运动学、机械布局、CAD 几何还是导出层。
   - `session_id/` 根目录必须导出 `需求文档.md`，记录设计需求、DoF/payload/reach 等约束，以及 DH 模型表。

## 何时调用 `robot_design_agent`

满足任一条件时调用：

- 用户要求生成机器人 CAD、STEP、STL、装配模型或“跑一下建模”。
- 用户给出机器人需求并希望得到可落地的 CAD 初版。
- 用户要求验证当前 MVP 建模链路是否能跑通。

调用时参数建议：

```json
{
  "request": "用户原始需求，保留关键数字和约束",
  "dof": 4,
  "reach_mm": 400,
  "payload_g": 500,
  "export_filename": "整机.step"
}
```

不要在主 agent 中直接调用 `robot_sdk` 或手写 CadQuery 代码绕过 `robot_design_agent`。

## 何时不调用 `robot_design_agent`

- 用户只问概念解释，例如“DH 为什么不等于 CAD 装配变换”。
- 用户只要求 review 某段设计流程。
- 用户只要求列计划、拆阶段、比较 skill / subagent / SDK 分工。

这类问题按本 skill 直接回答即可。

## 审查清单

回答或调用 subagent 前，检查：

- 需求是否包含 DOF、reach、payload、mounting、environment。
- DH / POE 是否只停留在运动学层，没有被当成实体 pose。
- 是否存在 `MechanicalLayout` 作为 CAD 前置语义层。
- 每个 joint 是否有 axis frame。
- 每个 link 是否有 body frame 和输入/输出 interface。
- 每个 CAD part 是否有 mate feature。
- 每条装配关系是否能追溯到 feature / constraint。
- 输出路径是否落在当前 workspace/session 目录。

## 推荐回答格式

如果是解释或 review 类问题：

```text
结论：...

分工：
- Skill：...
- Subagent：...
- SDK：...

下一步：...
```

如果调用了 `robot_design_agent`：

```text
结论：CAD MVP 已生成 / 未生成。

结果：
- STEP package：...
- 需求文档：...
- 整机 STEP：...
- 子总成：...
- 零件 STEP：...
- DOF：...
- reach：...
- 验证：...

注意：...
```
