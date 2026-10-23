"""GN1x Arena G1 静态抓放技能配置。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from typing import Any, Mapping


DEFAULT_GROOT_URL = "https://u405425-xd7z-334db773.weste.seetacloud.com:8443"
# 旧环境变量/调用方兼容名。
DEFAULT_WLA_URL = DEFAULT_GROOT_URL


@dataclass(frozen=True)
class G1ManipulationConfig:
    enabled: bool = True
    groot_url: str = DEFAULT_GROOT_URL
    inference_timeout_seconds: float = 180.0
    default_max_policy_calls: int = 20
    default_execute_steps: int = 5
    max_execute_steps: int = 8
    pick_lift_m: float = 0.04
    pick_stable_steps: int = 5
    contact_loss_steps: int = 3
    closed_threshold: float = 0.12
    release_open_threshold: float = 0.60
    settle_timeout_seconds: float = 2.0

    @property
    def wla_url(self) -> str:
        return self.groot_url

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "G1ManipulationConfig":
        values = dict(raw or {})
        url = values.get("groot_url") or values.get("wla_url") or DEFAULT_GROOT_URL
        result = cls(
            enabled=bool(values.get("enabled", True)),
            groot_url=str(url).rstrip("/"),
            inference_timeout_seconds=float(values.get("inference_timeout_seconds", 180.0)),
            default_max_policy_calls=int(values.get("default_max_policy_calls", 20)),
            default_execute_steps=int(values.get("default_execute_steps", 5)),
            max_execute_steps=int(values.get("max_execute_steps", 8)),
            pick_lift_m=float(values.get("pick_lift_m", 0.04)),
            pick_stable_steps=int(values.get("pick_stable_steps", 5)),
            contact_loss_steps=int(values.get("contact_loss_steps", 3)),
            closed_threshold=float(values.get("closed_threshold", 0.12)),
            release_open_threshold=float(values.get("release_open_threshold", 0.60)),
            settle_timeout_seconds=float(values.get("settle_timeout_seconds", 2.0)),
        )
        if not result.groot_url.startswith(("http://", "https://")):
            raise ValueError("groot_url 必须是 HTTP(S) URL")
        positive = (
            result.inference_timeout_seconds, result.pick_lift_m,
            result.settle_timeout_seconds,
        )
        if not all(math.isfinite(value) and value > 0 for value in positive):
            raise ValueError("GN1x 超时、抬升高度和落稳超时必须为正有限数")
        if not 1 <= result.default_execute_steps <= result.max_execute_steps <= 40:
            raise ValueError("动作块执行步数配置不合法")
        if not 1 <= result.default_max_policy_calls <= 100:
            raise ValueError("default_max_policy_calls 必须在 [1,100]")
        if result.pick_stable_steps < 1 or result.contact_loss_steps < 1:
            raise ValueError("稳定与接触丢失计数必须为正整数")
        if not 0 <= result.closed_threshold < result.release_open_threshold <= 1:
            raise ValueError("Dex3 闭合/释放阈值不合法")
        return result

    def to_plugin_config(self) -> dict[str, Any]:
        return asdict(self)


def load_g1_manipulation_config() -> G1ManipulationConfig:
    return G1ManipulationConfig.from_mapping({
        "enabled": os.environ.get("G1_MANIPULATION_ENABLED", "1").lower()
        not in {"0", "false", "no", "off"},
        "groot_url": os.environ.get("GROOT_URL") or os.environ.get("WLA_URL")
        or DEFAULT_GROOT_URL,
        "inference_timeout_seconds": os.environ.get(
            "GROOT_TIMEOUT_SECONDS", os.environ.get("WLA_TIMEOUT_SECONDS", "180")
        ),
    })


__all__ = [
    "DEFAULT_GROOT_URL", "DEFAULT_WLA_URL", "G1ManipulationConfig",
    "load_g1_manipulation_config",
]
