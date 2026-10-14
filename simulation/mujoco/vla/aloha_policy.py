"""本地 ALOHA → AutoDL MCP：严格区分 LIBERO OSC 和 ALOHA 关节位置动作。"""
import base64
import io
import json

import numpy as np
from PIL import Image

from .aloha_contract import ACTION_REPRESENTATION, CAMERAS, CONTRACT_VERSION, validate_actions, validate_state


def encode_aloha_image(rgb):
    value = np.asarray(rgb)
    if value.dtype != np.uint8 or value.ndim != 3 or value.shape[2] != 3:
        raise ValueError("ALOHA 相机必须是 H×W×3 uint8 RGB")
    stream = io.BytesIO()
    Image.fromarray(value).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def parse_aloha_actions(result):
    if getattr(result, "is_error", False):
        raise ValueError("ALOHA MCP 返回推理错误")
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
    if not isinstance(payload, dict) or payload.get("robot_executed") is not False:
        raise ValueError("ALOHA MCP 必须是 robot_executed=false 的纯推理服务")
    if (payload.get("contract_version") != CONTRACT_VERSION or
            payload.get("action_representation") != ACTION_REPRESENTATION or
            payload.get("policy_config") != "pi05_aloha"):
        raise ValueError("ALOHA 服务契约不匹配，不能将 LIBERO/增量动作当作双臂关节位置")
    actions = validate_actions(payload.get("actions"))
    if payload.get("shape") != list(actions.shape):
        raise ValueError("ALOHA shape 与动作数组不一致")
    return actions


class PiAlohaMcpPolicy:
    def __init__(self, hub, tool_name, *, timeout):
        self.hub, self.tool_name, self.timeout = hub, tool_name, timeout

    async def predict(self, prompt, observation):
        state = validate_state(observation["state"])
        if set(observation["images"]) != set(CAMERAS):
            raise ValueError("ALOHA 需要主相机和左右腕部相机")
        result = await self.hub.acall_tool_raw(self.tool_name, {
            "prompt": prompt, "state": state.tolist(),
            "images_base64": {name: encode_aloha_image(observation["images"][name]) for name in CAMERAS},
        }, timeout=self.timeout)
        return parse_aloha_actions(result)
