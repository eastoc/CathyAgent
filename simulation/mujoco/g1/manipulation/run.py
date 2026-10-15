"""预览、夹爪自检或连接 AutoDL HTTP API；仿真在等待网络时暂停。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import numpy as np

from .build_scene import DESTINATION
from .contract import CALIBRATION, JOINT_GROUPS, SIM_DIMS, VERSION
from .runtime import G1ManipulationEnv


def validate_response(response, metadata, horizon):
    if response.get("schema_version") != VERSION or response.get("action_representation") != "absolute_physical":
        raise ValueError("服务器返回了未知动作格式")
    actions = response["actions"]
    if set(actions) - set(SIM_DIMS):
        raise ValueError("拒绝包含下半身或未知字段的动作")
    result = {}
    for key in (*JOINT_GROUPS, "left_hand", "right_hand"):
        value = np.asarray(actions[key], dtype=float)
        expected = (len(metadata["action_delta_indices"]), SIM_DIMS[key])
        if value.shape != expected or not np.isfinite(value).all():
            raise ValueError(f"服务器 action.{key} 形状应为 {expected}")
        result[key] = value[:horizon]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=DESTINATION / "scene.xml")
    parser.add_argument("--calibration", type=Path, default=CALIBRATION)
    parser.add_argument("--url", help="AutoDL 服务 URL，或 SSH 转发后的 http://127.0.0.1:16006；不传时仅本地预览")
    parser.add_argument("--prompt", default="Pick up the red cube and place it on the green target.")
    parser.add_argument("--chunks", type=int, default=5)
    parser.add_argument("--execution-horizon", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--viewer", action="store_true", help="macOS 使用 mjpython -m 启动")
    parser.add_argument("--output-dir", type=Path, default=Path("simulation/mujoco/g1/manipulation/outputs"))
    args = parser.parse_args()
    if args.chunks <= 0 or args.execution_horizon <= 0 or args.seconds <= 0:
        parser.error("chunks、execution-horizon 和 seconds 必须为正")
    if args.self_test and args.url:
        parser.error("--self-test 与 --url 不能同时使用")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    env = G1ManipulationEnv(args.scene, args.calibration)
    viewer = None
    client = None
    try:
        if args.viewer:
            import mujoco.viewer
            viewer = mujoco.viewer.launch_passive(env.model, env.data)
            viewer.cam.lookat[:] = [0.25, 0, 0.85]
            viewer.cam.distance = 2.4
            viewer.cam.azimuth = 135
            viewer.cam.elevation = -20

        def tick(target=None, capture=False):
            started = time.perf_counter()
            env.step(target, capture=capture)
            if viewer:
                if not viewer.is_running():
                    raise KeyboardInterrupt
                viewer.sync()
                time.sleep(max(0, env.dt - (time.perf_counter() - started)))

        if args.url:
            import httpx
            headers = {}
            if os.environ.get("GROOT_API_TOKEN"):
                headers["Authorization"] = f"Bearer {os.environ['GROOT_API_TOKEN']}"
            client = httpx.Client(base_url=args.url.rstrip("/"), headers=headers, timeout=120)
            reply = client.get("/metadata")
            reply.raise_for_status()
            metadata = reply.json()
            env.validate_server(metadata)
            if args.execution_horizon > len(metadata["action_delta_indices"]):
                raise ValueError("execution-horizon 不能超过 checkpoint 的 action horizon")
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
            # 真正采集历史帧；基于数据频率（本项目自定义 20 Hz），不是 HTTP 请求次数。
            env.capture()
            for _ in range(-min(metadata["video_delta_indices"])):
                tick(capture=True)
            for chunk in range(args.chunks):
                reply = client.post("/infer", json=env.request(args.prompt, metadata))
                reply.raise_for_status()
                response = reply.json()
                actions = validate_response(response, metadata, args.execution_horizon)
                for index in range(args.execution_horizon):
                    tick({key: value[index] for key, value in actions.items()}, capture=True)
                print(f"chunk={chunk + 1} backend={response['backend']} inference_ms={response['inference_ms']:.1f}")
        else:
            initial = env.state()
            target = {key: initial[key] for key in (*JOINT_GROUPS, "left_hand", "right_hand")}
            for index in range(round(args.seconds / env.dt)):
                if args.self_test:
                    fraction = index / max(1, round(args.seconds / env.dt) - 1)
                    opening = 0.0 if fraction < 0.5 else 1.0
                    target.update(left_hand=[opening], right_hand=[opening])
                tick(target)
            print("本地夹爪/物理自检完成；没有运行 GR00T，也未评估抓取成功率。")
        from PIL import Image
        for camera in ("ego_view", "overview"):
            Image.fromarray(env.render(camera)).save(args.output_dir / f"{camera}.png")
        state_path = args.output_dir / "state.json"
        state_path.write_text(json.dumps({k: v.tolist() for k, v in env.state().items()}, indent=2))
        print(f"预览与状态：{args.output_dir.resolve()}")
    finally:
        if client:
            client.close()
        if viewer:
            viewer.close()
        env.close()


if __name__ == "__main__":
    main()
