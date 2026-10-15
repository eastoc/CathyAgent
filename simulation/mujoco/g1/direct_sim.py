"""不依赖 Unitree SDK/DDS 的 29-DoF G1 MuJoCo 环境。"""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import mujoco.viewer
import numpy as np

from .sonic_onnx.inprocess import InMemoryUnitreeBridge
from .sonic_onnx.parameters import DEFAULT_ANGLES, EFFORT_LIMITS


class DirectG1MujocoEnv:
    _ASSIST_FADE_SECONDS = 0.75
    _ASSIST_SUPPORT_FRACTION = 0.70
    _FOOT_CONTACT_PENETRATION_M = 2e-3

    def __init__(
        self,
        scene_path: Path,
        *,
        sim_dt: float,
        onscreen: bool,
        initial_pose_xy_yaw: tuple[float, float, float] | None = None,
    ) -> None:
        self.mj_model = mujoco.MjModel.from_xml_path(str(scene_path))
        self.mj_data = mujoco.MjData(self.mj_model)
        self.mj_model.opt.timestep = sim_dt
        self.sim_dt = sim_dt
        self.qpos_offset = 7
        self.qvel_offset = 6
        self.body_joint_index = np.arange(1, 30, dtype=np.int64)
        self.qpos_indices = np.asarray(
            [self.mj_model.jnt_qposadr[i] for i in self.body_joint_index],
            dtype=np.int64,
        )
        self.qvel_indices = np.asarray(
            [self.mj_model.jnt_dofadr[i] for i in self.body_joint_index],
            dtype=np.int64,
        )
        if self.mj_model.nu != 29 or self.mj_model.nq != 36:
            raise RuntimeError(
                f"厨房场景应为 29-DoF floating G1，实际 nu={self.mj_model.nu}, "
                f"nq={self.mj_model.nq}"
            )
        # 场景 XML 使用 position actuator；同进程低层控制需要与官方 bridge 一样
        # 直接写关节力矩，因此运行时改为 unit-gain motor 语义。
        self.mj_model.actuator_gainprm[:] = 0.0
        self.mj_model.actuator_gainprm[:, 0] = 1.0
        self.mj_model.actuator_biasprm[:] = 0.0
        self.mj_model.actuator_ctrllimited[:] = False
        self.torque_limits = EFFORT_LIMITS.copy()
        self.unitree_bridge: InMemoryUnitreeBridge | None = None
        self.pelvis_body_id = self.mj_model.body("pelvis").id
        # 与官方 G1 sim2sim 默认配置一致的虚拟保护带。
        self.elastic_band_enabled = True
        self._elastic_band_strength = 1.0
        self._reset_to_stable_pose(initial_pose_xy_yaw)
        self._standing_root_height = float(self.mj_data.qpos[2])
        robot_body_ids = []
        for body_id in range(1, self.mj_model.nbody):
            ancestor = body_id
            while ancestor != 0 and ancestor != self.pelvis_body_id:
                ancestor = int(self.mj_model.body_parentid[ancestor])
            if ancestor == self.pelvis_body_id:
                robot_body_ids.append(body_id)
        self._robot_weight = float(
            np.sum(self.mj_model.body_mass[robot_body_ids])
            * abs(self.mj_model.opt.gravity[2])
        )
        if onscreen:
            self.viewer = mujoco.viewer.launch_passive(
                self.mj_model, self.mj_data, show_left_ui=False, show_right_ui=False
            )
            self.viewer.cam.azimuth = 120
            self.viewer.cam.elevation = -30
            self.viewer.cam.distance = 2.0
            self.viewer.cam.lookat = np.asarray([0.0, 0.0, 0.5])
            self.viewer.cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING
            self.viewer.cam.trackbodyid = self.mj_model.body("pelvis").id
        else:
            self.viewer = None

    def _reset_to_stable_pose(
        self, initial_pose_xy_yaw: tuple[float, float, float] | None
    ) -> None:
        """原子设置根位姿和 SONIC 默认关节角，避免直腿姿态在接管前侧翻。"""
        self.mj_data.qpos[:] = self.mj_model.qpos0
        if initial_pose_xy_yaw is not None:
            x, y, yaw = initial_pose_xy_yaw
            self.mj_data.qpos[0:2] = (x, y)
            self.mj_data.qpos[3:7] = (
                math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0)
            )
        self.mj_data.qpos[self.qpos_indices] = DEFAULT_ANGLES
        self.mj_data.qvel[:] = 0.0
        self.mj_data.qacc[:] = 0.0
        self.mj_data.ctrl[:] = 0.0
        mujoco.mj_forward(self.mj_model, self.mj_data)

        floor_id = self.mj_model.geom("floor").id
        floor_top = (
            self.mj_data.geom_xpos[floor_id, 2]
            + self.mj_model.geom_size[floor_id, 2]
        )
        foot_body_ids = {
            self.mj_model.body("left_ankle_roll_link").id,
            self.mj_model.body("right_ankle_roll_link").id,
        }
        foot_spheres = [
            geom_id
            for geom_id in range(self.mj_model.ngeom)
            if self.mj_model.geom_bodyid[geom_id] in foot_body_ids
            and self.mj_model.geom_type[geom_id] == mujoco.mjtGeom.mjGEOM_SPHERE
            and self.mj_model.geom_contype[geom_id] != 0
        ]
        if len(foot_spheres) != 8:
            raise RuntimeError(
                f"G1 应有 8 个脚底接触球，实际找到 {len(foot_spheres)} 个"
            )
        lowest_sole = min(
            self.mj_data.geom_xpos[geom_id, 2]
            - self.mj_model.geom_size[geom_id, 0]
            for geom_id in foot_spheres
        )
        self.mj_data.qpos[2] += (
            floor_top - self._FOOT_CONTACT_PENETRATION_M - lowest_sole
        )
        mujoco.mj_forward(self.mj_model, self.mj_data)
        if self.mj_data.ncon < 8:
            raise RuntimeError(
                f"G1 初始脚底接触不完整: contacts={self.mj_data.ncon}/8"
            )

    def set_unitree_bridge(self, bridge: InMemoryUnitreeBridge) -> None:
        self.unitree_bridge = bridge

    def sim_step(self) -> None:
        if self.unitree_bridge is None:
            raise RuntimeError("尚未安装 SONIC 内存 bridge")
        q = self.mj_data.qpos[self.qpos_indices]
        dq = self.mj_data.qvel[self.qvel_indices]
        commands = self.unitree_bridge.low_cmd.motor_cmd
        tau = np.asarray([
            command.tau
            + command.kp * (command.q - q[i])
            + command.kd * (command.dq - dq[i])
            for i, command in enumerate(commands)
        ])
        self.mj_data.ctrl[:] = np.clip(tau, -self.torque_limits, self.torque_limits)
        target_strength = 1.0 if self.elastic_band_enabled else 0.0
        fade_step = self.sim_dt / self._ASSIST_FADE_SECONDS
        self._elastic_band_strength += float(np.clip(
            target_strength - self._elastic_band_strength, -fade_step, fade_step
        ))
        if self._elastic_band_strength > 1e-6:
            velocity = np.zeros(6, dtype=np.float64)
            mujoco.mj_objectVelocity(
                self.mj_model, self.mj_data, mujoco.mjtObj.mjOBJ_BODY,
                self.pelvis_body_id, velocity, 0,
            )
            linear_velocity = velocity[3:].copy()
            angular_velocity = velocity[:3].copy()
            position = self.mj_data.xpos[self.pelvis_body_id]
            quaternion = self.mj_data.xquat[self.pelvis_body_id]
            force = np.asarray([
                0.0,
                0.0,
                self._ASSIST_SUPPORT_FRACTION * self._robot_weight
                + 10000.0 * (self._standing_root_height - position[2])
                - 1000.0 * linear_velocity[2],
            ])
            # 等价于官方 ElasticBand 的姿态弹簧。保护带只在等待命令时启用，
            # 因此可以固定 roll/pitch/yaw；SONIC 接管后会清零外力。
            vector = np.asarray(quaternion[1:4], dtype=np.float64)
            vector_norm = float(np.linalg.norm(vector))
            if vector_norm > 1e-9:
                angle = 2.0 * math.atan2(vector_norm, float(quaternion[0]))
                if angle > math.pi:
                    angle -= 2.0 * math.pi
                rotation_vector = vector * (angle / vector_norm)
            else:
                rotation_vector = np.zeros(3, dtype=np.float64)
            torque = -1000.0 * rotation_vector - 10.0 * angular_velocity
            self.mj_data.xfrc_applied[self.pelvis_body_id] = (
                self._elastic_band_strength * np.concatenate([force, torque])
            )
        else:
            self.mj_data.xfrc_applied[self.pelvis_body_id] = 0.0
        mujoco.mj_step(self.mj_model, self.mj_data)
        if self.mj_data.qpos[2] < 0.2:
            raise RuntimeError(
                f"G1 已跌倒，base height={self.mj_data.qpos[2]:.3f} m"
            )

    def update_viewer(self) -> None:
        if self.viewer is not None:
            self.viewer.sync()

    def update_reward(self) -> None:
        return

    def close(self) -> None:
        if self.viewer is not None:
            self.viewer.close()


__all__ = ["DirectG1MujocoEnv"]
