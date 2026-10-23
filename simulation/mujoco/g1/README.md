# G1 已知地图导航与 GN1x 静态抓放

操作仿真已切换为 NVIDIA `GN1x-Tuned-Arena-G1-Static-PickNPlace`，并按其公开的
Arena G1 数据契约使用单路 `ego_view`、G1 双臂/腰部和左右 Dex3。AutoDL HTTP 服务在
容器内监听 6006；SONIC 到达岛台南侧后，操作入口会校验最终世界位姿和停止状态，再切换到
同一厨房坐标中的固定站立 Dex3 场景。

导航与 GN1x 高层工具合并为 `g1_vla` 模式。Agent 可见工具为
`get_map`、`navigate`、`g1_describe`、`g1_observe`、`g1_pick`、`g1_place`、
`g1_release`、`g1_stow` 和 `g1_stop`。系统提示词要求先把复合指令拆成可验证步骤，再依次执行；
`pick/place` 的动作块重规划、接触检测和结果验证都封装在一次工具调用内。

```bash
export GROOT_URL=https://u405425-xd7z-334db773.weste.seetacloud.com:8443

# 终端一：SONIC MuJoCo（默认无窗口）
python -m simulation.mujoco.g1.run_sonic_sim

# 终端二：地图导航 + GN1x manipulation Agent
mjpython -m simulation.mujoco \
  --control-mode g1_vla \
  --environment configs/environments/g1_open_kitchen_sonic.yaml \
  --agent
```

`g1_vla` 联调默认只打开一个窗口：终端一的 SONIC 导航仿真无窗口运行，并把完整根位姿与
29 个实际关节角发布到终端二的 G1 + Dex3。操作场景直接复用导航厨房，并加入苹果和盘子，
是 GN1x 图像与动作闭环对应的唯一 Viewer。关闭该窗口会停止后续操作；完全 headless 时添加
`--no-viewer`。单独调试行走时可在终端一添加 `--viewer`，此时才会额外打开导航窗口。

开始操作前先调用 `g1_describe`。仿真发送一帧 640×480 RGB `ego_view` 和 31 维绝对关节状态：
左右臂各 7、左右手各 7、腰部 3。服务返回 40 步绝对关节动作，以及
`base_height_command` 和 `navigate_command`。本地固定根部与腿部，并在后两项非零时直接拒绝
执行，避免静态 checkpoint 的命令被静默丢弃。

直接联调一段动作块：

```bash
conda activate agent
mjpython -m simulation.mujoco.g1.manipulation.run \
  --connect --task apple_to_plate --chunks 1 --execution-horizon 1
```

模型、数据字段和相机分别依据 [GN1x 模型卡](https://huggingface.co/nvidia/GN1x-Tuned-Arena-G1-Static-PickNPlace)、
[Arena G1 modality](https://github.com/isaac-sim/IsaacLab-Arena/blob/main/isaaclab_arena_gr00t/embodiments/g1/g1_sim_wbc_data_gr00t_n_1_7_config.py)
与 [IsaacLab-Arena G1 相机定义](https://github.com/isaac-sim/IsaacLab-Arena/blob/main/isaaclab_arena/embodiments/g1/g1.py)。

## 结论

当前实现采用“Agent 选可达地面点，导航服务强制复核，D* Lite 生成路径，SONIC 闭环执行”的四层结构。地图来自 MuJoCo 场景先验，不做实时建图；`navigate` 只接受世界系二维坐标和最终 yaw。冰箱、岛台等物体中心是语义地标，不是可导航表面。

## 数据流

```text
用户指令
  -> Agent: get_map
  -> Agent: 从绿色连通地面选择 (x, y, yaw)
  -> navigate
     -> 实体占据 / 膨胀半径 / 连通分量检查
     -> D* Lite + 视线简化
     -> 路点跟随器
     -> SONIC command/planner ZMQ
     -> 同进程 SONIC ONNX + G1 关节 PD
     -> MuJoCo 根 qpos 位姿反馈
     -> 到达 XY 后原地对齐 yaw
```

核心边界：

- `navigation/map.py`：解析 MJCF 障碍，栅格化并按 G1 半径膨胀。
- `navigation/reachability.py`：拒绝物体中心、膨胀禁区、地图外和断连目标。
- `navigation/planner.py`：八邻域 D* Lite，禁止对角穿角，按净空增加代价，并在地图变化后增量重规划。
- `sonic/follower.py`：将路径切线转换为世界系 `movement/facing`；到达坐标后单独对齐最终朝向。
- `sonic/executor.py`：20 Hz 闭环、位姿新鲜度、超时、取消和禁区复核。
- `plugin.py`：只暴露 `get_map()` 与 `navigate(goal_xy_m, goal_yaw_rad)`。

SONIC 的方向输入采用 MuJoCo 世界坐标系，`movement_direction` 与 `facing_direction` 相互独立；这里在行走阶段令二者都沿路径切线，最后令移动向量为零并对齐目标 yaw。协议与字段遵循 [NVIDIA Kinematic Planner 参考](https://nvlabs.github.io/GR00T-WholeBodyControl/references/planner_onnx.html)和[官方 ZMQ sender](https://github.com/NVlabs/GR00T-WholeBodyControl/blob/main/gear_sonic/utils/teleop/zmq/zmq_planner_sender.py)。

## 运行

SONIC 官方仓库默认放在：

```text
third_party/GR00T-WholeBodyControl
```

默认导航模型必须位于：

```text
gear_sonic_deploy/policy/release/model_encoder.onnx
gear_sonic_deploy/policy/release/model_decoder.onnx
gear_sonic_deploy/policy/release/observation_config.yaml
gear_sonic_deploy/planner/target_vel/V2/planner_sonic.onnx
```

在 `agent` Conda 环境执行预检：

```bash
conda activate agent
python -m simulation.mujoco.g1.check_sonic
```

预检分别检查 MuJoCo、三个官方 ONNX 模型和 ONNX Runtime provider。
本项目需要 `mujoco`、`pyyaml`、`pyzmq` 与 `onnxruntime>=1.20`，不再依赖
CycloneDDS、CUDA 或 TensorRT。模型接口与官方部署代码保持一致，
参考 [Kinematic Planner ONNX 接口](https://nvlabs.github.io/GR00T-WholeBodyControl/references/planner_onnx.html)。

可直接基准测试三个官方 ONNX 模型：

```bash
conda activate agent
python -m simulation.mujoco.g1.benchmark_sonic_onnx
```

MuJoCo 和 SONIC 在同一个进程运行：物理主循环为 200 Hz，编码器和策略解码器为
50 Hz，规划线程以 10 Hz 检查输入；慢走模式每 1 秒执行一次规划模型。规划结果按
官方方式从 30 Hz 重采样到 50 Hz，并用 8 帧融合新旧轨迹。规划器接口见
[官方 Kinematic Planner 参考](https://nvlabs.github.io/GR00T-WholeBodyControl/references/planner_onnx.html)。

只检查地图和工具：

```bash
python -m simulation.mujoco --control-mode g1_navigation --describe
python -m simulation.mujoco --control-mode g1_navigation --observe
python -m simulation.mujoco --control-mode g1_navigation --agent
```

默认配置 `execution_enabled: false`，此时 `navigate` 会完成可达性检查和 D* Lite 规划，但返回 `EXECUTION_DISABLED`，不会伪报已移动。启用真实 SONIC 执行时使用 `g1_open_kitchen_sonic.yaml`：

1. 终端一执行 `conda activate agent && python -m simulation.mujoco.g1.run_sonic_sim`。该入口默认无窗口，直接加载厨房 MJCF 和三个 ONNX 模型，在内存中交换 MuJoCo 状态与 29 关节控制目标，不启动 DDS。只有单独调试导航画面时才添加 `--viewer`；macOS 会自动切换到当前 Conda 环境的 `mjpython`。看到“`SONIC ONNX 同进程控制器就绪`”后继续。
2. 终端二执行 `conda activate agent && python -m simulation.mujoco --control-mode g1_navigation --environment configs/environments/g1_open_kitchen_sonic.yaml --agent`。Agent 在 `5556` 发布导航命令，并从 `5558` 接收真实根位姿、29个关节角和行走状态。

macOS 首次启动时只需允许两个进程使用本机 TCP 回环网络，供导航 ZMQ 命令和全身状态反馈使用。
不经过网络的闭环步态自检命令是：

```bash
python -m simulation.mujoco.g1.run_sonic_sim \
  --no-viewer --self-test-walk --max-seconds 12
```

独立的 `run_sonic_onnx` DDS 入口作为未来连接真实 G1 的适配器保留，不参与当前 MuJoCo 导航。

若已有自定义 MuJoCo 主循环，也可直接接入位姿发布器：

```python
from simulation.mujoco.g1.sonic import ZmqNavigationPosePublisher

nav_pose = ZmqNavigationPosePublisher()
nav_pose.open()
try:
    while viewer.is_running():
        sim.step()
        nav_pose.publish_qpos(sim.mj_data.qpos)
finally:
    nav_pose.close()
```

不允许用命令速度积分代替根位姿反馈。SONIC 官方 `g1_debug` 主要提供关节和 IMU 状态；二维导航到达判定必须读取 MuJoCo free-joint 的 `qpos[0:7]`。官方 MuJoCo 仿真也把 floating base pose 取自 `mj_data.qpos[:7]`，见 [BaseSimulator](https://github.com/NVlabs/GR00T-WholeBodyControl/blob/main/gear_sonic/utils/mujoco_sim/base_sim.py)。

## 测试

```bash
python -m unittest \
  simulation.mujoco.tests.test_g1_sonic_onnx \
  simulation.mujoco.tests.test_g1_navigation \
  simulation.mujoco.tests.test_g1_open_kitchen_asset -v
```

测试覆盖实体目标拒绝、膨胀净空、D* Lite、地图图像、SONIC packed wire format、闭环 XY/yaw 到达和取消前不启动运动。

## 总结

这套边界保证 Agent 只能选择地图上的地面位姿，服务端仍会独立拒绝不可达目标；只有 D* Lite 成功、SONIC 可用且 MuJoCo 根位姿持续更新时，G1 才会执行导航。
