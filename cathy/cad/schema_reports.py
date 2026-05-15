"""Stage Gate 报告 + QA 轮次报告 schema。"""

from __future__ import annotations

from typing import Any

from cathy.cad.schema_common import TIMESTAMP_PATTERN, run_validator

FINDING = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["blocker", "major", "minor", "nit"]},
        "location": {"type": "string", "minLength": 1},
        "issue": {"type": "string", "minLength": 1},
        "suggestion": {"type": "string"},
        "auto_patch": {
            "type": "object",
            "properties": {"file": {"type": "string"}, "diff": {"type": "string"}},
            "additionalProperties": True,
        },
    },
    "required": ["severity", "location", "issue"],
    "additionalProperties": True,
}

STAGE_REPORT_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "G<N>.report.yaml",
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "gate": {"type": "string", "enum": ["G1", "G2", "G3", "G4"]},
        "stage": {"type": "string", "enum": ["S1", "S2", "S3", "S4"]},
        "passed": {"type": "boolean"},
        "target_dir": {"type": "string"},
        "checks_run": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "predicate": {"type": "string", "minLength": 1},
                    "passed": {"type": "boolean"},
                    "detail": {"type": "string"},
                },
                "required": ["predicate", "passed"],
                "additionalProperties": True,
            },
        },
        "failing_predicates": {"type": "array", "items": {"type": "string"}},
        "checked_at": {"type": "string", "pattern": TIMESTAMP_PATTERN},
        "notes": {"type": "string"},
    },
    "required": ["gate", "stage", "passed", "checks_run", "checked_at"],
    "additionalProperties": True,
}

QA_ROUND_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "qa_history/round_NNN.yaml",
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "qa_round": {"type": "integer", "minimum": 1},
        "stage": {"type": "string", "enum": ["S1", "S2", "S3", "S4"]},
        "scope": {"type": "string", "enum": ["part", "module", "machine", "assembly"]},
        "target": {"type": "string", "minLength": 1},
        "verdict": {"type": "string", "enum": ["pass", "fail", "needs_clarification"]},
        "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "findings": {"type": "array", "items": FINDING},
        "checks_run": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "gate": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "failing_predicates": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["gate", "passed"],
                "additionalProperties": True,
            },
        },
        "spec_alignment": {"type": "object"},
        "next_action": {"type": "string", "enum": ["revise", "escalate_to_human", "continue"]},
        "notes": {"type": "string"},
        "created_at": {"type": "string", "pattern": TIMESTAMP_PATTERN},
    },
    "required": ["qa_round", "stage", "scope", "target", "verdict", "created_at"],
    "additionalProperties": True,
}


def validate_stage_report(data: Any, *, source: str = "<inline>") -> None:
    run_validator(STAGE_REPORT_SCHEMA, data, source=source)


def validate_qa_round(data: Any, *, source: str = "<inline>") -> None:
    run_validator(QA_ROUND_SCHEMA, data, source=source)

