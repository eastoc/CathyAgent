# AutoDL：GN1x Arena G1 Static PickNPlace

## 结论

AutoDL 操作模型应固定为 `nvidia/GN1x-Tuned-Arena-G1-Static-PickNPlace`，不能再用
`GR00T-N1.7-3B + REAL_G1` 代替。本地与服务端共享同一份契约：单路 640×480
`ego_view`、50 Hz、31 维上半身状态、40 步绝对关节动作，以及静态底盘命令安全检查。

官方依据：

- [GN1x checkpoint 模型卡](https://huggingface.co/nvidia/GN1x-Tuned-Arena-G1-Static-PickNPlace)
- [训练数据 info.json](https://huggingface.co/datasets/nvidia/Arena-G1-Static-PickNPlace-Task/blob/main/lerobot/meta/info.json)
- [训练数据 modality.json](https://huggingface.co/datasets/nvidia/Arena-G1-Static-PickNPlace-Task/blob/main/lerobot/meta/modality.json)
- [Arena G1 官方 modality 配置](https://github.com/isaac-sim/IsaacLab-Arena/blob/main/isaaclab_arena_gr00t/embodiments/g1/g1_sim_wbc_data_gr00t_n_1_7_config.py)
- [Arena G1 官方相机定义](https://github.com/isaac-sim/IsaacLab-Arena/blob/main/isaaclab_arena/embodiments/g1/g1.py)

## 对齐后的接口

| 项目 | 值 |
|---|---|
| checkpoint | `nvidia/GN1x-Tuned-Arena-G1-Static-PickNPlace` |
| embodiment | `new_embodiment` |
| 任务指令 | `move the apple to the plate` |
| 视频 | 当前时刻一帧 `ego_view`，RGB 640×480 |
| 状态 | `left_arm(7), right_arm(7), left_hand(7), right_hand(7), waist(3)` |
| 动作 | 上述 31 维 + `base_height_command(1)` + `navigate_command(3)` |
| 动作语义 | 40 步、绝对物理关节位置、50 Hz |
| 本地执行 | 双臂、双 Dex3、腰部；根部和腿固定 |
| 安全边界 | 底盘高度或导航输出绝对值超过 0.05 时拒绝整个动作步 |

GN1x 对应 IsaacLab-Arena 的静态 G1/WBC 环境。本地首阶段没有复刻 AGILE/WBC，因此明确固定
根部和腿部；不把非零移动命令静默忽略，也不宣称动力学与 Isaac Sim 完全等价。

## 先验证本地适配

在 CathyAgent 根目录执行：

```bash
conda activate agent
python -m simulation.mujoco.g1.manipulation.build_scene
/opt/anaconda3/envs/agent/bin/mjpython -m unittest \
  simulation.mujoco.g1.manipulation.test_manipulation -v

# macOS 有窗口预览；不会调用 GN1x
mjpython -m simulation.mujoco.g1.manipulation.run \
  --viewer --self-test --task apple_to_plate --seconds 5
```

预览图和最终状态写入 `simulation/mujoco/g1/manipulation/outputs/`。

## AutoDL 下载与切换

以下命令在 AutoDL 的 Isaac-GR00T 环境执行。上游安装方式以
[Isaac-GR00T 官方说明](https://github.com/NVIDIA/Isaac-GR00T#installation)为准。

```bash
cd /root/autodl-tmp/Isaac-GR00T
export HF_HOME=/root/autodl-tmp/huggingface

uvx --from huggingface-hub hf auth login
uvx --from huggingface-hub hf download \
  nvidia/GN1x-Tuned-Arena-G1-Static-PickNPlace \
  --local-dir /root/autodl-tmp/models/GN1x-Tuned-Arena-G1-Static-PickNPlace

uv pip install --python .venv/bin/python \
  -r /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/requirements-server.txt
```

启动前必须把本目录的 `server.py`、`contract.py`、`calibration.json` 和
`arena_g1_modality.py` 一起同步到 AutoDL；只替换模型目录不够。

```bash
cd /root/autodl-tmp/Isaac-GR00T
.venv/bin/python \
  /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/server.py \
  --model-path /root/autodl-tmp/models/GN1x-Tuned-Arena-G1-Static-PickNPlace \
  --embodiment-tag new_embodiment \
  --modality-config-path \
    /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/arena_g1_modality.py \
  --calibration \
    /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/calibration.json \
  --device cuda:0 --host 127.0.0.1 --port 6006
```

不要启动多个 worker，否则每个进程都会加载一份模型。服务可先用 `--mock` 验证 HTTP、图像和
动作契约；mock 只保持当前姿态，不代表真实模型推理。

```bash
curl --fail http://127.0.0.1:6006/health
curl --fail -H "Authorization: Bearer ${GROOT_API_TOKEN}" \
  http://127.0.0.1:6006/metadata
```

未设置 `GROOT_API_TOKEN` 时去掉 Authorization 请求头。

## 本地连接 AutoDL

推荐使用 SSH 隧道；主机和 SSH 端口替换为 AutoDL 控制台给出的值。平台说明见
[AutoDL SSH 隧道文档](https://www.autodl.com/docs/ssh_proxy/)。

```bash
ssh -N -L 16006:127.0.0.1:6006 \
  -p YOUR_SSH_PORT root@YOUR_AUTODL_SSH_HOST
```

另一个本地终端执行：

```bash
export GROOT_URL=http://127.0.0.1:16006
export GROOT_API_TOKEN=YOUR_TOKEN  # 服务端未设置时省略

mjpython -m simulation.mujoco.g1.manipulation.run \
  --connect --url "$GROOT_URL" --task apple_to_plate \
  --prompt "move the apple to the plate" \
  --chunks 5 --execution-horizon 5 --viewer
```

若使用 AutoDL 自定义服务，则服务端改为 `--host 0.0.0.0 --port 6006`，本地
`GROOT_URL` 使用控制台生成的 HTTPS 地址；端口映射规则见
[AutoDL 自定义服务文档](https://www.autodl.com/docs/port/)。

## 相机与 Dex3

`calibration.json` 已按 Arena 源定义转换：相机从 Isaac 的 `head_link` 坐标转换到本地
固定融合后的 `torso_link`，位置为 `[0.0488135, 0, 0.30925] m`，焦距 15 mm，推导的
垂直视场角为 55.2978°。场景只保留这一台策略相机。

Dex3 使用数据集顺序：`index_0, index_1, middle_0, middle_1, thumb_0, thumb_1,
thumb_2`，左右手各 7 个绝对关节角。不得压缩成单维夹爪开度，也不得补零或截断。

## 总结

本地接口、相机、Dex3 与 GN1x checkpoint 的公开训练契约已经统一。完成本地测试后，只需在
AutoDL 同步这些契约文件、下载目标 checkpoint 并按上述命令重启 6006 服务，即可进入真模型闭环联调。
