"""本地 debug / 开发入口; CLI REPL 入口：python -m cathy 或 python main.py。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 允许 `python cathy/cli.py` 这种直接运行方式：把项目根加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.config import get_llm, load_config  # noqa: E402

from .agent import Agent, AgentConfig  # noqa: E402
from .context import build_system_prompt  # noqa: E402
from .llm import LLMClient  # noqa: E402
from .tools.base import ToolRegistry  # noqa: E402
from .tools.current_datetime import CurrentDatetimeTool  # noqa: E402
from .tools.read_file import ReadFileTool  # noqa: E402
from .tools.web_search import WebSearchTool  # noqa: E402


BANNER = """\
==================================================
 CathyAgent · Phase 0 · Single-loop ReAct
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


def build_agent() -> Agent:
    cfg = load_config()
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

    registry = ToolRegistry()

    tavily_key = cfg.get("TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY", "")
    if tavily_key and "${" not in str(tavily_key):
        registry.register(WebSearchTool(api_key=tavily_key))
    else:
        print("[warn] 未配置 TAVILY_API_KEY，web_search 工具不会注册。")

    registry.register(ReadFileTool(root=Path.cwd()))
    registry.register(CurrentDatetimeTool())

    agent_cfg = cfg.get("AGENT") or {}
    system_prompt = build_system_prompt(
        project_root=Path.cwd(),
        extra=str(agent_cfg.get("extra_system") or "").strip(),
    )
    return Agent(
        llm=llm,
        tools=registry,
        config=AgentConfig(
            system_prompt=system_prompt,
            max_steps=int(agent_cfg.get("max_steps", 12)),
        ),
        on_event=_on_event,
    )


def main() -> None:
    agent = build_agent()
    print(BANNER)
    print(
        f"模型: {agent.llm.model}  |  "
        f"工具: {[t.name for t in agent.tools.list_tools()]}"
    )

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

        reply, _trace = agent.run(user_input)
        print(f"\n🤖 Cathy >\n{reply}")


if __name__ == "__main__":
    main()
