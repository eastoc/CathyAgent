"""手动烟测（联网，需 QWEN_API_KEY / TAVILY_API_KEY）。

不进 unittest 套件；直接 `python tests/manual_smoke.py` 运行。
专门用来分别打中 Phase 3 的各项能力：
  case 1  单步 ReAct（不应触发 subagent / read_skill）
  case 2  Skill 触发（应 read_skill + 按格式输出）
  case 3  复杂任务（应触发 planner_executor）
  case 4  会话续聊（验证 SQLite 持久化）

每条 case 的输出形如：
  [tool_call] ...    （主 agent 调的工具，子 agent 内部不打印——隔离）
  [tool_result] ...
  REPLY: ...
  STEPS: [tool_call, tool_result, ..., final]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cathy.cli import build_runtime  # noqa: E402


def _names_in_steps(steps: list[dict]) -> list[str]:
    return [s["name"] for s in steps if s.get("type") == "tool_call"]


def main() -> None:
    print("=" * 60)
    print("  Layer 3 · Manual Smoke (live LLM)")
    print("=" * 60)

    agent, store = build_runtime()
    session = store.get_or_create("smoke-phase3")

    cases = [
        ("单步 ReAct", "现在几点"),
        ("Skill 触发", "用 summarize skill 把项目根的 README.md 总结一下"),
        ("复杂任务（应派给 planner_executor）",
         "请用 200-300 字介绍 LangGraph 是什么、它和普通 ReAct 的区别，至少给出 2 条最近的官方/权威来源链接"),
        ("会话续聊", "刚才那个 LangGraph 介绍，提取 3 个关键词"),
    ]

    for i, (label, q) in enumerate(cases, 1):
        print("\n" + "─" * 60)
        print(f"Case {i} · {label}")
        print(f"  Q: {q}")
        print("─" * 60)
        try:
            reply, trace = agent.run(session, q)
        except Exception as exc:
            print(f"!! 执行抛错：{type(exc).__name__}: {exc}")
            continue
        tool_names = _names_in_steps(trace.steps)
        print(f"  TOOLS_CALLED: {tool_names}")
        print(f"  TRACE_TYPES : {[s['type'] for s in trace.steps]}")
        print(f"  REPLY ↓\n{reply.strip()[:1200]}")

    store.close()
    print("\n" + "=" * 60)
    print("  Done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
