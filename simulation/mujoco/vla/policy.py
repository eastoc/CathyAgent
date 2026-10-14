"""真实 AutoDL pi_predict 契约；图像编码与动作验证均在宿主完成。"""
from __future__ import annotations

import base64
import io
import json
from typing import Any

import numpy as np
from PIL import Image


def encode_policy_image(rgb: np.ndarray, *, rotate_180: bool = True) -> str:
    array = np.asarray(rgb)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("策略相机必须是 H×W×3 uint8 RGB")
    if rotate_180:
        array = array[::-1, ::-1]
    image = Image.fromarray(np.ascontiguousarray(array))
    # AutoDL decode_image 负责官方 resize_with_pad(224)，此处只校正图像方向。
    if image.width != image.height:
        raise ValueError("当前 LIBERO 适配器只接受正方形相机帧")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def parse_actions(result: Any) -> np.ndarray:
    if getattr(result, "is_error", False):
        raise ValueError("π MCP 返回推理错误")
    payload = getattr(result, "structured_content", None)
    if payload is None:
        payload = getattr(result, "data", None)
    if payload is None:
        for block in getattr(result, "content", ()):
            if getattr(block, "type", None) == "text":
                try:
                    payload = json.loads(block.text)
                    break
                except json.JSONDecodeError:
                    continue
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if not isinstance(payload, dict) or "actions" not in payload:
        raise ValueError("pi_predict 必须返回 {actions: [[7维动作], ...]}")
    if payload.get("robot_executed") is not False:
        raise ValueError("需要 robot_executed=false 的纯推理服务，禁止混用远端仿真动作")
    actions = np.asarray(payload["actions"], dtype=np.float64)
    if actions.ndim != 2 or actions.shape[1] != 7 or not 1 <= len(actions) <= 512:
        raise ValueError(f"π 动作维度错误: {actions.shape}; 要求 [1..512,7]")
    if not np.isfinite(actions).all():
        raise ValueError("π 动作包含 NaN/Inf")
    # robosuite 的 OSC 输入范围；轻微超界裁剪，明显不匹配则拒绝执行。
    if np.abs(actions).max() > 2:
        raise ValueError("π 动作范围明显不匹配归一化 OSC_POSE 契约")
    return np.clip(actions, -1, 1)


class PiMcpPolicy:
    def __init__(self, hub, tool_name: str, *, timeout: float, rotate_180: bool):
        self.hub, self.tool_name = hub, tool_name
        self.timeout, self.rotate_180 = timeout, rotate_180

    async def predict(self, prompt: str, observation: dict[str, Any]) -> np.ndarray:
        state = np.asarray(observation["state"], dtype=float)
        if state.shape != (8,) or not np.isfinite(state).all():
            raise ValueError("π0.5-LIBERO 本体状态必须是8维有限数")
        result = await self.hub.acall_tool_raw(self.tool_name, {
            "prompt": prompt, "state": state.tolist(),
            "image_base64": encode_policy_image(observation["image"], rotate_180=self.rotate_180),
            "wrist_image_base64": encode_policy_image(observation["wrist_image"], rotate_180=self.rotate_180),
        }, timeout=self.timeout)
        return parse_actions(result)
