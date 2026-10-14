"""UnifoLM-WMA `/predict_action` HTTP 客户端。"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import requests


class WMAClientError(RuntimeError):
    pass


class WMAClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 600.0,
        action_horizon: int = 16,
        action_dim: int = 7,
        image_height: int = 480,
        image_width: int = 640,
        session: requests.Session | None = None,
    ) -> None:
        normalized = base_url.rstrip("/")
        self.endpoint = (
            normalized
            if normalized.endswith("/predict_action")
            else f"{normalized}/predict_action"
        )
        self.timeout_seconds = float(timeout_seconds)
        self.action_horizon = int(action_horizon)
        self.action_dim = int(action_dim)
        self.image_height = int(image_height)
        self.image_width = int(image_width)
        self.session = session or requests.Session()

    def _validate_observation(
        self,
        images: np.ndarray,
        states: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        image_array = np.asarray(images)
        state_array = np.asarray(states, dtype=np.float32)
        expected_images = (2, self.image_height, self.image_width, 3)
        if image_array.shape != expected_images:
            raise WMAClientError(
                f"WMA 图像必须是 {expected_images}，实际 {image_array.shape}"
            )
        if image_array.dtype != np.uint8:
            raise WMAClientError("WMA 图像必须是 uint8 RGB")
        if state_array.shape != (2, 7) or not np.isfinite(state_array).all():
            raise WMAClientError(
                f"WMA 状态必须是有限的 (2,7)，实际 {state_array.shape}"
            )
        return image_array, state_array

    def _predict_sync(
        self,
        instruction: str,
        images: np.ndarray,
        states: np.ndarray,
    ) -> np.ndarray:
        image_array, state_array = self._validate_observation(images, states)
        chw_images = image_array.transpose(0, 3, 1, 2)
        payload = {
            "language_instruction": instruction,
            "observation.images.top": chw_images.tolist(),
            "observation.state": state_array.tolist(),
            "action": np.zeros(
                (self.action_horizon, self.action_dim), dtype=np.float32
            ).tolist(),
        }
        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                timeout=(10.0, self.timeout_seconds),
            )
            response.raise_for_status()
            result: Any = response.json()
        except requests.RequestException as exc:
            raise WMAClientError(f"AutoDL WMA 请求失败: {exc}") from exc
        except ValueError as exc:
            raise WMAClientError("AutoDL WMA 返回的不是合法 JSON") from exc

        if not isinstance(result, dict) or result.get("result") != "ok":
            detail = result.get("desc") if isinstance(result, dict) else repr(result)
            raise WMAClientError(f"AutoDL WMA 推理失败: {str(detail)[:2000]}")
        actions = np.asarray(result.get("action"), dtype=np.float32)
        expected = (self.action_horizon, self.action_dim)
        if actions.shape != expected or not np.isfinite(actions).all():
            raise WMAClientError(
                f"AutoDL WMA 动作必须是有限的 {expected}，实际 {actions.shape}"
            )
        return actions

    async def predict(
        self,
        instruction: str,
        images: np.ndarray,
        states: np.ndarray,
    ) -> np.ndarray:
        if not isinstance(instruction, str) or not instruction.strip():
            raise WMAClientError("instruction 不能为空")
        return await asyncio.to_thread(
            self._predict_sync,
            instruction.strip(),
            images,
            states,
        )

    def close(self) -> None:
        self.session.close()
