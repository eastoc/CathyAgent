"""Subagent 子系统 —— 独立 turn loop 的执行实体。

公开 API：
- Subagent / SubagentResult                —— 抽象基类与产出结构
- SubagentRunner                           —— 朴素 ReAct 子循环（被 PlannerExecutor 内部复用）
- PlannerExecutorSubagent                  —— 基于 LangGraph 的 plan-execute-replan 子 agent
- SubagentToolPlugin / build_subagent_tool_manifest —— 把 Subagent 包装成主 agent 可见的工具
"""

from .base import Subagent, SubagentResult
from .planner_executor import PlannerExecutorSubagent
from .runner import SubagentRunner
from .tool_plugin import SubagentToolPlugin, build_subagent_tool_manifest

__all__ = [
    "PlannerExecutorSubagent",
    "Subagent",
    "SubagentResult",
    "SubagentRunner",
    "SubagentToolPlugin",
    "build_subagent_tool_manifest",
]
