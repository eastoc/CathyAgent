"""多级上下文装配器（System Prompt 构建中心）。

借鉴 Claude Code 的多级上下文设计：把 system prompt 拆成若干"层"，
每一层独立来源、独立演进，最终由 ContextAssembler 拼接成一个完整 prompt。

层级（从静态到动态）：

  1. SYSTEM   —— 内置角色 / 工具调用规则 / 回答风格（本文件 DEFAULT_SYSTEM_PROMPT）
  2. PROJECT  —— 项目级规则，自动读取 AGENTS.md / CATHY.md
  3. SKILL    —— 当前激活的 Skill 内容（Phase 3 起接入）
  4. EXTRA    —— 用户在 config.yaml / CLI 临时附加的指令

Session 历史 与 Scratchpad 不在此装配，由 messages 列表承担。

暴露给外部的入口仅一个：build_system_prompt(...)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = """\
你是 Cathy，一个使用工具完成任务的中文助手。

## 工作原则

1. 不确定就先调工具，不要凭空回答。
2. 工具调用应当目的明确、参数完整；一次只调用真正需要的工具。
3. 同一信息可由多个工具获取时，优先选择最直接、副作用最小的工具。
4. 工具失败时阅读错误信息并自适应：换参数、换工具、或如实告知用户。

## 工具能力概览

- `web_search`：联网搜索最新信息（新闻、事实核对、文档）。
- `read_file`：读取本地文本文件，仅限当前工作目录子树。
- `get_current_datetime`：获取当前系统时间。涉及"今天/现在/星期几/N 天后"等时间问题时必须调用，不要凭模型记忆作答。

后续可用工具会通过 OpenAI tool schema 自动注入，请按其 description 与 input_schema 调用。

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
    skill: str = ""
    extra: str = ""

    def assemble(self) -> str:
        parts: list[str] = []
        if self.system:
            parts.append(self.system.strip())
        if self.project:
            parts.append("## 项目级规则（来自 AGENTS.md / CATHY.md）\n\n" + self.project.strip())
        if self.skill:
            parts.append("## 当前 Skill\n\n" + self.skill.strip())
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
    skill: str = "",
    extra: str = "",
) -> str:
    """装配最终 system prompt。

    Args:
        project_root: 项目根目录；提供时会尝试读取 AGENTS.md / CATHY.md。
        system_override: 完整替换内置默认 SYSTEM 层（罕用）。
        skill: 已激活 Skill 的全文（Phase 3 起注入）。
        extra: 临时附加指令（来自 config.yaml 的 AGENT.extra_system 或 CLI 参数）。
    """
    layers = ContextLayers(
        system=(system_override if system_override is not None else DEFAULT_SYSTEM_PROMPT),
        project=load_project_rules(project_root) if project_root else "",
        skill=skill,
        extra=extra,
    )
    return layers.assemble()
