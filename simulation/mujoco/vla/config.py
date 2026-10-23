"""VLA 配置与约束，独立于 Z1 单关节夹爪配置。"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..config import _load_yaml

DEFAULT_VLA_ENVIRONMENT = "configs/environments/aloha_three_cube.yaml"
LEGACY_VLA_ENVIRONMENT = "configs/environments/dual_panda_three_cube.yaml"


def _vector(value, size, name):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{name} 必须为{size}维数组")
    result = tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in result):
        raise ValueError(f"{name} 必须为有限数")
    return result


def load_vla_config(path: str | Path) -> dict[str, Any]:
    cfg = _load_yaml(path)
    if cfg.get("backend") not in {"robosuite_dual_panda", "native_aloha"}:
        raise ValueError("VLA 后端需要 native_aloha 或 robosuite_dual_panda 场景")
    aloha = cfg["backend"] == "native_aloha"
    if cfg.get("schema_version") != 1:
        raise ValueError("VLA 场景只支持 schema_version=1")
    scene, obs, runtime, policy = (cfg[key] for key in ("scene", "observation", "runtime", "policy"))
    for name in ("replan_steps", "default_skill_steps", "max_skill_steps"):
        value = policy[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"policy.{name} 必须为正整数")
    if not policy["replan_steps"] <= policy["default_skill_steps"] <= policy["max_skill_steps"]:
        raise ValueError("需满足 replan_steps <= default_skill_steps <= max_skill_steps")
    for name in ("inference_timeout_seconds", "skill_timeout_seconds"):
        if not math.isfinite(float(policy[name])) or float(policy[name]) <= 0:
            raise ValueError(f"policy.{name} 必须为正有限数")
    arms = policy["allowed_arms"]
    valid_arms = {"both"} if aloha else {"left", "right"}
    if not arms or not set(arms) <= valid_arms or policy["active_arm"] not in arms:
        raise ValueError(f"此后端只支持 {sorted(valid_arms)}；active_arm 必须在 allowed_arms 中")
    if runtime["control_hz"] != (50 if aloha else 20):
        raise ValueError("ALOHA 要求50Hz；LIBERO 要求20Hz")
    if obs["policy_size"] != 224:
        raise ValueError("当前适配器要求224策略输入")
    if aloha:
        if not 10 <= float(obs["wrist_fovy"]) <= 120:
            raise ValueError("ALOHA 腕部视野必须在10..120度之间")
        if (obs["render_height"], obs["render_width"]) != (480, 640) or obs["policy_rotate_180"]:
            raise ValueError("ALOHA 使用480×640正常方向的 RGB，不旋转180度")
        if policy.get("profile") != "pi05_aloha" or policy["tool"] != "pi_aloha_predict":
            raise ValueError("ALOHA 需要 pi05_aloha / pi_aloha_predict，不能复用 LIBERO 服务")
        _vector(scene["initial_arm_qpos"], 6, "initial_arm_qpos")
        if not 0 <= scene["initial_gripper"] <= 1:
            raise ValueError("initial_gripper 必须为0..1")
        for name in ("max_joint_velocity", "max_gripper_velocity"):
            if not math.isfinite(float(runtime[name])) or runtime[name] <= 0:
                raise ValueError(f"runtime.{name} 必须为正有限数")
    elif obs["render_size"] != 256:
        raise ValueError("LIBERO 要求256渲染")
    if set(scene["cubes"]) != {"red", "green", "blue"}:
        raise ValueError("场景需有 red/green/blue 三个方块")
    for key in ("cube_half_size", "box_height", "box_wall_thickness"):
        if not math.isfinite(float(scene[key])) or scene[key] <= 0:
            raise ValueError(f"scene.{key} 必须为正有限数")
    if not math.isfinite(float(scene["table_height"])) or scene["table_height"] < 0:
        raise ValueError("table_height 必须是非负有限数")
    if aloha and scene["table_height"] != 0:
        raise ValueError("当前 ALOHA 资产以桌面 z=0 为基准")
    if runtime["max_episode_steps"] < policy["max_skill_steps"]:
        raise ValueError("episode 步数必须至少容纳一次最大技能")
    for name in ("settle_steps", "max_episode_steps"):
        value = runtime[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"runtime.{name} 必须为非负整数")
    if not isinstance(runtime["viewer"], bool) or not isinstance(obs["policy_rotate_180"], bool):
        raise ValueError("viewer 和 policy_rotate_180 必须为布尔值")
    table = _vector(scene["table_size"], 3, "table_size")
    if min(table) <= 0:
        raise ValueError("table_size 必须全部为正数")
    center = (0, 0)
    if aloha:
        center = _vector(scene["table_center_xy"], 2, "table_center_xy")
        if center != (0, 0.6):
            raise ValueError("当前 ALOHA 资产的桌面中心必须为[0,0.6]")
    else:
        _vector(scene["arm_y_offsets"], 2, "arm_y_offsets")
        _vector(obs["agentview_pos"], 3, "agentview_pos")
        quat = _vector(obs["agentview_quat"], 4, "agentview_quat")
        if not math.isclose(sum(v * v for v in quat), 1, abs_tol=1e-5):
            raise ValueError("相机四元数必须归一化")
    jitter = float(scene["position_jitter"])
    if not math.isfinite(jitter) or jitter < 0:
        raise ValueError("position_jitter 必须为非负有限数")
    if aloha and jitter != 0:
        raise ValueError("当前 ALOHA 场景使用固定初始位置，position_jitter 必须为0")
    half = scene["cube_half_size"]
    cube_positions = [_vector(scene["cubes"][color], 2, color) for color in ("red", "green", "blue")]
    for xy in cube_positions:
        if any(abs(xy[i] - center[i]) + half + jitter >= table[i] / 2 for i in (0, 1)):
            raise ValueError("方块含随机偏移必须位于桌面内")
    for i, a in enumerate(cube_positions):
        for b in cube_positions[i + 1:]:
            if all(abs(a[k] - b[k]) < 2 * (half + jitter) for k in (0, 1)):
                raise ValueError("方块的初始位置/随机偏移范围重叠")
    box = _vector(scene["box_xy"], 2, "box_xy")
    inner = _vector(scene["box_inner_half_size"], 2, "box_inner_half_size")
    if min(inner) <= half:
        raise ValueError("盒子内部尺寸必须大于方块")
    outer = [v + scene["box_wall_thickness"] for v in inner]
    if any(abs(box[i] - center[i]) + outer[i] >= table[i] / 2 for i in (0, 1)):
        raise ValueError("盒子必须位于桌面内")
    return cfg
