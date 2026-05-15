"""``python -m tools.cad.validate_project <PATH>``：扫描并校验一个 CAD 项目。

输出每一条错误的"具体路径 + 字段名 + 原因"，方便人 / agent 一眼定位。退出码：

* 0  全绿
* 1  有 ERROR（schema 失败 / 缺关键文件）
* 2  CLI 参数错误 / 路径不存在

详见 ``ROADMAP_CAD_AGENT_v0.5.md`` §3.1 / Phase 0 验收。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# 允许直接 ``python tools/cad/validate_project.py`` 跑。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from cathy.cad import (  # noqa: E402
    Project,
    ProjectError,
    SchemaError,
    validate_machine_meta,
    validate_module_meta,
    validate_part_meta,
    validate_qa_round,
    validate_stage_report,
)


# ---------------------------------------------------------------------------
# 报告数据结构
# ---------------------------------------------------------------------------


@dataclass
class Diagnostic:
    """一条诊断信息。``level`` ∈ {error, warn, info}。"""

    level: str
    where: str
    detail: str

    def render(self) -> str:
        prefix = {"error": "ERROR", "warn": "WARN ", "info": "INFO "}.get(
            self.level, self.level.upper()
        )
        return f"[{prefix}] {self.where}: {self.detail}"


class _Collector:
    def __init__(self) -> None:
        self._items: list[Diagnostic] = []

    def err(self, where: str, detail: str) -> None:
        self._items.append(Diagnostic("error", where, detail))

    def warn(self, where: str, detail: str) -> None:
        self._items.append(Diagnostic("warn", where, detail))

    def info(self, where: str, detail: str) -> None:
        self._items.append(Diagnostic("info", where, detail))

    @property
    def items(self) -> list[Diagnostic]:
        return list(self._items)

    @property
    def has_error(self) -> bool:
        return any(it.level == "error" for it in self._items)


# ---------------------------------------------------------------------------
# 校验逻辑
# ---------------------------------------------------------------------------


def _required_dirs(project: Project) -> list[tuple[str, Path]]:
    return [
        ("parts/", project.parts_dir),
        ("modules/", project.modules_dir),
        ("artifacts/", project.artifacts_dir),
        ("artifacts/previews/", project.artifacts_dir / "previews"),
        ("artifacts/qa_history/", project.artifacts_dir / "qa_history"),
        ("library_imports/", project.library_imports_dir),
        ("stages/", project.stages_dir),
        ("stages/S1_selection/", project.stage_dir("S1")),
        ("stages/S2_custom/", project.stage_dir("S2")),
        ("stages/S3_modules/", project.stage_dir("S3")),
        ("stages/S4_full/", project.stage_dir("S4")),
    ]


def _required_files(project: Project) -> list[tuple[str, Path]]:
    return [
        ("machine.meta.yaml", project.machine_meta_path),
        ("motion_spec.json", project.motion_spec_path),
        (".cad_todo.json", project.todo_path),
    ]


def _validate_skeleton(project: Project, col: _Collector) -> None:
    for label, p in _required_dirs(project):
        if not p.is_dir():
            col.err(label, f"目录缺失（应位于 {p}）")
    for label, p in _required_files(project):
        if not p.is_file():
            col.err(label, f"文件缺失（应位于 {p}）")


def _validate_yaml_file(
    path: Path,
    validator,
    col: _Collector,
) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        col.err(str(path), f"YAML 语法错误: {e}")
        return None
    if not isinstance(data, dict):
        col.err(str(path), "顶层必须是 mapping（YAML 对象）")
        return None
    try:
        validator(data, source=str(path))
    except SchemaError as e:
        for iss in e.issues:
            field = iss.path or "<root>"
            col.err(iss.source, f"字段 `{field}` → {iss.message}")
    return data


def _validate_machine(project: Project, col: _Collector) -> None:
    _validate_yaml_file(project.machine_meta_path, validate_machine_meta, col)


def _validate_motion_spec(project: Project, col: _Collector) -> None:
    p = project.motion_spec_path
    if not p.exists():
        return
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        col.err(str(p), f"motion_spec.json 不是合法 JSON: {e}")


def _validate_todo(project: Project, col: _Collector) -> None:
    p = project.todo_path
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        col.err(str(p), f".cad_todo.json 不是合法 JSON: {e}")
        return
    if not isinstance(data, dict) or "todos" not in data:
        col.err(str(p), ".cad_todo.json 必须是 {todos: [...]} 形式")
    elif not isinstance(data["todos"], list):
        col.err(str(p), ".cad_todo.json `todos` 字段必须是数组")


def _validate_parts(project: Project, col: _Collector) -> None:
    if not project.parts_dir.is_dir():
        return
    for sub in sorted(project.parts_dir.iterdir()):
        if not sub.is_dir():
            continue
        gen = sub / "gen.py"
        meta = sub / "part.meta.yaml"
        product = sub / "part.step"
        qa = sub / "qa_history"
        if not gen.exists():
            col.err(f"parts/{sub.name}/gen.py", "缺少 gen.py（非标件三件套不完整）")
        if not meta.exists():
            col.err(f"parts/{sub.name}/part.meta.yaml", "缺少 part.meta.yaml")
        else:
            _validate_yaml_file(meta, validate_part_meta, col)
        if not product.exists():
            col.warn(
                f"parts/{sub.name}/part.step",
                "缺少 part.step（CI 可重生，可暂时跳过）",
            )
        if not qa.is_dir():
            col.err(f"parts/{sub.name}/qa_history/", "缺少 qa_history 目录占位")
        else:
            _validate_qa_rounds(qa, col)


def _validate_modules(project: Project, col: _Collector) -> None:
    if not project.modules_dir.is_dir():
        return
    for sub in sorted(project.modules_dir.iterdir()):
        if not sub.is_dir():
            continue
        assemble = sub / "assemble.py"
        meta = sub / "module.meta.yaml"
        product = sub / "module.FCStd"
        qa = sub / "qa_history"
        if not assemble.exists():
            col.err(f"modules/{sub.name}/assemble.py", "缺少 assemble.py")
        if not meta.exists():
            col.err(f"modules/{sub.name}/module.meta.yaml", "缺少 module.meta.yaml")
        else:
            _validate_yaml_file(meta, validate_module_meta, col)
        if not product.exists():
            col.warn(
                f"modules/{sub.name}/module.FCStd",
                "缺少 module.FCStd（装配产物，未跑 P7 时正常）",
            )
        if not qa.is_dir():
            col.err(f"modules/{sub.name}/qa_history/", "缺少 qa_history 目录占位")
        else:
            _validate_qa_rounds(qa, col)


def _validate_artifacts(project: Project, col: _Collector) -> None:
    """artifacts 是单实例（整机），三件套**允许暂不存在**（仅 P8 跑完才有）。"""
    art = project.artifacts_dir
    meta = art / "main.meta.yaml"
    if meta.exists():
        _validate_yaml_file(meta, validate_machine_meta, col)
    # main.FCStd / assemble_main.py 在 P8 跑完前可缺；仅 info
    fcstd = art / "main.FCStd"
    if not fcstd.exists():
        col.info("artifacts/main.FCStd", "整机未生成（P8 完成后才会出现）")
    qa = art / "qa_history"
    if qa.is_dir():
        _validate_qa_rounds(qa, col)


def _validate_qa_rounds(qa_dir: Path, col: _Collector) -> None:
    for p in sorted(qa_dir.glob("round_*.yaml")):
        _validate_yaml_file(p, validate_qa_round, col)
    final = qa_dir / "final.yaml"
    if final.exists():
        _validate_yaml_file(final, validate_qa_round, col)


def _validate_stages(project: Project, col: _Collector) -> None:
    """每个 stage 目录下若有 G<N>.report.yaml 就校验；S1 还要看 bom.yaml。"""
    for stage_key in ("S1", "S2", "S3", "S4"):
        try:
            d = project.stage_dir(stage_key)
        except ProjectError:
            continue
        gate = d / f"G{stage_key[1]}.report.yaml"
        if gate.exists():
            _validate_yaml_file(gate, validate_stage_report, col)


def validate_project(project_root: Path) -> _Collector:
    col = _Collector()
    try:
        project = Project.open(project_root)
    except ProjectError as e:
        col.err(str(project_root), str(e))
        return col

    _validate_skeleton(project, col)
    _validate_machine(project, col)
    _validate_motion_spec(project, col)
    _validate_todo(project, col)
    _validate_parts(project, col)
    _validate_modules(project, col)
    _validate_artifacts(project, col)
    _validate_stages(project, col)
    return col


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(col: _Collector, *, project_root: Path) -> None:
    errors = [it for it in col.items if it.level == "error"]
    warns = [it for it in col.items if it.level == "warn"]
    infos = [it for it in col.items if it.level == "info"]

    for it in col.items:
        print(it.render())

    summary = (
        f"\n[validate_project] {project_root}: "
        f"errors={len(errors)} warns={len(warns)} infos={len(infos)}"
    )
    print(summary)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools.cad.validate_project",
        description="扫描并校验一个 CAD 项目目录；输出每一条问题的具体路径 + 字段。",
    )
    p.add_argument("path", type=Path, help="项目根目录路径")
    p.add_argument(
        "--strict",
        action="store_true",
        help="把 warn 也视为失败（退出码 1）",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = args.path.expanduser().resolve()
    if not root.is_dir():
        print(f"[validate_project] 项目根不存在或不是目录: {root}", file=sys.stderr)
        return 2

    col = validate_project(root)
    _print_report(col, project_root=root)

    if col.has_error:
        return 1
    if args.strict and any(it.level == "warn" for it in col.items):
        return 1
    return 0


def collect_paths(project: Project) -> Iterable[Path]:
    """暴露给单测：返回所有应存在的目录 + 文件路径列表。"""
    for _, p in _required_dirs(project):
        yield p
    for _, p in _required_files(project):
        yield p


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
