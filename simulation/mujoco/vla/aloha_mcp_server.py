"""AutoDL 纯推理 MCP。与 aloha_contract.py 一起复制；不加载权重、不执行机器人。"""
import argparse
import asyncio
import base64
import io
import threading

import numpy as np
from PIL import Image

if __package__:
    from .aloha_contract import ACTION_REPRESENTATION, CAMERAS, CONTRACT_VERSION, validate_actions, validate_state
else:
    from aloha_contract import ACTION_REPRESENTATION, CAMERAS, CONTRACT_VERSION, validate_actions, validate_state


def decode_image(encoded):
    if not isinstance(encoded, str) or len(encoded) > 8_000_000:
        raise ValueError("图像必须是长度不超过8MB的 PNG/JPEG 纯 Base64 字符串")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 6_000_000:
        raise ValueError("压缩图像过大")
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in {"PNG", "JPEG"} or image.width * image.height > 2_000_000:
            raise ValueError("只接受不超过200万像素的 PNG/JPEG")
        # 官方 AlohaInputs 要求 CHW；ResizeImages transform 负责224尺寸对齐。
        return np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8).transpose(2, 0, 1))


def create_server(policy):
    from fastmcp import FastMCP
    metadata = policy.get_server_metadata()
    expected = {"contract_version": CONTRACT_VERSION, "action_representation": ACTION_REPRESENTATION,
                "policy_config": "pi05_aloha", "state_dim": 14, "action_dim": 14}
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise RuntimeError("模型服务不是本套 π0.5-ALOHA 契约；请先用 serve_aloha_policy.py 启动8000端口")
    if set(metadata.get("cameras", ())) != set(CAMERAS) or metadata.get("control_hz") != 50:
        raise RuntimeError("模型服务的 ALOHA 相机或控制频率不匹配")
    server = FastMCP("pi05-aloha")
    inference_lock = threading.Lock()
    broken = False

    def predict(observation):
        nonlocal broken
        # 不排队无限累积请求；同一个 WebSocket 的消息必须严格串行。
        if not inference_lock.acquire(blocking=False):
            raise RuntimeError("模型正在推理，请等待当前调用完成")
        try:
            if broken:
                raise RuntimeError("模型连接已失效，请重启 MCP 服务以重新连接")
            try:
                result = policy.infer(observation)
            except Exception:
                broken = True
                raise
            actions = validate_actions(result["actions"])
            return {**expected, "actions": actions.tolist(), "shape": list(actions.shape),
                    "robot_executed": False}
        finally:
            inference_lock.release()

    @server.tool()
    def pi_aloha_info() -> dict:
        """返回 π0.5 ALOHA 接口信息，不推理、不执行机器人。"""
        return {**expected, "cameras": list(CAMERAS), "control_hz": 50,
                "task_performance_validated": False, "robot_executed": False}

    @server.tool()
    async def pi_aloha_predict(prompt: str, state: list[float], images_base64: dict[str, str]) -> dict:
        """根据 ALOHA 观测预测14维双臂绝对关节目标序列，不执行机器人。

        state 顺序：左臂6个关节弧度、左夹爪0..1、右臂6个关节弧度、右夹爪0..1。
        images_base64 必须包含 cam_high、cam_left_wrist、cam_right_wrist 三张
        正常方向 RGB 的 PNG/JPEG 纯 Base64，不带 data: 前缀。
        返回 actions[H,14]；前后两组各6维是弧度目标，不是末端增量。
        """
        if not prompt.strip() or len(prompt) > 2000:
            raise ValueError("prompt 必须是1..2000字符的子任务指令")
        if set(images_base64) != set(CAMERAS):
            raise ValueError(f"images_base64 必须恰好包含 {CAMERAS}")
        observation = {"state": validate_state(state), "prompt": prompt,
                       "images": {name: decode_image(images_base64[name]) for name in CAMERAS}}
        return await asyncio.to_thread(predict, observation)

    @server.tool()
    async def pi_aloha_smoke_test() -> dict:
        """用黑图和假状态检查推理链路，不执行机器人、不代表任务成功。"""
        state = np.array([0, -0.96, 1.16, 0, -0.3, 0, 0.8] * 2, dtype=np.float32)
        observation = {"state": state, "prompt": "pick up the red cube",
                       "images": {name: np.zeros((3, 224, 224), dtype=np.uint8) for name in CAMERAS}}
        result = await asyncio.to_thread(predict, observation)
        return {"ok": True, "action_shape": result["shape"], "contract_version": CONTRACT_VERSION,
                "robot_executed": False, "task_success": None}

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-host", default="127.0.0.1")
    parser.add_argument("--policy-port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6006)
    args = parser.parse_args()
    from openpi_client.websocket_client_policy import WebsocketClientPolicy
    policy = WebsocketClientPolicy(host=args.policy_host, port=args.policy_port)
    create_server(policy).run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
