"""多级上下文装配器（System Prompt + 历史消息预算控制）。

借鉴 Claude Code 的多级上下文设计：把上下文拆成若干"层"，
每一层独立来源、独立演进，最终由 ContextAssembler 拼接成发给 LLM 的 messages。

层级（从静态到动态）：

  1. SYSTEM   —— 内置角色 / 工具调用规则 / 回答风格（DEFAULT_SYSTEM_PROMPT）
  2. PROJECT  —— 项目级规则，自动读取 AGENTS.md / CATHY.md
  3. SKILL    —— 当前激活的 Skill 内容（Phase 3 起接入）
  4. EXTRA    —— 用户在 config.yaml / CLI 临时附加的指令
  5. SESSION  —— 历史消息（来自 SQLite，Phase 2 起接入）
  6. SCRATCHPAD —— 当前任务工具结果（Phase 3 起做"对外摘要 vs 内部全量"分离）

对外接口：
- build_system_prompt(...): 仅返回 system 层（向下兼容旧调用方）。
- ContextAssembler.assemble(session, user_input): 返回完整 messages（含历史 + 预算硬截断）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .hooks import HookManager
    from .session.models import Message, Session

DEFAULT_SYSTEM_PROMPT = """\
你是 Cathy，一个使用工具完成任务的中文助手。

## 工作原则

1. 不确定就先调工具，不要凭空回答。
2. 工具调用应当目的明确、参数完整；一次只调用真正需要的工具。
3. 同一信息可由多个工具获取时，优先选择最直接、副作用最小的工具。
4. 工具失败时阅读错误信息并自适应：换参数、换工具、或如实告知用户。

## 工具能力概览

- `web_search`：联网搜索最新信息（新闻、事实核对、文档）。
- `read_file` / `list_dir` / `write_file`：本地文件操作，限当前工作目录子树。
- `get_current_datetime`：获取当前系统时间。涉及"今天/现在/星期几/N 天后"等时间问题时必须调用。
- `read_skill`：按名读取 SKILL.md 全文。当任务匹配下面"可用 Skills"目录里的某条时调用。
- `planner_executor`：把一个**复杂、多步骤**的子任务派给规划-执行子 agent。

其它工具会通过 OpenAI tool schema 自动注入，请按其 description 与 input_schema 调用。

## 何时调 `planner_executor`（关键决策点）

简单任务直接自己用 ReAct 完成；只有当任务**同时**满足以下 ≥2 项时才考虑派给子 agent：
- 需要先做计划再分步执行（不是单步可解）；
- 中间产物长、留在主上下文会污染后续对话；
- 步骤之间相对独立，可以拆。

派出时入参 `goal` 必须包含**完整背景**（子 agent 看不到本会话历史）。
子 agent 返回的是凝练后的 `final_answer`，请把它整合进给用户的回答。

## Skills 使用约定

- system prompt 末尾的"可用 Skills"列出了所有可加载的指令模板（只有 name + 一句描述）。
- 当任务和某个 skill 匹配时（例如要做摘要 / 写博客），先 `read_skill(name=...)` 拿到全文，再按全文里的格式与步骤产出。
- 主 agent 自己也能用 skill；不需要必须派给 subagent。

## 回答风格

- 使用中文，简洁、有结构。
- 列举多条信息时使用列表；对比信息使用小表格。
- 引用搜索结果时附上链接；引用文件时附上路径。
- 不复述用户问题，不冗余客套。
"""

PROJECT_RULES_FILES = ("AGENTS.md", "CATHY.md")


@dataclass
class ContextLayers:
    """多级上下文的内存表示。各层均为字符串，空串视为不启用。"""

    system: str = ""
    project: str = ""
    skill_catalog: str = ""
    extra: str = ""

    def assemble(self) -> str:
        parts: list[str] = []
        if self.system:
            parts.append(self.system.strip())
        if self.project:
            parts.append("## 项目级规则（来自 AGENTS.md / CATHY.md）\n\n" + self.project.strip())
        if self.skill_catalog:
            parts.append(self.skill_catalog.strip())
        if self.extra:
            parts.append("## 附加指令\n\n" + self.extra.strip())
        return "\n\n---\n\n".join(parts)


def load_project_rules(root: Path) -> str:
    """读取项目根下的 AGENTS.md / CATHY.md 之一作为 PROJECT 层。"""
    for name in PROJECT_RULES_FILES:
        path = root / name
        if path.exists() and path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                continue
    return ""


def build_system_prompt(
    *,
    project_root: Path | None = None,
    system_override: str | None = None,
    skill_catalog: str = "",
    extra: str = "",
) -> str:
    """装配最终 system prompt。

    Args:
        project_root: 项目根目录；提供时会尝试读取 AGENTS.md / CATHY.md。
        system_override: 完整替换内置默认 SYSTEM 层（罕用）。
        skill_catalog: 由 build_skill_catalog 生成的 skill 目录字符串（不含正文）。
        extra: 临时附加指令（来自 config.yaml 的 AGENT.extra_system 或 CLI 参数）。
    """
    layers = ContextLayers(
        system=(system_override if system_override is not None else DEFAULT_SYSTEM_PROMPT),
        project=load_project_rules(project_root) if project_root else "",
        skill_catalog=skill_catalog,
        extra=extra,
    )
    return layers.assemble()


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：英文 ~4 字符/token，中文 ~1.5 字符/token。

    Phase 2 用 max(len/4, len/1.5) 作为保守上界，避免低估预算。
    Phase 7 改为 tiktoken 精确计算。
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


class ContextAssembler:
    """有状态的上下文装配器。一个 Session 共用一个实例。

    职责：
    1. 持有 system_prompt（启动时一次构建，不变）。
    2. 每轮把 [system] + 历史(预算内) + 新 user 输入 拼成完整 messages。
    3. 历史超预算时，从尾部回溯到最近的 user 边界硬截断，避免切断 tool_call/tool_result 配对。
    """

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        skill_catalog: str = "",
        extra: str = "",
        token_budget: int = 8000,
        hooks: "HookManager | None" = None,
    ) -> None:
        self.system_prompt = build_system_prompt(
            project_root=project_root, skill_catalog=skill_catalog, extra=extra,
        )
        self.token_budget = max(256, int(token_budget))
        self.hooks = hooks  # 仅 PreCompact 用；None 等价于无 hook

    def append_system_layer(self, text: str) -> None:
        """运行期追加一段到 system prompt 末尾。

        SessionStart hook 的 inject_context 会通过这个接口拼到底层 SYSTEM 层之后，
        立即对所有后续轮次生效。
        """
        text = (text or "").strip()
        if not text:
            return
        self.system_prompt = f"{self.system_prompt}\n\n---\n\n{text}"

    def assemble(
        self,
        session: "Session",
        user_input: str | None = None,
    ) -> list[dict]:
        """返回本轮要发给 LLM 的完整 messages。

        - user_input 为 None 时不追加（适合 agent 内部循环里把工具结果 append 进 session 后再调一次 LLM）。
        - user_input 非 None 时**仅装配**进返回值，不写入 session（持久化由调用方负责）。
        """
        msgs: list[dict] = [{"role": "system", "content": self.system_prompt}]
        history = self._fit_to_budget(session.messages)
        msgs.extend(m.to_openai_dict() for m in history)
        if user_input is not None and user_input != "":
            msgs.append({"role": "user", "content": user_input})
        return msgs

    def _fit_to_budget(self, msgs: list["Message"]) -> list["Message"]:
        if not msgs:
            return []

        total_tokens = sum(self._msg_tokens(m) for m in msgs)
        if total_tokens <= self.token_budget:
            return list(msgs)

        # === Hook: PreCompact（命中预算才触发；MVP 只通知，不接受改写） ===
        if self.hooks is not None:
            try:
                from .hooks import HookEvent, PRE_COMPACT  # 避免顶层循环导入

                if self.hooks.has_hooks_for(PRE_COMPACT):
                    self.hooks.dispatch(
                        HookEvent(
                            type=PRE_COMPACT,
                            payload={
                                "total_tokens": total_tokens,
                                "budget": self.token_budget,
                                "msg_count": len(msgs),
                            },
                        )
                    )
            except Exception:
                # PreCompact 仅观测性质，永不影响主流程
                pass

        # 找所有 user 消息位置作为合法切分点
        user_indices = [i for i, m in enumerate(msgs) if m.role == "user"]
        if not user_indices:
            return list(msgs)  # 极少见，原样返回

        # 从越早的切分点开始尝试，第一个落在预算内的就是结果；否则保留最后一段
        best = msgs[user_indices[-1]:]
        for start in user_indices:
            slice_ = msgs[start:]
            tokens = sum(self._msg_tokens(m) for m in slice_)
            if tokens <= self.token_budget:
                best = slice_
                break
        return best

    @staticmethod
    def _msg_tokens(m: "Message") -> int:
        cost = _estimate_tokens(m.content or "")
        if m.tool_calls:
            for tc in m.tool_calls:
                fn = tc.get("function") or {}
                cost += _estimate_tokens(str(fn.get("name", "")))
                cost += _estimate_tokens(str(fn.get("arguments", "")))
        return cost
