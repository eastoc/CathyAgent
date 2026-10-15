"""共享的 HTTP 契约；模型维度从 checkpoint 读取，夹爪仿真契约单独定义。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

VERSION = "g1-upper-v1"
REAL_G1 = "real_g1_relative_eef_relative_joints"
LANGUAGE_KEY = "annotation.human.task_description"
UPPER_KEYS = (
    "left_wrist_eef_9d", "right_wrist_eef_9d", "left_hand", "right_hand",
    "left_arm", "right_arm", "waist",
)
SIM_DIMS = dict(zip(UPPER_KEYS, (9, 9, 1, 1, 7, 7, 3)))
ARM_SUFFIXES = (
    "shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow",
    "wrist_roll", "wrist_pitch", "wrist_yaw",
)
JOINT_GROUPS = {
    **{f"{side}_arm": tuple(f"{side}_{j}_joint" for j in ARM_SUFFIXES)
       for side in ("left", "right")},
    "waist": ("waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint"),
}
CALIBRATION = Path(__file__).with_name("calibration.json")


def load_calibration(path=CALIBRATION):
    config = json.loads(Path(path).read_text())
    if config["control_hz"] <= 0 or config["gripper"]["max_width_m"] <= 0:
        raise ValueError("control_hz 和 max_width_m 必须为正数")
    if config["eef"]["source"] not in ("wrist", "tcp"):
        raise ValueError("eef.source 必须是 wrist 或 tcp")
    if config["eef"]["rotation_6d"] not in ("first_two_rows", "first_two_columns"):
        raise ValueError("未知 rotation_6d 编码")
    return config


def calibration_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def encode_image(rgb):
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def decode_image(encoded):
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw)) as img:
        if img.width > 1920 or img.height > 1080:
            raise ValueError("图像最大支持 1920×1080")
        return np.asarray(img.convert("RGB"), dtype=np.uint8)


def sim_metadata():
    return {
        "schema_version": VERSION, "embodiment": "new_embodiment",
        "state_dims": SIM_DIMS.copy(), "action_dims": SIM_DIMS.copy(),
        "video_keys": ["ego_view"], "video_delta_indices": [-20, 0],
        "state_delta_indices": [0], "action_delta_indices": list(range(40)),
        "language_key": LANGUAGE_KEY,
        "action_representation": "absolute_physical",
        "output_scope": "upper_body_only",
    }


def observation_from_request(payload, metadata):
    if payload.get("schema_version") != VERSION:
        raise ValueError(f"schema_version 应为 {VERSION}")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4096:
        raise ValueError("prompt 必须是 1–4096 字符的非空字符串")
    state = {}
    source = payload.get("state", {})
    if set(source) != set(metadata["state_dims"]):
        raise ValueError(f"state keys 应为 {list(metadata['state_dims'])}")
    for key, dim in metadata["state_dims"].items():
        value = np.asarray(source[key], dtype=np.float32)
        expected = (len(metadata["state_delta_indices"]), dim)
        if value.shape != expected or not np.isfinite(value).all():
            raise ValueError(f"state.{key} 必须是有限数值，shape={expected}")
        state[key] = value[None]
    video = {}
    source = payload.get("video", {})
    if set(source) != set(metadata["video_keys"]):
        raise ValueError(f"video keys 应为 {metadata['video_keys']}")
    for key in metadata["video_keys"]:
        if len(source[key]) != len(metadata["video_delta_indices"]):
            raise ValueError(f"video.{key} 历史帧数量不匹配")
        video[key] = np.stack([decode_image(v) for v in source[key]])[None]
    return {"video": video, "state": state,
            "language": {metadata["language_key"]: [[prompt]]}}


def upper_actions(action, metadata):
    """官方 get_action 已解码绝对值；不做二次反归一化或相对累加。"""
    output = {}
    horizon = len(metadata["action_delta_indices"])
    for key, dim in metadata["action_dims"].items():
        if key not in UPPER_KEYS:
            continue
        value = np.asarray(action[key], dtype=np.float32)
        if value.shape != (1, horizon, dim) or not np.isfinite(value).all():
            raise ValueError(f"模型 action.{key} 的形状或数值不合法")
        output[key] = value[0].tolist()
    if not all(k in output for k in ("left_arm", "right_arm", "waist", "left_hand", "right_hand")):
        raise ValueError("checkpoint 缺少双臂、双手或腰部输出，不能用于本接口")
    return output
