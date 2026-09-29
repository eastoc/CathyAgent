"""MuJoCo 模块的诊断、回归和交互式 Agent 入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from .agent_runtime import build_simulation_agent
from .backend import MujocoBackend
from .config import DEFAULT_ENVIRONMENT, load_environment_config
from .controller import Z1Controller
from .evaluation import Z1PickPlaceEvaluator
from .factory import create_runtime
from .viewer import MujocoViewer


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
    """实时显示模型文本、工具调用和结构化工具结果。"""
    stream = agent.astream(session, user_input)
    response_parts: list[str] = []
    text_stream_open = False

    while True:
        terminal_type = ""
        terminal_content = ""
        pending_task_ids: tuple[str, ...] = ()

        async for event in stream:
            payload = event.payload
            if event.type == "model_started":
                response_parts = []
                text_stream_open = False
            elif event.type == "model_text_delta" and payload.get("text"):
                if not text_stream_open:
                    print("\n🤖 Cathy > ", end="", flush=True)
                    text_stream_open = True
                delta = str(payload["text"])
                response_parts.append(delta)
                print(delta, end="", flush=True)
            elif event.type == "tool_started":
                text_stream_open = False
                print(
                    f"\n\n🔧 调用 {payload.get('name')}"
                    f"\n{_preview(payload.get('args') or {})}",
                    flush=True,
                )
            elif event.type == "tool_completed":
                text_stream_open = False
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
            stream = agent.astream_task(session, pending_task_ids[0])
            continue

        streamed_text = "".join(response_parts).strip()
        if terminal_content and terminal_content.strip() != streamed_text:
            print(f"\n\n🤖 Cathy > {terminal_content}", flush=True)
        elif response_parts:
            print(flush=True)
        if terminal_type in {"run_failed", "run_cancelled"}:
            print(f"[{terminal_type}]", flush=True)
        return


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CathyAgent MuJoCo Z1 runtime")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
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
    return parser.parse_args()


def _run_diagnostic(args: argparse.Namespace) -> None:
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
    runtime = await build_simulation_agent(
        environment=args.environment,
        viewer_enabled=False if args.no_viewer else None,
    )
    try:
        session = await runtime.store.aget_or_create(args.session)
        runtime.agent.tools.attach_session(session.id)
        print(
            "CathyAgent MuJoCo · 输入 manipulation 指令；"
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


def main() -> None:
    args = _parse_args()
    if args.agent:
        asyncio.run(_run_agent(args))
    else:
        _run_diagnostic(args)


if __name__ == "__main__":
    main()
