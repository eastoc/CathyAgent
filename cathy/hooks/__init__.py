"""cathy.hooks —— 事件式中间件系统（Phase 3.5）。

对外暴露：

    from cathy.hooks import (
        HookEvent, HookDecision,
        HookManager, make_event,
        SESSION_START, USER_PROMPT_SUBMIT, PRE_TOOL_USE, POST_TOOL_USE,
        STOP, SUBAGENT_STOP, PRE_COMPACT, NOTIFICATION,
    )

主循环（agent.py / cli.py / subagent / context.py）只依赖 HookManager.dispatch
和事件常量，不需要关心 runner 实现细节。
"""

from .events import (
    ALL_EVENTS,
    HookDecision,
    HookEvent,
    NOTIFICATION,
    POST_TOOL_USE,
    PRE_COMPACT,
    PRE_TOOL_USE,
    SESSION_START,
    STOP,
    SUBAGENT_STOP,
    USER_PROMPT_SUBMIT,
    merge_decisions,
)
from .manager import HookManager, empty_manager, make_event
from .runners import HookSpec, RunnerInvocation

__all__ = [
    "ALL_EVENTS",
    "HookDecision",
    "HookEvent",
    "HookManager",
    "HookSpec",
    "RunnerInvocation",
    "NOTIFICATION",
    "POST_TOOL_USE",
    "PRE_COMPACT",
    "PRE_TOOL_USE",
    "SESSION_START",
    "STOP",
    "SUBAGENT_STOP",
    "USER_PROMPT_SUBMIT",
    "empty_manager",
    "make_event",
    "merge_decisions",
]
