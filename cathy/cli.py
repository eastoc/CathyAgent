"""CLI REPL 入口：python -m cathy 或 python main.py。"""

from __future__ import annotations

import argparse
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
from .context import ContextAssembler  # noqa: E402
from .hooks import HookEvent, HookManager, SESSION_START  # noqa: E402
from .llm import LLMClient  # noqa: E402
from .mcp import (  # noqa: E402
    HAS_FASTMCP,
    McpError,
    McpHub,
    McpToolPlugin,
    build_mcp_manifest,
    infer_mcp_client_roots_from_servers,
    normalize_root_uri,
)
from .plugins import PluginError, PluginRegistry  # noqa: E402
from .session.store import SessionStore  # noqa: E402
from .plugins.registry import ToolView  # noqa: E402
from .skills import (  # noqa: E402
    SkillsPlugin,
    build_skill_catalog,
    build_skills_manifest,
    discover_skills,
)
from .subagent import (  # noqa: E402
    PlannerExecutorSubagent,
    SubagentToolPlugin,
    build_subagent_tool_manifest,
)


BANNER = """\
==================================================
 CathyAgent · Phase 3.5 · Subagent + Skills + Hooks
 输入问题开始，输入 quit / exit / q 退出
=================================================="""

MVP_HOOK_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse"}


def _on_event(event: str, payload: dict) -> None:
    if event == "tool_call":
        args_preview = payload.get("args")
        print(f"\n[tool_call] {payload['name']} args={args_preview}")
    elif event == "tool_result":
        result = payload.get("result", "")
        if isinstance(result, str) and len(result) > 600:
            result = result[:600] + " …(已省略)"
        print(f"[tool_result] {payload['name']} →\n{result}")


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


def _hook_log(entry: dict) -> None:
    """HookManager 加载 / 执行错误时的轻量日志（stderr 风格输出到控制台）。"""
    print(f"[hook][{entry.get('phase', '?')}] {entry}")


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
        print("[mcp] enabled=true 但未配置 mcp_servers，跳过")
        return
    if not HAS_FASTMCP:
        print("[mcp] 未安装 fastmcp，跳过；如需启用请 `pip install fastmcp`")
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
        print(f"[mcp] 启动失败，跳过: {exc}")
        return

    try:
        manifest = build_mcp_manifest(hub, project_root=_PROJECT_ROOT)
    except PluginError as exc:
        print(f"[mcp] {exc}")
        hub.shutdown()
        return

    registry.register_internal_plugin(
        manifest, McpToolPlugin(hub), skip_initialize=True,
    )
    tool_names = sorted(t.outer_name for t in hub.list_tools())
    print(f"[mcp] connected servers={list(servers.keys())} tools={tool_names}")


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
        print(f"[hooks] 未知 HOOKS_PROFILE={profile!r}，回退为 mvp")
    slim = {k: v for k, v in hooks_cfg.items() if k in MVP_HOOK_EVENTS}
    return slim, "mvp"


def build_runtime(cfg: dict | None = None) -> tuple[Agent, SessionStore, HookManager]:
    cfg = cfg if cfg is not None else load_config()
    llm_conf = get_llm(cfg)
    provider = llm_conf.get("name") or get_llm_provider(cfg)

    if "${" in str(llm_conf.get("api_key", "")):
        sys.exit(
            f"LLM 供应商 {provider!r} 的 API Key 未注入。"
            f"请在 config/.env 中配置对应环境变量（见 config.yaml → LLM.{provider}.api_key）后重试。"
        )

    llm = LLMClient(
        api_key=llm_conf["api_key"],
        base_url=llm_conf["api_base"],
        model=llm_conf["model"],
        temperature=float(llm_conf.get("temperature", 0.7)),
        max_tokens=int(llm_conf.get("max_tokens") or 4096),
    )

    plugins_dirs = [
        _PROJECT_ROOT / "plugins" / "builtin",
        _PROJECT_ROOT / "plugins" / "community",
    ]
    registry = PluginRegistry(
        plugins_dirs=plugins_dirs,
        plugin_configs=_build_plugin_configs(cfg),
    )
    loaded = registry.discover_and_load()
    print(f"[plugins] loaded: {loaded}")

    # ---- Hooks（Phase 3.5 中间件） ----
    active_hooks_cfg, hooks_profile = _select_hooks_config(cfg)
    hooks = HookManager.from_config(
        active_hooks_cfg,
        project_root=_PROJECT_ROOT,
        on_log=_hook_log,
    )
    summary = hooks.summary()
    if summary:
        print(f"[hooks] profile={hooks_profile} active: {summary}")

    # ---- Skills（静态模板）：read_skill 工具 + system prompt 注入目录 ----
    skills = discover_skills([_PROJECT_ROOT / "skills"])
    skills_plugin = SkillsPlugin(skills=skills)
    registry.register_internal_plugin(build_skills_manifest(), skills_plugin)
    print(f"[skills] loaded: {sorted(s.name for s in skills)}")

    # ---- MCP（Phase 5：外部工具生态） ----
    # 注意：先于 subagent 注册，子 agent 内部也能用 mcp 工具。
    _maybe_register_mcp(cfg, registry)

    # ---- Subagent：planner_executor（LangGraph） ----
    # 子 agent 内部能用：除 planner_executor 自身以外的所有工具（含 read_skill）。
    subagent_tool_view = ToolView(registry, blocked={"planner_executor"})
    planner_executor = PlannerExecutorSubagent(llm=llm, tools=subagent_tool_view)
    registry.register_internal_plugin(
        build_subagent_tool_manifest(planner_executor),
        SubagentToolPlugin(planner_executor, hooks=hooks),
    )
    print("[subagents] exposed: planner_executor")

    skill_catalog = build_skill_catalog(skills)

    agent_cfg = cfg.get("AGENT") or {}
    session_cfg = cfg.get("SESSION") or {}
    assembler = ContextAssembler(
        project_root=Path.cwd(),
        skill_catalog=skill_catalog,
        extra=str(agent_cfg.get("extra_system") or "").strip(),
        token_budget=int(session_cfg.get("token_budget", 8000)),
        hooks=hooks,
    )

    store = SessionStore(_resolve_db_path(cfg))

    agent = Agent(
        llm=llm,
        tools=registry,
        assembler=assembler,
        store=store,
        config=AgentConfig(max_steps=int(agent_cfg.get("max_steps", 12))),
        on_event=_on_event,
        hooks=hooks,
        permission_cfg=dict(cfg.get("PERMISSION") or {}),
    )
    return agent, store, hooks


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="cathy", description="CathyAgent CLI")
    p.add_argument("--session", default=None, help="指定会话 ID 续聊；不传则新建")
    p.add_argument("--list-sessions", action="store_true", help="列出最近的会话并退出")
    return p.parse_args(argv)


def _print_session_list(store: SessionStore) -> None:
    rows = store.list_sessions()
    if not rows:
        print("(暂无会话)")
        return
    print(f"{'ID':10} {'更新时间':25} {'消息数':>5}  创建时间")
    for r in rows:
        print(f"{r['id']:10} {r['updated_at']:25} {r['msg_count']:>5}  {r['created_at']}")


def main() -> None:
    args = _parse_args()
    agent, store, hooks = build_runtime()

    if args.list_sessions:
        _print_session_list(store)
        store.close()
        return

    session = store.get_or_create(args.session)
    is_new = len(session.messages) == 0
    # 把会话上下文广播给支持 attach_session 的插件（shell_exec/file_ops 等）。
    agent.tools.attach_session(session.id)

    # === Hook: SessionStart（拿到 session 后立刻派发，可往 system prompt 注入上下文） ===
    if hooks.has_hooks_for(SESSION_START):
        decision = hooks.dispatch(
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

    try:
        while True:
            try:
                user_input = input("\n你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                return
            if user_input.lower() in {"quit", "exit", "q"}:
                print("再见！")
                return
            if not user_input:
                continue

            reply, _trace = agent.run(session, user_input)
            print(f"\n🤖 Cathy >\n{reply}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
