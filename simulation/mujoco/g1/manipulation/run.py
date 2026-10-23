"""预览 Arena G1 Dex3 场景，或连接 AutoDL GN1x 做闭环联调。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time

from .client import Gr00tClient
from .config import DEFAULT_GROOT_URL
from .contract import CALIBRATION, SCENE, validate_actions
from .runtime import G1ManipulationEnv
from .tasks import DEFAULT_TASK, TASKS, validate_handoff


validate_response = validate_actions


async def _run(args) -> None:
    env = G1ManipulationEnv(args.scene, args.calibration, viewer_enabled=args.viewer)
    env.reset(args.task, randomize=args.randomize, seed=args.seed)
    prompt = args.prompt or TASKS[args.task].instruction
    client = Gr00tClient(args.url, timeout_seconds=args.timeout) if args.connect else None
    try:
        if client is None:
            target = env.hold_target()
            if args.self_test:
                target["left_hand"] = env.closed_hand_pose("left")
            for index in range(round(args.seconds / env.dt)):
                if args.self_test and index == round(args.seconds / env.dt) // 2:
                    target["left_hand"] = env.open_hand_pose("left")
                env.step(target)
                if args.viewer:
                    time.sleep(env.dt)
            print("本地 Arena G1/Dex3 物理预览完成；没有运行 GN1x。")
        else:
            metadata = await client.metadata()
            env.validate_server(metadata)
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
            for chunk in range(args.chunks):
                response = await client.infer(env.request(prompt, metadata))
                actions = validate_actions(response, metadata, args.execution_horizon)
                for index in range(args.execution_horizon):
                    env.step_policy_action(
                        {key: value[index] for key, value in actions.items()},
                        capture=True,
                    )
                    if args.viewer:
                        time.sleep(env.dt)
                status = env.task_status()
                print(f"chunk={chunk + 1} success={status['success']}")
                if status["success"]:
                    break

        args.output_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        for camera in ("ego_view", "overview"):
            Image.fromarray(env.render(camera)).save(args.output_dir / f"{camera}.png")
        (args.output_dir / "state.json").write_text(
            json.dumps({
                "robot": {key: value.tolist() for key, value in env.state().items()},
                "task": env.task_status(),
            }, indent=2),
            encoding="utf-8",
        )
        print(f"预览与状态：{args.output_dir.resolve()}")
    finally:
        if client is not None:
            await client.aclose()
        env.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=SCENE)
    parser.add_argument("--calibration", type=Path, default=CALIBRATION)
    parser.add_argument("--url", default=DEFAULT_GROOT_URL,
                        help="AutoDL HTTPS 地址；外部 8443 映射容器 6006")
    parser.add_argument("--connect", action="store_true", help="实际连接 GN1x 服务")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--task", choices=sorted(TASKS), default=DEFAULT_TASK)
    parser.add_argument("--prompt")
    parser.add_argument("--navigation-pose", nargs=3, type=float,
                        metavar=("X", "Y", "YAW"))
    parser.add_argument("--randomize", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--chunks", type=int, default=5)
    parser.add_argument("--execution-horizon", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--viewer", action="store_true", help="macOS 使用 mjpython -m 启动")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("simulation/mujoco/g1/manipulation/outputs"),
    )
    args = parser.parse_args()
    if min(args.chunks, args.execution_horizon, args.seconds, args.timeout) <= 0:
        parser.error("chunks、execution-horizon、seconds 和 timeout 必须为正")
    if args.navigation_pose:
        validate_handoff(*args.navigation_pose)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()


__all__ = ["validate_response", "main"]
