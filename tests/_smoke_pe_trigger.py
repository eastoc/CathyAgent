"""强制触发 planner_executor 的复杂任务烟测（带进度打印）。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cathy.cli import build_runtime  # noqa: E402
from cathy.subagent import planner_executor as pe_mod  # noqa: E402


def _patch_progress() -> None:
    """给 planner_executor 三个节点加耗时打印，便于观察是否卡住。"""
    PE = pe_mod.PlannerExecutorSubagent
    orig_planner = PE._planner_node
    orig_executor = PE._executor_node
    orig_replanner = PE._replanner_node

    def planner(self, state):
        t = time.time()
        print(f"  [pe.planner] start  goal={state['goal'][:40]}...")
        out = orig_planner(self, state)
        print(f"  [pe.planner] done   plan={out.get('plan')}  ({time.time()-t:.1f}s)")
        return out

    def executor(self, state):
        plan = state.get("plan") or []
        step = plan[0] if plan else "(none)"
        t = time.time()
        print(f"  [pe.executor] step  iter={state.get('iterations')}  current={step[:50]}")
        out = orig_executor(self, state)
        last = (out.get("past_steps") or [])[-1]
        print(f"  [pe.executor] done  result={last.get('result','')[:80]}  ({time.time()-t:.1f}s)")
        return out

    def replanner(self, state):
        t = time.time()
        print(f"  [pe.replanner] iter={state.get('iterations')}  plan_left={len(state.get('plan') or [])}")
        out = orig_replanner(self, state)
        if out.get("response"):
            print(f"  [pe.replanner] FINISH  ({time.time()-t:.1f}s)")
        else:
            print(f"  [pe.replanner] CONTINUE  new_plan={out.get('plan')}  ({time.time()-t:.1f}s)")
        return out

    PE._planner_node = planner
    PE._executor_node = executor
    PE._replanner_node = replanner


def main() -> None:
    _patch_progress()

    agent, store = build_runtime()
    session = store.get_or_create("smoke-pe-trigger-2")

    q = (
        "请帮我做一份关于「LangGraph 与 AutoGen 的对比」的小型调研报告，要求：\n"
        "1) 各自的核心抽象（节点/边/状态 vs Agent/Group）；\n"
        "2) 各自最适合的 3 个使用场景；\n"
        "3) 用一个 markdown 表格做总结性对比（>= 5 行）；\n"
        "4) 每节都要附 1-2 条权威来源链接；\n"
        "5) 总长度 600-800 字。\n"
        "请按计划分步执行：先收集资料，再分别写每节，最后整合输出。"
    )
    print(f"Q: {q[:80]} ...\n")

    t0 = time.time()
    reply, trace = agent.run(session, q)
    elapsed = time.time() - t0

    tool_calls = [s.get("name") for s in trace.steps if s.get("type") == "tool_call"]
    print(f"\nELAPSED       : {elapsed:.1f}s")
    print(f"TOOLS_CALLED  : {tool_calls}")
    print(f"STEP_TYPES    : {[s['type'] for s in trace.steps]}")
    if "planner_executor" in tool_calls:
        print("[OK] planner_executor 被触发")
    else:
        print("[WARN] 仍未触发 planner_executor，模型选择直接搜索")

    print("\n--- REPLY (前 1500 字) ---")
    print(reply[:1500])
    store.close()


if __name__ == "__main__":
    main()
