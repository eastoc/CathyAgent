"""CAD schema 共享常量、片段与通用校验器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError

SCHEMA_VERSION = "0.5.0"

# 命名约束：小写字母开头 + 小写数字下划线。
ID_PATTERN = r"^[a-z][a-z0-9_]*$"

# ISO 8601（宽松）
TIMESTAMP_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)

VEC3 = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "z": {"type": "number"},
    },
    "required": ["x", "y", "z"],
    "additionalProperties": False,
}

TOPOLOGY = {
    "type": "object",
    "properties": {
        "vertices": {"type": "integer", "minimum": 0},
        "edges": {"type": "integer", "minimum": 0},
        "faces": {"type": "integer", "minimum": 0},
        "solids": {"type": "integer", "minimum": 0},
    },
    "required": ["vertices", "edges", "faces", "solids"],
    "additionalProperties": False,
}

SOURCE_COMMON = {
    "type": "object",
    "properties": {
        "generator": {"type": "string", "minLength": 1},
        "generator_sha": {"type": "string"},
        "params_hash": {"type": "string"},
        "freecad_version": {"type": "string"},
        "build123d_version": {"type": "string"},
    },
    "required": ["generator"],
    "additionalProperties": True,
}

INTERFACE = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": r"^if_[a-z0-9_]+$"},
        "type": {
            "type": "string",
            "enum": ["bolt_circle", "face", "shaft", "bore", "keyway", "dowel", "custom"],
        },
        "plane": {"type": "string"},
        "diameter": {"type": "number", "exclusiveMinimum": 0},
        "bolts": {"type": "integer", "minimum": 0},
        "bolt_size": {"type": "string", "pattern": r"^M\d+(\.\d+)?$"},
        "notes": {"type": "string"},
    },
    "required": ["id", "type"],
    "additionalProperties": True,
}

LIBRARY_REF = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["standard", "motor", "reducer", "bearing", "other"],
        },
        "gb": {"type": "string"},
        "model": {"type": "string"},
        "size": {"type": "string"},
        "quantity": {"type": "integer", "minimum": 1},
        "placeholder": {"type": "boolean"},
    },
    "required": ["kind", "quantity"],
    "additionalProperties": True,
}

MATERIAL = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "density_kg_m3": {"type": "number", "exclusiveMinimum": 0},
    },
    "required": ["name"],
    "additionalProperties": True,
}


@dataclass(frozen=True)
class ValidationIssue:
    source: str
    path: str
    message: str

    def __str__(self) -> str:  # pragma: no cover
        loc = f"{self.source}:{self.path}" if self.path else self.source
        return f"{loc} → {self.message}"


class SchemaError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = list(issues)
        super().__init__(self._format(issues))

    @staticmethod
    def _format(issues: list[ValidationIssue]) -> str:
        if not issues:
            return "schema validation failed"
        return "\n".join(f"  - {iss}" for iss in issues)


def format_path(err: ValidationError) -> str:
    parts: list[str] = []
    for item in err.absolute_path:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts)


def run_validator(schema: dict[str, Any], data: Any, *, source: str) -> None:
    """统一校验入口：纯 jsonschema。"""
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    raise SchemaError(
        [ValidationIssue(source=source, path=format_path(e), message=e.message) for e in errors]
    )

