# Robot CAD Agent Plan

## 结论

第一阶段已经完成：CathyAgent 现在可以把一段机器人 CAD 需求跑成可复现的 MVP 产物，包括 `需求文档.md`、粗粒度 CadQuery 零件、语义命名的 STEP package、整机 STEP 和基础验证报告。

下一阶段的重点不是继续堆 CAD 外形，而是先补齐 **资料驱动的运动学建模能力**：当用户说“参考 UR3E / UR5 / 某机型 DH 模型”时，系统不能再使用占位 DH 模板，而应走 profile / search / validation 流程。

核心原则保持不变：**DH / POE 是运动学模型，不等于 CAD 实体装配变换**。CAD 仍必须经过 `MechanicalLayout` 语义层。

## 当前状态

### 已完成

- `robot_sdk/types.py`：核心数据契约，包括 `RobotRequirement`、`KinematicModel`、`DHParam`、`MechanicalLayout`、`PartFeature`、`AssemblyConstraint`。
- `robot_sdk/structure/ids.py`：串联链 ID、frame ID、interface ID 生成。
- `robot_sdk/kinematics/dh.py`：DH transform、FK、reach 估算。
- `robot_sdk/layout/`：`MechanicalLayout`、frame mapping、layout validation。
- `robot_sdk/assembly/`：mate feature、neutral constraint、CadQuery adapter。
- `robot_sdk/cad/`：粗粒度零件、deterministic assembly placement、STEP package 导出。
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

- DH 模型仍是 MVP 占位模板：根据 `reach` 和 `dof` 分配 `a`，并把 `alpha/d/theta` 设为 0。
- `robot_design_agent` 不识别 UR3E / UR5 / PUMA 等机型 profile。
- `robot_design_agent` 不会自己调用 `search_agent` 搜资料。
- “参考 UR3E”目前只会进入 request 文本，不会影响 DH 参数。
- `MechanicalLayout` 目前只有 `tabletop_serial_arm` 简化模板。
- CAD 几何仍是概念级：box link、cylinder joint、simple flange。

## 下一阶段目标

阶段 2 的目标：把 DH 生成从“占位模板”升级为“资料/机型/profile 驱动”。

用户输入：

```text
6DoF 机械臂，工作空间 500mm，DH 模型参考 UR3E。
```

期望系统行为：

```text
识别 UR3E 参考
-> 优先查本地 robot profile
-> 本地没有时调用 search_agent 搜资料
-> 提取/确认 DH 参数、单位、standard/modified convention
-> 必要时按目标 workspace 缩放或标注不可缩放
-> 调 robot_sdk.kinematics.dh 做 FK/reach 校验
-> 写入 需求文档.md
-> 再进入 MechanicalLayout 和 CAD 导出
```

## 阶段 2 实现顺序

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

### 3. `subagents/robot_design_agent/kinematics_rules.py`

把当前 `_build_simple_kinematic_model()` 拆成规则化入口：

```text
build_kinematic_model(requirement, request_text)
  -> detect profile reference
  -> load local profile if available
  -> optionally scale profile
  -> fallback to MVP template
```

首版先不做 LLM 子 agent，保持确定性规则。

识别规则：

- `ur3e` / `UR3E` / `UR3 e`
- `ur5e`
- `puma560`
- “参考 XXX”
- “类似 XXX”

fallback 规则：

- 如果没有 profile reference，继续用当前 MVP simple DH。
- 如果有 reference 但 profile 不存在，返回 warning，不要静默使用 simple DH。

### 4. Search-Assisted DH

当本地 profile 没有命中时，再考虑搜索。

建议先由主 agent 编排：

```text
主 agent
  -> read_skill(robot_cad_design)
  -> 判断需要资料
  -> search_agent 搜索官方/可信来源
  -> 把整理后的 DH 表传给 robot_design_agent
```

之后再做内部子 agent：

```text
subagents/kinematics_agent/
  agent.py
```

`kinematics_agent` 职责：

- 搜索资料。
- 提取 DH 表。
- 判断 standard DH / modified DH。
- 统一单位。
- 调 SDK FK/reach 校验。
- 输出 `KinematicModel` 和 citation/source summary。

### 5. `需求文档.md` 增强

需求文档新增：

- `运动学来源`
- `profile 名称`
- `source`
- `exact_profile / scaled_profile / template_dh`
- `scale factor`
- `FK/reach 校验结果`
- `warnings`

如果用户要求 UR3E，但系统使用了 fallback template，文档必须显式警告。

### 6. Tests

新增测试：

- `tests/test_robot_sdk_kinematic_profiles.py`
- `tests/test_robot_sdk_kinematic_scaling.py`
- `tests/test_robot_design_agent_kinematics_rules.py`

关键断言：

- `UR3E` 文本能命中 `ur3e` profile。
- profile DH 不会被 simple template 覆盖。
- scaled profile 会记录 scale factor。
- fallback template 会产生 warning。
- `需求文档.md` 能展示 profile/source/scale/reach check。

## 阶段 2 验收标准

- 用户说“DH 模型参考 UR3E”时，不再生成 `150, 70, 70, ...` 这种占位 DH。
- 输出能说明 DH 来源是 `ur3e profile`、搜索资料，还是 fallback template。
- `需求文档.md` 中包含完整 DH 表、单位、convention、来源和 warnings。
- SDK 能对 profile DH 做 FK/reach 基础校验。
- fallback 行为必须可见，不能把 template DH 伪装成参考机型 DH。

## 阶段 3 预告

阶段 2 完成后，再拆更细的 subagent：

- `requirement_agent`：需求结构化、缺失信息补全。
- `kinematics_agent`：profile/search-assisted DH、FK/reach 校验。
- `layout_agent`：从 `KinematicModel` 生成 `MechanicalLayout`。
- `cad_agent`：生成 CAD、STEP package、需求文档。
- `verification_agent`：整体验证报告。

拆分前提：

- `RobotRequirement`
- `KinematicModel`
- `MechanicalLayout`
- `CadQueryStepPackageExportResult`

这些数据契约必须稳定。

## 暂不做

- 电机/减速器真实选型。
- 动力学/力矩闭环。
- 碰撞检测和运动规划。
- 工业级制造图纸。
- 复杂外形建模。
- 让 LLM 直接手写 CAD pose 或替代 `MechanicalLayout`。

## 总结

第一阶段已经证明“需求 -> DH -> MechanicalLayout -> CadQuery -> STEP package”的闭环能跑通。下一阶段要解决的是运动学可信度：把 DH 参数从 MVP 占位模板升级为 profile/search-assisted 的可追溯模型。只有这一步稳定后，再拆 `kinematics_agent`、`layout_agent`、`cad_agent` 才不会把错误数据契约扩散到更多 agent 里。
