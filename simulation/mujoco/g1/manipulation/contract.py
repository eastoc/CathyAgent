"""仿真与 AutoDL GR00T 上半身 HTTP 服务共享的契约。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from PIL import Image


VERSION = "g1-upper-v1"
MODEL_ID = "nvidia/GN1x-Tuned-Arena-G1-Static-PickNPlace"
EMBODIMENT = "new_embodiment"
LANGUAGE_KEY = "annotation.human.task_description"
VIDEO_KEYS = ("ego_view",)
VIDEO_DELTA_INDICES = (0,)
STATE_DELTA_INDICES = (0,)
ACTION_DELTA_INDICES = tuple(range(40))

ARM_SUFFIXES = (
    "shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow",
    "wrist_roll", "wrist_pitch", "wrist_yaw",
)
HAND_SUFFIXES = (
    "index_0", "index_1", "middle_0", "middle_1",
    "thumb_0", "thumb_1", "thumb_2",
)
JOINT_GROUPS = {
    "left_arm": tuple(f"left_{name}_joint" for name in ARM_SUFFIXES),
    "right_arm": tuple(f"right_{name}_joint" for name in ARM_SUFFIXES),
    "left_hand": tuple(f"left_hand_{name}_joint" for name in HAND_SUFFIXES),
    "right_hand": tuple(f"right_hand_{name}_joint" for name in HAND_SUFFIXES),
    "waist": ("waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint"),
}
# Unitree/MuJoCo hardware order。GN1x 仍只消费 JOINT_GROUPS 中的 31 维上身
# 状态；这 29 个关节仅用于把 SONIC 全身输出完整显示在同一个 Dex3 场景中。
LEG_JOINTS = tuple(
    f"{side}_{name}_joint"
    for side in ("left", "right")
    for name in (
        "hip_pitch", "hip_roll", "hip_yaw", "knee", "ankle_pitch", "ankle_roll",
    )
)
SONIC_JOINTS = (
    *LEG_JOINTS,
    *JOINT_GROUPS["waist"],
    *JOINT_GROUPS["left_arm"],
    *JOINT_GROUPS["right_arm"],
)
STATE_DIMS = {key: len(names) for key, names in JOINT_GROUPS.items()}
ACTION_DIMS = STATE_DIMS.copy()
EXECUTED_ACTION_KEYS = tuple(JOINT_GROUPS)
STATIC_ACTION_KEYS: tuple[str, ...] = ()

CALIBRATION = Path(__file__).with_name("calibration.json")
SCENE = Path(__file__).resolve().parents[2] / "assets/g1_dex3/scene.xml"


def load_calibration(path: str | Path = CALIBRATION) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("model_id") != MODEL_ID:
        raise ValueError(f"calibration.model_id 必须是 {MODEL_ID}")
    if config.get("embodiment") != EMBODIMENT:
        raise ValueError(f"calibration.embodiment 必须是 {EMBODIMENT}")
    if config.get("control_hz") != 50:
        raise ValueError("Arena G1 数据与策略控制频率必须是 50 Hz")
    camera = config.get("camera", {})
    if camera.get("name") != "ego_view" or (camera.get("width"), camera.get("height")) != (640, 480):
        raise ValueError("GN1x 必须使用单路 640x480 ego_view")
    if len(camera.get("pos_m", ())) != 3 or len(camera.get("xyaxes", ())) != 6:
        raise ValueError("ego_view 缺少 MuJoCo 相机外参")
    if not 0 < float(camera.get("fovy_deg", 0)) < 180:
        raise ValueError("ego_view.fovy_deg 不合法")
    dex3 = config.get("dex3", {})
    if tuple(dex3.get("joint_order", ())) != HAND_SUFFIXES:
        raise ValueError("Dex3 关节顺序必须与 Arena 数据集一致")
    for pose_name in ("open_pose_rad", "closed_pose_rad"):
        for side in ("left", "right"):
            value = np.asarray(dex3.get(pose_name, {}).get(side, ()), dtype=float)
            if value.shape != (7,) or not np.isfinite(value).all():
                raise ValueError(f"dex3.{pose_name}.{side} 必须是 7 个有限关节角")
    initial = config.get("initial_pose_rad", {})
    for key, dim in STATE_DIMS.items():
        value = np.asarray(initial.get(key, ()), dtype=float)
        if value.shape != (dim,) or not np.isfinite(value).all():
            raise ValueError(f"initial_pose_rad.{key} 必须是 {dim} 个有限关节角")
    return config


def calibration_hash(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(config), sort_keys=True).encode()).hexdigest()


def encode_image(rgb: np.ndarray) -> str:
    value = np.asarray(rgb)
    if value.dtype != np.uint8 or value.ndim != 3 or value.shape[2] != 3:
        raise ValueError("图像必须是 HxWx3 uint8 RGB")
    buffer = io.BytesIO()
    Image.fromarray(value).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def decode_image(encoded: str) -> np.ndarray:
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        if image.width > 1920 or image.height > 1080:
            raise ValueError("图像最大支持 1920x1080")
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def sim_metadata() -> dict[str, Any]:
    return {
        "schema_version": VERSION,
        "embodiment": EMBODIMENT,
        "state_dims": STATE_DIMS.copy(),
        "action_dims": ACTION_DIMS.copy(),
        "video_keys": list(VIDEO_KEYS),
        "video_delta_indices": list(VIDEO_DELTA_INDICES),
        "state_delta_indices": list(STATE_DELTA_INDICES),
        "action_delta_indices": list(ACTION_DELTA_INDICES),
        "language_key": LANGUAGE_KEY,
        "action_representation": "absolute_physical",
        "output_scope": "upper_body_only",
    }


def validate_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    expected = sim_metadata()
    for key in (
        "schema_version", "embodiment", "state_dims", "action_dims",
        "video_keys", "video_delta_indices", "state_delta_indices",
        "action_delta_indices", "language_key", "action_representation",
    ):
        if metadata.get(key) != expected[key]:
            raise ValueError(
                f"GN1x metadata.{key}={metadata.get(key)!r}，需要 {expected[key]!r}"
            )
    remote_model = metadata.get("model_id") or metadata.get("model_path")
    model_name = MODEL_ID.rsplit("/", 1)[-1]
    if remote_model and MODEL_ID not in str(remote_model) and model_name not in str(remote_model):
        raise ValueError(f"AutoDL 当前模型不是 {MODEL_ID}: {remote_model}")
    return dict(metadata)


def observation_from_request(payload: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    validate_metadata(metadata)
    if payload.get("schema_version") != VERSION:
        raise ValueError(f"schema_version 应为 {VERSION}")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4096:
        raise ValueError("prompt 必须是 1-4096 字符的非空字符串")
    source_state = payload.get("state", {})
    if set(source_state) != set(STATE_DIMS):
        raise ValueError(f"state keys 应为 {list(STATE_DIMS)}")
    state: dict[str, np.ndarray] = {}
    for key, dim in STATE_DIMS.items():
        value = np.asarray(source_state[key], dtype=np.float32)
        expected_shape = (1, dim)
        if value.shape != expected_shape or not np.isfinite(value).all():
            raise ValueError(f"state.{key} 必须是有限数组 shape={expected_shape}")
        state[key] = value[None]
    source_video = payload.get("video", {})
    if set(source_video) != set(VIDEO_KEYS):
        raise ValueError(f"video keys 应为 {list(VIDEO_KEYS)}")
    frames = source_video["ego_view"]
    if not isinstance(frames, list) or len(frames) != 1:
        raise ValueError("video.ego_view 必须只有当前帧")
    video = {"ego_view": np.stack([decode_image(frames[0])])[None]}
    return {
        "video": video,
        "state": state,
        "language": {LANGUAGE_KEY: [[prompt.strip()]]},
    }


def upper_actions(action: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """官方 ``get_action`` 已反归一化；仅校验并序列化绝对物理动作。"""
    validate_metadata(metadata)
    horizon = len(ACTION_DELTA_INDICES)
    result: dict[str, Any] = {}
    for key, dim in ACTION_DIMS.items():
        if key not in action:
            raise ValueError(f"GR00T 返回缺少 action.{key}")
        value = np.asarray(action[key], dtype=np.float32)
        expected_shape = (1, horizon, dim)
        if value.shape != expected_shape or not np.isfinite(value).all():
            raise ValueError(f"GR00T action.{key} 应为有限数组 shape={expected_shape}")
        result[key] = value[0].tolist()
    return result


def validate_actions(response: Mapping[str, Any], metadata: Mapping[str, Any],
                     execute_steps: int) -> dict[str, np.ndarray]:
    validate_metadata(metadata)
    if response.get("schema_version") != VERSION:
        raise ValueError("服务器返回了未知 GR00T 协议")
    if response.get("action_representation") != "absolute_physical":
        raise ValueError("服务器动作必须是反归一化后的绝对物理量")
    source = response.get("actions", {})
    horizon = len(ACTION_DELTA_INDICES)
    if not 1 <= execute_steps <= horizon:
        raise ValueError("execute_steps 超过 GN1x 40 步动作块")
    result: dict[str, np.ndarray] = {}
    for key, dim in ACTION_DIMS.items():
        value = np.asarray(source.get(key), dtype=np.float32)
        expected_shape = (horizon, dim)
        if value.shape != expected_shape or not np.isfinite(value).all():
            raise ValueError(f"服务器 action.{key} 应为有限数组 shape={expected_shape}")
        result[key] = value[:execute_steps].copy()
    return result


__all__ = [
    "VERSION", "MODEL_ID", "EMBODIMENT", "LANGUAGE_KEY", "VIDEO_KEYS",
    "VIDEO_DELTA_INDICES", "STATE_DELTA_INDICES", "ACTION_DELTA_INDICES",
    "ARM_SUFFIXES", "HAND_SUFFIXES", "JOINT_GROUPS", "STATE_DIMS",
    "ACTION_DIMS", "EXECUTED_ACTION_KEYS", "STATIC_ACTION_KEYS", "CALIBRATION",
    "SCENE", "load_calibration", "calibration_hash", "encode_image",
    "decode_image", "sim_metadata", "validate_metadata",
    "observation_from_request", "upper_actions", "validate_actions",
]
