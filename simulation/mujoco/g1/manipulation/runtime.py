"""GN1x Arena G1 的 MuJoCo 执行环境：单目视觉、Dex3 与绝对关节控制。"""

from __future__ import annotations

from contextlib import nullcontext
import importlib
import math
from pathlib import Path
from typing import Any, Mapping

import mujoco
import numpy as np

from .contract import (
    ACTION_DIMS, CALIBRATION, EXECUTED_ACTION_KEYS, JOINT_GROUPS, MODEL_ID,
    SCENE, SONIC_JOINTS, STATIC_ACTION_KEYS, VERSION, calibration_hash, encode_image,
    load_calibration, validate_metadata,
)
from .tasks import DEFAULT_TASK, TASKS
from ..sonic_onnx.parameters import DEFAULT_ANGLES


class G1ManipulationEnv:
    """全身导航可视化、固定根基座的 Arena G1 静态操作适配层。"""

    ACTION_LIMIT_TOLERANCE = 0.05

    def __init__(self, scene_path=SCENE, calibration_path=CALIBRATION, *,
                 viewer_enabled: bool = False):
        self.calibration = load_calibration(calibration_path)
        self.model = mujoco.MjModel.from_xml_path(str(Path(scene_path)))
        self.data = mujoco.MjData(self.model)
        self.calibration_sha256 = calibration_hash(self.calibration)
        self._validate_scene_hash()

        self.dt = 1.0 / float(self.calibration["control_hz"])
        substeps = self.dt / self.model.opt.timestep
        if not np.isclose(substeps, round(substeps), atol=1e-7):
            raise ValueError("MuJoCo timestep 必须整除 Arena 50 Hz 控制周期")
        self.substeps = int(round(substeps))

        self.joints = {
            key: np.asarray([self.model.joint(name).id for name in names], dtype=int)
            for key, names in JOINT_GROUPS.items()
        }
        self.qadr = {
            key: self.model.jnt_qposadr[ids].copy() for key, ids in self.joints.items()
        }
        self.dadr = {
            key: self.model.jnt_dofadr[ids].copy() for key, ids in self.joints.items()
        }
        self.actuators = {
            key: np.asarray([self.model.actuator(name).id for name in names], dtype=int)
            for key, names in JOINT_GROUPS.items()
        }
        self.navigation_joints = np.asarray(
            [self.model.joint(name).id for name in SONIC_JOINTS], dtype=int
        )
        self.navigation_qadr = self.model.jnt_qposadr[self.navigation_joints].copy()
        self.navigation_dadr = self.model.jnt_dofadr[self.navigation_joints].copy()
        self.navigation_actuators = np.asarray(
            [self.model.actuator(name).id for name in SONIC_JOINTS], dtype=int
        )
        for key in JOINT_GROUPS:
            if not np.array_equal(
                self.model.actuator_trnid[self.actuators[key], 0], self.joints[key]
            ):
                raise ValueError(f"{key} 的执行器与 Arena 关节顺序不匹配")
        self._validate_robot_joint_scope()

        self.renderer: mujoco.Renderer | None = None
        self.viewer = None
        self.current_task = DEFAULT_TASK
        self._stable_success_steps = 0
        self._required_success_steps = 10
        self._latest_frame: np.ndarray | None = None
        self.gripper_geoms = {
            side: {
                part: {
                    gid for gid in range(self.model.ngeom)
                    if (self.model.geom(gid).name or "").startswith(
                        f"{side}_hand_{part}"
                    )
                }
                for part in ("thumb", "index", "middle", "palm")
            }
            for side in ("left", "right")
        }
        self.reset()
        if viewer_enabled:
            self._launch_viewer()

    def _validate_scene_hash(self) -> None:
        text_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_TEXT, "calibration_sha256"
        )
        if text_id < 0:
            raise ValueError("Dex3 场景缺少 calibration_sha256；请重新运行 build_scene")
        start = int(self.model.text_adr[text_id])
        size = int(self.model.text_size[text_id])
        scene_hash = bytes(
            self.model.text_data[start:start + size]
        ).rstrip(b"\0").decode()
        if scene_hash != self.calibration_sha256:
            raise ValueError("Dex3 场景与 GN1x calibration.json 不一致；请重新运行 build_scene")

    def _validate_robot_joint_scope(self) -> None:
        allowed = {
            *SONIC_JOINTS,
            *(name for names in JOINT_GROUPS.values() for name in names),
        }
        pelvis = self.model.body("pelvis").id
        for body_id in range(1, self.model.nbody):
            ancestor = body_id
            while ancestor and ancestor != pelvis:
                ancestor = self.model.body_parentid[ancestor]
            if not ancestor:
                continue
            first = self.model.body_jntadr[body_id]
            for joint_id in range(first, first + self.model.body_jntnum[body_id]):
                if self.model.joint(joint_id).name not in allowed:
                    raise ValueError("GN1x 静态场景包含未声明的机器人可动关节")

    def _launch_viewer(self) -> None:
        try:
            viewer = importlib.import_module("mujoco.viewer")
            self.viewer = viewer.launch_passive(
                self.model, self.data, show_left_ui=False, show_right_ui=False
            )
            self.viewer.cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING
            self.viewer.cam.trackbodyid = self.model.body("pelvis").id
            self.viewer.cam.azimuth = 135
            self.viewer.cam.elevation = -25
            self.viewer.cam.distance = 3.2
            self.viewer.sync()
        except Exception as exc:
            self.viewer = None
            raise RuntimeError(
                "G1 Dex3 MuJoCo Viewer 启动失败；macOS 请使用 mjpython，"
                "无桌面环境请传 --no-viewer"
            ) from exc

    def _viewer_lock(self):
        if self.viewer is not None and self.viewer.is_running():
            return self.viewer.lock()
        return nullcontext()

    def _sync_viewer(self) -> None:
        if self.viewer is not None:
            if not self.viewer.is_running():
                raise RuntimeError("G1 Dex3 MuJoCo Viewer 已关闭")
            self.viewer.sync()

    def set_navigation_pose(self, x: float, y: float, yaw: float) -> None:
        values = np.asarray([x, y, yaw], dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("导航位姿必须是有限数")
        pelvis = self.model.body("pelvis").id
        with self._viewer_lock():
            self.model.body_pos[pelvis, :2] = values[:2]
            self.model.body_quat[pelvis] = (
                math.cos(values[2] / 2), 0.0, 0.0, math.sin(values[2] / 2)
            )
            mujoco.mj_forward(self.model, self.data)
        self._sync_viewer()

    def set_navigation_state(self, root_qpos: Any, joint_position_hardware: Any) -> None:
        """把 SONIC 根位姿和 29 个硬件顺序关节完整投影到 Dex3 场景。"""
        root = np.asarray(root_qpos, dtype=float)
        joints = np.asarray(joint_position_hardware, dtype=float)
        if root.shape != (7,) or joints.shape != (29,):
            raise ValueError("SONIC 映射需要 root_qpos[7] 和 hardware joints[29]")
        if not np.isfinite(root).all() or not np.isfinite(joints).all():
            raise ValueError("SONIC 映射状态必须是有限数")
        norm = float(np.linalg.norm(root[3:]))
        if norm < 1e-8:
            raise ValueError("SONIC 根四元数无效")
        root = root.copy()
        root[3:] /= norm
        pelvis = self.model.body("pelvis").id
        with self._viewer_lock():
            self.model.body_pos[pelvis] = root[:3]
            self.model.body_quat[pelvis] = root[3:]
            self.data.qpos[self.navigation_qadr] = joints
            self.data.qvel[self.navigation_dadr] = 0.0
            self.data.ctrl[self.navigation_actuators] = joints
            mujoco.mj_forward(self.model, self.data)
        self._sync_viewer()

    def reset(self, task_id=DEFAULT_TASK, *, randomize=False, seed=None):
        if task_id not in TASKS:
            raise ValueError(f"未知任务 {task_id!r}，可选值为 {sorted(TASKS)}")
        with self._viewer_lock():
            mujoco.mj_resetData(self.model, self.data)
            navigation_bounds = self.model.jnt_range[self.navigation_joints]
            navigation_pose = np.clip(
                DEFAULT_ANGLES, navigation_bounds[:, 0], navigation_bounds[:, 1]
            )
            self.data.qpos[self.navigation_qadr] = navigation_pose
            self.data.ctrl[self.navigation_actuators] = navigation_pose
            for key in JOINT_GROUPS:
                value = np.asarray(self.calibration["initial_pose_rad"][key], dtype=float)
                bounds = self.model.jnt_range[self.joints[key]]
                value = np.clip(value, bounds[:, 0], bounds[:, 1])
                self.data.qpos[self.qadr[key]] = value
                self.data.ctrl[self.actuators[key]] = value

            spec = TASKS[task_id]
            position = np.asarray(spec.object_spawn_pos_m, dtype=float)
            if randomize:
                rng = np.random.default_rng(seed)
                position[:2] += rng.uniform((-0.025, -0.018), (0.025, 0.018))
            joint = self.model.joint(spec.object_joint)
            qadr = joint.qposadr[0]
            self.data.qpos[qadr:qadr + 7] = [*position, 1.0, 0.0, 0.0, 0.0]
            self.data.qvel[joint.dofadr[0]:joint.dofadr[0] + 6] = 0.0
            mocap_id = self.model.body_mocapid[self.model.body(spec.target_body).id]
            if mocap_id < 0:
                raise ValueError(f"目标 {spec.target_body} 必须是 mocap body")
            self.data.mocap_pos[mocap_id] = spec.target_pos_m
            self.data.mocap_quat[mocap_id] = [1.0, 0.0, 0.0, 0.0]
            self.current_task = task_id
            self._stable_success_steps = 0
            self._latest_frame = None
            mujoco.mj_forward(self.model, self.data)
        self._sync_viewer()
        return self.task_status()

    @property
    def task(self):
        return TASKS[self.current_task]

    def state(self) -> dict[str, np.ndarray]:
        return {
            key: np.asarray(self.data.qpos[address], dtype=np.float32).copy()
            for key, address in self.qadr.items()
        }

    def open_hand_pose(self, side: str) -> np.ndarray:
        return np.asarray(
            self.calibration["dex3"]["open_pose_rad"][side], dtype=float
        ).copy()

    def closed_hand_pose(self, side: str) -> np.ndarray:
        return np.asarray(
            self.calibration["dex3"]["closed_pose_rad"][side], dtype=float
        ).copy()

    def hand_opening(self, side: str) -> float:
        current = np.asarray(self.data.qpos[self.qadr[f"{side}_hand"]], dtype=float)
        closed = self.closed_hand_pose(side)
        opened = self.open_hand_pose(side)
        direction = opened - closed
        return float(np.clip(np.dot(current - closed, direction) /
                             np.dot(direction, direction), 0.0, 1.0))

    def hold_target(self) -> dict[str, np.ndarray]:
        return {
            key: np.clip(
                self.data.qpos[self.qadr[key]],
                self.model.jnt_range[self.joints[key], 0],
                self.model.jnt_range[self.joints[key], 1],
            ).copy()
            for key in JOINT_GROUPS
        }

    def _object_geoms(self, body_id: int) -> set[int]:
        return {
            gid for gid in range(self.model.ngeom)
            if self.model.geom_bodyid[gid] == body_id
        }

    def _object_touches_hand(self, object_body_id: int) -> bool:
        object_geoms = self._object_geoms(object_body_id)
        hand_geoms = set().union(*(
            set().union(*parts.values()) for parts in self.gripper_geoms.values()
        ))
        for index in range(self.data.ncon):
            pair = {
                int(self.data.contact[index].geom1),
                int(self.data.contact[index].geom2),
            }
            if pair & object_geoms and pair & hand_geoms:
                return True
        return False

    def grasp_status(self, hand: str | None = None, task_id: str | None = None):
        if hand not in (None, "left", "right"):
            raise ValueError("hand 必须是 left、right 或 None")
        task_id = task_id or self.current_task
        if task_id != self.current_task:
            raise ValueError("grasp_status.task_id 与当前任务不一致")
        body_id = self.model.body(TASKS[task_id].object_body).id
        object_geoms = self._object_geoms(body_id)
        result: dict[str, Any] = {}
        for side in ("left", "right"):
            contacts: dict[str, bool] = {}
            for part, geoms in self.gripper_geoms[side].items():
                contacts[part] = any(
                    ({int(self.data.contact[i].geom1), int(self.data.contact[i].geom2)} & geoms)
                    and ({int(self.data.contact[i].geom1), int(self.data.contact[i].geom2)} & object_geoms)
                    for i in range(self.data.ncon)
                )
            opposition = contacts["thumb"] and (contacts["index"] or contacts["middle"])
            result[side] = {
                **contacts,
                "opposition_contact": opposition,
                "bilateral_contact": opposition,
                "object_contact": any(contacts.values()),
                "opening": self.hand_opening(side),
            }
        payload = {
            "task_id": task_id,
            "object_position_m": self.data.xpos[body_id].tolist(),
            "hands": result,
        }
        if hand is None:
            return payload
        return {
            **result[hand], "task_id": task_id, "hand": hand,
            "object_position_m": self.data.xpos[body_id].tolist(),
        }

    def _placement_state(self) -> dict[str, Any]:
        spec = self.task
        body_id = self.model.body(spec.object_body).id
        site_id = self.model.site(spec.target_site).id
        target_rotation = self.data.site_xmat[site_id].reshape(3, 3)
        relative = target_rotation.T @ (
            self.data.xpos[body_id] - self.data.site_xpos[site_id]
        )
        inside = bool(np.all(np.abs(relative) <= np.asarray(spec.position_tolerance_m)))
        upright_cosine = float(self.data.xmat[body_id].reshape(3, 3)[2, 2])
        joint = self.model.joint(spec.object_joint)
        velocity = self.data.qvel[joint.dofadr[0]:joint.dofadr[0] + 6]
        speed = float(np.linalg.norm(velocity[:3]))
        angular_speed = float(np.linalg.norm(velocity[3:]))
        released = not self._object_touches_hand(body_id)
        settled = speed < 0.04 and angular_speed < 0.20
        return {
            "inside_target": inside,
            "upright_cosine": upright_cosine,
            "linear_speed_mps": speed,
            "angular_speed_rps": angular_speed,
            "released": released,
            "settled": settled,
            "raw_success": bool(
                inside and upright_cosine >= spec.upright_cosine_min
                and settled and released
            ),
            "object_position_m": self.data.xpos[body_id].tolist(),
            "target_position_m": self.data.site_xpos[site_id].tolist(),
        }

    def task_status(self) -> dict[str, Any]:
        return {
            "task_id": self.current_task,
            "instruction": self.task.instruction,
            **self._placement_state(),
            "stable_steps": self._stable_success_steps,
            "required_stable_steps": self._required_success_steps,
            "success": self._stable_success_steps >= self._required_success_steps,
        }

    def _update_task_success(self) -> None:
        if self._placement_state()["raw_success"]:
            self._stable_success_steps += 1
        else:
            self._stable_success_steps = 0

    def describe(self) -> dict[str, Any]:
        return {
            "environment_id": "arena_g1_static_apple_picknplace_mujoco",
            "robot_id": "unitree_g1_29dof_dex3",
            "schema_version": VERSION,
            "model_id": MODEL_ID,
            "control_hz": self.calibration["control_hz"],
            "cameras": [self.calibration["camera"]["name"]],
            "camera_resolution": [640, 480],
            "calibration_sha256": self.calibration_sha256,
            "tasks": {
                key: {"instruction": value.instruction} for key, value in TASKS.items()
            },
            "hand_encoding": self.calibration["dex3"]["hand_encoding"],
            "output_scope": "arms_dex3_waist",
            "fixed_static_outputs": list(STATIC_ACTION_KEYS),
        }

    def render(self, camera="ego_view") -> np.ndarray:
        cfg = self.calibration["camera"]
        if self.renderer is None:
            self.renderer = mujoco.Renderer(
                self.model, height=cfg["height"], width=cfg["width"]
            )
        with self._viewer_lock():
            self.renderer.update_scene(self.data, camera=camera)
            return self.renderer.render().copy()

    def capture(self) -> np.ndarray:
        self._latest_frame = self.render("ego_view")
        return self._latest_frame

    def validate_server(self, metadata: Mapping[str, Any]) -> None:
        validate_metadata(metadata)
        remote_hash = metadata.get("calibration_sha256")
        if remote_hash not in (None, self.calibration_sha256):
            raise ValueError("AutoDL GN1x 服务与本地相机/Dex3 标定不一致")

    def request(self, prompt: str, metadata: Mapping[str, Any], *_unused) -> dict[str, Any]:
        self.validate_server(metadata)
        frame = self.capture()
        return {
            "schema_version": VERSION,
            "prompt": prompt,
            "video": {"ego_view": [encode_image(frame)]},
            "state": {key: [value.tolist()] for key, value in self.state().items()},
        }

    def policy_target(self, action: Mapping[str, Any]) -> dict[str, np.ndarray]:
        if set(action) != set(ACTION_DIMS):
            raise ValueError(f"GR00T 单步动作字段必须是 {sorted(ACTION_DIMS)}")
        return {
            key: np.asarray(action[key], dtype=float).copy()
            for key in EXECUTED_ACTION_KEYS
        }

    def step_policy_action(self, action: Mapping[str, Any], *, capture=False) -> None:
        self.step(self.policy_target(action), capture=capture)

    def step(self, target: Mapping[str, Any] | None = None, *, capture=False) -> None:
        with self._viewer_lock():
            if target is not None:
                if set(target) != set(JOINT_GROUPS):
                    raise ValueError(f"本地执行字段必须是 {sorted(JOINT_GROUPS)}")
                prepared: dict[str, np.ndarray] = {}
                for key in JOINT_GROUPS:
                    value = np.asarray(target[key], dtype=float)
                    expected_shape = (len(JOINT_GROUPS[key]),)
                    if value.shape != expected_shape or not np.isfinite(value).all():
                        raise ValueError(f"{key} 目标必须是有限数组 shape={expected_shape}")
                    bounds = self.model.jnt_range[self.joints[key]]
                    if np.any(value < bounds[:, 0] - self.ACTION_LIMIT_TOLERANCE) or np.any(
                        value > bounds[:, 1] + self.ACTION_LIMIT_TOLERANCE
                    ):
                        raise ValueError(f"{key} 的 GN1x 目标明显超出硬限位")
                    speed_key = (
                        "max_hand_speed_rad_s" if key.endswith("hand")
                        else "max_waist_speed_rad_s" if key == "waist"
                        else "max_arm_speed_rad_s"
                    )
                    max_delta = float(self.calibration["execution"][speed_key]) * self.dt
                    current = self.data.ctrl[self.actuators[key]]
                    desired = np.clip(value, bounds[:, 0], bounds[:, 1])
                    prepared[key] = current + np.clip(
                        desired - current, -max_delta, max_delta
                    )
                for key, value in prepared.items():
                    self.data.ctrl[self.actuators[key]] = value
            for _ in range(self.substeps):
                mujoco.mj_step(self.model, self.data)
            if not np.isfinite(self.data.qpos).all():
                raise RuntimeError("MuJoCo 状态出现非有限数")
            self._update_task_success()
        if capture:
            self.capture()
        self._sync_viewer()

    def close(self) -> None:
        if self.viewer is not None:
            self.viewer.close()
            self.viewer = None
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None


__all__ = ["G1ManipulationEnv"]
