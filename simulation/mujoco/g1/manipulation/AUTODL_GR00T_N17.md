# AutoDL GR00T N1.7 + G1 双夹爪 manipulation

可以使用 **AutoDL GPU 推理 → HTTP 6006 → 本地 MuJoCo 上半身控制**。本项目已提供
HTTP 服务、仿真客户端和双夹爪 G1。此版本固定 pelvis 与双腿，控制双臂 14 关节、腰部
3 关节和 2 个夹爪，适合先验证桌面操作。现有 SONIC 导航仍使用原模型；本版本尚未实现
行走中 manipulation 或两个控制器之间的切换。

**当前不能声称已经与 NVIDIA 的训练相机/夹爪物理标定完全对齐。** 用户尚未提供对应数据集、
相机内外参、夹爪规格和 TCP 标定。默认几何是可运行的项目配置，标记为
`training_alignment_verified: false`。基础模型含 `REAL_G1` 接口，不代表换夹爪后必然零样本抓取成功。
[官方模型说明](https://huggingface.co/nvidia/GR00T-N1.7-3B)说明可针对具体机器人和任务进行 post-training。

## 1. 接口选择与已核实的约束

本项目选择 HTTP API，便于 AutoDL 自定义服务和本地 Python 客户端使用，不额外部署 MCP。

| 项目 | 本实现 |
|---|---|
| AutoDL 容器内服务端口 | `6006` |
| 本地 SSH 转发入口 | `16006`（可选；本地不部署推理服务） |
| 接口 | `GET /health`、`GET /metadata`、`POST /infer`、`GET /docs` |
| 基础 checkpoint | `nvidia/GR00T-N1.7-3B`，tag `REAL_G1` |
| 夹爪微调 checkpoint | 本项目 `NEW_EMBODIMENT` 配置训练的模型 |
| 云端对外输出 | 双腕位姿、双臂、双手/夹爪、腰部；过滤底盘高度和导航命令 |
| 本地实际执行 | 双臂与腰部绝对关节角 + 两夹爪开度；不再对 EEF 同时做 IK |
| 本地仿真 | 物理 500 Hz、项目默认动作采样 20 Hz、每次执行预测前 8 步再重规划 |
| 网络等待 | 暂停仿真时间；这是同步仿真客户端，不是实机实时控制器 |

N1.7 的[官方 processor 配置](https://huggingface.co/nvidia/GR00T-N1.7-3B/blob/main/processor_config.json)
给出的 REAL_G1 视频键为 `ego_view`，历史索引为 `[-20, 0]`，状态为当前时刻的
`left_wrist_eef_9d`、`right_wrist_eef_9d`、`left_hand`、`right_hand`、`left_arm`、`right_arm`、`waist`，
预测 40 个时间步，并包含 `base_height_command` 和 `navigate_command`。本服务在输出端删除后两项。
**基础模型内部仍预测完整动作；要让训练动作空间本身只有上半身，使用本文的自定义微调配置。**

`Gr00tPolicy.get_action()` 已完成反归一化及相对→绝对转换，因此本地不能再加一次当前关节角。
依据：[官方 decode_action 实现](https://github.com/NVIDIA/Isaac-GR00T/blob/main/gr00t/model/gr00t_n1d7/processing_gr00t_n1d7.py)。

`left_hand/right_hand` 的维度必须读取实际 checkpoint 的 `statistics.json`，不能从键名推断为
一个夹爪开度。服务端启动后 `/metadata` 会报告真实维度；客户端遇到不兼容维度会直接报错，
不会补零伪造灵巧手状态，也不会把多个手指关节平均成一个夹爪指令。

## 2. AutoDL 环境准备

推荐先选一张 24 GB 或更大显存的 NVIDIA GPU 做推理；这是为图像历史和运行开销预留余量的选择。
官方推理起点为 16 GB+，微调建议 40 GB+。使用 Ubuntu 22.04/24.04、CUDA 12.8 工具链、Python 3.12，
具体依赖以克隆到的上游版本为准。[官方安装要求](https://github.com/NVIDIA/Isaac-GR00T#installation)

以下命令在 AutoDL 的终端执行：

```bash
nvidia-smi
nvcc --version
apt-get update
apt-get install -y git git-lfs ffmpeg tmux
git lfs install

mkdir -p /root/autodl-tmp
cd /root/autodl-tmp
git clone --recurse-submodules https://github.com/NVIDIA/Isaac-GR00T.git
cd Isaac-GR00T
python -m pip install uv
# 即使 AutoDL 是 x86_64，上游 uv 锁定也会读取 aarch64 的本地 wheel。
# 确保它已由 Git LFS 展开为完整 ZIP，而不是文本指针。
git lfs pull --include="scripts/deployment/dgpu/wheels/torchcodec-0.8.0-cp312-cp312-linux_aarch64.whl"
python -m zipfile -t \
  scripts/deployment/dgpu/wheels/torchcodec-0.8.0-cp312-cp312-linux_aarch64.whl
uv sync --python 3.12
git rev-parse HEAD > /root/autodl-tmp/isaac-groot-commit.txt
uv run python -c 'import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())'
```

`torch.cuda.is_available()` 应为 `True`。`nvidia-smi` 的 CUDA 数字是驱动兼容上限，
不能代替 `nvcc`/PyTorch CUDA 环境检查。FFmpeg 需使用上游支持的版本；当前安装指南注明
`torchcodec==0.8.0` 支持 FFmpeg 4–7。

先在 [Cosmos-Reason2-2B](https://huggingface.co/nvidia/Cosmos-Reason2-2B) 页面申请访问权限，
再登录并下载。当前 GR00T 会加载这个 gated backbone，仅下载 GR00T 权重不一定足够。

```bash
cd /root/autodl-tmp/Isaac-GR00T
export HF_HOME=/root/autodl-tmp/huggingface
# 使用隔离的 CLI，下载模型时不触发当前 GR00T 项目的 uv sync。
uvx --from huggingface-hub hf auth login
uvx --from huggingface-hub hf download nvidia/GR00T-N1.7-3B \
  --local-dir /root/autodl-tmp/models/GR00T-N1.7-3B
uvx --from huggingface-hub hf download nvidia/Cosmos-Reason2-2B
```

如果网络不可达，先配置实例可用的联网方式；401/403 则检查账号授权和 token，不能用改模型名解决。
若出现 `unable to locate the end of central directory record`，执行
`git lfs fetch origin && git lfs checkout` 后再次运行上面的 `python -m zipfile -t`；
只有输出 `Done testing` 才继续 `uv sync`。这是仓库依赖文件问题，不是模型权重损坏。

## 3. 上传本项目与启动 6006 API

将 CathyAgent 项目同步到 `/root/autodl-tmp/CathyAgent`。GPU 端实际只需要
`simulation/mujoco/g1/manipulation/` 下的服务与契约文件；直接运行 `server.py` 不依赖 MuJoCo。
本地未提交文件不会自动出现在云端 `git clone` 的目录中，需要一并同步。

在 AutoDL 执行：

```bash
cd /root/autodl-tmp/Isaac-GR00T
uv pip install --python .venv/bin/python \
  -r /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/requirements-server.txt

# 先读接口和真实状态/动作维度，不占 GPU。
.venv/bin/python /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/inspect_checkpoint.py \
  /root/autodl-tmp/models/GR00T-N1.7-3B

tmux new -s groot
# 在 tmux 中运行：
.venv/bin/python /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/server.py \
  --model-path /root/autodl-tmp/models/GR00T-N1.7-3B \
  --embodiment-tag REAL_G1 \
  --device cuda:0 \
  --host 127.0.0.1 --port 6006
```

等待模型加载完成。另开终端验证：

```bash
curl --fail http://127.0.0.1:6006/health
curl --fail http://127.0.0.1:6006/metadata
```

`/health` 的 `backend` 应为 `gr00t_n1.7`。如果是 `mock_hold_not_gr00t`，运行的只是联调模式。
不要启动多个 uvicorn worker，否则每个进程会各加载一份模型。

不使用 HTTP 封装时，也可以启动官方 ZMQ 服务：

```bash
uv run python gr00t/eval/run_gr00t_server.py \
  --model-path /root/autodl-tmp/models/GR00T-N1.7-3B \
  --embodiment-tag REAL_G1 --device cuda:0 \
  --host 127.0.0.1 --port 6006
```

这是**替代方案**，不能与 HTTP 服务同时占用 6006，也不能使用 HTTP 客户端或 `curl /infer` 访问它。
官方 ZMQ 服务本身不会过滤下半身输出。[官方 Policy API](https://github.com/NVIDIA/Isaac-GR00T/blob/main/getting_started/policy.md)

## 4. 本地连接 AutoDL

推荐 SSH 隧道。在本地终端，将主机和 SSH 端口替换为 AutoDL 控制台给出的值：

```bash
ssh -N -L 16006:127.0.0.1:6006 -p YOUR_SSH_PORT root@YOUR_AUTODL_SSH_HOST
```

保持终端运行，客户端地址为 `http://127.0.0.1:16006`。`16006` 是本地隧道入口，
右侧 `6006` 才是 AutoDL 服务端口；本地不需要启动推理服务。SSH 登录端口也不等于这两个端口。
[AutoDL SSH 隧道说明](https://www.autodl.com/docs/ssh_proxy/)

使用 AutoDL「自定义服务」时，HTTP 服务改为 `--host 0.0.0.0 --port 6006`，选择 HTTP 映射，
客户端使用控制台生成的完整 HTTPS URL。公网地址可能使用 8443，容器内部依然监听 6006。
开放服务的账号认证限制以控制台为准。[AutoDL 端口说明](https://www.autodl.com/docs/port/)
不要使用“实例公网 IP:6006”替代平台映射地址。

HTTP 服务支持可选鉴权：在服务端和本地客户端设置相同的 `GROOT_API_TOKEN` 环境变量。
服务读取 `Authorization: Bearer ...`；`/health` 不要求 token。

## 5. 本地 G1 夹爪模型与联调

在 CathyAgent 根目录执行，已有 `agent` 环境可直接使用：

```bash
conda activate agent
python -m pip install -r simulation/mujoco/requirements.txt
python -m pip install httpx Pillow

python -m simulation.mujoco.g1.manipulation.build_scene
python -m simulation.mujoco.g1.manipulation.run --self-test --seconds 3
```

有窗口预览，macOS 用 `mjpython`，Linux 可用 `python`：

```bash
mjpython -m simulation.mujoco.g1.manipulation.run --viewer --self-test --seconds 10
```

无窗口 Linux 渲染可设置 `MUJOCO_GL=egl`（GPU）或安装 OSMesa 后使用 `osmesa`。
预览 PNG 与最后的真实关节状态保存在 `simulation/mujoco/g1/manipulation/outputs/`。

先验证 HTTP/图像/动作管线，在 AutoDL 服务终端把模型命令临时替换为：

```bash
.venv/bin/python /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/server.py \
  --mock --host 127.0.0.1 --port 6006
```

然后本地：

```bash
python -m simulation.mujoco.g1.manipulation.run \
  --url http://127.0.0.1:16006 --chunks 2 --execution-horizon 8 \
  --prompt "Pick up the red cube and place it on the green target."
```

mock 只回传当前姿态，验证传输而不抓取。接入真实模型时恢复第 3 节的启动命令。
若 REAL_G1 的手状态不是本项目定义的一维夹爪开度，客户端会拒绝运行；按下一节准备微调模型，
不能靠关闭 strict、换 tag 或补零使其变成语义兼容的夹爪策略。

## 6. 夹爪和相机如何对齐

没有统一的“所有 N1.7 训练数据相机位姿”：它是跨 embodiment 模型，必须选定目标数据集。
当前已对齐可核实的字段、历史索引、绝对动作输出语义；物理外参仍待目标数据核实。

`calibration.json` 集中定义相机安装和夹爪参数。当前默认值：

| 参数 | 项目默认值（不是官方训练标定） |
|---|---|
| 相机父坐标系 | `torso_link`，包含腰部运动 |
| 相机位置 | `[0.08, 0, 0.42] m`，相对 torso |
| 观察方向 | 向前并向下 35°；MuJoCo 相机沿自身 `-Z` 看，`+Y` 为画面上方 |
| 图像 | RGB，640×480，垂直 FOV 60°，中心主点、无畸变 |
| 左右夹爪安装 | wrist_yaw_link 下 `[0.05, ±0.003, 0] m` |
| 指间净开口 | `0–0.08 m`；每指滑动 `0–0.04 m` |
| TCP | 夹爪安装坐标系下 `[0.09, 0, 0] m` |
| 夹爪状态 | 一维，`0=闭合`，`1=张开`；不是弧度 |
| 腕位姿 | pelvis 系中的腕关节 link 原点，与 TCP 分开 |
| 上半身 | 双臂各 7 rad；腰部 yaw/roll/pitch 共 3 rad |

`XYZ_ROT6D` 使用旋转矩阵前两行展开，与
[官方 EndEffectorPose 实现](https://github.com/NVIDIA/Isaac-GR00T/blob/main/gr00t/data/state_action/pose.py)
一致；不要与“前两列展开”混用。

真正对齐需要从目标数据取得：相机 K 与畸变/裁剪处理、`T_torso_camera`、
左右 `T_wrist_gripper` 与 `T_gripper_tcp`、EEF 参考系与轴方向、夹爪数值单位与开闭方向、
关节顺序和采样 fps。中心主点、方形像素的标定可用
`fovy = 2 * atan(H / (2 * fy))` 转为本模型 FOV；非中心主点或非方形像素需要扩展
MuJoCo 相机内参配置/使用一致的图像重投影，不能只调一个 FOV 解决。

修改标定后重新运行 `build_scene --calibration YOUR_FILE`，运行客户端也传同一文件。
XML 内保存标定 SHA256，客户端会检查是否忘记重新生成。`[-20, 0]` 表示数据采样步，
只有在本项目 20 Hz 数据中才是一秒历史；不能直接推断官方所有训练数据也是 20 Hz。

用同一模型、标定文件、相机和动作频率采集自定义示范，是目前最可复现的夹爪对齐路径。
如果后续接 SONIC，必须接入其上半身目标/全身跟踪通道，重新验证换夹爪后的惯量和稳定性；
不能把本模型的 19 个 actuator 写入原来假设 29 电机的数组。

## 7. 训练一个只预测上半身的 N1.7

已提供 `finetune_config.py`，注册 `NEW_EMBODIMENT`，状态与动作组维度均为
`9+9+1+1+7+7+3=37`：其中 18 维是双腕位姿，实际关节/夹爪命令是 19 维。
不含腿、根位姿、base height 或 navigation 输出。
双腕和双臂用 relative 表示训练，夹爪与腰部用 absolute；公开 API 解码后统一为 absolute。

先采集本模型的任务示范并转成
[官方 GR00T LeRobot v2 格式](https://github.com/NVIDIA/Isaac-GR00T/blob/main/getting_started/data_preparation.md)，
提供 `meta/info.json`、`meta/modality.json`、episode/task 元数据、parquet 和 `ego_view` 视频。
`modality.json` 按上述顺序切片 `[0:9] [9:18] [18:19] [19:20] [20:27] [27:34] [34:37]`。
记录的是物理单位的绝对示范目标，由训练 processor 做相对转换，不能预先做一次 delta 再重复转换。
EEF 示范目标与对应关节目标须通过同一正运动学一致生成。仅有本项目 XML 或 self-test
并不构成任务训练数据；本次没有生成或训练抓取数据。

在 AutoDL 的 Isaac-GR00T 根目录执行（数据路径须先存在）：

```bash
uv run python gr00t/experiment/launch_finetune.py \
  --base-model-path /root/autodl-tmp/models/GR00T-N1.7-3B \
  --dataset-path /root/autodl-tmp/datasets/g1_parallel_gripper \
  --embodiment-tag NEW_EMBODIMENT \
  --modality-config-path /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/finetune_config.py \
  --num-gpus 1 --global-batch-size 8 \
  --max-steps 10000 --save-steps 1000 --save-total-limit 3 \
  --output-dir /root/autodl-tmp/checkpoints/g1_parallel_gripper
```

这些步数和 batch 是起跑配置，需要按显存、数据量和验证集调整，并非抓取成功保证。
先做 open-loop 预测对比，再做未见物体/场景的闭环评估。
[官方微调流程](https://github.com/NVIDIA/Isaac-GR00T/blob/main/getting_started/finetune_new_embodiment.md)

训练完成并确认 checkpoint 目录后启动：

```bash
.venv/bin/python /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/server.py \
  --model-path /root/autodl-tmp/checkpoints/g1_parallel_gripper/checkpoint-10000 \
  --embodiment-tag NEW_EMBODIMENT --device cuda:0 \
  --calibration /root/autodl-tmp/CathyAgent/simulation/mujoco/g1/manipulation/calibration.json \
  --host 127.0.0.1 --port 6006
```

`--calibration` 只用于确实按该文件采集/训练的模型，不要给基础 REAL_G1 模型附一个本地文件就
宣称它完成了标定。保存训练时的标定副本；服务端与客户端会核对其 SHA256。

## 8. 验收范围

本地自动检查覆盖图像历史、HTTP 鉴权/请求、过滤底盘输出、绝对动作不重复累加、
关节限速/非法动作拒绝、夹爪对称开合、固定下半身以及标定不一致检测：

```bash
python -m pip install -r simulation/mujoco/g1/manipulation/requirements-server.txt
python -m unittest simulation.mujoco.g1.manipulation.test_manipulation -v
```

这些检查验证接口与物理模型，不验证 N1.7 的任务成功率。当前交付没有连接你的 AutoDL 实例，
没有在本机加载 N1.7 CUDA 权重，也没有证明基础模型在此夹爪上的零样本能力。
最终验收应包含：真实模型加载、一次真实推理、训练/仿真标定复核、物体抓取/搬运的闭环成功率。

结论：6006 HTTP 服务与双夹爪上半身仿真入口已准备好；基础模型可先验证推理，可靠的
夹爪 manipulation 需要匹配训练数据和标定，必要时使用提供的上半身微调配置。
