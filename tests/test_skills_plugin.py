"""SkillsPlugin（read_skill 工具）测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.plugins.registry import PluginRegistry  # noqa: E402
from cathy.skills import SkillSpec, SkillsPlugin, build_skills_manifest  # noqa: E402


class SkillsPluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.skills = [
            SkillSpec(name="summarize", description="摘要", body="正文-summarize"),
            SkillSpec(name="write_blog", description="博客", body="正文-write_blog"),
        ]
        self.plugin = SkillsPlugin(skills=self.skills)
        self.plugin.initialize({})

    def test_read_existing_skill_returns_full_body(self) -> None:
        out = self.plugin.execute("read_skill", {"name": "summarize"})
        self.assertIn("# Skill: summarize", out)
        self.assertIn("正文-summarize", out)

    def test_read_unknown_returns_helpful_error(self) -> None:
        out = self.plugin.execute("read_skill", {"name": "nope"})
        self.assertIn("[ToolError:read_skill]", out)
        self.assertIn("当前可用", out)
        self.assertIn("summarize", out)
        self.assertIn("write_blog", out)

    def test_empty_name_returns_error(self) -> None:
        out = self.plugin.execute("read_skill", {"name": ""})
        self.assertIn("[ToolError:read_skill]", out)

    def test_registry_routes_through_schema_validation(self) -> None:
        registry = PluginRegistry(plugins_dirs=[])
        registry.register_internal_plugin(build_skills_manifest(), self.plugin)

        # 缺 name 应当被 schema 拦截，不进入 plugin
        bad = registry.call("read_skill", {})
        self.assertTrue(bad.startswith("[ToolError:read_skill]"))

        good = registry.call("read_skill", {"name": "write_blog"})
        self.assertIn("正文-write_blog", good)

    def test_schema_only_one_function_tool(self) -> None:
        registry = PluginRegistry(plugins_dirs=[])
        registry.register_internal_plugin(build_skills_manifest(), self.plugin)
        schemas = registry.openai_schemas()
        names = [s["function"]["name"] for s in schemas]
        self.assertEqual(names, ["read_skill"])


if __name__ == "__main__":
    unittest.main()
