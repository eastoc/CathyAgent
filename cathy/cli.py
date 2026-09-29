"""CLI REPL 入口：python -m cathy 或 python main.py。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# 允许 `python cathy/cli.py` 这种直接运行方式：把项目根加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.config import get_llm, get_llm_provider, load_config  # noqa: E402

from .agent import Agent, AgentConfig  # noqa: E402
from .artifacts import LocalArtifactStore  # noqa: E402
from .context import ContextAssembler, ROBOT_HARNESS_SYSTEM_PROMPT  # noqa: E402
from .hooks import HookEvent, HookManager, SESSION_START  # noqa: E402
from .logger import configure_logging, get_logger  # noqa: E402
from .model_clients import build_model_client  # noqa: E402
from .mcp import (  # noqa: E402
    HAS_FASTMCP,
    McpError,
    McpHub,
    McpToolPlugin,
    build_mcp_manifest,
    infer_mcp_client_roots_from_servers,
    normalize_root_uri,
)
from .plugins import PluginError, PluginRegistry, build_tool_catalog  # noqa: E402
from .session.store import AsyncSessionStore, SessionStore  # noqa: E402
from .plugins.registry import ToolView  # noqa: E402
from .skills import (  # noqa: E402
    SkillsPlugin,
    build_skill_catalog,
    build_skills_manifest,
    discover_skills,
)
from .subagent import SubagentToolPlugin, build_subagent_tool_manifest  # noqa: E402
from .telemetry import RawRunExporter, RunJournal  # noqa: E402


BANNER = """\
==================================================
 CathyAgent · Phase 3.5 · Subagent + Skills + Hooks
 输入问题开始，输入 quit / exit / q 退出
=================================================="""

MVP_HOOK_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse"}
logger = get_logger(__name__)

GENERAL_RUNTIME_PROFILE = "general"
ROBOT_HARNESS_RUNTIME_PROFILE = "robot_harness"


def _on_event(event: str, payload: dict) -> None:
    if event == "tool_call":
        args_preview = payload.get("args")
        logger.info("[tool_call] %s args=%s", payload["name"], args_preview)
    elif event == "tool_result":
        result = payload.get("result", "")
        if isinstance(result, str) and len(result) > 600:
            result = result[:600] + " …(已省略)"
        logger.info("[tool_result] %s →\n%s", payload["name"], result)


def _build_plugin_configs(cfg: dict) -> dict[str, dict]:
    tavily_key = cfg.get("TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY", "")
    has_tavily = bool(tavily_key) and "${" not in str(tavily_key)
    sandbox_cfg = cfg.get("SANDBOX") or {}
    workspace_root = Path(sandbox_cfg.get("workspace_root") or "workspaces")
    if not workspace_root.is_absolute():
        workspace_root = _PROJECT_ROOT / workspace_root
    return {
        "web_search": {"api_key": tavily_key} if has_tavily else {},
        "file_ops": {
            "workspace_root": str(workspace_root),
        },
        "current_datetime": {},
        "shell_exec": {
            "backend": str(sandbox_cfg.get("backend") or "local_restricted"),
            "workspace_root": str(workspace_root),
            "default_timeout_sec": float(sandbox_cfg.get("default_timeout_sec", 10)),
            "max_timeout_sec": float(sandbox_cfg.get("max_timeout_sec", 30)),
            "max_output_bytes": int(sandbox_cfg.get("max_output_bytes", 65536)),
            "allow_network": bool((sandbox_cfg.get("network") or {}).get("allow", False)),
            "allowed_domains": list((sandbox_cfg.get("network") or {}).get("allowed_domains") or []),
        },
    }


def _resolve_db_path(cfg: dict) -> Path:
    session_cfg = cfg.get("SESSION") or {}
    raw = session_cfg.get("db_path") or "data/sessions.db"
    p = Path(raw)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p


def _resolve_artifacts_path(cfg: dict) -> Path:
    session_cfg = cfg.get("SESSION") or {}
    raw = session_cfg.get("artifacts_path") or "data/artifacts"
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def _hook_log(entry: dict) -> None:
    """HookManager 加载 / 执行错误时的轻量日志（stderr 风格输出到控制台）。"""
    logger.warning("[hook][%s] %s", entry.get("phase", "?"), entry)


def _resolve_mcp_client_roots(
    mcp_cfg: dict[str, Any],
    servers: dict[str, Any],
) -> list[str] | None:
    """FastMCP Client 的 roots：配置项 `MCP.roots` 优先，否则从 filesystem stdio 推断。

    所有 root 都会被规范化为 `file://...` URI，避免 FastMCP/pydantic 的 url_parsing 报错。
    """
    if "roots" in mcp_cfg:
        raw = mcp_cfg["roots"]
        if raw is None:
            return None
        if isinstance(raw, list):
            normalized = [normalize_root_uri(str(x)) for x in raw]
            return [r for r in normalized if r]
        return None
    inferred = infer_mcp_client_roots_from_servers(servers)
    return inferred if inferred else None


def _maybe_register_mcp(cfg: dict[str, Any], registry: PluginRegistry) -> None:
    """按 cfg.MCP 启动 McpHub 并注册 mcp 插件；任意失败都不应阻断主流程。"""
    mcp_cfg = cfg.get("MCP") or {}
    if not bool(mcp_cfg.get("enabled", False)):
        return
    servers = mcp_cfg.get("mcp_servers") or {}
    if not servers:
        logger.warning("[mcp] enabled=true 但未配置 mcp_servers，跳过")
        return
    if not HAS_FASTMCP:
        logger.warning("[mcp] 未安装 fastmcp，跳过；如需启用请 `pip install fastmcp`")
        return

    hub = McpHub(
        {"mcpServers": dict(servers)},
        connect_timeout=float(mcp_cfg.get("connect_timeout_sec", 60)),
        default_call_timeout=float(mcp_cfg.get("call_timeout_sec", 120)),
        roots=_resolve_mcp_client_roots(mcp_cfg, servers),
        server_names=list(servers.keys()),
    )
    try:
        hub.start()
    except McpError as exc:
        logger.warning("[mcp] 启动失败，跳过: %s", exc)
        return

    try:
        manifest = build_mcp_manifest(hub, project_root=_PROJECT_ROOT)
    except PluginError as exc:
        logger.warning("[mcp] %s", exc)
        hub.shutdown()
        return

    registry.register_internal_plugin(
        manifest, McpToolPlugin(hub), skip_initialize=True,
    )
    tool_names = sorted(t.outer_name for t in hub.list_tools())
    logger.info("[mcp] connected servers=%s tools=%s", list(servers.keys()), tool_names)


def _select_hooks_config(cfg: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """按 HOOKS_PROFILE 选择生效 hooks。

    - mvp（默认）：只启用 UserPromptSubmit / PreToolUse / PostToolUse
    - full：启用 HOOKS 中全部事件
    """
    hooks_cfg = dict(cfg.get("HOOKS") or {})
    profile = str(cfg.get("HOOKS_PROFILE") or "mvp").strip().lower()
    if profile == "full":
        return hooks_cfg, "full"
    if profile not in {"", "mvp"}:
        logger.warning("[hooks] 未知 HOOKS_PROFILE=%r，回退为 mvp", profile)
    slim = {k: v for k, v in hooks_cfg.items() if k in MVP_HOOK_EVENTS}
    return slim, "mvp"


def _runtime_profile(cfg: dict[str, Any]) -> str:
    profile = str(cfg.get("RUNTIME_PROFILE") or GENERAL_RUNTIME_PROFILE).strip().lower()
    if profile not in {GENERAL_RUNTIME_PROFILE, ROBOT_HARNESS_RUNTIME_PROFILE}:
        raise ValueError(
            f"未知 RUNTIME_PROFILE={profile!r}；"
            f"可选值: {GENERAL_RUNTIME_PROFILE}, {ROBOT_HARNESS_RUNTIME_PROFILE}"
        )
    return profile


def build_runtime(
    cfg: dict | None = None,
    *,
    store: SessionStore | AsyncSessionStore | None = None,
) -> tuple[Agent, SessionStore | AsyncSessionStore, HookManager]:
    cfg = cfg if cfg is not None else load_config()
    runtime_profile = _runtime_profile(cfg)
    robot_harness = runtime_profile == ROBOT_HARNESS_RUNTIME_PROFILE
    llm_conf = get_llm(cfg)
    provider = llm_conf.get("name") or get_llm_provider(cfg)

    if "${" in str(llm_conf.get("api_key", "")):
        sys.exit(
            f"LLM 供应商 {provider!r} 的 API Key 未注入。"
            f"请在 config/.env 中配置对应环境变量（见 config.yaml → LLM.{provider}.api_key）后重试。"
        )

    llm = build_model_client(llm_conf)

    plugins_dirs = (
        []
        if robot_harness
        else [
            _PROJECT_ROOT / "plugins" / "builtin",
            _PROJECT_ROOT / "plugins" / "community",
        ]
    )
    registry = PluginRegistry(
        plugins_dirs=plugins_dirs,
        plugin_configs=_build_plugin_configs(cfg),
    )
    loaded = registry.discover_and_load()
    logger.info("[runtime] profile=%s plugins=%s", runtime_profile, loaded)

    # ---- Hooks（Phase 3.5 中间件） ----
    active_hooks_cfg, hooks_profile = _select_hooks_config(cfg)
    hooks = HookManager.from_config(
        active_hooks_cfg,
        project_root=_PROJECT_ROOT,
        on_log=_hook_log,
    )
    summary = hooks.summary()
    if summary:
        logger.info("[hooks] profile=%s active: %s", hooks_profile, summary)

    skills = []
    if not robot_harness:
        # 离线 RunJournal 导出不需要加载 LangGraph；Subagent 只在通用运行时导入。
        from subagents.planner_executor import PlannerExecutorSubagent
        from subagents.search_agent import SearchAgent

        # ---- Skills（静态模板）：read_skill 工具 + system prompt 注入目录 ----
        skills = discover_skills([_PROJECT_ROOT / "skills"])
        skills_plugin = SkillsPlugin(skills=skills)
        registry.register_internal_plugin(build_skills_manifest(), skills_plugin)
        logger.info("[skills] loaded: %s", sorted(s.name for s in skills))

        # ---- MCP（Phase 5：外部工具生态） ----
        # 注意：先于 subagent 注册，子 agent 内部也能用 mcp 工具。
        _maybe_register_mcp(cfg, registry)

        # ---- SearchAgent：封装 web_search，父 agent 不直接暴露裸 web_search。 ----
        if registry.get_tool_descriptor("web_search") is not None:
            search_agent = SearchAgent(
                llm=llm,
                tools=ToolView(registry, allowed={"web_search"}),
            )
            registry.register_internal_plugin(
                build_subagent_tool_manifest(search_agent),
                SubagentToolPlugin(search_agent, hooks=hooks),
            )
            logger.info("[subagents] exposed: search_agent")
        else:
            logger.warning("[subagents] web_search 未加载，跳过 search_agent")

        # ---- Subagent：planner_executor（LangGraph） ----
        # 子 agent 内部能用：除 planner_executor 自身和裸 web_search 以外的所有工具。
        subagent_tool_view = ToolView(
            registry,
            blocked={"planner_executor", "web_search"},
        )
        planner_executor = PlannerExecutorSubagent(llm=llm, tools=subagent_tool_view)
        registry.register_internal_plugin(
            build_subagent_tool_manifest(planner_executor),
            SubagentToolPlugin(planner_executor, hooks=hooks),
        )
        logger.info("[subagents] exposed: planner_executor")

        exposed_tools = ToolView(registry, blocked={"web_search"})
    else:
        # 双保险：即使未来 registry 获得内部工具，robot harness 仍不向模型暴露。
        exposed_tools = ToolView(registry, allowed=set())

    tool_catalog = build_tool_catalog(exposed_tools.list_tools())
    skill_catalog = build_skill_catalog(skills)

    agent_cfg = cfg.get("AGENT") or {}
    session_cfg = cfg.get("SESSION") or {}
    assembler = ContextAssembler(
        project_root=None if robot_harness else Path.cwd(),
        system_override=(ROBOT_HARNESS_SYSTEM_PROMPT if robot_harness else None),
        tool_catalog=tool_catalog,
        skill_catalog=skill_catalog,
        extra=str(agent_cfg.get("extra_system") or "").strip(),
        token_budget=int(session_cfg.get("token_budget", 8000)),
        hooks=hooks,
    )

    store = store or SessionStore(_resolve_db_path(cfg))
    artifact_store = LocalArtifactStore(_resolve_artifacts_path(cfg))

    agent = Agent(
        llm=llm,
        tools=exposed_tools,
        assembler=assembler,
        store=store,
        config=AgentConfig(max_steps=int(agent_cfg.get("max_steps", 12))),
        on_event=_on_event,
        hooks=hooks,
        permission_cfg=dict(cfg.get("PERMISSION") or {}),
        attachment_resolver=artifact_store,
        event_sink=RunJournal(store),
    )
    return agent, store, hooks


async def build_async_runtime(
    cfg: dict | None = None,
) -> tuple[Agent, AsyncSessionStore, HookManager]:
    """构建由单一事件循环持有的 CLI Runtime。"""
    resolved_cfg = cfg if cfg is not None else load_config()
    store = await AsyncSessionStore.open(_resolve_db_path(resolved_cfg))
    try:
        agent, built_store, hooks = build_runtime(resolved_cfg, store=store)
    except BaseException:
        await store.aclose()
        raise
    assert built_store is store
    return agent, store, hooks


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="cathy", description="CathyAgent CLI")
    p.add_argument("--session", default=None, help="指定会话 ID 续聊；不传则新建")
    p.add_argument("--list-sessions", action="store_true", help="列出最近的会话并退出")
    p.add_argument(
        "--export-runs",
        metavar="PATH",
        help="把 RunJournal 无损导出为 cathy.raw-run.v1 JSONL",
    )
    return p.parse_args(argv)


def _print_session_list(store: SessionStore) -> None:
    rows = store.list_sessions()
    if not rows:
        print("(暂无会话)")
        return
    print(f"{'ID':10} {'更新时间':25} {'消息数':>5}  创建时间")
    for r in rows:
        print(f"{r['id']:10} {r['updated_at']:25} {r['msg_count']:>5}  {r['created_at']}")


def _print_session_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("(暂无会话)")
        return
    print(f"{'ID':10} {'更新时间':25} {'消息数':>5}  创建时间")
    for row in rows:
        print(
            f"{row['id']:10} {row['updated_at']:25} "
            f"{row['msg_count']:>5}  {row['created_at']}"
        )


async def _ainput(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, input, prompt)


async def _print_agent_stream(agent: Agent, session, user_input: str) -> None:
    """消费 AgentEvent，并在 pending tool 完成后继续同一轮响应。"""
    stream = agent.astream(session, user_input)
    printed_header = False
    printed_text = False

    while True:
        terminal_type = ""
        terminal_content = ""
        pending_task_ids: tuple[str, ...] = ()

        async for event in stream:
            if event.type == "model_text_delta" and event.payload.get("text"):
                if not printed_header:
                    print("\n🤖 Cathy >")
                    printed_header = True
                print(str(event.payload["text"]), end="", flush=True)
                printed_text = True
            elif event.type in {
                "run_completed",
                "run_pending",
                "run_failed",
                "run_cancelled",
            }:
                terminal_type = event.type
                terminal_content = str(event.payload.get("content") or "")
                pending_task_ids = tuple(event.payload.get("task_ids") or ())

        if terminal_type == "run_pending" and pending_task_ids:
            stream = agent.astream_task(session, pending_task_ids[0])
            continue

        if terminal_content and not printed_text:
            if not printed_header:
                print("\n🤖 Cathy >")
                printed_header = True
            print(terminal_content, end="")
        if printed_header:
            print()
        return


async def _aclose_runtime(agent: Agent, store: AsyncSessionStore) -> None:
    """按后台任务、插件、模型、Store 的顺序释放 Runtime。"""
    loop = asyncio.get_running_loop()
    try:
        await agent.task_registry.shutdown(cancel=True)
    except Exception as exc:
        logger.warning("[shutdown][tasks] %s", exc)

    try:
        if agent.event_sink is not None:
            await agent.event_sink.aflush()
    except Exception as exc:
        logger.warning("[shutdown][journal] %s", exc)

    try:
        await loop.run_in_executor(None, agent.tools.shutdown)
    except Exception as exc:
        logger.warning("[shutdown][tools] %s", exc)

    try:
        model_aclose = getattr(agent.llm, "aclose", None)
        if callable(model_aclose):
            await model_aclose()
        model_close = getattr(agent.llm, "close", None)
        if callable(model_close):
            await loop.run_in_executor(None, model_close)
    except Exception as exc:
        logger.warning("[shutdown][model] %s", exc)
    finally:
        await store.aclose()


async def amain(argv: list[str] | None = None) -> None:
    configure_logging()
    args = _parse_args(argv)

    if args.export_runs:
        cfg = load_config()
        export_store = await AsyncSessionStore.open(_resolve_db_path(cfg))
        try:
            count = await RawRunExporter(export_store).aexport_jsonl(args.export_runs)
            print(f"已导出 {count} 条 root run 到 {args.export_runs}")
        finally:
            await export_store.aclose()
        return

    agent, store, hooks = await build_async_runtime()

    try:
        if args.list_sessions:
            _print_session_rows(await store.alist_sessions())
            return

        session = await store.aget_or_create(args.session)
        is_new = len(session.messages) == 0
        # 把会话上下文广播给支持 attach_session 的插件（shell_exec/file_ops 等）。
        agent.tools.attach_session(session.id)

        # === Hook: SessionStart ===
        if hooks.has_hooks_for(SESSION_START):
            decision = await hooks.adispatch(
                HookEvent(
                    type=SESSION_START,
                    session_id=session.id,
                    payload={"new": is_new, "msg_count": len(session.messages)},
                )
            )
            if decision.inject_context:
                agent.assembler.append_system_layer(
                    f"[hook:SessionStart]\n{decision.inject_context}"
                )

        print(BANNER)
        descriptors = agent.tools.list_tools()
        print(
            f"模型: {agent.llm.model}  |  "
            f"会话: {session.id}（{len(session.messages)} 条历史）  |  "
            f"工具: {[d.name for d in descriptors]}"
        )
        print("提示: `python main.py --session", session.id, "` 可在新进程中续聊\n")

        while True:
            try:
                user_input = (await _ainput("\n你 > ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                return
            if user_input.lower() in {"quit", "exit", "q"}:
                print("再见！")
                return
            if not user_input:
                continue

            try:
                await _print_agent_stream(agent, session, user_input)
            except asyncio.CancelledError:
                print("\n[当前任务已取消]")
                raise
            except Exception as exc:
                logger.exception("[cli] Agent 执行失败")
                print(f"\n[执行失败] {type(exc).__name__}: {exc}")
    finally:
        await _aclose_runtime(agent, store)


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("\n再见！")


if __name__ == "__main__":
    main()
