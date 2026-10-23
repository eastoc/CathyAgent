"""MuJoCo 模块的诊断、回归和交互式 Agent 入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse

from cathy.logger import configure_logging

from .agent_runtime import OUTPUT_ROOT, build_simulation_agent
from .backend import MujocoBackend
from .config import DEFAULT_ENVIRONMENT, DEFAULT_WMA_ENVIRONMENT, load_environment_config
from .controller import Z1Controller
from .evaluation import Z1PickPlaceEvaluator
from .factory import create_runtime
from .viewer import MujocoViewer


_MJPYTHON_REEXEC_ENV = "CATHY_G1_VLA_MJPYTHON_REEXEC"


class _ManagedSonicBackend:
    """管理 g1_vla 自动启动的无窗口 SONIC 仿真进程。"""

    def __init__(self, process: subprocess.Popen[bytes], log_file, log_path: Path) -> None:
        self.process = process
        self.log_file = log_file
        self.log_path = log_path

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_file.close()


def _tcp_listener_available(endpoint: str, *, timeout: float = 0.15) -> bool:
    """检查本机 ZMQ TCP endpoint 是否已有发布进程监听。"""
    parsed = urlparse(endpoint)
    if parsed.scheme != "tcp" or parsed.port is None:
        return False
    host = parsed.hostname or "127.0.0.1"
    if host in {"*", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    try:
        with socket.create_connection((host, parsed.port), timeout=timeout):
            return True
    except OSError:
        return False


def _sonic_state_available(
    endpoint: str,
    *,
    topic: str,
    timeout: float = 1.5,
) -> bool:
    """端口监听不代表 MuJoCo 主循环存活；必须收到一帧新鲜全身状态。"""
    from .g1.sonic.pose import ZmqNavigationPoseProvider

    provider = ZmqNavigationPoseProvider(
        endpoint,
        topic=topic,
        first_sample_timeout_seconds=timeout,
    )
    try:
        sample = provider()
        return time.monotonic() - sample.monotonic_time <= timeout
    except RuntimeError:
        return False
    finally:
        provider.close()


def _tail_text(path: Path, *, limit: int = 4000) -> str:
    try:
        value = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return value[-limit:]


def _start_sonic_backend(environment: str) -> _ManagedSonicBackend:
    """启动全新的无窗口导航仿真，并等待全身状态发布端口就绪。"""
    from .g1.config import load_g1_navigation_config

    navigation = load_g1_navigation_config(environment)
    pose_endpoint = navigation.sonic.pose_endpoint
    if _tcp_listener_available(pose_endpoint):
        if _sonic_state_available(
            pose_endpoint, topic=navigation.sonic.pose_topic
        ):
            raise RuntimeError(
                f"检测到已有 SONIC 位姿后端 {pose_endpoint}。默认启动必须创建新的"
                "仿真，不能把上次任务的最终位姿当成新任务起点；请停止旧 "
                "run_sonic_sim 后重试。若确实要续接该后端，请显式添加 "
                "--external-sonic"
            )
        raise RuntimeError(
            f"检测到 {pose_endpoint} 正在监听，但 1.5s 内没有新的 MuJoCo "
            "全身状态；请停止旧 run_sonic_sim 进程后重启，不能复用僵死后端"
        )

    OUTPUT_ROOT.joinpath("logs").mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_ROOT / "logs" / "g1-sonic-backend.log"
    log_file = log_path.open("wb")
    python = Path(sys.executable)
    sibling_python = python.with_name("python")
    if sibling_python.is_file():
        python = sibling_python
    command = [
        str(python), "-u", "-m", "simulation.mujoco.g1.run_sonic_sim",
        "--no-viewer", "--environment", environment,
    ]
    print(
        "正在自动启动无窗口 SONIC 导航仿真；"
        f"起点=({navigation.initial_pose_xy_yaw[0]:.3f}, "
        f"{navigation.initial_pose_xy_yaw[1]:.3f}, "
        f"yaw={navigation.initial_pose_xy_yaw[2]:.3f})；"
        f"日志: {log_path}",
        flush=True,
    )
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parents[2],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    managed = _ManagedSonicBackend(process, log_file, log_path)
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = _tail_text(log_path)
            managed.close()
            raise RuntimeError(
                "SONIC 导航仿真启动失败。"
                f"日志: {log_path}\n{detail}"
            )
        if _tcp_listener_available(pose_endpoint) and _sonic_state_available(
            pose_endpoint,
            topic=navigation.sonic.pose_topic,
            timeout=1.0,
        ):
            print(f"SONIC 全身状态已就绪: {pose_endpoint}", flush=True)
            return managed
        time.sleep(0.1)

    detail = _tail_text(log_path)
    managed.close()
    raise RuntimeError(
        f"SONIC 导航仿真在 45s 内未就绪: {pose_endpoint}。"
        f"日志: {log_path}\n{detail}"
    )


def _ensure_macos_g1_viewer_runtime(args: argparse.Namespace) -> None:
    """macOS 的被动 Viewer 必须由 mjpython 在主线程启动。"""
    if (
        sys.platform != "darwin"
        or not args.agent
        or args.control_mode != "g1_vla"
        or args.no_viewer
        or os.environ.get(_MJPYTHON_REEXEC_ENV) == "1"
    ):
        return
    mjpython = shutil.which("mjpython")
    if mjpython is None:
        raise RuntimeError(
            "g1_vla 默认打开 Dex3 MuJoCo Viewer，但当前环境找不到 mjpython；"
            "请安装 mujoco，或传 --no-viewer 进行无窗口运行"
        )
    env = os.environ.copy()
    env[_MJPYTHON_REEXEC_ENV] = "1"
    print(f"macOS Viewer 需要 mjpython，正在自动切换到: {mjpython}", flush=True)
    os.execve(
        mjpython,
        [mjpython, "-m", "simulation.mujoco", *sys.argv[1:]],
        env,
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _preview(value: Any, *, limit: int = 8000) -> str:
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[:limit]}\n... [结果过长，已截断 {len(rendered) - limit} 字符]"


async def _stream_agent_turn(agent, session, user_input: str) -> None:
    """实时显示接口返回的推理/摘要、正文及工具执行结果。"""
    stream = agent.astream(session, user_input)
    response_parts: list[str] = []
    text_stream_open = False
    reasoning_open = False

    while True:
        terminal_type = ""
        terminal_content = ""
        pending_task_ids: tuple[str, ...] = ()

        async for event in stream:
            payload = event.payload
            if event.type == "model_started":
                response_parts = []
                text_stream_open = False
                reasoning_open = False
                print(
                    f"\n⏳ 模型请求，第 {payload.get('step', 0) + 1} 轮",
                    flush=True,
                )
            elif event.type == "model_reasoning_delta" and payload.get("text"):
                text_stream_open = False
                if not reasoning_open:
                    print("\n💭 推理/摘要 > ", end="", flush=True)
                    reasoning_open = True
                print(str(payload["text"]), end="", flush=True)
            elif event.type == "model_text_delta" and payload.get("text"):
                reasoning_open = False
                if not text_stream_open:
                    print("\n🤖 Cathy > ", end="", flush=True)
                    text_stream_open = True
                delta = str(payload["text"])
                response_parts.append(delta)
                print(delta, end="", flush=True)
            elif event.type == "tool_started":
                text_stream_open = False
                reasoning_open = False
                print(
                    f"\n\n🔧 调用 {payload.get('name')}"
                    f"\n{_preview(payload.get('args') or {})}",
                    flush=True,
                )
            elif event.type == "tool_completed":
                text_stream_open = False
                reasoning_open = False
                status = str(payload.get("status") or "unknown")
                latency = int(payload.get("latency_ms") or 0)
                print(
                    f"\n✅ 工具结果 {payload.get('name')} "
                    f"status={status} latency={latency}ms"
                    f"\n{_preview(payload.get('result') or '')}",
                    flush=True,
                )
                artifacts = payload.get("artifacts") or []
                if artifacts:
                    print(f"🖼️ artifacts={artifacts}", flush=True)
            elif event.type == "tool_task_queued":
                text_stream_open = False
                reasoning_open = False
                print(
                    f"\n⏳ 后台工具 {payload.get('name')} "
                    f"task_id={payload.get('task_id')}",
                    flush=True,
                )
            elif event.type in {
                "run_completed",
                "run_pending",
                "run_failed",
                "run_cancelled",
            }:
                terminal_type = event.type
                terminal_content = str(payload.get("content") or "")
                pending_task_ids = tuple(payload.get("task_ids") or ())

        if terminal_type == "run_pending" and pending_task_ids:
            print(f"\n⏳ 等待后台任务 {pending_task_ids[0]}", flush=True)
            text_stream_open = False
            reasoning_open = False
            stream = agent.astream_task(session, pending_task_ids[0])
            continue

        streamed_text = "".join(response_parts).strip()
        if terminal_content and terminal_content.strip() != streamed_text:
            print(f"\n\n🤖 Cathy > {terminal_content}", flush=True)
        elif response_parts or reasoning_open:
            print(flush=True)
        if terminal_type in {"run_failed", "run_cancelled"}:
            print(f"[{terminal_type}]", flush=True)
        return


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CathyAgent MuJoCo runtime")
    parser.add_argument("--environment", default=None)
    parser.add_argument(
        "--control-mode",
        choices=["local_direct", "g1_navigation", "g1_vla"],
        default="local_direct",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--describe", action="store_true", help="打印环境能力")
    mode.add_argument("--observe", action="store_true", help="打印无图像状态观测")
    mode.add_argument(
        "--scripted",
        choices=["red", "blue", "green", "all"],
        help="运行特权确定性物理回归（不属于 Agent 工具）",
    )
    mode.add_argument("--agent", action="store_true", help="启动多轮 Agent REPL")
    parser.add_argument("--session", default=None, help="续接已有 Agent 会话")
    parser.add_argument(
        "--no-viewer",
        action="store_true",
        help="不打开 MuJoCo 图形窗口（用于 CI/headless）",
    )
    parser.add_argument(
        "--external-sonic",
        action="store_true",
        help="g1_vla 使用外部已启动的 SONIC 导航仿真，不自动启动后台进程",
    )
    args = parser.parse_args()
    if args.environment is None:
        if args.control_mode in {"g1_navigation", "g1_vla"}:
            from .g1.config import DEFAULT_G1_ENVIRONMENT, DEFAULT_G1_SONIC_ENVIRONMENT

            args.environment = (
                DEFAULT_G1_SONIC_ENVIRONMENT
                if args.control_mode == "g1_vla"
                else DEFAULT_G1_ENVIRONMENT
            )
        elif args.agent:
            args.environment = DEFAULT_WMA_ENVIRONMENT
        else:
            args.environment = DEFAULT_ENVIRONMENT
    if args.control_mode in {"g1_navigation", "g1_vla"} and args.scripted:
        parser.error("--scripted 是 Z1 特权回归入口，不用于 G1 导航")
    return args


def _run_diagnostic(args: argparse.Namespace) -> None:
    if args.control_mode in {"g1_navigation", "g1_vla"}:
        from .g1.config import load_g1_navigation_config
        from .g1.service import G1NavigationService

        service = G1NavigationService(load_g1_navigation_config(args.environment))
        try:
            if args.observe:
                print(_json(service.get_map().metadata))
            else:
                capabilities = ["get_map", "navigate"]
                if args.control_mode == "g1_vla":
                    capabilities.extend([
                        "g1_describe", "g1_observe", "g1_pick", "g1_place",
                        "g1_release", "g1_stow", "g1_stop",
                    ])
                print(
                    _json(
                        {
                            "environment_id": service.config.environment_id,
                            "capabilities": capabilities,
                            "frame": service.context.frame,
                            "map_version": service.context.map_version,
                            "inflation_radius_m": service.context.inflation_radius_m,
                            "execution_enabled": service.config.execution_enabled,
                        }
                    )
                )
        finally:
            service.close()
        return
    if args.scripted:
        config = load_environment_config(args.environment)
        backend = MujocoBackend(config)
        viewer_config = config.runtime.viewer
        if args.no_viewer:
            viewer_config = type(viewer_config)(
                enabled=False,
                show_left_ui=viewer_config.show_left_ui,
                show_right_ui=viewer_config.show_right_ui,
            )
        viewer = MujocoViewer(backend, viewer_config)
        try:
            viewer.open()
            backend.set_state_lock(viewer.lock)
            backend.set_sync_callback(viewer.sync)
            evaluator = Z1PickPlaceEvaluator(
                backend,
                Z1Controller(backend, config.controller),
            )
            colors = (
                evaluator.COLORS if args.scripted == "all" else (args.scripted,)
            )
            print(_json(evaluator.run_sequence(colors)))
        finally:
            backend.set_sync_callback(None)
            backend.set_state_lock(None)
            viewer.close()
            backend.close()
        return

    runtime = create_runtime(
        args.environment,
        viewer_enabled=False if args.no_viewer else None,
    )
    try:
        operation = "observe" if args.observe else "describe"
        params = {"render": False} if operation == "observe" else {}
        value = runtime.call(operation, params)
        print(_json(value.state if operation == "observe" else value))
    finally:
        runtime.close()


async def _run_agent(args: argparse.Namespace) -> None:
    viewer_enabled = False if args.no_viewer else None
    if args.control_mode == "g1_vla":
        viewer_enabled = not args.no_viewer
    sonic_backend = None
    try:
        if args.control_mode == "g1_vla" and not args.external_sonic:
            sonic_backend = await asyncio.to_thread(
                _start_sonic_backend, args.environment
            )
        runtime = await build_simulation_agent(
            environment=args.environment,
            viewer_enabled=viewer_enabled,
            control_mode=args.control_mode,
        )
        try:
            session = await runtime.store.aget_or_create(args.session)
            runtime.agent.tools.attach_session(session.id)
            task_kind = {
                "g1_navigation": "G1 导航",
                "g1_vla": "G1 导航与 NVIDIA GN1x Arena manipulation",
            }.get(args.control_mode, "manipulation")
            print(
                f"CathyAgent MuJoCo · 输入 {task_kind} 指令；"
                "输入 quit / exit / q 退出\n"
                f"session={session.id} tools="
                f"{[item.name for item in runtime.agent.tools.list_tools()]}"
            )
            while True:
                try:
                    text = (await asyncio.to_thread(input, "\n你 > ")).strip()
                except (EOFError, KeyboardInterrupt):
                    return
                if text.lower() in {"quit", "exit", "q"}:
                    return
                if not text:
                    continue
                await _stream_agent_turn(runtime.agent, session, text)
        finally:
            await runtime.close()
    finally:
        if sonic_backend is not None:
            await asyncio.to_thread(sonic_backend.close)


def main() -> None:
    args = _parse_args()
    _ensure_macos_g1_viewer_runtime(args)
    configure_logging(log_dir=OUTPUT_ROOT / "logs", console=True)
    if args.agent:
        asyncio.run(_run_agent(args))
    else:
        _run_diagnostic(args)


if __name__ == "__main__":
    main()
