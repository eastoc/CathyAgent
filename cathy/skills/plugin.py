"""SkillsPlugin —— `read_skill(name)` 工具。

主 agent / 子 agent 都可以调用这个工具按需拉取某个 SKILL.md 的正文。
（progressive disclosure：目录在 system prompt，正文按需）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..plugins.base import PluginError, ToolPlugin
from ..plugins.manifest import Execution, PluginManifest, ToolSpec
from .manifest import SkillSpec


def build_skills_manifest() -> PluginManifest:
    return PluginManifest(
        name="skills",
        version="0.1.0",
        description="按名读取 SKILL.md 全文（progressive disclosure）。",
        tools=[
            ToolSpec(
                name="read_skill",
                description=(
                    "读取一个 skill 的完整指令文档。"
                    "当任务匹配 system prompt 中列出的某个 skill 时调用。"
                    "返回该 skill 的全文，请按其指示行事。"
                ),
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "skill 名（来自 system prompt 中的可用 Skills 目录）。",
                        }
                    },
                },
            )
        ],
        permissions={"network": False, "filesystem": True},
        execution=Execution(runtime="python", entrypoint="<internal>:SkillsPlugin"),
        metadata={"trust_level": "builtin", "kind": "skills"},
        source_dir=Path(__file__).parent,
    )


class SkillsPlugin(ToolPlugin):
    def __init__(self, *, skills: list[SkillSpec]) -> None:
        self._skills: dict[str, SkillSpec] = {s.name: s for s in skills}

    def initialize(self, config: dict[str, Any]) -> None:
        return None

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != "read_skill":
            raise PluginError(f"skills 插件不支持的工具: {tool_name}")

        name = str(params.get("name", "")).strip()
        if not name:
            return "[ToolError:read_skill] 参数 name 为空"
        spec = self._skills.get(name)
        if spec is None:
            available = ", ".join(sorted(self._skills)) or "(none)"
            return (
                f"[ToolError:read_skill] 未知 skill: {name!r}。"
                f"当前可用：{available}"
            )
        return f"# Skill: {spec.name}\n\n{spec.body}"

    @property
    def skills(self) -> dict[str, SkillSpec]:
        return dict(self._skills)
