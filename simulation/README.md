# Simulation

`simulation/` 是 CathyAgent 的可选仿真扩展层。它拥有自己的配置、模型资源、运行时、工具插件、测试和输出目录，不把仿真器细节写进 `cathy/` 核心。

```text
CathyAgent (Harness / 多轮上下文)
    -> ToolResult / JSON Schema
simulation/mujoco/plugin.py
    -> 同进程异步命令
simulation/mujoco/runtime.py (独占状态线程)
    -> Backend + Controller + Observation
MuJoCo model/data
```

当前只实现 MuJoCo；以后接入 Isaac Sim、RoboDojo 或真机时，应在 `simulation/` 或独立硬件包中实现同类 Runtime 契约，而不是让 CathyAgent 核心依赖具体仿真器。

详见 [MuJoCo 模块说明](mujoco/README.md)。
