"""STEP package export for build123d source-level robot assemblies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from robot_sdk.cad.source_assembly import SourceStepExportResult, export_source_step
from robot_sdk.cad.source_robot_builder import SourceRobotAssemblyResult


@dataclass(frozen=True)
class SourceSubassemblyExportSpec:
    """Naming and membership plan for one source-level subassembly export."""

    name: str
    part_ids: list[str]
    part_export_names: dict[str, str] | None = None


@dataclass(frozen=True)
class SourceSubassemblyExportResult:
    """STEP exports under one semantic source subassembly directory."""

    name: str
    directory: Path
    part_ids: list[str]
    part_export_names: dict[str, str]
    part_exports: list[SourceStepExportResult]
    assembly_export: SourceStepExportResult
    assembly_source: str = "source_joint"
    source_local_subassembly_name: str | None = None
    local_solve_usage: str = "not_applicable"


@dataclass(frozen=True)
class SourceStepPackageExportResult:
    """Multi-file STEP package for source-level build123d assemblies."""

    root_dir: Path
    whole_machine_export: SourceStepExportResult
    subassemblies: list[SourceSubassemblyExportResult]
    whole_machine_assembly_source: str = "source_joint"

    @property
    def all_exports(self) -> list[SourceStepExportResult]:
        exports = [self.whole_machine_export]
        for subassembly in self.subassemblies:
            exports.extend(subassembly.part_exports)
            exports.append(subassembly.assembly_export)
        return exports

    @property
    def file_count(self) -> int:
        return len(self.all_exports)


def export_source_robot_step_package(
    source_result: SourceRobotAssemblyResult,
    root_dir: str | Path,
    *,
    whole_machine_filename: str = "整机.step",
    subassemblies: Sequence[Any] | None = None,
) -> SourceStepPackageExportResult:
    """Export source-level parts, subassemblies, and whole machine STEP files."""

    root = Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    specs = (
        [_normalize_spec(item) for item in subassemblies]
        if subassemblies is not None
        else _default_source_subassembly_specs(source_result)
    )

    whole_machine_export = export_source_step(
        source_result.assembly,
        root / _step_filename(whole_machine_filename),
    )

    exported_subassemblies: list[SourceSubassemblyExportResult] = []
    for spec in specs:
        subassembly_name = _safe_path_stem(spec.name)
        subassembly_dir = root / subassembly_name
        subassembly_dir.mkdir(parents=True, exist_ok=True)
        part_export_names = dict(spec.part_export_names or {})

        part_exports: list[SourceStepExportResult] = []
        children: list[Any] = []
        for part_id in spec.part_ids:
            part = source_result.part_catalog.require(part_id)
            children.append(part.solid)
            part_exports.append(
                export_source_step(
                    part.solid,
                    subassembly_dir
                    / _step_filename(part_export_names.get(part_id, part_id)),
                )
            )

        subassembly = _source_compound(subassembly_name, children)
        assembly_export = export_source_step(
            subassembly,
            subassembly_dir / _step_filename(subassembly_name),
        )
        exported_subassemblies.append(
            SourceSubassemblyExportResult(
                name=subassembly_name,
                directory=subassembly_dir,
                part_ids=list(spec.part_ids),
                part_export_names=part_export_names,
                part_exports=part_exports,
                assembly_export=assembly_export,
            )
        )

    return SourceStepPackageExportResult(
        root_dir=root,
        whole_machine_export=whole_machine_export,
        subassemblies=exported_subassemblies,
        whole_machine_assembly_source=str(
            source_result.metadata.get("production_assembly_source") or "source_joint"
        ),
    )


def _default_source_subassembly_specs(
    source_result: SourceRobotAssemblyResult,
) -> list[SourceSubassemblyExportSpec]:
    return [
        SourceSubassemblyExportSpec(
            name=part_id,
            part_ids=[part_id],
            part_export_names={part_id: f"{part_id}_part"},
        )
        for part_id in source_result.part_catalog.part_ids()
    ]


def _normalize_spec(value: Any) -> SourceSubassemblyExportSpec:
    name = str(getattr(value, "name", "") or "").strip()
    part_ids = [str(item) for item in getattr(value, "part_ids", []) or []]
    part_export_names = getattr(value, "part_export_names", None)
    if not name:
        raise ValueError("source subassembly spec name is required")
    if not part_ids:
        raise ValueError(f"source subassembly spec `{name}` has no parts")
    return SourceSubassemblyExportSpec(
        name=name,
        part_ids=part_ids,
        part_export_names=dict(part_export_names or {}),
    )


def _source_compound(name: str, children: Sequence[Any]) -> Any:
    try:
        import build123d
    except ModuleNotFoundError as exc:
        raise RuntimeError("source STEP package export requires build123d") from exc
    return build123d.Compound(label=_safe_path_stem(name), children=list(children))


def _step_filename(name: str) -> str:
    filename = _safe_path_stem(name)
    if filename.lower().endswith((".step", ".stp")):
        return filename
    return f"{filename}.step"


def _safe_path_stem(name: str) -> str:
    text = str(name or "").strip() or "unnamed"
    text = text.replace("/", "_").replace("\\", "_").replace(":", "_")
    text = text.strip(" .")
    return text or "unnamed"


__all__ = [
    "SourceStepPackageExportResult",
    "SourceSubassemblyExportResult",
    "SourceSubassemblyExportSpec",
    "export_source_robot_step_package",
]
