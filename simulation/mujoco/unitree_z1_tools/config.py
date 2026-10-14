"""仅属于 simulation 的 Z1 WMA 工具配置。"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping


DEFAULT_WMA_BASE_URL = (
    "https://u548917-epls-671d4d59.westd.seetacloud.com:8443"
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false 或 1/0")


@dataclass(frozen=True)
class UnitreeZ1ToolsConfig:
    enabled: bool = True
    wma_url: str = DEFAULT_WMA_BASE_URL
    inference_timeout_seconds: float = 600.0
    policy_camera: str = "cam_high"
    image_height: int = 480
    image_width: int = 640
    action_horizon: int = 16
    action_period_seconds: float = 1.0 / 30.0
    default_max_rounds: int = 3
    default_execute_steps: int = 16
    gripper_closed_threshold: float = 0.8
    grasp_lift_m: float = 0.05
    grasp_lift_duration_seconds: float = 1.0
    max_arm_speed_rad_s: float = 2.0 * math.pi
    max_gripper_step_rad: float = 5.0 / 20.0 / 25.6 * 3.0

    def to_plugin_config(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "UnitreeZ1ToolsConfig":
        values = dict(raw or {})
        config = cls(
            enabled=bool(values.get("enabled", True)),
            wma_url=str(values.get("wma_url") or DEFAULT_WMA_BASE_URL).rstrip("/"),
            inference_timeout_seconds=float(
                values.get("inference_timeout_seconds", 600.0)
            ),
            policy_camera=str(values.get("policy_camera") or "cam_high"),
            image_height=int(values.get("image_height", 480)),
            image_width=int(values.get("image_width", 640)),
            action_horizon=int(values.get("action_horizon", 16)),
            action_period_seconds=float(
                values.get("action_period_seconds", 1.0 / 30.0)
            ),
            default_max_rounds=int(values.get("default_max_rounds", 3)),
            default_execute_steps=int(values.get("default_execute_steps", 16)),
            gripper_closed_threshold=float(
                values.get("gripper_closed_threshold", 0.8)
            ),
            grasp_lift_m=float(values.get("grasp_lift_m", 0.05)),
            grasp_lift_duration_seconds=float(
                values.get("grasp_lift_duration_seconds", 1.0)
            ),
            max_arm_speed_rad_s=float(
                values.get("max_arm_speed_rad_s", 2.0 * math.pi)
            ),
            max_gripper_step_rad=float(
                values.get("max_gripper_step_rad", 5.0 / 20.0 / 25.6 * 3.0)
            ),
        )
        if not config.wma_url.startswith(("http://", "https://")):
            raise ValueError("wma_url 必须以 http:// 或 https:// 开头")
        if config.inference_timeout_seconds <= 0:
            raise ValueError("inference_timeout_seconds 必须大于 0")
        if (config.image_height, config.image_width) != (480, 640):
            raise ValueError("Z1 cam_high 原始观测必须是 480x640")
        if config.action_horizon != 16:
            raise ValueError("当前 UnifoLM-WMA 服务要求 action_horizon=16")
        if config.action_period_seconds <= 0:
            raise ValueError("action_period_seconds 必须大于 0")
        if not 1 <= config.default_execute_steps <= config.action_horizon:
            raise ValueError("default_execute_steps 必须在 [1, action_horizon]")
        if not 1 <= config.default_max_rounds <= 10:
            raise ValueError("default_max_rounds 必须在 [1, 10]")
        if not -1 <= config.gripper_closed_threshold <= 1:
            raise ValueError("gripper_closed_threshold 必须在 [-1, 1]")
        if not 0 < config.grasp_lift_m <= 0.15:
            raise ValueError("grasp_lift_m 必须在 (0, 0.15]")
        if config.grasp_lift_duration_seconds <= 0:
            raise ValueError("grasp_lift_duration_seconds 必须大于 0")
        if config.max_arm_speed_rad_s <= 0:
            raise ValueError("max_arm_speed_rad_s 必须大于 0")
        if config.max_gripper_step_rad <= 0:
            raise ValueError("max_gripper_step_rad 必须大于 0")
        return config


def load_unitree_z1_tools_config() -> UnitreeZ1ToolsConfig:
    """读取环境变量，不依赖项目通用 config。"""

    return UnitreeZ1ToolsConfig.from_mapping(
        {
            "enabled": _env_bool("UNITREE_Z1_TOOLS_ENABLED", True),
            "wma_url": os.environ.get("UNIFOLM_WMA_URL", DEFAULT_WMA_BASE_URL),
            "inference_timeout_seconds": os.environ.get(
                "UNIFOLM_WMA_TIMEOUT_SECONDS", "600"
            ),
            "policy_camera": os.environ.get("UNIFOLM_WMA_CAMERA", "cam_high"),
        }
    )
