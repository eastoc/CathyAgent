# G1 Dex3-1 manipulation 场景

`upstream/g1_body29_hand14.xml` 及 `upstream/meshes/` 来自 Unitree 官方
[unitree_lerobot G1 资产](https://github.com/unitreerobotics/unitree_lerobot/tree/main/unitree_lerobot/eval_robot/assets/g1)，
固定到提交 `41c2805742de879ddab2d8d6beaeaf215f876395`。上游采用 Apache-2.0 许可。

下载整个官方仓库压缩包：

<https://github.com/unitreerobotics/unitree_lerobot/archive/refs/heads/main.zip>

解压后，将 `unitree_lerobot-main/unitree_lerobot/eval_robot/assets/g1/` 完整复制为本目录下的
`upstream/`。至少应包含：

```text
upstream/g1_body29_hand14.xml
upstream/meshes/left_hand_palm_link.STL
upstream/meshes/right_hand_palm_link.STL
```

`g1_dex3.xml` 和 `scene.xml` 由
`python -m simulation.mujoco.g1.manipulation.build_scene` 生成。生成器固定 pelvis 与腿部，
保留 17 个腰部/双臂关节和左右各 7 个 Dex3-1 关节，加入 ego_view、腕/EEF/TCP site、
接触命名和厨房抓放任务。不要手工修改生成文件。

Dex3 的物理 DDS 传输顺序是：

```text
thumb_0, thumb_1, thumb_2, index_0, index_1, middle_0, middle_1
```

GN1x Arena G1 checkpoint 的数据张量顺序不同，本项目在关节名层显式映射为：

```text
index_0, index_1, middle_0, middle_1, thumb_0, thumb_1, thumb_2
```

相机、TCP 和手部开合参考姿态见
[`calibration.json`](../../g1/manipulation/calibration.json)。相机外参、分辨率和 Dex3 顺序按
[IsaacLab-Arena G1 定义](https://github.com/isaac-sim/IsaacLab-Arena/blob/main/isaaclab_arena/embodiments/g1/g1.py)
与 [Arena G1 modality](https://github.com/isaac-sim/IsaacLab-Arena/blob/main/isaaclab_arena_gr00t/embodiments/g1/g1_sim_wbc_data_gr00t_n_1_7_config.py)
转换；服务通过 `calibration_sha256` 阻止本地与 AutoDL 使用不同标定。
