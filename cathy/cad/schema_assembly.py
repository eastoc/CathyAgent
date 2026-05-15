"""装配体 schema。

核心建模：
- module 与 machine 本质都是 assembly；
- 对外提供 validate_assembly_meta；
- 同时保留 validate_module_meta / validate_machine_meta 兼容旧调用。
"""

from __future__ import annotations

from typing import Any

from cathy.cad.schema_common import (
    ID_PATTERN,
    LIBRARY_REF,
    SchemaError,
    SOURCE_COMMON,
    TIMESTAMP_PATTERN,
    ValidationIssue,
    VEC3,
    run_validator,
)

COMPONENT_USED = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["part", "assembly", "module"]},
        "ref_id": {"type": "string", "pattern": ID_PATTERN},
        "instance_id": {"type": "string"},
        "transform": {
            "type": "object",
            "properties": {
                "translate": VEC3,
                "rotate_deg": VEC3,
            },
            "additionalProperties": True,
        },
    },
    "required": ["ref_id"],
    "additionalProperties": True,
}

JOINT = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "type": {
            "type": "string",
            "enum": ["revolute", "prismatic", "fixed", "spherical", "planar"],
        },
        "parent": {"type": "string"},
        "child": {"type": "string"},
        "axis": VEC3,
        "limit_lower": {"type": "number"},
        "limit_upper": {"type": "number"},
    },
    "required": ["id", "type"],
    "additionalProperties": True,
}

ASSEMBLY_META_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "assembly.meta.yaml",
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "assembly_id": {"type": "string", "pattern": ID_PATTERN},
        "assembly_kind": {"type": "string", "enum": ["module", "machine"]},
        "display_name": {"type": "string"},
        "source": SOURCE_COMMON,
        "components_used": {"type": "array", "items": COMPONENT_USED},
        "library_refs": {"type": "array", "items": LIBRARY_REF},
        "joints": {"type": "array", "items": JOINT},
        "bom_rollup": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "identifier": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                },
                "required": ["kind", "identifier", "quantity"],
                "additionalProperties": True,
            },
        },
        "total_bom": {"type": "array", "items": LIBRARY_REF},
        "machine_type": {"type": "string", "minLength": 1},
        "dof_count": {"type": "integer", "minimum": 0},
        "chain_topology": {"type": "string"},
        "mass_estimate_kg": {"type": "number", "minimum": 0},
        "envelope": {
            "type": "object",
            "properties": {"bbox": VEC3},
            "required": ["bbox"],
            "additionalProperties": True,
        },
        "stages": {
            "type": "object",
            "properties": {
                "S1": {"type": "string"},
                "S2": {"type": "string"},
                "S3": {"type": "string"},
                "S4": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "created_at": {"type": "string", "pattern": TIMESTAMP_PATTERN},
    },
    "required": ["assembly_id", "assembly_kind", "created_at"],
    "additionalProperties": True,
}


def _normalize_legacy_assembly(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return data
    d = dict(data)
    # module -> assembly
    if "module_id" in d and "assembly_id" not in d:
        d["assembly_id"] = d["module_id"]
    if "parts_used" in d and "components_used" not in d:
        d["components_used"] = [{"kind": "part", "ref_id": p.get("part_id", "") , **{k:v for k,v in p.items() if k!="part_id"}} for p in d.get("parts_used", [])]
    # machine -> assembly
    if "machine_id" in d and "assembly_id" not in d:
        d["assembly_id"] = d["machine_id"]
    if "modules_used" in d and "components_used" not in d:
        d["components_used"] = [
            {"kind": "module", "ref_id": m.get("module_id", ""), **{k: v for k, v in m.items() if k != "module_id"}}
            for m in d.get("modules_used", [])
        ]
    return d


def validate_assembly_meta(
    data: Any,
    *,
    source: str = "<inline>",
    expected_kind: str | None = None,
) -> None:
    normalized = _normalize_legacy_assembly(data)
    if isinstance(normalized, dict):
        if expected_kind and "assembly_kind" not in normalized:
            normalized["assembly_kind"] = expected_kind
    run_validator(ASSEMBLY_META_SCHEMA, normalized, source=source)
    if expected_kind and isinstance(normalized, dict):
        if normalized.get("assembly_kind") != expected_kind:
            raise ValueError(
                f"{source}: assembly_kind={normalized.get('assembly_kind')!r}, expected {expected_kind!r}"
            )


def validate_module_meta(data: Any, *, source: str = "<inline>") -> None:
    normalized = _normalize_legacy_assembly(data)
    validate_assembly_meta(normalized, source=source, expected_kind="module")
    issues: list[ValidationIssue] = []
    if isinstance(normalized, dict):
        if "source" not in normalized:
            issues.append(ValidationIssue(source, "source", "'source' is a required property"))
        has_components = bool(normalized.get("components_used"))
        if not has_components:
            issues.append(
                ValidationIssue(
                    source,
                    "components_used",
                    "module assembly must include at least one component",
                )
            )
    if issues:
        raise SchemaError(issues)


def validate_machine_meta(data: Any, *, source: str = "<inline>") -> None:
    normalized = _normalize_legacy_assembly(data)
    validate_assembly_meta(normalized, source=source, expected_kind="machine")
    issues: list[ValidationIssue] = []
    if isinstance(normalized, dict):
        if "machine_type" not in normalized:
            issues.append(
                ValidationIssue(source, "machine_type", "'machine_type' is a required property")
            )
        if "dof_count" not in normalized:
            issues.append(
                ValidationIssue(source, "dof_count", "'dof_count' is a required property")
            )
    if issues:
        raise SchemaError(issues)

