"""Skills 发现 + 目录字符串构造。"""

from __future__ import annotations

from pathlib import Path

from ..logger import get_logger
from .manifest import SkillError, SkillSpec, parse_skill_md

logger = get_logger(__name__)


def discover_skills(roots: list[Path]) -> list[SkillSpec]:
    """扫描多个根目录下的 SKILL.md（一层子目录）。

    解析失败的 skill 会打印告警并跳过；同名 skill 仅保留首个。
    """
    found: list[SkillSpec] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            md = child / "SKILL.md"
            if not md.exists():
                continue
            try:
                spec = parse_skill_md(md)
            except SkillError as exc:
                logger.warning("[skill][skip] %s: %s", md, exc)
                continue
            if spec.name in seen:
                logger.warning("[skill][skip] %s: 同名 skill 已存在: %s", md, spec.name)
                continue
            seen.add(spec.name)
            found.append(spec)
    return found


def build_skill_catalog(skills: list[SkillSpec]) -> str:
    """把 skills 列表压缩成目录字符串，注入主 agent 的 system prompt。

    设计：name + 一句话描述。**不放正文**，要正文请调 read_skill(name) 工具。
    """
    if not skills:
        return ""
    lines = ["## 可用 Skills（按需用 `read_skill(name)` 读取全文）", ""]
    for s in sorted(skills, key=lambda s: s.name):
        lines.append(f"- `{s.name}`: {s.description}")
    return "\n".join(lines)
