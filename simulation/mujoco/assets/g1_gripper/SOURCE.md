# G1 双夹爪 manipulation 场景

`g1_gripper.xml` 由 `simulation.mujoco.g1.manipulation.build_scene` 从项目已有
`g1_open_kitchen/g1/g1.xml` 派生，网格通过相对路径复用原资产。
机器人原模型来自 [MuJoCo Menagerie / Unitree G1](https://github.com/google-deepmind/mujoco_menagerie/tree/main/unitree_g1)，
遵循原模型的 BSD-3-Clause 许可，见 [原许可证](../g1_open_kitchen/g1/LICENSE)。

本项目修改：移除固定橡胶手可视网格，增加左右各一组平行双指夹爪、对称约束、夹爪碰撞、
腕坐标系、TCP site 和头部 ego_view 相机。manipulation 版本固定 pelvis 与腿部，
保留 17 个上半身关节和 2 个夹爪执行器。红色方块是可动态抓取的 free body。
原导航资产未替换，原 SONIC 29-DoF 控制器也不直接加载本模型。

夹爪为本项目的简化几何和质量参数，不是某一商用夹爪的精确 CAD。
上游 wrist_yaw_link 的惯性保留，新增夹爪质量为估计值；用于接口和桌面操作仿真，未做实机动力学辨识。
相机和夹爪默认参数见 [calibration.json](../../g1/manipulation/calibration.json)，
这些参数不是 NVIDIA 公布的训练标定，`training_alignment_verified` 初始为 false。

重新生成：

```bash
python -m simulation.mujoco.g1.manipulation.build_scene
```

请修改生成器与标定文件后重新生成，不要只修改生成后的 XML。
