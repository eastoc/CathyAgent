"""零件（part.meta.yaml）schema。"""

from __future__ import annotations

from typing import Any

from cathy.cad.schema_common import (
    ID_PATTERN,
    INTERFACE,
    LIBRARY_REF,
    MATERIAL,
    SOURCE_COMMON,
    TIMESTAMP_PATTERN,
    TOPOLOGY,
    VEC3,
    run_validator,
)

PART_META_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "part.meta.yaml",
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "part_id": {"type": "string", "pattern": ID_PATTERN},
        "display_name": {"type": "string"},
        "source": SOURCE_COMMON,
        "geometry": {
            "type": "object",
            "properties": {
                "bbox": VEC3,
                "center": VEC3,
                "volume_mm3": {"type": "number", "minimum": 0},
                "topology": TOPOLOGY,
                "is_closed": {"type": "boolean"},
            },
            "required": ["bbox", "topology", "is_closed"],
            "additionalProperties": True,
        },
        "interfaces": {"type": "array", "items": INTERFACE},
        "material": MATERIAL,
        "library_refs": {"type": "array", "items": LIBRARY_REF},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "created_at": {"type": "string", "pattern": TIMESTAMP_PATTERN},
    },
    "required": ["part_id", "source", "geometry", "created_at"],
    "additionalProperties": True,
}


def validate_part_meta(data: Any, *, source: str = "<inline>") -> None:
    run_validator(PART_META_SCHEMA, data, source=source)

