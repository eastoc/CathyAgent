"""把任意 Subagent 实例包装成对父 agent 透明的 ToolPlugin。

Phase 3.5 起：execute() 返回前会触发 SubagentStop hook，可改写 final_answer。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..hooks import HookEvent, HookManager, SUBAGENT_STOP
from ..plugins.base import PluginError, ToolPlugin
from ..plugins.manifest import Execution, PluginManifest, ToolSpec
from .base import Subagent


def build_subagent_tool_manifest(subagent: Subagent) -> PluginManifest:
    return PluginManifest(
        name=subagent.name,
        version="0.1.0",
        description=subagent.description,
        tools=[
            ToolSpec(
                name=subagent.name,
                description=subagent.description,
                input_schema=subagent.input_schema,
            )
        ],
        permissions={"network": False, "filesystem": False},
        execution=Execution(
            runtime="python",
            entrypoint="<internal>:SubagentToolPlugin",
        ),
        metadata={"trust_level": "builtin", "kind": "subagent"},
        source_dir=Path(__file__).parent,
    )


class SubagentToolPlugin(ToolPlugin):
    """ToolPlugin 适配器：把 Subagent.run() 暴露成 OpenAI function tool。

    可选注入 HookManager，子 agent 跑完会触发 SubagentStop。
    """

    def __init__(self, subagent: Subagent, *, hooks: HookManager | None = None) -> None:
        self._subagent = subagent
        self._hooks = hooks
        self._session_id = ""  # 由父 agent 通过 attach_session 注入；缺省为空

    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def attach_session(self, session_id: str) -> None:
        """父 agent 在会话开始后调用一次，让子 agent 和 hook 事件拿到 session_id。"""
        self._session_id = session_id or ""
        attach = getattr(self._subagent, "attach_session", None)
        if callable(attach):
            attach(self._session_id)

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != self._subagent.name:
            raise PluginError(
                f"{self._subagent.name} 插件不支持的工具: {tool_name}"
            )
        result = self._subagent.run(params)
        final = result.final_answer or f"[subagent:{self._subagent.name}] 子任务无输出"

        if self._hooks is not None and self._hooks.has_hooks_for(SUBAGENT_STOP):
            decision = self._hooks.dispatch(
                HookEvent(
                    type=SUBAGENT_STOP,
                    session_id=self._session_id,
                    matcher_target=self._subagent.name,
                    payload={
                        "subagent": self._subagent.name,
                        "params": params,
                        "final_answer": final,
                    },
                )
            )
            if decision.rewrite_final_answer is not None:
                final = str(decision.rewrite_final_answer)
            if decision.inject_context:
                final = f"{final}\n\n[hook:SubagentStop] {decision.inject_context}"

        return final

    @property
    def subagent(self) -> Subagent:
        return self._subagent
