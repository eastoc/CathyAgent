"""Subagent 子系统 —— 独立 turn loop 的执行实体。

公开 API：
- Subagent / SubagentResult                —— 抽象基类与产出结构
- SubagentRunner                           —— 朴素 ReAct 子循环（被 PlannerExecutor 内部复用）
- SubagentToolPlugin / build_subagent_tool_manifest —— 把 Subagent 包装成主 agent 可见的工具
"""

from .base import Subagent, SubagentResult

__all__ = [
    "Subagent",
    "SubagentResult",
    "SubagentRunner",
    "SubagentToolPlugin",
    "build_subagent_tool_manifest",
]


def __getattr__(name: str):
    if name == "SubagentRunner":
        from .runner import SubagentRunner

        return SubagentRunner
    if name in {"SubagentToolPlugin", "build_subagent_tool_manifest"}:
        from .tool_plugin import SubagentToolPlugin, build_subagent_tool_manifest

        return {
            "SubagentToolPlugin": SubagentToolPlugin,
            "build_subagent_tool_manifest": build_subagent_tool_manifest,
        }[name]
    raise AttributeError(name)
