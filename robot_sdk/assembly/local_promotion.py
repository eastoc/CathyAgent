"""Promotion checks for local semantic subassembly solves."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LOCAL_SUBASSEMBLY_POSE_DELTA_WARNING_MM = 0.1
LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_WARNING_MM = 0.1
LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG = 0.5


@dataclass(frozen=True)
class LocalSubassemblyPromotionMetrics:
    """Max residual and pose-delta metrics for one local subassembly."""

    max_pose_delta_mm: float = 0.0
    max_origin_residual_mm: float = 0.0
    max_normal_residual_deg: float = 0.0
    max_tangent_residual_deg: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "max_pose_delta_mm": self.max_pose_delta_mm,
            "max_origin_residual_mm": self.max_origin_residual_mm,
            "max_normal_residual_deg": self.max_normal_residual_deg,
            "max_tangent_residual_deg": self.max_tangent_residual_deg,
        }


def build_local_subassembly_promotion_report(
    local_subassemblies: list[dict[str, Any]],
) -> dict[str, object]:
    """Return ready/blocked promotion records for solved local subassemblies."""

    ready: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    for subassembly in local_subassemblies:
        if not isinstance(subassembly, dict):
            continue
        metrics = local_subassembly_promotion_metrics(subassembly)
        item = {
            "name": str(subassembly.get("name") or ""),
            "role": local_subassembly_role(subassembly),
            "source": local_subassembly_source(subassembly),
            "part_ids": list(subassembly.get("part_ids") or []),
            **metrics.to_dict(),
        }
        if local_subassembly_is_promotion_ready(subassembly, metrics):
            ready.append(item)
        else:
            blocked.append(
                {
                    **item,
                    "solved": bool(subassembly.get("solved", True)),
                    "solve_error": subassembly.get("solve_error"),
                }
            )

    return {
        "ready_local_subassemblies": ready,
        "blocked_local_subassemblies": blocked,
        "promotion_thresholds": {
            "pose_delta_warning_mm": LOCAL_SUBASSEMBLY_POSE_DELTA_WARNING_MM,
            "origin_residual_warning_mm": LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_WARNING_MM,
            "angle_residual_warning_deg": LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG,
        },
    }


def ready_local_subassembly_names(local_subassemblies: list[dict[str, Any]]) -> set[str]:
    """Return names of local subassemblies that are ready for production export."""

    report = build_local_subassembly_promotion_report(local_subassemblies)
    return {
        str(item.get("name"))
        for item in report["ready_local_subassemblies"]
        if isinstance(item, dict) and item.get("name")
    }


def local_subassembly_promotion_metrics(
    subassembly: dict[str, Any],
) -> LocalSubassemblyPromotionMetrics:
    """Compute max pose/residual metrics from a local subassembly summary."""

    max_pose_delta = 0.0
    max_origin_residual = 0.0
    max_normal_residual = 0.0
    max_tangent_residual = 0.0

    pose_report = subassembly.get("pose_delta_report")
    if isinstance(pose_report, dict):
        value = pose_report.get("max_translation_delta_mm")
        if isinstance(value, (int, float)):
            max_pose_delta = max(max_pose_delta, float(value))

    residual_reports = subassembly.get("residual_reports")
    if isinstance(residual_reports, list):
        for residual in residual_reports:
            if not isinstance(residual, dict):
                continue
            value = residual.get("origin_delta_mm")
            if isinstance(value, (int, float)):
                max_origin_residual = max(max_origin_residual, float(value))
            value = residual.get("normal_angle_deg")
            if isinstance(value, (int, float)):
                max_normal_residual = max(max_normal_residual, float(value))
            value = residual.get("tangent_angle_deg")
            if isinstance(value, (int, float)):
                max_tangent_residual = max(max_tangent_residual, float(value))

    return LocalSubassemblyPromotionMetrics(
        max_pose_delta_mm=max_pose_delta,
        max_origin_residual_mm=max_origin_residual,
        max_normal_residual_deg=max_normal_residual,
        max_tangent_residual_deg=max_tangent_residual,
    )


def local_subassembly_is_promotion_ready(
    subassembly: dict[str, Any],
    metrics: LocalSubassemblyPromotionMetrics | None = None,
) -> bool:
    """Return true when a local subassembly is clean enough for production export."""

    metrics = metrics or local_subassembly_promotion_metrics(subassembly)
    if not bool(subassembly.get("solved", True)):
        return False
    if subassembly.get("solve_error"):
        return False
    return (
        metrics.max_pose_delta_mm <= LOCAL_SUBASSEMBLY_POSE_DELTA_WARNING_MM
        and metrics.max_origin_residual_mm
        <= LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_WARNING_MM
        and metrics.max_normal_residual_deg
        <= LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG
        and metrics.max_tangent_residual_deg
        <= LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG
    )


def local_subassembly_role(subassembly: dict[str, Any]) -> str:
    role = subassembly.get("role")
    if isinstance(role, str) and role:
        return role
    metadata = subassembly.get("spec_metadata")
    if isinstance(metadata, dict):
        role = metadata.get("role")
        if isinstance(role, str):
            return role
    return "local_subassembly"


def local_subassembly_source(subassembly: dict[str, Any]) -> str:
    source = subassembly.get("source")
    if isinstance(source, str) and source:
        return source
    metadata = subassembly.get("spec_metadata")
    if isinstance(metadata, dict):
        source = metadata.get("source")
        if isinstance(source, str):
            return source
    return "unknown"


__all__ = [
    "LOCAL_SUBASSEMBLY_ANGLE_RESIDUAL_WARNING_DEG",
    "LOCAL_SUBASSEMBLY_ORIGIN_RESIDUAL_WARNING_MM",
    "LOCAL_SUBASSEMBLY_POSE_DELTA_WARNING_MM",
    "LocalSubassemblyPromotionMetrics",
    "build_local_subassembly_promotion_report",
    "local_subassembly_is_promotion_ready",
    "local_subassembly_promotion_metrics",
    "local_subassembly_role",
    "local_subassembly_source",
    "ready_local_subassembly_names",
]
