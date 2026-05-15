"""CAD 项目目录的读写 API（任意路径作为根）。

设计要点（与 ``ROADMAP_CAD_AGENT_v0.5.md`` §3.1 / Phase 0 对齐）：

- **项目根路径不写死**：由调用方传入；CLI 用 ``--project <PATH>``，
  SDK 用 ``Project.open(Path(...))``，5 个 cad-* subagent 全部经此 API 读写，
  不允许任何 subagent 自己拼 ``os.path.join`` 路径。
- **三层三件套统一接口**：``write_triplet(scope, id, gen, meta, product)`` /
  ``read_triplet(scope, id)``；scope = ``part`` | ``module`` | ``machine``。
- **QA 评审 trail 统一接口**：``append_qa_round(scope, id, feedback)``；
  自动编号 round_NNN.yaml；终态时另写一份 final.yaml。
- **写盘前一定先 schema 校验**：把契约层做"宁可写不进去，也不让脏数据落盘"。

不依赖 FreeCAD / build123d，可在 CI 纯 Python 跑。
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from cathy.cad import schema as _schema

# ---------------------------------------------------------------------------
# 常量：目录约定
# ---------------------------------------------------------------------------

_PARTS_DIR = "parts"
_MODULES_DIR = "modules"
_ARTIFACTS_DIR = "artifacts"
_LIBRARY_IMPORTS_DIR = "library_imports"
_STAGES_DIR = "stages"
_QA_HISTORY_DIR = "qa_history"
_TODO_FILE = ".cad_todo.json"
_README_FILE = "README.md"
_MACHINE_META_FILE = "machine.meta.yaml"
_MOTION_SPEC_FILE = "motion_spec.json"

# Stage 子目录 + 默认初始文件
_STAGES = ("S1_selection", "S2_custom", "S3_modules", "S4_full")

# 三层三件套的"产物文件名"约定
_PRODUCT_BY_SCOPE = {
    "part": "part.step",
    "module": "module.FCStd",
    "machine": "main.FCStd",
}
_GEN_BY_SCOPE = {
    "part": "gen.py",
    "module": "assemble.py",
    "machine": "assemble_main.py",
}
_META_BY_SCOPE = {
    "part": "part.meta.yaml",
    "module": "module.meta.yaml",
    "machine": "main.meta.yaml",
}
_VALIDATOR_BY_SCOPE = {
    "part": _schema.validate_part_meta,
    "module": _schema.validate_module_meta,
    "machine": _schema.validate_machine_meta,
}

_VALID_SCOPES = tuple(_PRODUCT_BY_SCOPE.keys())

# part / module 的 ID 命名约束（与 schema 中 _ID_PATTERN 一致）
_ID_REGEX = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ProjectError(RuntimeError):
    """项目目录读写过程中的统一异常。"""


@dataclass(frozen=True)
class TripletPaths:
    """三件套的三个路径 + qa_history 目录。"""

    scope: str
    id: str
    dir: Path
    gen: Path
    meta: Path
    product: Path
    qa_history: Path


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class Project:
    """打开/创建一个 CAD 项目。

    用法：

    .. code-block:: python

        proj = Project.open("/任意/路径")          # 不存在则不会自动建
        proj = Project.init("/任意/路径")           # 显式初始化空骨架
        proj.parts_dir                              # -> <root>/parts
        proj.write_triplet("part", "flange_v1", gen=..., meta=..., product_path=...)
        proj.append_qa_round("part", "flange_v1", feedback={...})
    """

    def __init__(self, root: Path):
        self._root = Path(root).expanduser().resolve()

    # -- 工厂方法 ------------------------------------------------------------

    @classmethod
    def open(cls, root: str | Path) -> "Project":
        """打开既有项目目录。不存在则报错。"""
        p = Path(root).expanduser().resolve()
        if not p.is_dir():
            raise ProjectError(f"项目根不存在或不是目录: {p}")
        return cls(p)

    @classmethod
    def init(
        cls,
        root: str | Path,
        *,
        machine_id: str = "machine_v1",
        machine_type: str = "robotic_arm",
        dof_count: int = 6,
        exist_ok: bool = True,
        write_readme: bool = True,
    ) -> "Project":
        """在 ``root`` 处初始化完整空骨架，返回 Project 实例。

        - 若 ``root`` 已存在且非空 + ``exist_ok=False``：抛错。
        - 自动写 ``machine.meta.yaml`` 占位（最小合法集，可被后续阶段覆盖）。
        - 自动写 ``motion_spec.json`` 占位（空字典 + machine_type / dof_count）。
        - 自动写 ``.cad_todo.json``（空 todo 列表）。
        """
        root = Path(root).expanduser().resolve()
        if root.exists():
            if not root.is_dir():
                raise ProjectError(f"目标已存在且不是目录: {root}")
            if any(root.iterdir()) and not exist_ok:
                raise ProjectError(f"目标目录非空: {root}")
        else:
            root.mkdir(parents=True, exist_ok=True)

        # 顶层固定目录
        (root / _PARTS_DIR).mkdir(exist_ok=True)
        (root / _MODULES_DIR).mkdir(exist_ok=True)
        (root / _ARTIFACTS_DIR).mkdir(exist_ok=True)
        (root / _ARTIFACTS_DIR / "previews").mkdir(exist_ok=True)
        (root / _ARTIFACTS_DIR / _QA_HISTORY_DIR).mkdir(exist_ok=True)
        (root / _LIBRARY_IMPORTS_DIR).mkdir(exist_ok=True)
        (root / _STAGES_DIR).mkdir(exist_ok=True)
        for stage in _STAGES:
            (root / _STAGES_DIR / stage).mkdir(exist_ok=True)

        # machine.meta.yaml 占位（最小合法集）
        machine_meta_path = root / _MACHINE_META_FILE
        if not machine_meta_path.exists():
            machine_meta: dict[str, Any] = {
                "schema_version": _schema.SCHEMA_VERSION,
                "machine_id": machine_id,
                "machine_type": machine_type,
                "dof_count": dof_count,
                "source": {"generator": "tools/cad/init_project.py"},
                "modules_used": [],
                "stages": {"S1": "pending", "S2": "pending", "S3": "pending", "S4": "pending"},
                "created_at": _now_iso(),
            }
            _schema.validate_machine_meta(machine_meta, source=str(machine_meta_path))
            _write_yaml(machine_meta_path, machine_meta)

        # motion_spec.json 占位
        motion_spec_path = root / _MOTION_SPEC_FILE
        if not motion_spec_path.exists():
            motion_spec_path.write_text(
                json.dumps(
                    {
                        "machine_type": machine_type,
                        "dof_count": dof_count,
                        "joints": [],
                        "links": [],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        # .cad_todo.json 占位
        todo_path = root / _TODO_FILE
        if not todo_path.exists():
            todo_path.write_text(
                json.dumps({"todos": []}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # README
        if write_readme:
            readme = root / _README_FILE
            if not readme.exists():
                readme.write_text(_DEFAULT_README, encoding="utf-8")

        return cls(root)

    # -- 路径属性 ------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def parts_dir(self) -> Path:
        return self._root / _PARTS_DIR

    @property
    def modules_dir(self) -> Path:
        return self._root / _MODULES_DIR

    @property
    def artifacts_dir(self) -> Path:
        return self._root / _ARTIFACTS_DIR

    @property
    def library_imports_dir(self) -> Path:
        return self._root / _LIBRARY_IMPORTS_DIR

    @property
    def stages_dir(self) -> Path:
        return self._root / _STAGES_DIR

    @property
    def machine_meta_path(self) -> Path:
        return self._root / _MACHINE_META_FILE

    @property
    def motion_spec_path(self) -> Path:
        return self._root / _MOTION_SPEC_FILE

    @property
    def todo_path(self) -> Path:
        return self._root / _TODO_FILE

    def stage_dir(self, stage: str) -> Path:
        """``stage`` 可以是 ``S1`` / ``S1_selection`` / 任一已知形态。"""
        key = stage.upper().split("_", 1)[0]
        mapping = {s.split("_", 1)[0]: s for s in _STAGES}
        if key not in mapping:
            raise ProjectError(f"未知 stage: {stage}; 合法值: {list(mapping)}")
        return self.stages_dir / mapping[key]

    # -- 三件套读写 ----------------------------------------------------------

    def triplet_paths(self, scope: str, id: str) -> TripletPaths:
        _ensure_scope(scope)
        _ensure_id(id)
        if scope == "machine":
            d = self.artifacts_dir
        elif scope == "module":
            d = self.modules_dir / id
        else:  # part
            d = self.parts_dir / id
        return TripletPaths(
            scope=scope,
            id=id,
            dir=d,
            gen=d / _GEN_BY_SCOPE[scope],
            meta=d / _META_BY_SCOPE[scope],
            product=d / _PRODUCT_BY_SCOPE[scope],
            qa_history=d / _QA_HISTORY_DIR,
        )

    def write_triplet(
        self,
        scope: str,
        id: str,
        *,
        gen: str,
        meta: dict[str, Any],
        product: bytes | str | Path | None = None,
    ) -> TripletPaths:
        """落盘一份三件套；落盘前对 meta 强制 schema 校验。

        - ``gen``：Python 源码字符串。
        - ``meta``：dict，会先经 jsonschema 校验，不合规直接抛 :class:`SchemaError`。
        - ``product``：可选；可传 bytes（直接写入）或现有文件 Path（复制）。
        """
        paths = self.triplet_paths(scope, id)
        paths.dir.mkdir(parents=True, exist_ok=True)
        paths.qa_history.mkdir(exist_ok=True)

        # 校验 meta（按 scope 选 validator）
        validator = _VALIDATOR_BY_SCOPE[scope]
        # 注入 schema_version / created_at 兜底
        meta = dict(meta)
        meta.setdefault("schema_version", _schema.SCHEMA_VERSION)
        meta.setdefault("created_at", _now_iso())
        validator(meta, source=str(paths.meta))

        # 落盘
        paths.gen.write_text(gen, encoding="utf-8")
        _write_yaml(paths.meta, meta)
        if product is not None:
            _write_product(paths.product, product)
        return paths

    def read_triplet(self, scope: str, id: str) -> tuple[str, dict[str, Any], Path | None]:
        """读三件套：返回 ``(gen_code, meta_dict, product_path_or_None)``。"""
        paths = self.triplet_paths(scope, id)
        if not paths.dir.is_dir():
            raise ProjectError(f"{scope}/{id} 目录不存在: {paths.dir}")
        if not paths.gen.exists():
            raise ProjectError(f"缺少生成代码: {paths.gen}")
        if not paths.meta.exists():
            raise ProjectError(f"缺少 meta: {paths.meta}")
        gen_code = paths.gen.read_text(encoding="utf-8")
        meta = _read_yaml(paths.meta)
        validator = _VALIDATOR_BY_SCOPE[scope]
        validator(meta, source=str(paths.meta))
        product = paths.product if paths.product.exists() else None
        return gen_code, meta, product

    def list_ids(self, scope: str) -> list[str]:
        _ensure_scope(scope)
        if scope == "machine":
            # machine 只有 artifacts/（单实例），有 main.meta.yaml 才算"存在"
            return ["main"] if (self.artifacts_dir / _META_BY_SCOPE["machine"]).exists() else []
        base = self.parts_dir if scope == "part" else self.modules_dir
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    # -- QA Round trail ------------------------------------------------------

    def append_qa_round(
        self,
        scope: str,
        id: str,
        feedback: dict[str, Any],
        *,
        final: bool = False,
    ) -> Path:
        """把一轮 QA 反馈写到 ``<target_dir>/qa_history/round_NNN.yaml``。

        - 自动编号：扫描已有 round_*.yaml，取最大值 + 1。
        - 自动注入 ``qa_round`` / ``created_at`` 兜底字段（若未提供）。
        - 写入前强制 schema 校验。
        - 若 ``final=True``：另写一份 ``final.yaml``（不覆盖编号的 round 历史）。
        """
        paths = self.triplet_paths(scope, id)
        paths.qa_history.mkdir(parents=True, exist_ok=True)

        next_round = _next_round_number(paths.qa_history)
        data = dict(feedback)
        data.setdefault("schema_version", _schema.SCHEMA_VERSION)
        data.setdefault("qa_round", next_round)
        data.setdefault("scope", scope)
        data.setdefault("target", id)
        data.setdefault("created_at", _now_iso())

        round_path = paths.qa_history / f"round_{next_round:03d}.yaml"
        _schema.validate_qa_round(data, source=str(round_path))
        _write_yaml(round_path, data)

        if final:
            final_path = paths.qa_history / "final.yaml"
            _write_yaml(final_path, data)

        return round_path

    def read_qa_history(self, scope: str, id: str) -> list[dict[str, Any]]:
        """读全部 round_NNN.yaml（升序），不含 final.yaml。"""
        paths = self.triplet_paths(scope, id)
        if not paths.qa_history.is_dir():
            return []
        rounds = sorted(
            p for p in paths.qa_history.glob("round_*.yaml") if p.is_file()
        )
        return [_read_yaml(p) for p in rounds]

    # -- Stage report --------------------------------------------------------

    def write_stage_report(self, stage: str, report: dict[str, Any]) -> Path:
        """落 ``stages/S<N>_*/G<N>.report.yaml``；写前 schema 校验。"""
        stage_key = stage.upper().split("_", 1)[0]  # S1 / S2 / S3 / S4
        gate_key = "G" + stage_key[1:]
        d = self.stage_dir(stage_key)
        d.mkdir(parents=True, exist_ok=True)
        data = dict(report)
        data.setdefault("schema_version", _schema.SCHEMA_VERSION)
        data.setdefault("stage", stage_key)
        data.setdefault("gate", gate_key)
        data.setdefault("checked_at", _now_iso())
        out = d / f"{gate_key}.report.yaml"
        _schema.validate_stage_report(data, source=str(out))
        _write_yaml(out, data)
        return out

    def read_stage_report(self, stage: str) -> dict[str, Any] | None:
        stage_key = stage.upper().split("_", 1)[0]
        gate_key = "G" + stage_key[1:]
        p = self.stage_dir(stage_key) / f"{gate_key}.report.yaml"
        if not p.exists():
            return None
        data = _read_yaml(p)
        _schema.validate_stage_report(data, source=str(p))
        return data

    # -- Meta 快捷读写 -------------------------------------------------------

    def read_meta(self, scope: str, id: str | None = None) -> dict[str, Any]:
        """``scope`` 取 ``part`` / ``module`` / ``machine``。machine 不需要 id。"""
        if scope == "machine":
            if not self.machine_meta_path.exists():
                raise ProjectError(f"缺少 machine.meta: {self.machine_meta_path}")
            data = _read_yaml(self.machine_meta_path)
            _schema.validate_machine_meta(data, source=str(self.machine_meta_path))
            return data
        if id is None:
            raise ProjectError(f"读 {scope} meta 必须指定 id")
        _, meta, _ = self.read_triplet(scope, id)
        return meta

    # -- 内部清理 / 调试 -----------------------------------------------------

    def remove(self) -> None:  # pragma: no cover - 调试用
        """谨慎使用：递归删除整个项目根。"""
        if self._root.exists():
            shutil.rmtree(self._root)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _ensure_scope(scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise ProjectError(f"未知 scope: {scope}; 合法值: {list(_VALID_SCOPES)}")


def _ensure_id(id: str) -> None:
    if not _ID_REGEX.match(id):
        raise ProjectError(
            f"非法 id: {id!r}; 必须匹配 [a-z][a-z0-9_]* （小写 + 下划线）"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ProjectError(f"yaml 顶层必须是 mapping: {path}")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


def _write_product(path: Path, product: bytes | str | Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(product, bytes):
        path.write_bytes(product)
    elif isinstance(product, (str, Path)):
        src = Path(product)
        if src == path:
            return
        if not src.exists():
            raise ProjectError(f"产物源文件不存在: {src}")
        shutil.copyfile(src, path)
    else:  # pragma: no cover - 类型守卫
        raise ProjectError(f"不支持的 product 类型: {type(product)!r}")


_ROUND_RE = re.compile(r"^round_(\d{3,})\.yaml$")


def _next_round_number(qa_dir: Path) -> int:
    nums: list[int] = []
    for p in qa_dir.glob("round_*.yaml"):
        m = _ROUND_RE.match(p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def iter_part_ids(project: Project) -> Iterable[str]:
    yield from project.list_ids("part")


def iter_module_ids(project: Project) -> Iterable[str]:
    yield from project.list_ids("module")


_DEFAULT_README = """# CAD Project

本目录由 `tools/cad/init_project.py` 初始化，遵循 CathyAgent CAD Agent 的契约
（见 `ROADMAP_CAD_AGENT_v0.5.md` §3）。

- `parts/<part_id>/`     非标件三件套（gen.py + part.meta.yaml + part.step）
- `modules/<module_id>/` 模块装配三件套（assemble.py + module.meta.yaml + module.FCStd）
- `artifacts/`           整机交付（assemble_main.py + main.meta.yaml + main.FCStd）
- `stages/S{1..4}_*/`    每阶段的 BOM / Gate 报告
- `library_imports/`     标准件 / 电机 / 减速器导入快照（按 hash 去重）
- `.cad_todo.json`       Stage Director 的 todo 列表

任何 subagent 都通过 `cathy.cad.project.Project` API 读写，不允许直接拼路径。
"""


__all__ = [
    "Project",
    "ProjectError",
    "TripletPaths",
    "iter_part_ids",
    "iter_module_ids",
]
