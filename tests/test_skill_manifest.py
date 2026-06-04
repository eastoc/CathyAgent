"""SKILL.md 解析测试（cathy.skills.manifest）。"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.skills import (  # noqa: E402
    SkillError,
    SkillSpec,
    discover_skills,
    parse_skill_md,
)
from cathy.skills.loader import build_skill_catalog  # noqa: E402


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


class SkillManifestTest(unittest.TestCase):
    def test_parse_full_front_matter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = _write(
                Path(tmp) / "summarize" / "SKILL.md",
                """\
                ---
                name: summarize
                description: 给一段文本输出三段式摘要
                triggers: [摘要, 概括]
                ---

                你是摘要助手。请按下面格式输出。
                """,
            )
            spec = parse_skill_md(md)
            self.assertIsInstance(spec, SkillSpec)
            self.assertEqual(spec.name, "summarize")
            self.assertEqual(spec.description, "给一段文本输出三段式摘要")
            self.assertEqual(spec.triggers, ["摘要", "概括"])
            self.assertIn("摘要助手", spec.body)

    def test_parse_without_front_matter_uses_dirname_and_first_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = _write(
                Path(tmp) / "no_meta" / "SKILL.md",
                """\
                # 无 front-matter 兜底
                正文内容。
                """,
            )
            spec = parse_skill_md(md)
            self.assertEqual(spec.name, "no_meta")
            self.assertEqual(spec.description, "无 front-matter 兜底")
            self.assertEqual(spec.triggers, [])

    def test_invalid_name_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = _write(
                Path(tmp) / "bad" / "SKILL.md",
                """\
                ---
                name: BadName
                ---

                正文
                """,
            )
            with self.assertRaises(SkillError):
                parse_skill_md(md)

    def test_invalid_triggers_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = _write(
                Path(tmp) / "bad_triggers" / "SKILL.md",
                """\
                ---
                name: bad_triggers
                description: ok
                triggers: not_a_list
                ---

                正文
                """,
            )
            with self.assertRaises(SkillError):
                parse_skill_md(md)

    def test_empty_body_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = _write(
                Path(tmp) / "empty" / "SKILL.md",
                """\
                ---
                name: empty
                description: ok
                ---

                """,
            )
            with self.assertRaises(SkillError):
                parse_skill_md(md)

    def test_discover_skills_skips_invalid_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "alpha" / "SKILL.md",
                """\
                ---
                name: alpha
                description: ok
                ---
                正文 alpha
                """,
            )
            _write(
                root / "beta" / "SKILL.md",
                """\
                ---
                name: alpha
                description: 重复名
                ---
                重复 skill
                """,
            )
            _write(
                root / "broken" / "SKILL.md",
                """\
                ---
                name: BadCase
                ---
                正文
                """,
            )

            specs = discover_skills([root])
            names = sorted(s.name for s in specs)
            self.assertEqual(names, ["alpha"])

    def test_build_skill_catalog_format(self) -> None:
        specs = [
            SkillSpec(name="alpha", description="阿尔法描述", body="x"),
            SkillSpec(name="bravo", description="布拉沃", body="y"),
        ]
        cat = build_skill_catalog(specs)
        self.assertIn("可用 Skills", cat)
        self.assertIn("`alpha`", cat)
        self.assertIn("阿尔法描述", cat)
        self.assertIn("`bravo`", cat)
        self.assertEqual(build_skill_catalog([]), "")

    def test_project_robot_cad_design_skill_is_discoverable(self) -> None:
        specs = discover_skills([PROJECT_ROOT / "skills"])
        by_name = {spec.name: spec for spec in specs}

        self.assertIn("robot_cad_design", by_name)
        spec = by_name["robot_cad_design"]
        self.assertIn("机器人 CAD 设计方法论", spec.description)
        self.assertIn("robot_design_agent", spec.body)
        self.assertIn("MechanicalLayout", spec.body)


if __name__ == "__main__":
    unittest.main()
