"""CAD export helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CadQueryExportResult:
    """Result of exporting a CadQuery object."""

    path: Path
    export_type: str
    exists: bool
    size_bytes: int | None = None


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

    save = getattr(assembly, "save", None)
    if not callable(save):
        raise TypeError("assembly must provide a CadQuery-compatible save method")

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save(
        str(output_path),
        exportType=export_type,
        mode=mode,
        tolerance=tolerance,
        angularTolerance=angular_tolerance,
        **kwargs,
    )
    exists = output_path.exists()
    size_bytes = output_path.stat().st_size if exists else None
    return CadQueryExportResult(
        path=output_path,
        export_type=export_type,
        exists=exists,
        size_bytes=size_bytes,
    )
