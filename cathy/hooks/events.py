"""Hook 事件与决策的数据模型。

8 类事件（与 Claude Code 协议对齐）：
- SessionStart       : 会话启动后第一次拿到 Session
- UserPromptSubmit   : Agent.run 入口，user 消息持久化前
- PreToolUse         : Agent 主循环里 tools.call() 之前
- PostToolUse        : tools.call() 之后
- Stop               : 主循环 final 分支返回前
- SubagentStop       : SubagentToolPlugin.execute 之后
- PreCompact         : ContextAssembler._fit_to_budget 入口（命中预算才触发）
- Notification       : 任意位置主动调，常用于路由到 IM / 邮件

设计原则：
1. event 与 decision 都是纯数据，可被 JSON 序列化（CommandRunner 通过 stdin/stdout 传递时需要）。
2. 任何 hook 都可以返回 HookDecision；多 hook 串联用 merge_decisions 合并。
3. 一旦任意 hook 标记 block=True，后续 hook 的 block 字段不会被覆盖（block 短路）。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


SESSION_START = "SessionStart"
USER_PROMPT_SUBMIT = "UserPromptSubmit"
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
STOP = "Stop"
SUBAGENT_STOP = "SubagentStop"
PRE_COMPACT = "PreCompact"
NOTIFICATION = "Notification"

ALL_EVENTS: tuple[str, ...] = (
    SESSION_START,
    USER_PROMPT_SUBMIT,
    PRE_TOOL_USE,
    POST_TOOL_USE,
    STOP,
    SUBAGENT_STOP,
    PRE_COMPACT,
    NOTIFICATION,
)


@dataclass
class HookEvent:
    """传给 hook 的事件对象。

    Attributes:
        type: 事件类型（见上面 8 个常量之一）。
        session_id: 当前会话 id；子 agent 中也会沿用主会话 id（便于审计串联）。
        matcher_target: 用于 matcher 字符串匹配的目标值。
            - PreToolUse / PostToolUse: 工具名
            - SubagentStop: subagent 名
            - 其它事件: 空字符串（matcher 直接当 "*" 匹配处理）
        payload: 事件相关数据。约定：
            - UserPromptSubmit: {"user_input": str}
            - PreToolUse:       {"tool": str, "params": dict}
            - PostToolUse:      {"tool": str, "params": dict, "result": str, "latency_ms": int}
            - Stop:             {"final_answer": str, "step": int}
            - SubagentStop:     {"subagent": str, "params": dict, "final_answer": str}
            - PreCompact:       {"total_tokens": int, "budget": int, "msg_count": int}
            - SessionStart:     {"new": bool, "msg_count": int}
            - Notification:     {"message": str, "level": str}
        ts_ms: 事件创建时的时间戳（毫秒）。
        meta: 任意上层附加元信息，hook 之间也可以互相传递（merge_decisions 不会触碰）。
    """

    type: str
    session_id: str = ""
    matcher_target: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        """给 CommandRunner 通过 stdin 传给子进程的 JSON 形态。"""
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_json_dict(), ensure_ascii=False)


@dataclass
class HookDecision:
    """hook 返回的决策。所有字段都是可选的，None 表示"无意见"。

    语义约定：
    - block: True 时主循环按事件类型作短路处理（详见 Agent / SubagentToolPlugin 注释）。
    - block_reason: 必须配合 block=True 给出原因，会回灌给 LLM 让它 self-correct。
    - inject_context: 注入给 LLM 的上下文文本：
        * SessionStart: 拼到 system prompt 末尾
        * UserPromptSubmit: 拼到 user 消息内容前
        * PostToolUse: 拼到 tool result 末尾
    - rewrite_params: 仅 PreToolUse 有意义；不为 None 时整体替换 params dict
    - rewrite_user_input: 仅 UserPromptSubmit 有意义；整体替换 user 文本
    - rewrite_final_answer: Stop / SubagentStop 有意义；整体替换最终回答
    - extra: 自由字段，给链上后续 hook 看；不影响主循环
    """

    block: bool = False
    block_reason: str = ""
    inject_context: str = ""
    rewrite_params: dict[str, Any] | None = None
    rewrite_user_input: str | None = None
    rewrite_final_answer: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def noop(cls) -> "HookDecision":
        return cls()

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "HookDecision":
        """从 JSON dict 还原（CommandRunner 解析子进程 stdout 时调用）。

        宽容处理：未知字段忽略，缺字段走默认。
        """
        if not d:
            return cls()
        return cls(
            block=bool(d.get("block", False)),
            block_reason=str(d.get("block_reason", "") or ""),
            inject_context=str(d.get("inject_context", "") or ""),
            rewrite_params=d.get("rewrite_params"),
            rewrite_user_input=d.get("rewrite_user_input"),
            rewrite_final_answer=d.get("rewrite_final_answer"),
            extra=dict(d.get("extra") or {}),
        )

    def is_noop(self) -> bool:
        return (
            not self.block
            and not self.inject_context
            and self.rewrite_params is None
            and self.rewrite_user_input is None
            and self.rewrite_final_answer is None
            and not self.extra
        )


def merge_decisions(decisions: list[HookDecision]) -> HookDecision:
    """把同一事件下多个 hook 的决策按顺序合并成一个最终决策。

    合并规则（保证可预测、不与 LLM 抢上下文）：
    - block：任意一个 True 就 True；block_reason 取**第一个**触发 block 的 hook 的原因。
    - inject_context：按顺序拼接（双换行分隔，自动去重首尾空白）。
    - rewrite_params / rewrite_user_input / rewrite_final_answer：**后到覆盖前到**。
      （hook 在配置里的顺序决定优先级；写在最后的 hook 拥有最终发言权。）
    - extra：浅 merge，后到覆盖前到。
    """
    out = HookDecision()
    inject_parts: list[str] = []
    block_reason_set = False
    
    for d in decisions:
        if d.block:
            out.block = True
            if not block_reason_set and d.block_reason:
                out.block_reason = d.block_reason
                block_reason_set = True
        if d.inject_context:
            piece = d.inject_context.strip()
            if piece:
                inject_parts.append(piece)
        if d.rewrite_params is not None:
            out.rewrite_params = d.rewrite_params
        if d.rewrite_user_input is not None:
            out.rewrite_user_input = d.rewrite_user_input
        if d.rewrite_final_answer is not None:
            out.rewrite_final_answer = d.rewrite_final_answer
        if d.extra:
            out.extra.update(d.extra)

    if inject_parts:
        out.inject_context = "\n\n".join(inject_parts)

    return out
