"""SKILL.md 解析（纯模板，不含 subagent runtime 概念）。

每一份 SKILL.md 描述**一类任务该怎么做**（提示 + 约束 + 步骤），
不指定执行者、不携带工具白名单——执行者由调用方决定。

Front-matter 字段（YAML，全部可选）：

    name             skill 名（小写下划线）；省略时取目录名
    description      一句话能力描述（必备：会出现在主 agent 的 system prompt 目录里）
    triggers         （可选）关键词列表，未来用于自动匹配；本期未使用
    version          （可选）字符串，便于以后做演进

正文（front-matter 之外）= skill 全文，由 read_skill(name) 工具按需返回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_FRONT_MATTER_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SkillError(ValueError):
    """SKILL.md 解析失败。"""


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    body: str
    triggers: list[str] = field(default_factory=list)
    version: str = "0.1.0"
    source_path: Path | None = None


def parse_skill_md(path: Path) -> SkillSpec:
    if not path.exists() or not path.is_file():
        raise SkillError(f"SKILL.md 不存在: {path}")

    text = path.read_text(encoding="utf-8")
    fm: dict[str, Any]
    body: str

    m = _FRONT_MATTER_RE.match(text)
    if m:
        try:
            fm = yaml.safe_load(m.group("fm")) or {}
        except yaml.YAMLError as exc:
            raise SkillError(f"{path}: front-matter 不是合法 YAML: {exc}") from exc
        if not isinstance(fm, dict):
            raise SkillError(f"{path}: front-matter 必须是对象")
        body = m.group("body").strip()
    else:
        fm = {}
        body = text.strip()

    name = str(fm.get("name") or path.parent.name).strip()
    if not _NAME_RE.match(name):
        raise SkillError(
            f"{path}: skill name 非法 {name!r}，只允许小写字母/数字/下划线开头小写字母"
        )

    description = str(fm.get("description") or "").strip()
    if not description:
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                description = line[:200]
                break
    if not description:
        description = f"Skill {name}"

    triggers_raw = fm.get("triggers") or []
    if not isinstance(triggers_raw, list) or not all(
        isinstance(t, str) for t in triggers_raw
    ):
        raise SkillError(f"{path}: triggers 必须是字符串数组")
    triggers = [t.strip() for t in triggers_raw if t.strip()]

    version = str(fm.get("version") or "0.1.0")

    if not body:
        raise SkillError(f"{path}: SKILL.md 正文为空")

    return SkillSpec(
        name=name,
        description=description,
        body=body,
        triggers=triggers,
        version=version,
        source_path=path,
    )
