"""Visual-geometry proxy checks for robot CAD outputs.

This module intentionally stays renderer-free.  It catches structural shapes
that are obviously wrong from layout/debug/bounding-box signals before a human
opens the STEP file, while leaving a later snapshot renderer room to add
image-based checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from robot_sdk.cad.snapshot import analyze_snapshot_package
from robot_sdk.layout.debug import LayoutDebugReport, build_layout_debug_report
from robot_sdk.structure.robot_structure_plan import RobotStructurePlan
from robot_sdk.types import KinematicModel, MechanicalLayout


VisualValidationSeverity = Literal["pass", "warning", "error"]

HIGH_DOF_THRESHOLD = 5
HORIZONTAL_CHAIN_RATIO = 4.0
PLANAR_Z_RATIO = 0.18
PARALLEL_AXIS_DOT_THRESHOLD = 0.96
SNAPSHOT_FLAT_ASPECT_RATIO = 5.5
SNAPSHOT_FLAT_HEIGHT_RATIO = 0.18
LOCAL_SUBASSEMBLY_FLAT_ASPECT_RATIO = 7.0
LOCAL_SUBASSEMBLY_FLAT_HEIGHT_RATIO = 0.14
SILHOUETTE_VIEWS = {"front", "isometric"}
FOCUS_SUBASSEMBLY_TERMS = ("wrist", "forearm", "tool", "end_effector")
REQUIRED_STRUCTURE_ROLES = {"base", "shoulder", "elbow", "wrist1", "wrist2", "wrist3", "tool"}
REQUIRED_SOURCE_JOINT_REVIEW_SNAPSHOTS = {
    "base_shoulder",
    "upper_arm",
    "forearm",
    "wrist_l5_j6",
    "tool_end",
}
MIN_STRUCTURE_ELEVATION_RATIO = 0.18
MIN_STRUCTURE_ELEVATION_MM = 20.0


@dataclass(frozen=True)
class VisualValidationIssue:
    """One visual-geometry validation item."""

    code: str
    severity: VisualValidationSeverity
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VisualValidationReport:
    """Renderer-free visual validation report."""

    issues: list[VisualValidationIssue]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[VisualValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[VisualValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def passes(self) -> list[VisualValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "pass"]


def validate_visual_geometry(
    *,
    layout: MechanicalLayout | None = None,
    kinematic_model: KinematicModel | None = None,
    structure_plan: RobotStructurePlan | None = None,
    step_package_result: object | None = None,
    snapshot_result: object | None = None,
) -> VisualValidationReport:
    """Validate CAD visual plausibility from deterministic geometry signals."""

    issues: list[VisualValidationIssue] = []
    dof = _dof(kinematic_model, layout)
    structure_backed = _has_structure_intent(layout, structure_plan)

    if (
        layout is None
        and structure_plan is None
        and step_package_result is None
        and snapshot_result is None
    ):
        _add(
            issues,
            "visual_artifacts_missing",
            "warning",
            "Visual geometry screening skipped because no layout or STEP package was provided.",
            {},
        )
        return VisualValidationReport(issues=issues)

    debug_report = _safe_debug_report(layout)
    if layout is not None:
        chain_metrics = _layout_chain_metrics(debug_report)
        if _is_high_dof(dof) and not structure_backed and _looks_like_horizontal_bead_chain(chain_metrics):
            _add(
                issues,
                "planar_serial_chain_visual_rejected",
                "error",
                "High-DOF robot layout looks like a flat serial bead chain; production CAD requires an explicit structure plan.",
                chain_metrics,
            )
        else:
            _add(
                issues,
                "layout_visual_topology_screened",
                "pass",
                "Mechanical layout passed renderer-free topology screening.",
                {
                    **chain_metrics,
                    "structure_backed": structure_backed,
                    "dof": dof,
                },
            )

    bbox_metrics = _step_package_bbox_metrics(step_package_result)
    if bbox_metrics:
        if _is_high_dof(dof) and not structure_backed and _looks_like_horizontal_bbox(bbox_metrics):
            _add(
                issues,
                "horizontal_bead_bbox_rejected",
                "error",
                "Whole-machine STEP bounding box is extremely elongated and thin for a high-DOF robot without structure intent.",
                bbox_metrics,
            )
        else:
            _add(
                issues,
                "step_bbox_visual_screened",
                "pass",
                "Whole-machine STEP bounding box passed visual geometry screening.",
                bbox_metrics,
            )

    _validate_structure_visual_cues(
        issues,
        layout=layout,
        structure_plan=structure_plan,
        dof=dof,
        structure_backed=structure_backed,
    )
    _validate_snapshots(
        issues,
        snapshot_result,
        step_package_result,
        dof=dof,
        structure_backed=structure_backed,
    )
    return VisualValidationReport(issues=issues)


def _dof(
    kinematic_model: KinematicModel | None,
    layout: MechanicalLayout | None,
) -> int | None:
    if kinematic_model is not None:
        return len(kinematic_model.joints)
    if layout is not None:
        return len(layout.joints)
    return None


def _is_high_dof(dof: int | None) -> bool:
    return dof is not None and dof >= HIGH_DOF_THRESHOLD


def _has_structure_intent(
    layout: MechanicalLayout | None,
    structure_plan: RobotStructurePlan | None,
) -> bool:
    if structure_plan is not None:
        return True
    metadata = layout.metadata if layout is not None else {}
    return (
        metadata.get("layout_template") == "structure_plan"
        or isinstance(metadata.get("structure_plan"), dict)
    )


def _safe_debug_report(layout: MechanicalLayout | None) -> LayoutDebugReport | None:
    if layout is None:
        return None
    try:
        return build_layout_debug_report(layout)
    except Exception:
        return None


def _layout_chain_metrics(report: LayoutDebugReport | None) -> dict[str, object]:
    if report is None:
        return {"debug_report_available": False}

    joint_origins = [joint.axis_origin for joint in report.joints]
    axis_directions = [joint.axis_direction for joint in report.joints]
    x_extent, y_extent, z_extent = _extents(joint_origins)
    max_minor_extent = max(y_extent, z_extent)
    axis_family_count = _axis_family_count(axis_directions)
    link_primitives = [
        str(link.primitive_type)
        for link in report.links
        if link.primitive_type is not None
    ]
    non_straight_primitives = [
        primitive
        for primitive in link_primitives
        if primitive not in {"straight_link", "None"}
    ]
    return {
        "debug_report_available": True,
        "joint_count": len(report.joints),
        "joint_x_extent_mm": x_extent,
        "joint_y_extent_mm": y_extent,
        "joint_z_extent_mm": z_extent,
        "joint_major_minor_ratio": _safe_ratio(x_extent, max_minor_extent),
        "axis_family_count": axis_family_count,
        "link_primitive_types": link_primitives,
        "non_straight_primitive_count": len(non_straight_primitives),
    }


def _looks_like_horizontal_bead_chain(metrics: dict[str, object]) -> bool:
    if not bool(metrics.get("debug_report_available")):
        return False
    x_extent = float(metrics.get("joint_x_extent_mm") or 0.0)
    z_extent = float(metrics.get("joint_z_extent_mm") or 0.0)
    ratio = float(metrics.get("joint_major_minor_ratio") or 0.0)
    axis_family_count = int(metrics.get("axis_family_count") or 0)
    return (
        x_extent > 150.0
        and z_extent <= max(25.0, PLANAR_Z_RATIO * x_extent)
        and ratio >= HORIZONTAL_CHAIN_RATIO
        and axis_family_count <= 1
    )


def _step_package_bbox_metrics(package_result: object | None) -> dict[str, object]:
    if package_result is None:
        return {}
    whole_export = getattr(package_result, "whole_machine_export", None)
    bbox = getattr(whole_export, "bbox", None)
    if bbox is None or not bool(getattr(bbox, "valid", False)):
        return {}
    xlen = float(getattr(bbox, "xlen", 0.0))
    ylen = float(getattr(bbox, "ylen", 0.0))
    zlen = float(getattr(bbox, "zlen", 0.0))
    return {
        "whole_bbox": bbox.to_dict() if callable(getattr(bbox, "to_dict", None)) else {},
        "whole_xlen_mm": xlen,
        "whole_ylen_mm": ylen,
        "whole_zlen_mm": zlen,
        "whole_major_minor_ratio": _safe_ratio(xlen, max(ylen, zlen)),
        "subassembly_count": len(getattr(package_result, "subassemblies", []) or []),
    }


def _looks_like_horizontal_bbox(metrics: dict[str, object]) -> bool:
    xlen = float(metrics.get("whole_xlen_mm") or 0.0)
    zlen = float(metrics.get("whole_zlen_mm") or 0.0)
    ratio = float(metrics.get("whole_major_minor_ratio") or 0.0)
    return (
        xlen > 200.0
        and zlen <= max(40.0, PLANAR_Z_RATIO * xlen)
        and ratio >= HORIZONTAL_CHAIN_RATIO
    )


def _validate_structure_visual_cues(
    issues: list[VisualValidationIssue],
    *,
    layout: MechanicalLayout | None,
    structure_plan: RobotStructurePlan | None,
    dof: int | None,
    structure_backed: bool,
) -> None:
    if not _is_high_dof(dof) or not structure_backed:
        return
    metrics = _structure_visual_metrics(layout, structure_plan)
    if not metrics:
        _add(
            issues,
            "structure_visual_cues_unavailable",
            "warning",
            "Structure-backed high-DOF robot has no parseable station geometry for visual cue screening.",
            {},
        )
        return

    missing_roles = list(metrics.get("missing_station_roles") or [])
    if missing_roles:
        _add(
            issues,
            "structure_visual_roles_missing",
            "error",
            "Structure-backed high-DOF robot is missing visible base/shoulder/elbow/wrist/tool station roles.",
            metrics,
        )
        return

    z_extent = float(metrics.get("station_z_extent_mm") or 0.0)
    major_extent = float(metrics.get("station_major_horizontal_extent_mm") or 0.0)
    z_ratio = float(metrics.get("station_z_to_horizontal_ratio") or 0.0)
    elevated_count = int(metrics.get("elevated_station_count") or 0)
    min_z_extent = max(MIN_STRUCTURE_ELEVATION_MM, major_extent * MIN_STRUCTURE_ELEVATION_RATIO)
    if z_extent < min_z_extent or z_ratio < MIN_STRUCTURE_ELEVATION_RATIO or elevated_count < 3:
        _add(
            issues,
            "structure_body_elevation_rejected",
            "error",
            "Structure-backed high-DOF robot stations do not show enough vertical body elevation for a shoulder/elbow/wrist arm.",
            {
                **metrics,
                "min_station_z_extent_mm": min_z_extent,
                "min_station_z_to_horizontal_ratio": MIN_STRUCTURE_ELEVATION_RATIO,
                "min_elevated_station_count": 3,
            },
        )
        return

    _add(
        issues,
        "structure_visual_cues_screened",
        "pass",
        "Structure station roles and vertical body elevation passed visual cue screening.",
        {
            **metrics,
            "min_station_z_extent_mm": min_z_extent,
            "min_station_z_to_horizontal_ratio": MIN_STRUCTURE_ELEVATION_RATIO,
            "min_elevated_station_count": 3,
        },
    )


def _structure_visual_metrics(
    layout: MechanicalLayout | None,
    structure_plan: RobotStructurePlan | None,
) -> dict[str, object]:
    stations = _structure_stations(layout, structure_plan)
    if not stations:
        return {}
    roles = sorted({station["role"] for station in stations})
    origins = [station["origin"] for station in stations]
    x_extent, y_extent, z_extent = _extents(origins)
    major_horizontal = max(x_extent, y_extent)
    min_z = min(origin[2] for origin in origins)
    elevated_threshold = max(5.0, major_horizontal * 0.05)
    elevated_roles = sorted(
        {
            station["role"]
            for station in stations
            if station["origin"][2] > min_z + elevated_threshold
        }
    )
    wrist_roles = sorted(role for role in roles if role.startswith("wrist"))
    return {
        "station_count": len(stations),
        "station_roles": roles,
        "missing_station_roles": sorted(REQUIRED_STRUCTURE_ROLES - set(roles)),
        "wrist_station_roles": wrist_roles,
        "station_x_extent_mm": x_extent,
        "station_y_extent_mm": y_extent,
        "station_z_extent_mm": z_extent,
        "station_major_horizontal_extent_mm": major_horizontal,
        "station_z_to_horizontal_ratio": _safe_ratio(z_extent, major_horizontal),
        "elevated_station_roles": elevated_roles,
        "elevated_station_count": len(elevated_roles),
    }


def _structure_stations(
    layout: MechanicalLayout | None,
    structure_plan: RobotStructurePlan | None,
) -> list[dict[str, object]]:
    plan = structure_plan
    if plan is None and layout is not None:
        metadata_plan = layout.metadata.get("structure_plan")
        if isinstance(metadata_plan, dict):
            return _station_dicts_from_payload(metadata_plan.get("stations"))
    if plan is None:
        return []
    return [
        {
            "id": station.id,
            "role": station.role,
            "origin": station.origin,
        }
        for station in plan.stations
    ]


def _station_dicts_from_payload(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        return []
    stations: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        origin = _vec3(item.get("origin"))
        if not role or origin is None:
            continue
        stations.append(
            {
                "id": str(item.get("id") or "").strip(),
                "role": role,
                "origin": origin,
            }
        )
    return stations


def _validate_snapshots(
    issues: list[VisualValidationIssue],
    snapshot_result: object | None,
    step_package_result: object | None,
    *,
    dof: int | None,
    structure_backed: bool,
) -> None:
    if snapshot_result is None:
        if step_package_result is None:
            _add(
                issues,
                "snapshot_render_not_configured",
                "warning",
                "Image-based STEP snapshot review is not configured yet; current check uses layout and bounding-box proxies.",
                {"next_step": "Generate STEP SVG snapshots after export."},
            )
        else:
            _add(
                issues,
                "visual_snapshot_missing",
                "warning",
                "STEP package was exported but no visual snapshot result was provided.",
                {"next_step": "Call generate_step_package_snapshots(...) before validation."},
            )
        return

    generated_count = int(getattr(snapshot_result, "generated_count", 0) or 0)
    real_count = int(getattr(snapshot_result, "real_step_snapshot_count", 0) or 0)
    fallback_count = int(getattr(snapshot_result, "fallback_count", 0) or 0)
    details = _snapshot_details(snapshot_result)
    if real_count > 0:
        _add(
            issues,
            "visual_snapshot_generated",
            "pass",
            f"{real_count} renderer-backed STEP SVG snapshots were generated.",
            details,
        )
    elif generated_count > 0:
        _add(
            issues,
            "visual_snapshot_fallback_only",
            "warning",
            "Only fallback bounding-box SVG snapshots were generated; STEP renderer-backed snapshots are unavailable.",
            details,
        )
    else:
        _add(
            issues,
            "visual_snapshot_generation_failed",
            "warning",
            "No visual snapshots were generated for the exported STEP package.",
            details,
        )

    if fallback_count > 0 and real_count > 0:
        _add(
            issues,
            "visual_snapshot_fallback_used",
            "warning",
            "Some STEP snapshots fell back to bounding-box SVG previews.",
            details,
        )
    _validate_snapshot_silhouette(
        issues,
        snapshot_result,
        dof=dof,
        structure_backed=structure_backed,
        base_details=details,
    )
    _validate_focus_subassembly_snapshots(
        issues,
        snapshot_result,
        base_details=details,
    )
    _validate_source_joint_review_snapshots(
        issues,
        snapshot_result,
        step_package_result,
        dof=dof,
        base_details=details,
    )


def _validate_snapshot_silhouette(
    issues: list[VisualValidationIssue],
    snapshot_result: object,
    *,
    dof: int | None,
    structure_backed: bool,
    base_details: dict[str, object],
) -> None:
    metrics = analyze_snapshot_package(snapshot_result)
    if not metrics:
        if int(getattr(snapshot_result, "real_step_snapshot_count", 0) or 0) > 0:
            _add(
                issues,
                "visual_snapshot_metrics_unavailable",
                "warning",
                "Renderer-backed snapshots were generated, but SVG silhouette metrics could not be parsed.",
                base_details,
            )
        return

    metric_details = {
        **base_details,
        "silhouette_metrics": [metric.to_dict() for metric in metrics],
        "flat_aspect_ratio_threshold": SNAPSHOT_FLAT_ASPECT_RATIO,
        "flat_height_ratio_threshold": SNAPSHOT_FLAT_HEIGHT_RATIO,
    }
    side_metrics = [
        metric
        for metric in metrics
        if metric.view in SILHOUETTE_VIEWS
    ]
    candidates = side_metrics or metrics
    flattest = max(candidates, key=lambda metric: metric.aspect_ratio)
    if (
        _is_high_dof(dof)
        and flattest.aspect_ratio >= SNAPSHOT_FLAT_ASPECT_RATIO
        and flattest.height_ratio <= SNAPSHOT_FLAT_HEIGHT_RATIO
    ):
        code = (
            "snapshot_horizontal_silhouette_rejected"
            if not structure_backed
            else "snapshot_structure_silhouette_rejected"
        )
        _add(
            issues,
            code,
            "error",
            "Renderer-backed STEP snapshot silhouette is too flat and elongated for a high-DOF robot arm.",
            {**metric_details, "triggering_view": flattest.to_dict()},
        )
        return

    _add(
        issues,
        "visual_snapshot_silhouette_screened",
        "pass",
        "Renderer-backed STEP SVG silhouette passed flat-chain screening.",
        metric_details,
    )


def _validate_focus_subassembly_snapshots(
    issues: list[VisualValidationIssue],
    snapshot_result: object,
    *,
    base_details: dict[str, object],
) -> None:
    metrics = [
        metric
        for metric in analyze_snapshot_package(snapshot_result)
        if _is_focus_subassembly_metric(metric)
    ]
    if not metrics:
        if getattr(snapshot_result, "subassembly_snapshots", None):
            _add(
                issues,
                "focus_subassembly_snapshot_metrics_unavailable",
                "warning",
                "Focused subassembly snapshots exist, but renderer-backed SVG metrics could not be parsed.",
                base_details,
            )
        return

    metric_details = {
        **base_details,
        "focus_subassembly_metrics": [metric.to_dict() for metric in metrics],
        "local_flat_aspect_ratio_threshold": LOCAL_SUBASSEMBLY_FLAT_ASPECT_RATIO,
        "local_flat_height_ratio_threshold": LOCAL_SUBASSEMBLY_FLAT_HEIGHT_RATIO,
    }
    candidates = [
        metric
        for metric in metrics
        if metric.view in SILHOUETTE_VIEWS
    ] or metrics
    flattest = max(candidates, key=lambda metric: metric.aspect_ratio)
    if (
        flattest.aspect_ratio >= LOCAL_SUBASSEMBLY_FLAT_ASPECT_RATIO
        and flattest.height_ratio <= LOCAL_SUBASSEMBLY_FLAT_HEIGHT_RATIO
    ):
        _add(
            issues,
            "focus_subassembly_silhouette_rejected",
            "error",
            "Focused subassembly STEP snapshot silhouette is excessively thin or elongated.",
            {**metric_details, "triggering_view": flattest.to_dict()},
        )
        return

    _add(
        issues,
        "focus_subassembly_snapshots_screened",
        "pass",
        "Focused subassembly STEP SVG snapshots passed local silhouette screening.",
        metric_details,
    )


def _validate_source_joint_review_snapshots(
    issues: list[VisualValidationIssue],
    snapshot_result: object,
    step_package_result: object | None,
    *,
    dof: int | None,
    base_details: dict[str, object],
) -> None:
    if not _is_high_dof(dof):
        return
    if str(getattr(step_package_result, "whole_machine_assembly_source", "")) != "source_joint":
        return

    snapshots = getattr(snapshot_result, "subassembly_snapshots", []) or []
    renderer_backed_targets = {
        str(getattr(snapshot, "target_name", "") or "")
        for snapshot in snapshots
        if bool(getattr(snapshot, "generated", False))
        and not bool(getattr(snapshot, "fallback_used", False))
    }
    missing = sorted(REQUIRED_SOURCE_JOINT_REVIEW_SNAPSHOTS - renderer_backed_targets)
    details = {
        **base_details,
        "required_source_joint_review_snapshots": sorted(
            REQUIRED_SOURCE_JOINT_REVIEW_SNAPSHOTS
        ),
        "renderer_backed_review_snapshot_targets": sorted(renderer_backed_targets),
        "missing_review_snapshot_targets": missing,
    }
    if missing:
        _add(
            issues,
            "source_joint_review_snapshots_missing",
            "error",
            "High-DOF source_joint output is missing renderer-backed review snapshots.",
            details,
        )
        return

    _add(
        issues,
        "source_joint_review_snapshots_ready",
        "pass",
        "High-DOF source_joint review subassemblies have renderer-backed STEP snapshots.",
        details,
    )


def _is_focus_subassembly_metric(metric: object) -> bool:
    target_name = str(getattr(metric, "target_name", "") or "")
    if not target_name or target_name == "whole_machine":
        return False
    lowered = target_name.lower()
    if any(term in lowered for term in FOCUS_SUBASSEMBLY_TERMS):
        return True
    return len(target_name.split("_")) >= 3


def _snapshot_details(snapshot_result: object) -> dict[str, object]:
    to_dict = getattr(snapshot_result, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, dict):
            return data
    snapshots = getattr(snapshot_result, "snapshots", []) or []
    return {
        "generated_count": int(getattr(snapshot_result, "generated_count", 0) or 0),
        "real_step_snapshot_count": int(
            getattr(snapshot_result, "real_step_snapshot_count", 0) or 0
        ),
        "fallback_count": int(getattr(snapshot_result, "fallback_count", 0) or 0),
        "snapshots": [
            item.to_dict() if callable(getattr(item, "to_dict", None)) else str(item)
            for item in snapshots
        ],
    }


def _extents(points: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    if not points:
        return (0.0, 0.0, 0.0)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    return (
        max(xs) - min(xs),
        max(ys) - min(ys),
        max(zs) - min(zs),
    )


def _vec3(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None


def _axis_family_count(directions: list[tuple[float, float, float]]) -> int:
    families: list[tuple[float, float, float]] = []
    for direction in directions:
        unit = _unit(direction)
        if unit is None:
            continue
        if any(abs(_dot(unit, family)) >= PARALLEL_AXIS_DOT_THRESHOLD for family in families):
            continue
        families.append(unit)
    return len(families)


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float] | None:
    length = sum(value * value for value in vector) ** 0.5
    if length <= 1e-9:
        return None
    return tuple(value / length for value in vector)  # type: ignore[return-value]


def _dot(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 1e-9:
        return float("inf") if numerator > 0 else 0.0
    return numerator / denominator


def _add(
    issues: list[VisualValidationIssue],
    code: str,
    severity: VisualValidationSeverity,
    message: str,
    details: dict[str, object],
) -> None:
    issues.append(
        VisualValidationIssue(
            code=code,
            severity=severity,
            message=message,
            details=details,
        )
    )


__all__ = [
    "VisualValidationIssue",
    "VisualValidationReport",
    "validate_visual_geometry",
]
