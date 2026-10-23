# ALOHA 双臂与 π0.5 MCP

默认 VLA 场景已切换到原生 MuJoCo 的 ALOHA / ViperX 300s 双臂，保留红、绿、蓝三个方块和开口盒子。
不需要 robosuite、numba、gym-aloha 或 dm_control，也不需要因此降级 MuJoCo。
机器人 MJCF、网格和许可证位于 `../assets/aloha`；固定来源版本见其中的 `SOURCE.md`。

链路：CathyAgent → 本地技能 MCP → ALOHA 执行器 → AutoDL 纯推理 MCP → OpenPI π0.5。
LLM 只规划语言子任务与看图验证，不输出低层动作。

## 使用范围

该目录保留 ALOHA 与 π0.5 的底层运行时、策略适配与测试，不再作为
`simulation.mujoco` 的 `control_mode` 启动入口。面向 Agent 的移动与操作联调使用
`g1_vla`，由其注册的工具集合决定可用能力。
无桌面 Linux 使用 `MUJOCO_GL=egl` 并加 `--no-viewer`；macOS 离屏相机用 CGL，
不会在工作线程创建 GLFW 隐藏窗口。

旧双 Panda 配置保留为 `configs/environments/dual_panda_three_cube.yaml`，
只在显式选择时才需要 robosuite。旧依赖放在 `requirements-libero.txt`，
不应安装到默认 ALOHA 环境。

## AutoDL 部署

完整步骤见 [AUTODL_ALOHA.md](AUTODL_ALOHA.md)。复制以下三个文件到 AutoDL：

- `serve_aloha_policy.py`：加载官方 `pi05_aloha` 配置＋`pi05_base`，提供内部 WebSocket。
- `aloha_mcp_server.py`：MCP 代理，不重复加载模型，不执行机器人。
- `aloha_contract.py`：两个服务共用的状态／动作校验。

现有 `config/config.yaml` 的 `MCP.enabled: true` 和 `robot_vla` URL 可保留。
公网 URL 指向 **6006 的 /mcp**，不是模型的 8000 WebSocket。
旧的 `pi_predict` 工具不能用于新场景，必须部署 `pi_aloha_predict`。

## 数据契约

- 模型配置：`pi05_aloha`；默认权重：`gs://openpi-assets/checkpoints/pi05_base`。
- `state[14]`：左臂六关节弧度、左夹爪0..1、右臂六关节弧度、右夹爪0..1。
- 三路 RGB：`cam_high`（原 top）、`cam_left_wrist`、`cam_right_wrist`。
- MCP 参数：`prompt`、`state`、`images_base64`（三张纯 Base64 PNG/JPEG）。
- MCP 将 HWC 转为官方 AlohaInputs 要求的 CHW，官方模型 transforms 完成224缩放、
  padding、归一化、关节符号及夹爪转换。本地和 MCP 不重复做这些变换。
- 返回 `actions[H,14]` 为标准 ALOHA **绝对关节位置目标**，不是 LIBERO 末端增量。
  官方输出 transforms 已恢复绝对目标，不再额外加当前状态，也不整体裁剪到[-1,1]。
- `contract_version=aloha_joint_position_v1`、`policy_config=pi05_aloha`、
  `action_representation=absolute_joint_position`、`robot_executed=false` 必须匹配。

物理模型每臂有两个手指关节，共16个位置执行器；每臂一个模型夹爪指令映射为
对称的两个手指位置，范围使用标准 ALOHA 的 `0.01844..0.05800`，最终受资产物理限位约束。
图像使用正常 top-down 方向，不使用 LIBERO 的180度旋转。

## 执行与限制

Agent 可见工具仍为 `robot_describe`、`robot_observe`、`robot_execute_skill`。

```json
{"instruction":"put the red cube in the box","arm":"both","max_policy_steps":150}
```

只能选择 `both`；如果希望左臂操作，应通过语言表达，不丢弃另一臂的预测维度。
物理步长0.002秒，控制50Hz，每次预测最多执行前5步再观察。
等待远端期间暂停物理推进，50Hz不是墙钟实时速度承诺。
默认关节目标限速2.5rad/s、夹爪归一化目标限速5/s；还应用物理关节限位。
`action_limited_steps` 记录目标被限制的步数；限速可能影响训练策略的执行表现。

任务默认150步、最多1000步，单次技能墙钟预算300秒。结束返回检查点与三张相机帧，
`task_success` 始终为 null，必须由 Agent 看图确认。取消后不应用迟到动作；
正在运行的 GPU 推理本身不保证能中断。同进程最近128个 request_id 支持去重。

这是自定义场景，不是官方 LIBERO，也不是 ALOHA TransferCube benchmark。
腕部相机沿用资产挂载位置，FOV 从20度拓宽为75度以便观察，可配置；没有宣称匹配真实训练标定。
`pi05_base` 是基础模型，虽然官方提供 ALOHA 适配配置，但不保证直接完成三色方块入盒。
14维双臂接口支持不等于已验证双臂协同任务表现；可能需要采集演示并微调。
官方 ALOHA 仿真示例的默认专用权重是 `pi0_aloha_sim`，不是本方案的 π0.5。

## 验证

```bash
# 无云端模型的协议／双 MCP 集成回归
python -m unittest simulation.mujoco.vla.tests.test_aloha simulation.mujoco.vla.tests.test_vla -v

# 真实 ALOHA 模型、三路采图、双臂步进、非法动作与取消检查
CATHY_VLA_PHYSICS_TEST=1 python -m unittest simulation.mujoco.vla.tests.test_aloha.AlohaPhysicsTest -v
```

本机53项回归测试全部通过，包括 MuJoCo3.10.0／NumPy2.2.6 的真实离屏、双臂步进、
macOS mjpython Viewer 与相机共存测试；默认 ALOHA 与原 Z1 命令行入口均通过检查。
云端脚本通过假模型＋真实 FastMCP 的端到端协议测试。
尚未在 AutoDL 实际加载新权重，未验证真实推理延迟或抓放成功率。

参考：[OpenPI ALOHA 适配器](https://github.com/Physical-Intelligence/openpi/blob/main/src/openpi/policies/aloha_policy.py)、
[官方服务默认权重](https://github.com/Physical-Intelligence/openpi/blob/main/scripts/serve_policy.py)、
[gym-aloha 控制定义](https://github.com/huggingface/gym-aloha/blob/main/gym_aloha/tasks/sim.py)。
