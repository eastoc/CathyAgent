# G1 开放厨房导航场景

这是一个四面墙封闭、8 m × 6 m 的平坦西式开放厨房，用于逐步接入二维建图、A* 路径规划和 SONIC
底层运控。当前版本完成场景几何与官方 29 自由度 Unitree G1 的导入。

场景包含中央岛台、靠墙橱柜、水槽、冰箱、垃圾桶，以及冰箱、水槽和岛台南侧三个导航目标。
中央岛台为 A* 提供上下两条绕行路线；垃圾桶用于验证低矮障碍检测。

使用已安装 MuJoCo 的 Python 环境预览：

```bash
python -m mujoco.viewer --mjcf simulation/mujoco/assets/g1_open_kitchen/scene.xml
```

Linux 无图形环境可直接编译模型检查资产完整性：

```bash
python -c "import mujoco; mujoco.MjModel.from_xml_path('simulation/mujoco/assets/g1_open_kitchen/scene.xml')"
```

导航边界、地图分辨率与目标点定义在 `navigation.yaml`。G1 模型来源见 `SOURCE.md`。
