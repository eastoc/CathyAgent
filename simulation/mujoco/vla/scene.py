"""robosuite 1.4.1 的双 Panda 环境；机器人资产来自已安装的 robosuite。

自定义任务不是 LIBERO benchmark；仅对齐 Panda / OSC_POSE / 相机观测契约。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import numpy as np


def make_environment(config: dict[str, Any]):
    # 延迟导入：Z1 和无仿真契约测试不需要 robosuite。
    import robosuite
    from robosuite.controllers import load_controller_config
    from robosuite.environments.manipulation.two_arm_env import TwoArmEnv
    from robosuite.models.arenas import TableArena
    from robosuite.models.objects import BoxObject
    from robosuite.models.tasks import ManipulationTask

    if robosuite.__version__ != "1.4.1":
        raise RuntimeError("此后端要求 robosuite==1.4.1，避免新控制器版本改变动作语义")
    scene, observation = config["scene"], config["observation"]

    class DualPandaThreeCube(TwoArmEnv):
        def __init__(self):
            self.rng = np.random.default_rng(scene["seed"])
            super().__init__(
                robots=["Panda", "Panda"], env_configuration="single-arm-parallel",
                controller_configs=load_controller_config(default_controller="OSC_POSE"),
                gripper_types="default", initialization_noise=None,
                # robosuite 1.4.1 在 macOS 的离屏后端仍创建 GLFW 窗口，
                # 不能在仿真工作线程使用。相机由 PandaRuntime 原生 Renderer 采集。
                has_renderer=False, has_offscreen_renderer=False,
                use_camera_obs=False, control_freq=config["runtime"]["control_hz"],
                horizon=config["runtime"]["max_episode_steps"], ignore_done=True,
                hard_reset=False,
                camera_names=["agentview", "robot0_eye_in_hand", "robot1_eye_in_hand"],
                camera_heights=observation["render_size"],
                camera_widths=observation["render_size"], camera_depths=False,
            )

        def _load_model(self):
            super()._load_model()
            for robot, offset in zip(self.robots, scene["arm_y_offsets"]):
                base = np.asarray(robot.robot_model.base_xpos_offset["table"](scene["table_size"][0]))
                robot.robot_model.set_base_xpos(base + [0, offset, 0])
            arena = TableArena(table_full_size=tuple(scene["table_size"]),
                               table_offset=(0, 0, scene["table_height"]),
                               table_friction=(1.0, 0.005, 0.0001))
            arena.set_origin([0, 0, 0])
            arena.set_camera(camera_name="agentview", pos=observation["agentview_pos"],
                             quat=observation["agentview_quat"])
            # 开口盒子：底板和四壁是独立 box geom，无不可见封口碰撞面。
            x, y = scene["box_xy"]
            hx, hy = scene["box_inner_half_size"]
            wall, height = scene["box_wall_thickness"], scene["box_height"]
            body = ET.SubElement(arena.worldbody, "body", name="target_box",
                                 pos=f"{x} {y} {scene['table_height']}")
            parts = [
                ("bottom", [0, 0, wall / 2], [hx + wall, hy + wall, wall / 2]),
                ("x_pos", [hx + wall / 2, 0, height / 2], [wall / 2, hy + wall, height / 2]),
                ("x_neg", [-hx - wall / 2, 0, height / 2], [wall / 2, hy + wall, height / 2]),
                ("y_pos", [0, hy + wall / 2, height / 2], [hx, wall / 2, height / 2]),
                ("y_neg", [0, -hy - wall / 2, height / 2], [hx, wall / 2, height / 2]),
            ]
            for name, pos, size in parts:
                ET.SubElement(body, "geom", name=f"target_box_{name}", type="box",
                              pos=" ".join(map(str, pos)), size=" ".join(map(str, size)),
                              rgba="0.55 0.40 0.22 1", friction="1 0.005 0.0001", group="1")
            half = scene["cube_half_size"]
            colors = {"red": [0.9, 0.04, 0.04, 1], "green": [0.04, 0.8, 0.08, 1],
                      "blue": [0.04, 0.15, 0.95, 1]}
            self.cubes = [BoxObject(name=f"{name}_cube", size=[half] * 3,
                                   rgba=rgba, density=500, friction=[1, 0.005, 0.0001])
                          for name, rgba in colors.items()]
            self.model = ManipulationTask(mujoco_arena=arena,
                                          mujoco_robots=[r.robot_model for r in self.robots],
                                          mujoco_objects=self.cubes)

        def _reset_internal(self):
            super()._reset_internal()
            for cube, color in zip(self.cubes, ("red", "green", "blue")):
                xy = np.asarray(scene["cubes"][color], dtype=float)
                jitter = scene["position_jitter"]
                xy = xy + self.rng.uniform(-jitter, jitter, 2)
                z = scene["table_height"] + scene["cube_half_size"] + 0.002
                self.sim.data.set_joint_qpos(cube.joints[0], [*xy, z, 1, 0, 0, 0])

        def reward(self, action=None):
            # 物体真值不进入策略或 Agent 上下文。
            return 0.0

        def _check_success(self):
            return False

    return DualPandaThreeCube()
