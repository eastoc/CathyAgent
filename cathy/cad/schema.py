"""CAD schema 聚合入口（已按实体拆分）。

拆分结构：
- ``schema_part.py``：零件（part.meta.yaml）
- ``schema_assembly.py``：装配体（assembly；兼容 module/machine）
- ``schema_reports.py``：Stage Gate 报告 + QA 轮次报告
- ``schema_common.py``：共享片段、错误类型、validator 运行器

对外继续保持兼容导出：``validate_module_meta`` / ``validate_machine_meta`` 不变。
"""

from __future__ import annotations

from cathy.cad.schema_assembly import (
    ASSEMBLY_META_SCHEMA,
    validate_assembly_meta,
    validate_machine_meta,
    validate_module_meta,
)
from cathy.cad.schema_common import SCHEMA_VERSION, SchemaError, ValidationIssue
from cathy.cad.schema_part import PART_META_SCHEMA, validate_part_meta
from cathy.cad.schema_reports import QA_ROUND_SCHEMA, STAGE_REPORT_SCHEMA, validate_qa_round, validate_stage_report

# 兼容导出：旧名字仍可用（module/machine 本质归一到 assembly）。
MODULE_META_SCHEMA = ASSEMBLY_META_SCHEMA
MACHINE_META_SCHEMA = ASSEMBLY_META_SCHEMA


__all__ = [
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
