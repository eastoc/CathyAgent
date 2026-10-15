"""MuJoCo 本地上半身控制器：具名关节映射、双指耦合、历史图像与限速。"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import mujoco
import numpy as np

from .build_scene import DESTINATION
from .contract import (
    CALIBRATION, JOINT_GROUPS, SIM_DIMS, VERSION,
    calibration_hash, encode_image, load_calibration,
)


class G1ManipulationEnv:
    def __init__(self, scene_path=DESTINATION / "scene.xml", calibration_path=CALIBRATION):
        self.calibration = load_calibration(calibration_path)
        self.model = mujoco.MjModel.from_xml_path(str(Path(scene_path)))
        self.data = mujoco.MjData(self.model)
        self.calibration_sha256 = calibration_hash(self.calibration)
        text_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_TEXT, "calibration_sha256")
        if text_id < 0:
            raise ValueError("场景缺少标定校验值，请运行 build_scene")
        start = int(self.model.text_adr[text_id])
        size = int(self.model.text_size[text_id])
        scene_hash = bytes(self.model.text_data[start:start + size]).rstrip(b"\0").decode()
        if scene_hash != self.calibration_sha256:
            raise ValueError("场景与标定文件不一致，请运行 build_scene --calibration ...")
        hz = self.calibration["control_hz"]
        self.dt = 1.0 / hz
        steps = self.dt / self.model.opt.timestep
        if not np.isclose(steps, round(steps)):
            raise ValueError("control_hz 必须整除物理仿真频率")
        self.substeps = round(steps)
        self.joints = {key: np.array([self.model.joint(n).id for n in names])
                       for key, names in JOINT_GROUPS.items()}
        self.qadr = {key: self.model.jnt_qposadr[ids] for key, ids in self.joints.items()}
        self.actuators = {key: np.array([self.model.actuator(n).id for n in names])
                          for key, names in JOINT_GROUPS.items()}
        for key, ids in self.actuators.items():
            if not np.array_equal(self.model.actuator_trnid[ids, 0], self.joints[key]):
                raise ValueError(f"{key} 的执行器与关节不匹配")
        allowed = {n for group in JOINT_GROUPS.values() for n in group}
        allowed.update(f"{side}_finger_{finger}_joint" for side in ("left", "right") for finger in ("a", "b"))
        for body_id in range(1, self.model.nbody):
            body = body_id
            while body and body != self.model.body("pelvis").id:
                body = self.model.body_parentid[body]
            if not body:
                continue
            for j in range(self.model.body_jntadr[body_id], self.model.body_jntadr[body_id] + self.model.body_jntnum[body_id]):
                if self.model.joint(j).name not in allowed:
                    raise ValueError("manipulation 场景必须固定基座和腿部")
        self.renderer = None
        self.history = deque(maxlen=1024)
        self.reset()

    def reset(self):
        mujoco.mj_resetData(self.model, self.data)
        for side in ("left", "right"):
            q = [-0.5, 0.2 if side == "left" else -0.2, 0, 0.3, 0, 0, 0]
            self.data.qpos[self.qadr[f"{side}_arm"]] = q
            self.data.ctrl[self.actuators[f"{side}_arm"]] = q
            for finger in ("a", "b"):
                adr = self.model.joint(f"{side}_finger_{finger}_joint").qposadr[0]
                self.data.qpos[adr] = self.calibration["gripper"]["max_width_m"] / 2
            self.data.ctrl[self.model.actuator(f"{side}_gripper").id] = self.calibration["gripper"]["max_width_m"] / 2
        self.history.clear()
        mujoco.mj_forward(self.model, self.data)

    def state(self):
        state = {key: self.data.qpos[adr].copy() for key, adr in self.qadr.items()}
        ref = self.model.body(self.calibration["eef"]["reference_body"]).id
        r_ref = self.data.xmat[ref].reshape(3, 3)
        p_ref = self.data.xpos[ref]
        for side in ("left", "right"):
            source = "wrist_eef" if self.calibration["eef"]["source"] == "wrist" else "tcp"
            site = self.model.site(f"{side}_{source}").id
            xyz = r_ref.T @ (self.data.site_xpos[site] - p_ref)
            rotation = r_ref.T @ self.data.site_xmat[site].reshape(3, 3)
            rot6 = (rotation[:2, :].reshape(6)
                    if self.calibration["eef"]["rotation_6d"] == "first_two_rows"
                    else rotation[:, :2].T.reshape(6))
            state[f"{side}_wrist_eef_9d"] = np.concatenate([xyz, rot6])
            opening = sum(self.data.qpos[self.model.joint(f"{side}_finger_{f}_joint").qposadr[0]]
                          for f in ("a", "b"))
            state[f"{side}_hand"] = np.array([np.clip(opening / self.calibration["gripper"]["max_width_m"], 0, 1)])
        return {k: np.asarray(v, dtype=np.float32) for k, v in state.items()}

    def render(self, camera="ego_view"):
        cfg = self.calibration["camera"]
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model, height=cfg["height"], width=cfg["width"])
        self.renderer.update_scene(self.data, camera=camera)
        return self.renderer.render().copy()

    def capture(self):
        self.history.append(self.render())

    def validate_server(self, metadata):
        if metadata.get("schema_version") != VERSION or metadata.get("action_representation") != "absolute_physical":
            raise ValueError("服务器协议或动作语义不匹配")
        if metadata["state_dims"] != SIM_DIMS:
            raise ValueError(
                f"checkpoint 状态维度 {metadata['state_dims']} 与夹爪模型 {SIM_DIMS} 不匹配。"
                "不能把多自由度灵巧手状态补零冒充夹爪；请使用此夹爪的 NEW_EMBODIMENT 微调 checkpoint。"
            )
        for key in (*JOINT_GROUPS, "left_hand", "right_hand"):
            if metadata["action_dims"].get(key) != SIM_DIMS[key]:
                raise ValueError(f"checkpoint action.{key} 维度不匹配")
        if metadata["video_keys"] != ["ego_view"] or metadata["state_delta_indices"] != [0]:
            raise ValueError("需要单路 ego_view 和当前状态")
        indices = metadata["video_delta_indices"]
        if not indices or max(indices) != 0 or min(indices) < -1023:
            raise ValueError("只支持最多 1024 帧的历史观测")
        if metadata.get("calibration_sha256") not in (None, self.calibration_sha256):
            raise ValueError("服务端训练标定与仿真不一致")
        if metadata.get("control_hz", self.calibration["control_hz"]) != self.calibration["control_hz"]:
            raise ValueError("训练与执行频率不匹配")

    def request(self, prompt, metadata):
        self.validate_server(metadata)
        indices = metadata["video_delta_indices"]
        if len(self.history) <= -min(indices):
            raise ValueError("历史图像不足；先运行真实仿真步并 capture，不能复制当前帧冒充历史")
        return {"schema_version": VERSION, "prompt": prompt,
                "calibration_sha256": self.calibration_sha256,
                "video": {"ego_view": [encode_image(self.history[i - 1]) for i in indices]},
                "state": {k: [v.tolist()] for k, v in self.state().items()}}

    def step(self, target=None, *, capture=False):
        if target is not None:
            required = {*JOINT_GROUPS, "left_hand", "right_hand"}
            if set(target) != required:
                raise ValueError(f"只接受执行字段 {sorted(required)}，不能写入下半身或混用 EEF/关节控制")
            # 完整验证后再写 ctrl，避免半个动作被应用。
            prepared = {}
            for key in required:
                value = np.asarray(target[key], dtype=float)
                if value.shape != (SIM_DIMS[key],) or not np.isfinite(value).all():
                    raise ValueError(f"{key} 维度或数值错误")
                if key in JOINT_GROUPS:
                    bounds = self.model.jnt_range[self.joints[key]]
                    if np.any(value < bounds[:, 0]) or np.any(value > bounds[:, 1]):
                        raise ValueError(f"{key} 超出关节范围")
                    current = self.data.ctrl[self.actuators[key]]
                    max_step = (0.5 if key == "waist" else 1.0) * self.dt
                    prepared[key] = current + np.clip(value - current, -max_step, max_step)
                else:
                    if not 0 <= value[0] <= 1:
                        raise ValueError("夹爪必须为 0（闭合）到 1（张开）")
                    side = key.split("_")[0]
                    aid = self.model.actuator(f"{side}_gripper").id
                    desired = value[0] * self.calibration["gripper"]["max_width_m"] / 2
                    prepared[key] = self.data.ctrl[aid] + np.clip(desired - self.data.ctrl[aid], -0.08 * self.dt, 0.08 * self.dt)
            for key, value in prepared.items():
                if key in JOINT_GROUPS:
                    self.data.ctrl[self.actuators[key]] = value
                else:
                    self.data.ctrl[self.model.actuator(key.split("_")[0] + "_gripper").id] = value
        for _ in range(self.substeps):
            mujoco.mj_step(self.model, self.data)
        if not np.isfinite(self.data.qpos).all():
            raise RuntimeError("MuJoCo 状态非有限数值")
        if capture:
            self.capture()

    def close(self):
        if self.renderer is not None:
            self.renderer.close()
