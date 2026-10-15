"""在单一进程中启动 MuJoCo、SONIC ONNX 推理和根位姿发布。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import time

from .config import DEFAULT_G1_ENVIRONMENT, load_g1_navigation_config
from .direct_sim import DirectG1MujocoEnv
from .sonic.pose import ZmqNavigationPosePublisher
from .sonic_onnx import (
    InMemoryUnitreeBridge, InProcessSonicController, SonicOnnxPaths,
)
from .sonic_onnx.protocol import PlannerCommand


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SONIC_ROOT = PROJECT_ROOT / "third_party" / "GR00T-WholeBodyControl"
_MJPYTHON_REEXEC_ENV = "CATHY_G1_MJPYTHON_REEXEC"


def _ensure_macos_viewer_runtime(*, no_viewer: bool) -> None:
    """在 macOS 打开 Viewer 时自动切换到当前环境的 mjpython。"""
    if (
        no_viewer
        or sys.platform != "darwin"
        or os.environ.get("MJPYTHON_BIN")
        or os.environ.get(_MJPYTHON_REEXEC_ENV) == "1"
    ):
        return

    mjpython = shutil.which("mjpython")
    if mjpython is None:
        raise RuntimeError(
            "macOS 打开 MuJoCo Viewer 需要 mjpython，但当前环境未找到该命令；"
            "请安装 mujoco，或添加 --no-viewer 进行无窗口仿真"
        )

    env = os.environ.copy()
    env[_MJPYTHON_REEXEC_ENV] = "1"
    print(f"macOS Viewer 需要 mjpython，正在自动切换到: {mjpython}", flush=True)
    os.execve(
        mjpython,
        [mjpython, "-m", "simulation.mujoco.g1.run_sonic_sim", *sys.argv[1:]],
        env,
    )


def _configure_sonic_imports(root: Path) -> None:
    root = root.expanduser().resolve()
    package = root / "gear_sonic"
    unitree = root / "external_dependencies" / "unitree_sdk2_python"
    if not package.is_dir() or not unitree.is_dir():
        raise RuntimeError(
            f"SONIC 源码目录不完整: {root}；需要 gear_sonic/ 和 "
            "external_dependencies/unitree_sdk2_python/"
        )
    for path in (root, unitree):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="G1 MuJoCo + SONIC ONNX 同进程仿真")
    parser.add_argument("--environment", default=DEFAULT_G1_ENVIRONMENT)
    parser.add_argument("--sim-frequency", type=int, default=200)
    parser.add_argument("--control-frequency", type=int, default=50)
    parser.add_argument("--planner-frequency", type=int, default=10)
    parser.add_argument("--provider", default="CPUExecutionProvider")
    parser.add_argument("--command-endpoint", default="tcp://127.0.0.1:5556")
    parser.add_argument("--pose-endpoint", default="tcp://*:5558")
    parser.add_argument(
        "--sonic-root",
        type=Path,
        default=Path(os.environ.get("SONIC_ROOT", DEFAULT_SONIC_ROOT)),
        help="GR00T-WholeBodyControl 仓库目录（也可设置 SONIC_ROOT）",
    )
    parser.add_argument("--no-viewer", action="store_true")
    parser.add_argument(
        "--disable-zmq", action="store_true",
        help="仅运行 MuJoCo+ONNX 自检，不接收导航命令或发布位姿",
    )
    parser.add_argument(
        "--max-seconds", type=float, default=0.0,
        help="大于 0 时在指定秒数后退出，供自动化自检使用",
    )
    parser.add_argument(
        "--self-test-walk", action="store_true",
        help="不等待导航 Agent，持续发送 0.35m/s 前进命令用于闭环自检",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    _ensure_macos_viewer_runtime(no_viewer=args.no_viewer)
    if min(args.sim_frequency, args.control_frequency, args.planner_frequency) <= 0:
        raise ValueError("仿真、控制和规划频率必须为正数")
    if args.control_frequency > args.sim_frequency:
        raise ValueError("control-frequency 不能高于 sim-frequency")
    navigation = load_g1_navigation_config(args.environment)
    network_disabled = args.disable_zmq or args.self_test_walk
    sim_dt = 1.0 / args.sim_frequency
    env = DirectG1MujocoEnv(
        navigation.scene_path,
        sim_dt=sim_dt,
        onscreen=not args.no_viewer,
        initial_pose_xy_yaw=navigation.initial_pose_xy_yaw,
    )
    bridge = InMemoryUnitreeBridge()
    env.set_unitree_bridge(bridge)
    controller = InProcessSonicController(
        SonicOnnxPaths.from_root(args.sonic_root.expanduser().resolve()),
        bridge,
        provider=args.provider,
        command_endpoint=None if network_disabled else args.command_endpoint,
        control_hz=args.control_frequency,
        planner_hz=args.planner_frequency,
        forced_command=(
            PlannerCommand(
                mode=1, movement=(1.0, 0.0, 0.0),
                facing=(1.0, 0.0, 0.0), speed=0.35, height=-1.0,
            )
            if args.self_test_walk else None
        ),
    )
    publisher = None
    if not network_disabled:
        publisher = ZmqNavigationPosePublisher(
            args.pose_endpoint, topic=navigation.sonic.pose_topic
        )
        publisher.open()
        publisher.publish_qpos(env.mj_data.qpos)
    controller.open(env)
    control_stride = max(1, round(args.sim_frequency / args.control_frequency))
    viewer_stride = max(1, round(0.02 / sim_dt))
    reward_stride = max(1, round(0.02 / sim_dt))
    counter = 0
    run_started = time.monotonic()
    try:
        while env.viewer is None or env.viewer.is_running():
            if args.max_seconds > 0 and time.monotonic() - run_started >= args.max_seconds:
                break
            started = time.monotonic()
            if counter % control_stride == 0:
                controller.step(env)
                # 站立等待时保留竖直保护；SONIC 接管后由策略完整控制身体。
                env.elastic_band_enabled = not controller.active
            env.sim_step()
            if publisher is not None:
                publisher.publish_qpos(env.mj_data.qpos)
            if counter % viewer_stride == 0:
                env.update_viewer()
            if counter % reward_stride == 0:
                env.update_reward()
            elapsed = time.monotonic() - started
            if elapsed < sim_dt:
                time.sleep(sim_dt - elapsed)
            counter += 1
    except KeyboardInterrupt:
        pass
    finally:
        if args.self_test_walk:
            print(
                "SONIC 前进自检结束："
                f"root_xy=({env.mj_data.qpos[0]:.3f}, {env.mj_data.qpos[1]:.3f}), "
                f"height={env.mj_data.qpos[2]:.3f}, "
                f"policy_ticks={controller.policy_ticks}, "
                f"max_abs_action={controller.max_abs_action:.3f}"
            )
        controller.close(env)
        if publisher is not None:
            publisher.close()
        env.close()


if __name__ == "__main__":
    main()
