"""STEP snapshot helpers.

The primary path imports exported STEP files back through CadQuery and writes
SVG previews from several orthographic directions.  When STEP rendering is not
available, the helper writes a bounding-box SVG fallback so the workflow still
leaves a visible review artifact and a precise warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Sequence

from robot_sdk.cad.export import (
    CadQueryExportResult,
    CadQueryStepPackageExportResult,
)


DEFAULT_SNAPSHOT_VIEWS = ("front", "top", "isometric")
VIEW_PROJECTION_DIRECTIONS = {
    "front": (0.0, -1.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "isometric": (1.0, -1.0, 0.8),
}
SVG_PATH_POINT_RE = re.compile(
    r"[ML]\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"[\s,]+"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


@dataclass(frozen=True)
class StepSnapshotResult:
    """Snapshot result for one exported STEP file and one camera view."""

    source_step_path: Path
    svg_path: Path
    view: str
    generated: bool
    renderer: str
    target_name: str | None = None
    fallback_used: bool = False
    error: str | None = None
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "source_step_path": str(self.source_step_path),
            "svg_path": str(self.svg_path),
            "view": self.view,
            "generated": self.generated,
            "renderer": self.renderer,
            "target_name": self.target_name,
            "fallback_used": self.fallback_used,
            "error": self.error,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class StepSnapshotPackageResult:
    """Snapshot result for a STEP package."""

    root_dir: Path
    snapshots_dir: Path
    whole_machine_snapshots: list[StepSnapshotResult]
    subassembly_snapshots: list[StepSnapshotResult]

    @property
    def snapshots(self) -> list[StepSnapshotResult]:
        return [*self.whole_machine_snapshots, *self.subassembly_snapshots]

    @property
    def generated_count(self) -> int:
        return sum(1 for snapshot in self.snapshots if snapshot.generated)

    @property
    def real_step_snapshot_count(self) -> int:
        return sum(
            1
            for snapshot in self.snapshots
            if snapshot.generated and not snapshot.fallback_used
        )

    @property
    def fallback_count(self) -> int:
        return sum(1 for snapshot in self.snapshots if snapshot.fallback_used)

    @property
    def ok(self) -> bool:
        return self.real_step_snapshot_count > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "root_dir": str(self.root_dir),
            "snapshots_dir": str(self.snapshots_dir),
            "generated_count": self.generated_count,
            "real_step_snapshot_count": self.real_step_snapshot_count,
            "fallback_count": self.fallback_count,
            "whole_machine_snapshots": [
                snapshot.to_dict() for snapshot in self.whole_machine_snapshots
            ],
            "subassembly_snapshots": [
                snapshot.to_dict() for snapshot in self.subassembly_snapshots
            ],
        }


@dataclass(frozen=True)
class SvgSnapshotMetrics:
    """Basic projected silhouette metrics parsed from one SVG snapshot."""

    svg_path: Path
    view: str
    target_name: str | None
    x_extent: float
    y_extent: float
    aspect_ratio: float
    height_ratio: float
    point_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "svg_path": str(self.svg_path),
            "view": self.view,
            "target_name": self.target_name,
            "x_extent": self.x_extent,
            "y_extent": self.y_extent,
            "aspect_ratio": self.aspect_ratio,
            "height_ratio": self.height_ratio,
            "point_count": self.point_count,
        }


def generate_step_package_snapshots(
    package_result: CadQueryStepPackageExportResult,
    *,
    views: Sequence[str] = DEFAULT_SNAPSHOT_VIEWS,
    include_subassemblies: bool = False,
    subassembly_names: Sequence[str] | None = None,
    snapshots_dir: str | Path | None = None,
    cq_module: object | None = None,
) -> StepSnapshotPackageResult:
    """Generate SVG snapshots for the whole machine and selected subassemblies."""

    output_dir = Path(snapshots_dir) if snapshots_dir else package_result.root_dir / "snapshots"
    whole = generate_step_snapshots(
        package_result.whole_machine_export,
        output_dir,
        export_name="whole_machine",
        views=views,
        cq_module=cq_module,
    )
    subassemblies: list[StepSnapshotResult] = []
    selected_names = {str(name) for name in subassembly_names or []}
    if include_subassemblies or selected_names:
        for subassembly in package_result.subassemblies:
            if selected_names and subassembly.name not in selected_names:
                continue
            subassemblies.extend(
                generate_step_snapshots(
                    subassembly.assembly_export,
                    output_dir,
                    export_name=subassembly.name,
                    views=views,
                    cq_module=cq_module,
                )
            )
    return StepSnapshotPackageResult(
        root_dir=package_result.root_dir,
        snapshots_dir=output_dir,
        whole_machine_snapshots=whole,
        subassembly_snapshots=subassemblies,
    )


def analyze_snapshot_package(
    snapshot_result: object,
) -> list[SvgSnapshotMetrics]:
    """Return SVG silhouette metrics for renderer-backed snapshots."""

    metrics: list[SvgSnapshotMetrics] = []
    for snapshot in _snapshot_items(snapshot_result):
        item = analyze_svg_snapshot(snapshot)
        if item is not None:
            metrics.append(item)
    return metrics


def analyze_svg_snapshot(snapshot: object) -> SvgSnapshotMetrics | None:
    """Parse projected silhouette metrics from one SVG snapshot."""

    if not bool(getattr(snapshot, "generated", False)):
        return None
    if bool(getattr(snapshot, "fallback_used", False)):
        return None
    svg_path = Path(getattr(snapshot, "svg_path", ""))
    if not svg_path.exists():
        return None
    try:
        text = svg_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = svg_path.read_text(encoding="utf-8", errors="ignore")
    points = [
        (float(match.group(1)), float(match.group(2)))
        for match in SVG_PATH_POINT_RE.finditer(text)
    ]
    if len(points) < 2:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_extent = max(xs) - min(xs)
    y_extent = max(ys) - min(ys)
    if x_extent <= 1e-9 or y_extent <= 1e-9:
        return None
    major = max(x_extent, y_extent)
    minor = min(x_extent, y_extent)
    return SvgSnapshotMetrics(
        svg_path=svg_path,
        view=str(getattr(snapshot, "view", "unknown")),
        target_name=getattr(snapshot, "target_name", None),
        x_extent=x_extent,
        y_extent=y_extent,
        aspect_ratio=major / minor,
        height_ratio=minor / major,
        point_count=len(points),
    )


def generate_step_snapshots(
    export_result: CadQueryExportResult,
    snapshots_dir: str | Path,
    *,
    export_name: str | None = None,
    views: Sequence[str] = DEFAULT_SNAPSHOT_VIEWS,
    cq_module: object | None = None,
) -> list[StepSnapshotResult]:
    """Generate SVG snapshots for one STEP export."""

    output_dir = Path(snapshots_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    step_path = export_result.path
    safe_name = _safe_stem(export_name or step_path.stem)
    results: list[StepSnapshotResult] = []
    for view in views:
        svg_path = output_dir / f"{safe_name}_{view}.svg"
        try:
            _render_step_svg(
                step_path,
                svg_path,
                view=view,
                cq_module=cq_module,
            )
            results.append(
                StepSnapshotResult(
                    source_step_path=step_path,
                    svg_path=svg_path,
                    view=view,
                    generated=svg_path.exists(),
                    renderer="cadquery_step_import",
                    target_name=safe_name,
                    size_bytes=svg_path.stat().st_size if svg_path.exists() else None,
                )
            )
        except Exception as exc:
            fallback_generated = _write_bbox_svg_fallback(
                export_result,
                svg_path,
                view=view,
                error=f"{type(exc).__name__}: {exc}",
            )
            results.append(
                StepSnapshotResult(
                    source_step_path=step_path,
                    svg_path=svg_path,
                    view=view,
                    generated=fallback_generated,
                    renderer="bbox_svg_fallback",
                    target_name=safe_name,
                    fallback_used=True,
                    error=f"{type(exc).__name__}: {exc}",
                    size_bytes=svg_path.stat().st_size if svg_path.exists() else None,
                )
            )
    return results


def _snapshot_items(snapshot_result: object) -> list[object]:
    snapshots = getattr(snapshot_result, "snapshots", None)
    if isinstance(snapshots, list):
        return snapshots
    combined: list[object] = []
    whole = getattr(snapshot_result, "whole_machine_snapshots", None)
    if isinstance(whole, list):
        combined.extend(whole)
    subassemblies = getattr(snapshot_result, "subassembly_snapshots", None)
    if isinstance(subassemblies, list):
        combined.extend(subassemblies)
    return combined


def _render_step_svg(
    step_path: Path,
    svg_path: Path,
    *,
    view: str,
    cq_module: object | None,
) -> None:
    cq = cq_module or _import_cadquery()
    importers = getattr(cq, "importers", None)
    exporters = getattr(cq, "exporters", None)
    import_step = getattr(importers, "importStep", None)
    export = getattr(exporters, "export", None)
    if not callable(import_step) or not callable(export):
        raise RuntimeError("CadQuery STEP import/export APIs are unavailable")
    imported = import_step(str(step_path))
    export(
        imported,
        str(svg_path),
        opt={
            "projectionDir": VIEW_PROJECTION_DIRECTIONS.get(
                view,
                VIEW_PROJECTION_DIRECTIONS["isometric"],
            ),
            "showAxes": False,
        },
    )


def _write_bbox_svg_fallback(
    export_result: CadQueryExportResult,
    svg_path: Path,
    *,
    view: str,
    error: str,
) -> bool:
    bbox = export_result.bbox
    if bbox is None or not bbox.valid:
        return False
    width, height = _bbox_view_size(bbox, view)
    scale = 320.0 / max(width, height, 1.0)
    draw_width = max(width * scale, 1.0)
    draw_height = max(height * scale, 1.0)
    canvas_width = draw_width + 80.0
    canvas_height = draw_height + 90.0
    x = (canvas_width - draw_width) / 2.0
    y = (canvas_height - draw_height) / 2.0 + 12.0
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(
        "\n".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{canvas_width:.0f}" height="{canvas_height:.0f}" '
                f'viewBox="0 0 {canvas_width:.0f} {canvas_height:.0f}">',
                '<rect width="100%" height="100%" fill="#f8fafc"/>',
                (
                    f'<rect x="{x:.2f}" y="{y:.2f}" width="{draw_width:.2f}" '
                    f'height="{draw_height:.2f}" fill="#b6c2c9" stroke="#1f2933" '
                    'stroke-width="2"/>'
                ),
                (
                    f'<text x="20" y="24" font-family="monospace" font-size="12" '
                    f'fill="#1f2933">bbox fallback: {view}</text>'
                ),
                (
                    f'<text x="20" y="{canvas_height - 18:.0f}" '
                    'font-family="monospace" font-size="10" fill="#52606d">'
                    f'{_escape(error[:96])}</text>'
                ),
                "</svg>",
            ]
        ),
        encoding="utf-8",
    )
    return True


def _bbox_view_size(bbox: object, view: str) -> tuple[float, float]:
    xlen = float(getattr(bbox, "xlen", 0.0))
    ylen = float(getattr(bbox, "ylen", 0.0))
    zlen = float(getattr(bbox, "zlen", 0.0))
    if view == "top":
        return xlen, ylen
    if view == "front":
        return xlen, zlen
    return xlen, max(ylen, zlen)


def _import_cadquery() -> object:
    try:
        import cadquery as cq
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("CadQuery is required for STEP snapshot rendering") from exc
    return cq


def _safe_stem(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return cleaned.strip("_") or "snapshot"


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


__all__ = [
    "DEFAULT_SNAPSHOT_VIEWS",
    "StepSnapshotPackageResult",
    "StepSnapshotResult",
    "SvgSnapshotMetrics",
    "analyze_snapshot_package",
    "analyze_svg_snapshot",
    "generate_step_package_snapshots",
    "generate_step_snapshots",
]
