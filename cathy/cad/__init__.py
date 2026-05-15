"""CAD Agent 产品层。

包含三层契约（part / module / machine）+ 项目目录读写 API + jsonschema 校验。
对外暴露：

- ``Project.open(root)`` —— 打开/创建一个用户指定根目录的 CAD 项目。
- ``schema.*`` —— 各类 yaml 的 jsonschema 定义与校验入口。

详见 ``ROADMAP_CAD_AGENT_v0.5.md`` §3。
"""

from __future__ import annotations

from cathy.cad.project import Project, ProjectError  # noqa: F401
from cathy.cad.schema import (  # noqa: F401
    ASSEMBLY_META_SCHEMA,
    MACHINE_META_SCHEMA,
    MODULE_META_SCHEMA,
    PART_META_SCHEMA,
    QA_ROUND_SCHEMA,
    STAGE_REPORT_SCHEMA,
    SCHEMA_VERSION,
    SchemaError,
    ValidationIssue,
    validate_assembly_meta,
    validate_machine_meta,
    validate_module_meta,
    validate_part_meta,
    validate_qa_round,
    validate_stage_report,
)

__all__ = [
    "Project",
    "ProjectError",
    "SCHEMA_VERSION",
    "SchemaError",
    "ValidationIssue",
    "PART_META_SCHEMA",
    "ASSEMBLY_META_SCHEMA",
    "MODULE_META_SCHEMA",
    "MACHINE_META_SCHEMA",
    "STAGE_REPORT_SCHEMA",
    "QA_ROUND_SCHEMA",
    "validate_part_meta",
    "validate_assembly_meta",
    "validate_module_meta",
    "validate_machine_meta",
    "validate_stage_report",
    "validate_qa_round",
]
