"""PlannerExecutor —— 基于 LangGraph 的 plan-execute-replan 子 agent。

状态机（LangGraph StateGraph）：

    [START] → planner ──→ executor ──┬──── (plan 仍有步骤) ──→ executor
                                      │
                                      └──── (plan 已空) ─────→ replanner ─┬─→ executor (continue：写入新 plan)
                                                                          │
                                                                          └─→ END (finish：写入 response)

- **planner**：对 goal 产出结构化 plan（编号列表）。无工具。
- **executor**：取 plan[0]，跑一个迷你 ReAct 子循环（带 tools）完成这一步；产出 (step, result) 进 past_steps，从 plan 弹出。
  仍有步骤就继续自循环；plan 走完才交给 replanner。
- **replanner**：基于 goal + past_steps，要么 finish 给最终答案，要么 continue 给一段补充 plan（≤ 4 步）回到 executor。
- **iterations 兜底**：达到 max_iterations 时 replanner 被强制走 finish 路径。

子 agent 不会看到父 session 历史；返回的 `response` 由父 agent 作为 tool_result 收回。
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from cathy.llm_errors import AgentFailure, LLMCallError
from cathy.subagent import Subagent, SubagentResult
from cathy.subagent.runner import SubagentRunner

# ToolView is consumed via the duck-typed `tools` parameter; no import needed here.


# ---------- 状态定义 ----------

class PEState(TypedDict, total=False):
    goal: str
    plan: list[str]
    past_steps: list[dict]  # [{"step": str, "result": str}]
    response: str
    iterations: int
    max_iterations: int
    trace: list[dict]


# ---------- 提示词模板 ----------

_PLANNER_SYSTEM = """\
你是一个**规划 agent**。给定一个总目标，请把它拆成 3-7 个**最小可执行步骤**，
每一步要：
- 具体可单独执行，不假设"等会儿就懂了"
- 顺序合理，前一步的产物可被后一步引用
- 用动词开头（"读取……"、"搜索……"、"整理……"、"撰写……"）

只输出步骤列表，**不要解释、不要寒暄、不要在外面再套别的内容**。

输出格式（严格）：
1. ……
2. ……
3. ……
"""

_REPLANNER_SYSTEM = """\
你是一个**复盘 agent**。给定原始目标、已经完成的步骤（含结果摘要）、以及尚未执行的计划，
请决定下一步：

- 如果已有信息足以回答原始目标，输出 finish
- 否则给出**修订后的剩余步骤**（可以增删改），且步骤数 ≤ 4

只输出 **一个 JSON 对象**，不要任何其它文字、不要代码块包裹：

{"action": "finish", "response": "<给原始目标的最终中文回答>"}
或
{"action": "continue", "plan": ["…", "…"]}
"""

_EXECUTOR_SYSTEM_TEMPLATE = """\
你是一个**执行 agent**，正在按计划完成一个大目标的某一步。

总目标：{goal}

已完成的步骤回顾（仅供参考，不要重复执行）：
{past_steps_summary}

当前要执行的步骤：
{current_step}

请**只完成当前步骤**，给出该步骤的产物（必要时调用工具），不要尝试一次完成整个目标。
最终用一段简洁中文回复你这一步得到的结论 / 数据 / 文件位置等。
"""


# ---------- 解析工具 ----------

_NUMBERED_LINE = re.compile(r"^\s*(?:\d+[.)、]|[-*])\s*(.+)\s*$")


def _parse_plan(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        m = _NUMBERED_LINE.match(line)
        if m:
            step = m.group(1).strip()
            if step:
                out.append(step)
    return out


def _parse_replan_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # 兼容 LLM 偶尔加上 ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "action" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _format_past_steps(past_steps: list[dict]) -> str:
    if not past_steps:
        return "(尚无已完成步骤)"
    lines = []
    for i, item in enumerate(past_steps, 1):
        step = item.get("step", "")
        result = item.get("result", "")
        if len(result) > 600:
            result = result[:600] + " …(已截断)"
        lines.append(f"{i}. {step}\n   → {result}")
    return "\n".join(lines)


def _llm_text(llm: Any, system: str, user: str) -> str:
    """对 LLMClient 做一次纯文本对话（无工具）。"""
    response = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    return (response.choices[0].message.content or "").strip()


# ---------- Subagent 实现 ----------

class PlannerExecutorSubagent(Subagent):
    """规划-执行-复盘 三阶段子 agent，基于 LangGraph。"""

    name = "planner_executor"
    description = (
        "规划-执行子 agent。把一个**复杂、多步骤**的目标交给它："
        "它会先拆解为 3-7 个步骤、按计划用工具逐步执行、最后整合为一个结论返回。"
        "适合：调研报告 / 多文件改造 / 信息收集与综合。简单单步任务请直接自己 ReAct，不要派给它。"
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["goal"],
        "properties": {
            "goal": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "完整的目标描述。子 agent 看不到父会话历史，"
                    "请把背景、约束、期望产物（格式 / 长度 / 引用要求）都写进来。"
                ),
            },
            "max_iterations": {
                "type": "integer",
                "minimum": 1,
                "maximum": 16,
                "description": "executor 节点最大执行次数（兜底防死循环），默认 8。",
            },
        },
    }

    def __init__(
        self,
        *,
        llm: Any,
        tools: Any,  # ToolView / PluginRegistry（鸭子兼容）
        executor_step_max: int = 4,
        default_max_iterations: int = 8,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._executor_step_max = max(1, int(executor_step_max))
        self._default_max_iterations = max(1, int(default_max_iterations))
        self._graph = self._build_graph()

    # -------- 节点实现 -------- #

    def _planner_node(self, state: PEState) -> PEState:
        plan_text = _llm_text(self._llm, _PLANNER_SYSTEM, state["goal"])
        plan = _parse_plan(plan_text)
        if not plan:
            # 兜底：解析失败时，把原始 goal 作为单步
            plan = [state["goal"]]
        trace = list(state.get("trace") or [])
        trace.append({"type": "plan", "plan": plan, "raw": plan_text})
        return {
            **state,
            "plan": plan,
            "past_steps": list(state.get("past_steps") or []),
            "iterations": int(state.get("iterations") or 0),
            "trace": trace,
        }

    def _executor_node(self, state: PEState) -> PEState:
        plan = list(state.get("plan") or [])
        past_steps = list(state.get("past_steps") or [])
        if not plan:
            return state  # 不应被路由到，安全兜底
        step = plan[0]

        system = _EXECUTOR_SYSTEM_TEMPLATE.format(
            goal=state["goal"],
            past_steps_summary=_format_past_steps(past_steps),
            current_step=step,
        )
        runner = SubagentRunner(
            llm=self._llm,
            tools=self._tools,
            system_prompt=system,
            max_steps=self._executor_step_max,
        )
        result = runner.run(step)

        past_steps.append({"step": step, "result": result.final_answer})
        trace = list(state.get("trace") or [])
        trace.append(
            {
                "type": "step",
                "step": step,
                "result": result.final_answer,
                "inner_trace": result.trace,
            }
        )
        return {
            **state,
            "plan": plan[1:],
            "past_steps": past_steps,
            "iterations": int(state.get("iterations") or 0) + 1,
            "trace": trace,
        }

    def _replanner_node(self, state: PEState) -> PEState:
        max_iter = self._max_iter_from_state(state)
        iterations = int(state.get("iterations") or 0)
        past_steps = list(state.get("past_steps") or [])
        plan = list(state.get("plan") or [])

        # 强制结束条件：迭代上限到达，或者计划已空
        if iterations >= max_iter or not plan:
            user = (
                f"原始目标：{state['goal']}\n\n"
                f"已完成步骤：\n{_format_past_steps(past_steps)}\n\n"
                f"请基于以上信息，输出 JSON：\n"
                f'{{"action":"finish","response":"<对原始目标的中文最终回答>"}}'
            )
        else:
            user = (
                f"原始目标：{state['goal']}\n\n"
                f"已完成步骤：\n{_format_past_steps(past_steps)}\n\n"
                f"剩余计划：\n"
                + "\n".join(f"- {s}" for s in plan)
                + "\n\n请输出决策 JSON。"
            )

        raw = _llm_text(self._llm, _REPLANNER_SYSTEM, user)
        decision = _parse_replan_json(raw)

        trace = list(state.get("trace") or [])
        trace.append({"type": "replan", "raw": raw, "decision": decision})

        if decision and decision.get("action") == "finish":
            return {
                **state,
                "response": str(decision.get("response") or "").strip()
                or self._fallback_synthesis(past_steps),
                "plan": plan,
                "past_steps": past_steps,
                "trace": trace,
            }
        if decision and decision.get("action") == "continue":
            new_plan = decision.get("plan") or []
            if isinstance(new_plan, list) and all(isinstance(x, str) for x in new_plan):
                return {
                    **state,
                    "plan": [s.strip() for s in new_plan if s.strip()][:4],
                    "past_steps": past_steps,
                    "trace": trace,
                }

        # 解析失败 / iterations 用尽：直接综合现有 past_steps 作为 response
        return {
            **state,
            "response": self._fallback_synthesis(past_steps),
            "plan": plan,
            "past_steps": past_steps,
            "trace": trace,
        }

    def _route_after_executor(self, state: PEState) -> str:
        # plan 还有剩 → 继续 executor；否则交给 replanner 决策。
        # 同时检查 iterations 上限，防止 plan 异常变长导致死循环。
        plan = state.get("plan") or []
        iterations = int(state.get("iterations") or 0)
        max_iter = self._max_iter_from_state(state)
        if plan and iterations < max_iter:
            return "executor"
        return "replanner"

    def _route_after_replan(self, state: PEState) -> str:
        return "end" if state.get("response") else "executor"

    @staticmethod
    def _fallback_synthesis(past_steps: list[dict]) -> str:
        if not past_steps:
            return "[planner_executor] 未能产出有效结果。"
        return "已执行的步骤摘要：\n" + _format_past_steps(past_steps)

    @staticmethod
    def _max_iter_from_state(state: PEState) -> int:
        # default_max_iterations 由实例属性提供；这里通过 trace[0] 注入太重，简化：
        # iterations 上限统一用 16 作硬上界，软上限通过实例 default_max_iterations 在 run() 里写入 state 即可。
        return int(state.get("max_iterations") or 8)

    # -------- 图装配 + 入口 -------- #

    def _build_graph(self):
        builder = StateGraph(PEState)
        builder.add_node("planner", self._planner_node)
        builder.add_node("executor", self._executor_node)
        builder.add_node("replanner", self._replanner_node)
        builder.set_entry_point("planner")
        builder.add_edge("planner", "executor")
        builder.add_conditional_edges(
            "executor",
            self._route_after_executor,
            {"executor": "executor", "replanner": "replanner"},
        )
        builder.add_conditional_edges(
            "replanner",
            self._route_after_replan,
            {"executor": "executor", "end": END},
        )
        return builder.compile()

    def run(self, params: dict[str, Any]) -> SubagentResult:
        goal = str(params["goal"])
        max_iter = int(params.get("max_iterations") or self._default_max_iterations)

        initial: PEState = {
            "goal": goal,
            "plan": [],
            "past_steps": [],
            "response": "",
            "iterations": 0,
            "max_iterations": max_iter,
            "trace": [],
        }
        # langgraph recursion_limit 给一个安全上界（>= 节点访问数）
        config = {"recursion_limit": max(8, max_iter * 3 + 4)}
        try:
            final_state = self._graph.invoke(initial, config=config)
        except Exception as exc:
            failure = _subagent_failure(exc, stage="planner_executor")
            return SubagentResult(
                final_answer=f"[planner_executor] 图执行失败: {type(exc).__name__}: {exc}",
                finished=False,
                status="failed",
                failure=failure,
            )

        result = SubagentResult(
            final_answer=str(final_state.get("response") or "").strip()
            or self._fallback_synthesis(list(final_state.get("past_steps") or [])),
            finished=bool(final_state.get("response")),
            trace=list(final_state.get("trace") or []),
        )
        return result


def _subagent_failure(exc: Exception, *, stage: str) -> AgentFailure:
    if isinstance(exc, LLMCallError):
        return exc.failure
    return AgentFailure(
        stage=stage,
        error_type=type(exc).__name__,
        reason="unknown",
        retryable=False,
        message=str(exc),
    )
