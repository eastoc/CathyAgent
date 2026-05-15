"""Phase 0 契约层 · schema 单测。

覆盖：
- part / module / machine meta 三层的最小合法集 + 关键缺字段
- stage report (G1–G4) schema
- QA round schema
- SchemaError.issues 是否带"具体路径 + 字段"
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.cad import schema  # noqa: E402
from cathy.cad.schema import (  # noqa: E402
    SchemaError,
    validate_assembly_meta,
    validate_machine_meta,
    validate_module_meta,
    validate_part_meta,
    validate_qa_round,
    validate_stage_report,
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _minimal_part() -> dict:
    return {
        "part_id": "shoulder_flange_v1",
        "source": {"generator": "gen.py"},
        "geometry": {
            "bbox": {"x": 120.0, "y": 120.0, "z": 18.0},
            "topology": {"vertices": 96, "edges": 144, "faces": 50, "solids": 1},
            "is_closed": True,
        },
        "created_at": _now(),
    }


def _minimal_module() -> dict:
    return {
        "module_id": "shoulder",
        "source": {"generator": "assemble.py"},
        "parts_used": [{"part_id": "shoulder_flange_v1"}],
        "created_at": _now(),
    }


def _minimal_machine() -> dict:
    return {
        "machine_id": "arm_6dof_v1",
        "machine_type": "robotic_arm",
        "dof_count": 6,
        "created_at": _now(),
    }


def _minimal_stage_report() -> dict:
    return {
        "gate": "G2",
        "stage": "S2",
        "passed": True,
        "checks_run": [
            {"predicate": "triplet_complete", "passed": True},
            {"predicate": "bbox_match", "passed": True},
        ],
        "checked_at": _now(),
    }


def _minimal_qa_round() -> dict:
    return {
        "qa_round": 1,
        "stage": "S2",
        "scope": "part",
        "target": "shoulder_flange_v1",
        "verdict": "pass",
        "created_at": _now(),
    }


class PartMetaSchemaTest(unittest.TestCase):
    def test_minimal_part_is_valid(self) -> None:
        validate_part_meta(_minimal_part(), source="<inline>")

    def test_missing_required_field_reports_specific_path(self) -> None:
        data = _minimal_part()
        del data["geometry"]["bbox"]
        with self.assertRaises(SchemaError) as ctx:
            validate_part_meta(data, source="part.meta.yaml")
        joined = "\n".join(str(i) for i in ctx.exception.issues)
        # 路径应能定位到 geometry 段
        self.assertIn("geometry", joined)
        self.assertTrue(any("bbox" in i.message for i in ctx.exception.issues))

    def test_bad_part_id_pattern(self) -> None:
        data = _minimal_part()
        data["part_id"] = "ShoulderFlange"  # 大写、CamelCase 应被拒
        with self.assertRaises(SchemaError):
            validate_part_meta(data)

    def test_bad_topology_negative(self) -> None:
        data = _minimal_part()
        data["geometry"]["topology"]["faces"] = -1
        with self.assertRaises(SchemaError) as ctx:
            validate_part_meta(data)
        self.assertTrue(any("faces" in i.path for i in ctx.exception.issues))

    def test_interface_id_must_start_with_if_prefix(self) -> None:
        data = _minimal_part()
        data["interfaces"] = [
            {"id": "motor_flange", "type": "bolt_circle"},  # 缺 if_ 前缀
        ]
        with self.assertRaises(SchemaError):
            validate_part_meta(data)

    def test_interface_minimal_legal(self) -> None:
        data = _minimal_part()
        data["interfaces"] = [
            {"id": "if_motor_flange", "type": "bolt_circle", "diameter": 64.0,
             "bolts": 4, "bolt_size": "M5", "plane": "z+"}
        ]
        validate_part_meta(data)

    def test_library_ref_minimal(self) -> None:
        data = _minimal_part()
        data["library_refs"] = [
            {"kind": "standard", "gb": "GB/T 70.1-2008", "size": "M5x16", "quantity": 4}
        ]
        validate_part_meta(data)


class ModuleMetaSchemaTest(unittest.TestCase):
    def test_minimal_module_is_valid(self) -> None:
        validate_module_meta(_minimal_module())

    def test_module_missing_parts_used_fails(self) -> None:
        data = _minimal_module()
        del data["parts_used"]
        with self.assertRaises(SchemaError):
            validate_module_meta(data)

    def test_joint_type_enum(self) -> None:
        data = _minimal_module()
        data["joints"] = [{"id": "j1", "type": "screw"}]  # 'screw' 不在 enum
        with self.assertRaises(SchemaError):
            validate_module_meta(data)

    def test_joint_revolute_is_legal(self) -> None:
        data = _minimal_module()
        data["joints"] = [
            {"id": "j1", "type": "revolute", "parent": "base", "child": "shoulder",
             "axis": {"x": 0, "y": 0, "z": 1}}
        ]
        validate_module_meta(data)


class AssemblyMetaSchemaTest(unittest.TestCase):
    def test_new_assembly_shape_valid(self) -> None:
        data = {
            "assembly_id": "wrist_pack",
            "assembly_kind": "module",
            "source": {"generator": "assemble.py"},
            "components_used": [
                {"kind": "part", "ref_id": "flange_v1"},
            ],
            "created_at": _now(),
        }
        validate_assembly_meta(data)

    def test_legacy_module_shape_still_valid(self) -> None:
        validate_assembly_meta(_minimal_module(), expected_kind="module")

    def test_kind_mismatch_rejected(self) -> None:
        data = {
            "assembly_id": "main",
            "assembly_kind": "module",
            "created_at": _now(),
        }
        with self.assertRaises(ValueError):
            validate_assembly_meta(data, expected_kind="machine")


class MachineMetaSchemaTest(unittest.TestCase):
    def test_minimal_machine_is_valid(self) -> None:
        validate_machine_meta(_minimal_machine())

    def test_missing_dof_count_fails(self) -> None:
        data = _minimal_machine()
        del data["dof_count"]
        with self.assertRaises(SchemaError):
            validate_machine_meta(data)

    def test_negative_dof_count_fails(self) -> None:
        data = _minimal_machine()
        data["dof_count"] = -1
        with self.assertRaises(SchemaError):
            validate_machine_meta(data)


class StageReportSchemaTest(unittest.TestCase):
    def test_minimal_stage_report(self) -> None:
        validate_stage_report(_minimal_stage_report())

    def test_invalid_gate(self) -> None:
        data = _minimal_stage_report()
        data["gate"] = "G9"
        with self.assertRaises(SchemaError):
            validate_stage_report(data)

    def test_checks_run_missing_predicate(self) -> None:
        data = _minimal_stage_report()
        data["checks_run"] = [{"passed": True}]
        with self.assertRaises(SchemaError):
            validate_stage_report(data)


class QARoundSchemaTest(unittest.TestCase):
    def test_minimal_qa_round_pass(self) -> None:
        validate_qa_round(_minimal_qa_round())

    def test_qa_round_with_findings(self) -> None:
        data = _minimal_qa_round()
        data["verdict"] = "fail"
        data["findings"] = [
            {"severity": "blocker", "location": "gen.py:L42",
             "issue": "中心孔 Φ24 与 BOM Φ20 不一致",
             "suggestion": "改成 20.0",
             "auto_patch": {"file": "gen.py", "diff": "- 24.0\n+ 20.0"}}
        ]
        validate_qa_round(data)

    def test_invalid_severity_rejected(self) -> None:
        data = _minimal_qa_round()
        data["findings"] = [
            {"severity": "WARNING", "location": "gen.py", "issue": "x"}
        ]
        with self.assertRaises(SchemaError):
            validate_qa_round(data)

    def test_score_out_of_range(self) -> None:
        data = _minimal_qa_round()
        data["score"] = 1.5
        with self.assertRaises(SchemaError):
            validate_qa_round(data)


class SchemaErrorReportingTest(unittest.TestCase):
    """SchemaError 必须把 source + 字段路径都带上。"""

    def test_issues_carry_source_and_path(self) -> None:
        bad = _minimal_part()
        bad["geometry"]["topology"]["faces"] = "not-a-number"
        with self.assertRaises(SchemaError) as ctx:
            validate_part_meta(bad, source="/tmp/x/part.meta.yaml")
        self.assertTrue(len(ctx.exception.issues) >= 1)
        iss = ctx.exception.issues[0]
        self.assertEqual(iss.source, "/tmp/x/part.meta.yaml")
        self.assertIn("topology", iss.path)
        self.assertIn("faces", iss.path)

    def test_schema_version_constant(self) -> None:
        self.assertIsInstance(schema.SCHEMA_VERSION, str)
        self.assertTrue(schema.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
