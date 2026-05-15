---
name: cad
description: 用 build123d 把自然语言 → 参数化 Python 生成器 → STEP/STL 产物，纯 headless，不依赖 FreeCAD GUI / MCP。
triggers:
  - cad
  - step
  - stl
  - 3mf
  - dxf
  - build123d
  - 零件
  - 装配
  - 法兰
  - bracket
  - enclosure
  - shaft
  - flange
version: 0.2.0
---

# CAD Skill（headless · build123d 优先）

本 skill 用于把自然语言的"零件 / 装配 / 夹具 / 机构"需求转成**可重生成的 Python CAD 源码**，并产出 STEP（主）+ STL（次）等可被任意 CAD 软件查看的工件。**全流程不打开任何 GUI，不连 freecad-mcp，不写 `.FCStd`。**

> 形态参考 `text-to-cad`（earthtojake/text-to-cad）：CAD 是代码工程，源码是真理，产物只是导出物。

---

## 何时使用本 skill

* 用户提到 CAD / STEP / STP / STL / 3MF / DXF / GLB / build123d；
* 用户要求"建一个零件 / 法兰 / 支架 / 外壳 / 轴 / 装配 ..."；
* 用户给出尺寸、孔、倒角、圆角、抽壳、阵列、扫掠、放样、布尔等几何意图；
* 用户希望在没有 FreeCAD 应用的环境（CI、远程、纯 CLI）下也能跑通。

**不要使用**本 skill 的场景：

* 用户明确点名要用 FreeCAD GUI / freecad-mcp（那条路走 `mcp__freecad__*`）；
* 任务是仿真 / FEA / CAM 路径规划 / 概念渲染图——本 skill 只负责"几何生产"。

---

## 默认假设（用户没指定时直接采用）

* 单位 **mm**，基准面 **XY**，挤出 / 拉伸方向 **+Z**；
* 原点在主体中心（板类构件则在底面中心）；
* 输出 **闭合正体积 solid**，不输出曲面；
* 主产物 = **STEP**，副产物按需 = **STL / 3MF / GLB / DXF**；
* 标准光孔：M3 = Φ3.4 / M4 = Φ4.5 / M5 = Φ5.5；
* 装饰倒圆 1.0–3.0 mm；外壳壁厚 2.0–3.0 mm；
* 仅当**信息缺失会让模型不可建**、或**关键尺寸有歧义**时才提一个澄清问题，否则采用上面默认值并在最后明文标注。

---

## 强制工作流（按顺序执行；任何一步失败 → 改源码 → 重跑）

> 你可用的工具是 `write_file`、`read_file`、`list_dir`、`shell_exec`。文件全部落在当前会话沙盒 `workspaces/<sid>/` 下，相对路径即可。

### 0. 环境自检（一次性）

```
shell_exec("python -c 'import build123d, sys; print(build123d.__version__, sys.executable)'")
```

* 报 `ModuleNotFoundError` → 告诉用户："请先 `pip install build123d`（约 250 MB，包含 OCP）"，然后停止。
* 报 OK → 继续。

### 1. 写"CAD brief"（不交给用户，存在脑里）

提炼：
* 关键参数（命名，单位 mm）；
* 主特征（板厚、孔阵列、倒角 / 圆角 / 抽壳 / 阵列）；
* 产物路径（**generator 与 STEP 同名同目录**，例：`flange.py` + `flange.step`）；
* 验证目标（bbox 期望、是否封闭、体积量级、孔数量等）。

### 2. 写参数化 Python 生成器（`<name>.py`）

* 顶部放 **`PARAMS`** dict 集中所有数值，便于后续单参数调整；
* 用 build123d 的 `with BuildPart() as p:` / `BuildSketch()` / `extrude` / `fillet` / `chamfer` / `Locations` 构造；
* 末尾**必须**：
  * `from build123d import export_step, export_stl`，导出 `<name>.step` 与可选 `<name>.stl`；
  * 调用 `print_report(part)`（见模板）打印一行机器可读 JSON inspect 报告：`{"bbox":..., "volume":..., "vertices":..., "edges":..., "faces":..., "solids":..., "is_closed": true}`；
  * 用 `if __name__ == "__main__": main()`。

> 模板见 `templates/calibration_block.py`（同目录），把它当起步骨架；不要凭空发明 API。

### 3. 用 shell_exec 跑生成器

```
shell_exec("python <name>.py")
```

期望最后一行 stdout 是 `INSPECT={...}` 形式的 JSON。**读这一行**确认：
* `solids >= 1` 且 `is_closed == true`；
* `bbox` 与你脑里的预期 ±0.5 mm；
* `volume` 量级合理（不是 0、也不是离谱大）；
* `faces` / `edges` 与预期拓扑大致一致（孔 1 个 → 增 1 个柱面、4 条圆边）。

任一条不过 → **回到第 2 步改源码**，不要去手改 STEP。

### 4. 独立 inspect（可选，更严谨）

需要更细的 STEP 自检时：

```
shell_exec("python {{SKILL_DIR}}/scripts/inspect_step.py <name>.step")
```

> `{{SKILL_DIR}}` 是本 skill 目录绝对路径（让用户在系统提示词或附加指令里告诉你；如果不知道，跳过这一步，依赖 generator 自带的 INSPECT 行即可）。

### 5. 报告

给用户一个紧凑回报：
* 生成物绝对路径（STEP / STL）；
* 关键尺寸与体积；
* 跑出来的 `INSPECT` 行（删掉过长字段）；
* 仍然成立的"假设"列表（默认值未改的那些）；
* 1 条建议下一步动作（比如"想把孔从 Φ8 改成 Φ10，请告诉我"）。

---

## 不要做的事（Non-negotiables）

* **不开 GUI**、不调 `mcp__freecad__*`、不操作 `.FCStd`。
* **不要直接编辑 STEP / STL**，所有改动都从 `<name>.py` 源码出发。
* 不要把"未确认"的参数当成已确认；澄清问题最多 1 个、最少必要。
* 不要把数值散落在脚本各处——**集中在 `PARAMS`**。
* 不要靠 `git diff` STEP/STL 来判断"是否改对了"；用 `INSPECT` 行或 `inspect_step.py` 的对比。
* 不要在 generator 里启动 viewer / OCP renderer / matplotlib 弹窗（headless 必须沉默）。

---

## 验收最小例（用户说"用 cad skill 做一块校准块"）

1. 你照模板 `templates/calibration_block.py` 复制到 `workspaces/<sid>/calibration_block.py`；
2. 把 `PARAMS` 里 `L=100, W=60, T=20, HOLE_D=8, CHAMFER=2` 留默认，命名/路径调成符合 brief；
3. `shell_exec("python calibration_block.py")`；
4. 校验 `INSPECT`：`bbox≈(100,60,20)`，`solids=1`，`is_closed=true`，`faces` 包含 4 个圆柱面（4 个孔）；
5. 回报 STEP/STL 路径与上述指标。

---

## 模板与脚本（同目录）

* `templates/calibration_block.py` — 起步模板，含矩形板 + 4 个通孔 + 顶面外周倒角，生成 `*.step / *.stl` 并打印 `INSPECT` 行；
* `scripts/inspect_step.py` — 独立 inspect CLI（输入 STEP/STL 路径，输出与 generator 同形态的 JSON 报告）。

—— 严格按以上流程；任何偏差请在回报里**写明原因**。
