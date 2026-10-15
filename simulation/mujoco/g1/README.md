# G1 已知地图导航

双夹爪、固定下半身的操作仿真与 AutoDL GR00T N1.7 HTTP 6006 部署，见
[manipulation 部署说明](manipulation/AUTODL_GR00T_N17.md)。该入口独立于下文的 SONIC 导航。

## 结论

当前实现采用“Agent 选可达地面点，导航服务强制复核，A* 生成路径，SONIC 闭环执行”的四层结构。地图来自 MuJoCo 场景先验，不做实时建图；`navigate` 只接受世界系二维坐标和最终 yaw。冰箱、岛台等物体中心是语义地标，不是可导航表面。

## 数据流

```text
用户指令
  -> Agent: get_map
  -> Agent: 从绿色连通地面选择 (x, y, yaw)
  -> navigate
     -> 实体占据 / 膨胀半径 / 连通分量检查
     -> A* + 视线简化
     -> 路点跟随器
     -> SONIC command/planner ZMQ
     -> 同进程 SONIC ONNX + G1 关节 PD
     -> MuJoCo 根 qpos 位姿反馈
     -> 到达 XY 后原地对齐 yaw
```

核心边界：

- `navigation/map.py`：解析 MJCF 障碍，栅格化并按 G1 半径膨胀。
- `navigation/reachability.py`：拒绝物体中心、膨胀禁区、地图外和断连目标。
- `navigation/planner.py`：八邻域 A*，禁止对角穿角，并按净空增加代价。
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

默认配置 `execution_enabled: false`，此时 `navigate` 会完成可达性检查和 A*，但返回 `EXECUTION_DISABLED`，不会伪报已移动。启用真实 SONIC 执行时使用 `g1_open_kitchen_sonic.yaml`：

1. 终端一执行 `conda activate agent && python -m simulation.mujoco.g1.run_sonic_sim`。macOS 开启 Viewer 时，该入口会自动切换到当前 Conda 环境的 `mjpython`；无窗口运行可传 `--no-viewer`。该入口直接加载厨房 MJCF 和三个 ONNX 模型，在内存中交换 MuJoCo 状态与 29 关节控制目标，不启动 DDS。看到“`SONIC ONNX 同进程控制器就绪`”后继续。
2. 终端二执行 `conda activate agent && python -m simulation.mujoco --control-mode g1_navigation --environment configs/environments/g1_open_kitchen_sonic.yaml --agent`。Agent 在 `5556` 发布导航命令，并从 `5558` 接收真实根位姿。

macOS 首次启动时只需允许两个进程使用本机 TCP 回环网络，供导航 ZMQ 命令和根位姿反馈使用。
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

测试覆盖实体目标拒绝、膨胀净空、A*、地图图像、SONIC packed wire format、闭环 XY/yaw 到达和取消前不启动运动。

## 总结

这套边界保证 Agent 只能选择地图上的地面位姿，服务端仍会独立拒绝不可达目标；只有 A* 成功、SONIC 可用且 MuJoCo 根位姿持续更新时，G1 才会执行导航。
