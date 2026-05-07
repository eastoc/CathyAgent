"""Skills 子系统 —— 静态能力模板（提示词 + 约束 + 步骤）。

设计哲学（对齐 Claude Code）：
- Skill 是**纯文档**：Markdown 文件，描述"这类任务该怎么做"。
- Skill 不是 agent，不会"启动"；是任何 agent 都可以加载的指令资源。
- Progressive disclosure：启动时只把 (name, description) 拼到 system prompt 作为目录；
  agent 真正决定使用某个 skill 时，调 `read_skill(name)` 拉取全文。

公开 API：
- SkillSpec / SkillError       —— 数据模型与异常
- discover_skills              —— 扫描目录得到 SkillSpec 列表
- build_skill_catalog          —— 生成 system prompt 用的目录字符串
- SkillsPlugin / build_skills_manifest —— `read_skill` 工具
"""

from .loader import build_skill_catalog, discover_skills
from .manifest import SkillError, SkillSpec, parse_skill_md
from .plugin import SkillsPlugin, build_skills_manifest

__all__ = [
    "SkillError",
    "SkillSpec",
    "SkillsPlugin",
    "build_skill_catalog",
    "build_skills_manifest",
    "discover_skills",
    "parse_skill_md",
]
