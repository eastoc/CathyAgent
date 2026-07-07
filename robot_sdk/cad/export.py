"""CAD export helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from robot_sdk.cad.bbox import CadBoundingBox, bounding_box_from_cad_object
from robot_sdk.cad.cq_assembly import CadQueryAssemblyResult


@dataclass(frozen=True)
class CadQueryExportResult:
    """Result of exporting a CadQuery object."""

    path: Path
    export_type: str
    exists: bool
    size_bytes: int | None = None
    bbox: CadBoundingBox | None = None


@dataclass(frozen=True)
class CadQuerySubassemblyExportSpec:
    """Naming and membership plan for one exported subassembly."""

    name: str
    part_ids: list[str]
    part_export_names: dict[str, str] | None = None
    source_local_subassembly_name: str | None = None
    local_solve_usage: str = "not_applicable"


@dataclass(frozen=True)
class CadQuerySubassemblyExportResult:
    """STEP export result for one subassembly directory."""

    name: str
    directory: Path
    part_ids: list[str]
    part_export_names: dict[str, str]
    part_exports: list[CadQueryExportResult]
    assembly_export: CadQueryExportResult
    assembly_source: str = "fixed_layout_pose"
    source_local_subassembly_name: str | None = None
    local_solve_usage: str = "not_applicable"


@dataclass(frozen=True)
class CadQueryStepPackageExportResult:
    """Multi-file STEP package: parts, subassemblies, and whole machine."""

    root_dir: Path
    whole_machine_export: CadQueryExportResult
    subassemblies: list[CadQuerySubassemblyExportResult]
    whole_machine_assembly_source: str = "fixed_layout_pose"

    @property
    def all_exports(self) -> list[CadQueryExportResult]:
        exports = [self.whole_machine_export]
        for subassembly in self.subassemblies:
            exports.extend(subassembly.part_exports)
            exports.append(subassembly.assembly_export)
        return exports

    @property
    def file_count(self) -> int:
        return len(self.all_exports)


def export_step(
    assembly: Any,
    path: str | Path,
    *,
    mode: str = "default",
    tolerance: float = 0.1,
    angular_tolerance: float = 0.1,
    **kwargs: Any,
) -> CadQueryExportResult:
    """Export a CadQuery Assembly-like object to STEP."""

    return export_cadquery_assembly(
        assembly,
        path,
        export_type="STEP",
        mode=mode,
        tolerance=tolerance,
        angular_tolerance=angular_tolerance,
        **kwargs,
    )


def export_cadquery_assembly(
    assembly: Any,
    path: str | Path,
    *,
    export_type: str,
    mode: str = "default",
    tolerance: float = 0.1,
    angular_tolerance: float = 0.1,
    **kwargs: Any,
) -> CadQueryExportResult:
    """Export a CadQuery Assembly-like object via its `save` method."""

    return export_cadquery_object(
        assembly,
        path,
        export_type=export_type,
        mode=mode,
        tolerance=tolerance,
        angular_tolerance=angular_tolerance,
        **kwargs,
    )


def export_cadquery_object(
    cad_object: Any,
    path: str | Path,
    *,
    export_type: str,
    mode: str = "default",
    tolerance: float = 0.1,
    angular_tolerance: float = 0.1,
    **kwargs: Any,
) -> CadQueryExportResult:
    """Export a CadQuery object using Assembly.save or CadQuery exporters."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    save = getattr(cad_object, "save", None)
    if callable(save):
        save(
            str(output_path),
            exportType=export_type,
            mode=mode,
            tolerance=tolerance,
            angularTolerance=angular_tolerance,
            **kwargs,
        )
    else:
        export_step_method = getattr(cad_object, "exportStep", None)
        if callable(export_step_method) and export_type.upper() == "STEP":
            export_step_method(str(output_path))
        else:
            _export_with_cadquery_exporters(
                cad_object,
                output_path,
                export_type=export_type,
                tolerance=tolerance,
                angular_tolerance=angular_tolerance,
                **kwargs,
            )

    exists = output_path.exists()
    size_bytes = output_path.stat().st_size if exists else None
    return CadQueryExportResult(
        path=output_path,
        export_type=export_type,
        exists=exists,
        size_bytes=size_bytes,
        bbox=bounding_box_from_cad_object(cad_object),
    )


def export_robot_step_package(
    cad_result: CadQueryAssemblyResult,
    root_dir: str | Path,
    *,
    whole_machine_filename: str = "整机.step",
    subassemblies: Sequence[CadQuerySubassemblyExportSpec] | None = None,
    mode: str = "default",
    tolerance: float = 0.1,
    angular_tolerance: float = 0.1,
    **kwargs: Any,
) -> CadQueryStepPackageExportResult:
    """Export parts, subassemblies, and the whole robot into one STEP package."""

    root = Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    whole_name = _step_filename(whole_machine_filename)

    specs = list(subassemblies) if subassemblies is not None else _default_subassembly_specs(cad_result)
    local_result_by_name = {
        str(result.name): result
        for result in cad_result.local_subassembly_results
        if bool(getattr(result, "solved", False))
    }
    exported_subassemblies: list[CadQuerySubassemblyExportResult] = []
    for spec in specs:
        subassembly_name = _safe_path_stem(spec.name)
        subassembly_dir = root / subassembly_name
        subassembly_dir.mkdir(parents=True, exist_ok=True)
        part_export_names = dict(spec.part_export_names or {})
        source_local_name = spec.source_local_subassembly_name
        source_local_result = (
            local_result_by_name.get(source_local_name)
            if source_local_name
            else None
        )

        part_exports: list[CadQueryExportResult] = []
        subassembly = (
            source_local_result.assembly
            if source_local_result is not None
            else _new_subassembly_like(cad_result.assembly)
        )
        for part_id in spec.part_ids:
            part = cad_result.part_catalog.require(part_id)
            if source_local_result is None:
                subassembly.add(
                    part.solid,
                    name=part_id,
                    loc=cad_result.part_locations.get(part_id),
                )
            part_exports.append(
                export_step(
                    part.solid,
                    subassembly_dir / _step_filename(part_export_names.get(part_id, part_id)),
                    mode=mode,
                    tolerance=tolerance,
                    angular_tolerance=angular_tolerance,
                    **kwargs,
                )
            )

        assembly_export = export_step(
            subassembly,
            subassembly_dir / _step_filename(subassembly_name),
            mode=mode,
            tolerance=tolerance,
            angular_tolerance=angular_tolerance,
            **kwargs,
        )
        exported_subassemblies.append(
            CadQuerySubassemblyExportResult(
                name=subassembly_name,
                directory=subassembly_dir,
                part_ids=list(spec.part_ids),
                part_export_names=part_export_names,
                part_exports=part_exports,
                assembly_export=assembly_export,
                assembly_source=(
                    "local_constraint_solve"
                    if source_local_result is not None
                    else "fixed_layout_pose"
                ),
                source_local_subassembly_name=source_local_name
                if source_local_result is not None
                else None,
                local_solve_usage=(
                    spec.local_solve_usage
                    if source_local_result is not None
                    and spec.local_solve_usage != "not_applicable"
                    else "experimental_local_solve"
                    if source_local_result is not None
                    else "not_applicable"
                ),
            )
        )

    whole_machine_export = export_step(
        cad_result.assembly,
        root / whole_name,
        mode=mode,
        tolerance=tolerance,
        angular_tolerance=angular_tolerance,
        **kwargs,
    )

    return CadQueryStepPackageExportResult(
        root_dir=root,
        whole_machine_export=whole_machine_export,
        subassemblies=exported_subassemblies,
        whole_machine_assembly_source=str(
            cad_result.metadata.get("production_assembly_source") or "fixed_layout_pose"
        ),
    )


def _export_with_cadquery_exporters(
    cad_object: Any,
    path: Path,
    *,
    export_type: str,
    tolerance: float,
    angular_tolerance: float,
    **kwargs: Any,
) -> None:
    if not _looks_cadquery_exportable(cad_object):
        raise TypeError("cad_object must be CadQuery-exportable or provide save/exportStep")
    try:
        import cadquery as cq  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise TypeError("cadquery exporters are required for this object") from exc

    cq.exporters.export(
        cad_object,
        str(path),
        exportType=export_type,
        tolerance=tolerance,
        angularTolerance=angular_tolerance,
        **kwargs,
    )


def _looks_cadquery_exportable(cad_object: Any) -> bool:
    return any(
        callable(getattr(cad_object, attr, None))
        for attr in ("val", "objects", "solids")
    )


def _new_subassembly_like(assembly: Any) -> Any:
    try:
        return type(assembly)()
    except Exception as exc:
        raise TypeError("assembly class must be constructible without arguments") from exc


def _default_subassembly_specs(
    cad_result: CadQueryAssemblyResult,
) -> list[CadQuerySubassemblyExportSpec]:
    part_ids = cad_result.part_catalog.part_ids()
    return [
        CadQuerySubassemblyExportSpec(
            name=part_id,
            part_ids=[part_id],
            part_export_names={part_id: f"{part_id}_part"},
        )
        for part_id in part_ids
    ]


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
