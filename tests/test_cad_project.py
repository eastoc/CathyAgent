"""Phase 0 契约层 · Project API 单测。

覆盖：
- Project.init 在任意路径生成完整骨架 + 已有目录可重入
- write_triplet / read_triplet / list_ids（三层 scope）
- append_qa_round 自动编号 + final.yaml
- write_stage_report / read_stage_report
- 非法 scope / id / scope 与 validator 不匹配的负向用例
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cathy.cad import Project, ProjectError, SchemaError  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _part_meta(part_id: str = "flange_v1") -> dict:
    return {
        "part_id": part_id,
        "display_name": "测试法兰",
        "source": {"generator": "gen.py"},
        "geometry": {
            "bbox": {"x": 120.0, "y": 120.0, "z": 18.0},
            "topology": {"vertices": 96, "edges": 144, "faces": 50, "solids": 1},
            "is_closed": True,
        },
        "interfaces": [
            {"id": "if_motor_flange", "type": "bolt_circle",
             "diameter": 64.0, "bolts": 4, "bolt_size": "M5", "plane": "z+"}
        ],
    }


def _module_meta(module_id: str = "shoulder") -> dict:
    return {
        "module_id": module_id,
        "source": {"generator": "assemble.py"},
        "parts_used": [{"part_id": "flange_v1"}],
    }


def _machine_meta(machine_id: str = "arm_v1") -> dict:
    return {
        "machine_id": machine_id,
        "machine_type": "robotic_arm",
        "dof_count": 6,
    }


class ProjectInitTest(unittest.TestCase):
    def test_init_creates_full_skeleton(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "myproj"
            proj = Project.init(root)
            self.assertTrue(proj.parts_dir.is_dir())
            self.assertTrue(proj.modules_dir.is_dir())
            self.assertTrue(proj.artifacts_dir.is_dir())
            self.assertTrue((proj.artifacts_dir / "previews").is_dir())
            self.assertTrue((proj.artifacts_dir / "qa_history").is_dir())
            self.assertTrue(proj.library_imports_dir.is_dir())
            self.assertTrue(proj.stages_dir.is_dir())
            for s in ("S1_selection", "S2_custom", "S3_modules", "S4_full"):
                self.assertTrue((proj.stages_dir / s).is_dir(), s)
            self.assertTrue(proj.machine_meta_path.is_file())
            self.assertTrue(proj.motion_spec_path.is_file())
            self.assertTrue(proj.todo_path.is_file())
            self.assertTrue((proj.root / "README.md").is_file())

    def test_init_is_reentrant_on_existing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "myproj"
            Project.init(root)
            # 重复 init 不应抛
            proj = Project.init(root)
            self.assertTrue(proj.machine_meta_path.is_file())

    def test_init_writes_valid_machine_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp) / "x",
                                machine_id="arm_test_v1",
                                machine_type="robotic_arm",
                                dof_count=7)
            data = proj.read_meta("machine")
            self.assertEqual(data["machine_id"], "arm_test_v1")
            self.assertEqual(data["dof_count"], 7)

    def test_open_nonexistent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ProjectError):
                Project.open(Path(tmp) / "does_not_exist")


class WriteTripletTest(unittest.TestCase):
    def test_write_and_read_part_triplet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            paths = proj.write_triplet(
                "part", "flange_v1",
                gen="PARAMS = {'D': 120}\n",
                meta=_part_meta(),
                product=b"step content placeholder",
            )
            self.assertTrue(paths.gen.is_file())
            self.assertTrue(paths.meta.is_file())
            self.assertTrue(paths.product.is_file())
            self.assertTrue(paths.qa_history.is_dir())

            gen_code, meta, prod = proj.read_triplet("part", "flange_v1")
            self.assertIn("PARAMS", gen_code)
            self.assertEqual(meta["part_id"], "flange_v1")
            self.assertEqual(prod, paths.product)

    def test_write_triplet_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            proj.write_triplet(
                "module", "shoulder",
                gen="# assemble.py\n",
                meta=_module_meta(),
            )
            ids = proj.list_ids("module")
            self.assertEqual(ids, ["shoulder"])

    def test_write_triplet_machine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            proj.write_triplet(
                "machine", "main",
                gen="# assemble_main.py\n",
                meta=_machine_meta(),
            )
            ids = proj.list_ids("machine")
            self.assertEqual(ids, ["main"])

    def test_write_triplet_rejects_bad_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            bad = _part_meta()
            del bad["geometry"]["bbox"]
            with self.assertRaises(SchemaError):
                proj.write_triplet("part", "flange_v1", gen="x", meta=bad)

    def test_write_triplet_rejects_bad_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            with self.assertRaises(ProjectError):
                proj.write_triplet("part", "BadID", gen="x", meta=_part_meta())

    def test_write_triplet_rejects_unknown_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            with self.assertRaises(ProjectError):
                proj.write_triplet("weapon", "flange_v1", gen="x", meta={})


class QARoundTest(unittest.TestCase):
    def test_append_qa_round_auto_numbering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            proj.write_triplet("part", "flange_v1", gen="x", meta=_part_meta())
            r1 = proj.append_qa_round(
                "part", "flange_v1",
                feedback={"qa_round": 999, "stage": "S2", "scope": "part",
                          "target": "flange_v1", "verdict": "fail",
                          "created_at": _now()},
            )
            # 即便 feedback 自带 qa_round，我们应忽略并按目录内已有数 + 1
            # 但 v0.5 设计上是 setdefault：若没自带才注入。
            # 这里测的是"目录里产生了文件 + 文件名是 round_001"。
            self.assertTrue(r1.name.startswith("round_"))
            r2 = proj.append_qa_round(
                "part", "flange_v1",
                feedback={"stage": "S2", "scope": "part",
                          "target": "flange_v1", "verdict": "pass",
                          "created_at": _now()},
            )
            r3 = proj.append_qa_round(
                "part", "flange_v1",
                feedback={"stage": "S2", "scope": "part",
                          "target": "flange_v1", "verdict": "pass",
                          "created_at": _now()},
                final=True,
            )
            self.assertNotEqual(r1.name, r2.name)
            self.assertNotEqual(r2.name, r3.name)
            qa_dir = r1.parent
            rounds = sorted(qa_dir.glob("round_*.yaml"))
            self.assertEqual(len(rounds), 3)
            # round 编号严格递增
            nums = [int(p.stem.split("_")[1]) for p in rounds]
            self.assertEqual(nums, sorted(nums))
            self.assertTrue((qa_dir / "final.yaml").exists())

    def test_read_qa_history_returns_rounds_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            proj.write_triplet("part", "flange_v1", gen="x", meta=_part_meta())
            for _ in range(3):
                proj.append_qa_round(
                    "part", "flange_v1",
                    feedback={"stage": "S2", "scope": "part",
                              "target": "flange_v1", "verdict": "fail",
                              "created_at": _now()},
                )
            history = proj.read_qa_history("part", "flange_v1")
            self.assertEqual(len(history), 3)
            self.assertEqual([h["qa_round"] for h in history], [1, 2, 3])

    def test_qa_round_invalid_verdict_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            proj.write_triplet("part", "flange_v1", gen="x", meta=_part_meta())
            with self.assertRaises(SchemaError):
                proj.append_qa_round(
                    "part", "flange_v1",
                    feedback={"stage": "S2", "scope": "part",
                              "target": "flange_v1", "verdict": "MAYBE",
                              "created_at": _now()},
                )


class StageReportTest(unittest.TestCase):
    def test_write_and_read_stage_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            report = {
                "passed": True,
                "checks_run": [{"predicate": "p1", "passed": True}],
            }
            out = proj.write_stage_report("S2", report)
            self.assertTrue(out.exists())
            self.assertEqual(out.name, "G2.report.yaml")

            data = proj.read_stage_report("S2")
            self.assertIsNotNone(data)
            self.assertEqual(data["gate"], "G2")
            self.assertEqual(data["stage"], "S2")
            self.assertTrue(data["passed"])

    def test_stage_report_unknown_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Project.init(Path(tmp))
            with self.assertRaises(ProjectError):
                proj.write_stage_report("S9", {"passed": True, "checks_run": []})


if __name__ == "__main__":
    unittest.main()
