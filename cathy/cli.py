"""CLI REPL 入口：python -m cathy 或 python main.py。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 允许 `python cathy/cli.py` 这种直接运行方式：把项目根加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.config import get_llm, load_config  # noqa: E402

from .agent import Agent, AgentConfig  # noqa: E402
from .context import ContextAssembler  # noqa: E402
from .llm import LLMClient  # noqa: E402
from .plugins import PluginRegistry  # noqa: E402
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
 CathyAgent · Phase 3 · Subagent + Skills
 输入问题开始，输入 quit / exit / q 退出
=================================================="""


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
    return {
        "web_search": {"api_key": tavily_key} if has_tavily else {},
        "file_ops": {"root": str(Path.cwd())},
        "current_datetime": {},
    }


def _resolve_db_path(cfg: dict) -> Path:
    session_cfg = cfg.get("SESSION") or {}
    raw = session_cfg.get("db_path") or "data/sessions.db"
    p = Path(raw)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p


def build_runtime(cfg: dict | None = None) -> tuple[Agent, SessionStore]:
    cfg = cfg if cfg is not None else load_config()
    llm_conf = get_llm(cfg, name="qwen")

    if "${" in str(llm_conf.get("api_key", "")):
        sys.exit(
            "QWEN_API_KEY 未注入。请在 config/.env 中设置 QWEN_API_KEY=... 后重试。"
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

    # ---- Skills（静态模板）：read_skill 工具 + system prompt 注入目录 ----
    skills = discover_skills([_PROJECT_ROOT / "skills"])
    skills_plugin = SkillsPlugin(skills=skills)
    registry.register_internal_plugin(build_skills_manifest(), skills_plugin)
    print(f"[skills] loaded: {sorted(s.name for s in skills)}")

    # ---- Subagent：planner_executor（LangGraph） ----
    # 子 agent 内部能用：除 planner_executor 自身以外的所有工具（含 read_skill）。
    subagent_tool_view = ToolView(registry, blocked={"planner_executor"})
    planner_executor = PlannerExecutorSubagent(llm=llm, tools=subagent_tool_view)
    registry.register_internal_plugin(
        build_subagent_tool_manifest(planner_executor),
        SubagentToolPlugin(planner_executor),
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
    )

    store = SessionStore(_resolve_db_path(cfg))

    agent = Agent(
        llm=llm,
        tools=registry,
        assembler=assembler,
        store=store,
        config=AgentConfig(max_steps=int(agent_cfg.get("max_steps", 12))),
        on_event=_on_event,
    )
    return agent, store


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
    agent, store = build_runtime()

    if args.list_sessions:
        _print_session_list(store)
        store.close()
        return

    session = store.get_or_create(args.session)

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
