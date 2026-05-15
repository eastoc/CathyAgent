"""Phase 0 契约层 · CLI 工具单测。

覆盖：
- ``tools.cad.init_project.main`` 在任意路径 init 成功 + 退出码 0
- ``tools.cad.validate_project.main`` 对完整项目全绿 + 退出码 0
- validate 对故意缺字段 / 缺目录 / 错误 yaml 给出"具体路径 + 字段名"诊断
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from cathy.cad import Project  # noqa: E402
from tools.cad import init_project as init_mod  # noqa: E402
from tools.cad import validate_project as validate_mod  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _part_meta(part_id: str = "flange_v1") -> dict:
    return {
        "part_id": part_id,
        "source": {"generator": "gen.py"},
        "geometry": {
            "bbox": {"x": 120.0, "y": 120.0, "z": 18.0},
            "topology": {"vertices": 96, "edges": 144, "faces": 50, "solids": 1},
            "is_closed": True,
        },
    }


class InitProjectCLITest(unittest.TestCase):
    def test_init_succeeds_on_empty_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "empty_demo"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_mod.main([str(target)])
            self.assertEqual(code, 0)
            self.assertTrue(target.is_dir())
            self.assertTrue((target / "parts").is_dir())
            self.assertTrue((target / "machine.meta.yaml").is_file())
            self.assertIn("init_project", buf.getvalue())

    def test_init_with_custom_machine_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_mod.main([
                    str(target),
                    "--machine-id", "arm_custom_v1",
                    "--machine-type", "robotic_arm",
                    "--dof", "5",
                    "--no-readme",
                ])
            self.assertEqual(code, 0)
            data = yaml.safe_load((target / "machine.meta.yaml").read_text())
            self.assertEqual(data["machine_id"], "arm_custom_v1")
            self.assertEqual(data["dof_count"], 5)
            self.assertFalse((target / "README.md").exists())

    def test_init_is_reentrant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "again"
            with redirect_stdout(io.StringIO()):
                init_mod.main([str(target)])
                code = init_mod.main([str(target)])
            self.assertEqual(code, 0)


class ValidateProjectCLITest(unittest.TestCase):
    def test_validate_clean_project_is_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "clean"
            Project.init(target)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = validate_mod.main([str(target)])
            self.assertEqual(code, 0, msg=buf.getvalue())
            out = buf.getvalue()
            self.assertIn("errors=0", out)

    def test_validate_missing_required_dir_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "broken"
            proj = Project.init(target)
            # 故意删 parts/
            import shutil
            shutil.rmtree(proj.parts_dir)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = validate_mod.main([str(target)])
            out = buf.getvalue()
            self.assertEqual(code, 1)
            self.assertIn("ERROR", out)
            self.assertIn("parts/", out)
            self.assertIn("目录缺失", out)

    def test_validate_missing_machine_meta_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bad_meta"
            proj = Project.init(target)
            # 故意把 machine.meta 的 dof_count 删了
            data = yaml.safe_load(proj.machine_meta_path.read_text())
            del data["dof_count"]
            proj.machine_meta_path.write_text(yaml.safe_dump(data, allow_unicode=True))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = validate_mod.main([str(target)])
            out = buf.getvalue()
            self.assertEqual(code, 1)
            # 报错必须能指出 dof_count
            self.assertIn("dof_count", out)
            self.assertIn(str(proj.machine_meta_path), out)

    def test_validate_bad_part_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bad_part"
            proj = Project.init(target)
            proj.write_triplet("part", "flange_v1",
                               gen="x\n", meta=_part_meta())
            # 故意把 part.meta.yaml 改坏：删 bbox
            paths = proj.triplet_paths("part", "flange_v1")
            data = yaml.safe_load(paths.meta.read_text())
            del data["geometry"]["bbox"]
            paths.meta.write_text(yaml.safe_dump(data, allow_unicode=True))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = validate_mod.main([str(target)])
            out = buf.getvalue()
            self.assertEqual(code, 1)
            self.assertIn("bbox", out)
            self.assertIn(str(paths.meta), out)

    def test_validate_part_missing_qa_history_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "no_qa"
            proj = Project.init(target)
            proj.write_triplet("part", "flange_v1",
                               gen="x\n", meta=_part_meta())
            qa = proj.parts_dir / "flange_v1" / "qa_history"
            import shutil
            shutil.rmtree(qa)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = validate_mod.main([str(target)])
            out = buf.getvalue()
            self.assertEqual(code, 1)
            self.assertIn("qa_history", out)

    def test_validate_bad_todo_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bad_todo"
            proj = Project.init(target)
            proj.todo_path.write_text("{not valid json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = validate_mod.main([str(target)])
            out = buf.getvalue()
            self.assertEqual(code, 1)
            self.assertIn(".cad_todo.json", out)

    def test_validate_nonexistent_path_returns_2(self) -> None:
        # CLI 写 stderr 是预期行为；这里只验证 exit code。
        from contextlib import redirect_stderr
        buf, err_buf = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err_buf):
            code = validate_mod.main(["/tmp/this/does/not/exist/xyz/agent_test"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
