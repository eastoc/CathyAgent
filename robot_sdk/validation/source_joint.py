"""Validation gates for build123d source-joint robot assemblies.

The source_joint path does not use CadQuery's global assembly solver. Its
promotion checks therefore focus on source facts: every adjacent part is joined
by a recorded part-local mate, the mate graph is one connected chain from base
to tool, and the exported STEP package has enough non-degenerate geometry to
review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from robot_sdk.cad.bbox import CadBoundingBox, bounding_box_from_cad_object


SourceJointValidationSeverity = Literal["pass", "warning", "error"]

SOURCE_ADJACENT_BBOX_OVERLAP_ERROR_RATIO = 0.40
SOURCE_NONADJACENT_BBOX_OVERLAP_ERROR_RATIO = 0.45
SOURCE_BBOX_OVERLAP_REPORT_LIMIT = 12


@dataclass(frozen=True)
class SourceJointValidationIssue:
    """One source_joint validation item."""

    code: str
    severity: SourceJointValidationSeverity
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceJointValidationReport:
    """Validation report for source-level build123d mate assembly."""

    issues: list[SourceJointValidationIssue]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[SourceJointValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[SourceJointValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def passes(self) -> list[SourceJointValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "pass"]


def validate_source_joint_assembly(
    *,
    cad_result: Any | None = None,
    step_package_result: Any | None = None,
) -> SourceJointValidationReport:
    """Validate a source_joint production assembly without solver assumptions."""

    issues: list[SourceJointValidationIssue] = []
    if not _is_source_joint_result(cad_result):
        return SourceJointValidationReport(issues=issues)

    part_ids = _source_part_ids(cad_result)
    source_mates = _source_mates(cad_result)
    _validate_source_mate_records(issues, source_mates)
    _validate_source_chain_graph(issues, part_ids, source_mates)
    _validate_source_part_bbox_interference(issues, cad_result, source_mates)
    _validate_source_step_package_gate(issues, step_package_result)
    _validate_source_review_bbox_gate(issues, cad_result, step_package_result)
    return SourceJointValidationReport(issues=issues)


def _validate_source_mate_records(
    issues: list[SourceJointValidationIssue],
    source_mates: list[dict[str, Any]],
) -> None:
    if not source_mates:
        _add(
            issues,
            "source_joint_mates_missing",
            "error",
            "Source-joint assembly has no recorded source mate relations.",
        )
        return

    incomplete: list[dict[str, object]] = []
    for index, mate in enumerate(source_mates, start=1):
        fixed = _endpoint(mate, "fixed_endpoint")
        moving = _endpoint(mate, "moving_endpoint")
        if not fixed.get("part") or not moving.get("part"):
            incomplete.append({"index": index, "mate": mate})
            continue
        if not fixed.get("frame") or not moving.get("frame"):
            incomplete.append({"index": index, "mate": mate})
            continue
        if not isinstance(fixed.get("location"), dict):
            incomplete.append({"index": index, "mate": mate})
            continue
        if not isinstance(moving.get("location"), dict):
            incomplete.append({"index": index, "mate": mate})

    if incomplete:
        _add(
            issues,
            "source_joint_mates_incomplete",
            "error",
            "One or more source mate records are missing part, frame, or local location metadata.",
            {"incomplete_mates": incomplete},
        )
        return

    _add(
        issues,
        "source_joint_mates_complete",
        "pass",
        f"Recorded {len(source_mates)} complete source-level mate relations.",
        {"source_mate_count": len(source_mates)},
    )


def _validate_source_chain_graph(
    issues: list[SourceJointValidationIssue],
    part_ids: list[str],
    source_mates: list[dict[str, Any]],
) -> None:
    if not part_ids:
        _add(
            issues,
            "source_joint_parts_missing",
            "error",
            "Source-joint assembly did not expose a part catalog.",
        )
        return

    expected_edges = max(0, len(part_ids) - 1)
    edges = _source_edges(source_mates)
    unknown_parts = sorted(
        {
            part
            for edge in edges
            for part in edge
            if part not in set(part_ids)
        }
    )
    duplicate_edges = _duplicate_edges(edges)
    component_report = _component_report(part_ids, edges)
    details = {
        "part_ids": part_ids,
        "source_mate_count": len(source_mates),
        "expected_serial_edge_count": expected_edges,
        "edges": [{"fixed_part_id": left, "moving_part_id": right} for left, right in edges],
        "unknown_parts": unknown_parts,
        "duplicate_edges": duplicate_edges,
        **component_report,
    }

    if unknown_parts:
        _add(
            issues,
            "source_joint_unknown_parts",
            "error",
            "Source mate graph references parts that are not in the source part catalog.",
            details,
        )
        return
    if len(edges) != expected_edges:
        _add(
            issues,
            "source_joint_serial_edge_count_mismatch",
            "error",
            "Source mate graph does not have the expected serial-chain edge count.",
            details,
        )
        return
    if duplicate_edges:
        _add(
            issues,
            "source_joint_duplicate_edges",
            "error",
            "Source mate graph contains duplicate part-pair edges.",
            details,
        )
        return
    if int(component_report["cluster_count"]) != 1 or not bool(
        component_report["base_to_terminal_connected"]
    ):
        _add(
            issues,
            "source_joint_chain_disconnected",
            "error",
            "Source mate graph is not connected from base to terminal part.",
            details,
        )
        return

    _add(
        issues,
        "source_joint_chain_connected",
        "pass",
        "Source mate graph forms a connected serial chain from base to terminal part.",
        details,
    )
    _add(
        issues,
        "source_joint_promotion_gate_ready",
        "pass",
        "Source-joint production gate is ready: complete mates, connected chain, no solver promotion required.",
        details,
    )


def _validate_source_step_package_gate(
    issues: list[SourceJointValidationIssue],
    step_package_result: Any | None,
) -> None:
    if step_package_result is None:
        _add(
            issues,
            "source_joint_step_package_missing",
            "warning",
            "Source-joint STEP package result was not provided.",
        )
        return
    assembly_source = str(
        getattr(step_package_result, "whole_machine_assembly_source", "") or ""
    )
    all_exports = list(getattr(step_package_result, "all_exports", []) or [])
    missing = [
        str(getattr(export, "path", ""))
        for export in all_exports
        if not bool(getattr(export, "exists", False))
        or int(getattr(export, "size_bytes", 0) or 0) <= 0
    ]
    invalid_bbox = [
        str(getattr(export, "path", ""))
        for export in all_exports
        if not bool(getattr(getattr(export, "bbox", None), "valid", False))
    ]
    details = {
        "whole_machine_assembly_source": assembly_source,
        "file_count": len(all_exports),
        "missing_or_empty_exports": missing,
        "invalid_bbox_exports": invalid_bbox,
    }
    if assembly_source != "source_joint":
        _add(
            issues,
            "source_joint_step_package_wrong_source",
            "error",
            "STEP package is not marked as source_joint production output.",
            details,
        )
        return
    if missing:
        _add(
            issues,
            "source_joint_step_package_incomplete",
            "error",
            "One or more source_joint STEP exports are missing or empty.",
            details,
        )
        return
    if invalid_bbox:
        _add(
            issues,
            "source_joint_step_package_bbox_invalid",
            "warning",
            "One or more source_joint STEP exports have unavailable or invalid bounding boxes.",
            details,
        )
        return
    _add(
        issues,
        "source_joint_step_package_ready",
        "pass",
        "Source-joint STEP package exports are present and expose non-degenerate bounding boxes.",
        details,
    )


def _validate_source_review_bbox_gate(
    issues: list[SourceJointValidationIssue],
    cad_result: Any | None,
    step_package_result: Any | None,
) -> None:
    metadata = _source_metadata(cad_result)
    if int(metadata.get("joint_count") or 0) < 5:
        return
    if step_package_result is None:
        return

    review_names = {
        "base_shoulder",
        "upper_arm",
        "forearm",
        "wrist_l5_j6",
        "tool_end",
    }
    by_name = {
        str(getattr(subassembly, "name", "") or ""): subassembly
        for subassembly in getattr(step_package_result, "subassemblies", []) or []
    }
    missing = sorted(review_names - set(by_name))
    if missing:
        _add(
            issues,
            "source_joint_review_subassemblies_missing",
            "error",
            "High-DOF source_joint package is missing review subassemblies.",
            {"missing_review_subassemblies": missing},
        )
        return

    metrics: dict[str, dict[str, object]] = {}
    failures: list[dict[str, object]] = []
    for name in sorted(review_names):
        subassembly = by_name[name]
        bbox = getattr(getattr(subassembly, "assembly_export", None), "bbox", None)
        item = _review_bbox_metrics(name, bbox)
        metrics[name] = item
        failure = _review_bbox_failure(name, item)
        if failure:
            failures.append(failure)

    details = {
        "review_bbox_metrics": metrics,
        "review_bbox_failures": failures,
    }
    if failures:
        _add(
            issues,
            "source_joint_review_bbox_ratio_failed",
            "error",
            (
                "One or more source_joint review subassemblies have implausible "
                "bbox proportions for a 5+ DOF robot arm."
            ),
            details,
        )
        return
    _add(
        issues,
        "source_joint_review_bbox_ratios_ready",
        "pass",
        (
            "Source-joint review subassembly bbox proportions passed base, arm, "
            "wrist, and tool checks."
        ),
        details,
    )


def _validate_source_part_bbox_interference(
    issues: list[SourceJointValidationIssue],
    cad_result: Any | None,
    source_mates: list[dict[str, Any]],
) -> None:
    catalog = getattr(cad_result, "part_catalog", None)
    part_ids_method = getattr(catalog, "part_ids", None)
    require_method = getattr(catalog, "require", None)
    if not callable(part_ids_method) or not callable(require_method):
        _add(
            issues,
            "source_joint_part_bbox_unavailable",
            "warning",
            "Source-joint part catalog is unavailable, so part-level bbox interference screening was skipped.",
        )
        return

    bboxes: dict[str, CadBoundingBox] = {}
    missing: list[str] = []
    for part_id in [str(item) for item in part_ids_method()]:
        try:
            part = require_method(part_id)
            bbox = bounding_box_from_cad_object(getattr(part, "solid", None))
        except Exception:
            bbox = None
        if bbox is None or not bbox.valid:
            missing.append(part_id)
            continue
        bboxes[part_id] = bbox

    if missing:
        _add(
            issues,
            "source_joint_part_bbox_partial",
            "warning",
            "Some source-joint parts do not expose valid bboxes for interference screening.",
            {"missing_part_bbox_ids": missing},
        )

    adjacent_pairs = {
        tuple(sorted(edge))
        for edge in _source_edges(source_mates)
        if len(edge) == 2
    }
    overlap_failures: list[dict[str, object]] = []
    overlap_observations: list[dict[str, object]] = []
    part_items = list(bboxes.items())
    for index, (left_id, left_bbox) in enumerate(part_items):
        for right_id, right_bbox in part_items[index + 1 :]:
            overlap_volume = _bbox_intersection_volume(left_bbox, right_bbox)
            if overlap_volume <= 1e-6:
                continue
            left_volume = _bbox_volume(left_bbox)
            right_volume = _bbox_volume(right_bbox)
            smaller_volume = min(left_volume, right_volume)
            if smaller_volume <= 1e-6:
                continue
            ratio = overlap_volume / smaller_volume
            pair = tuple(sorted((left_id, right_id)))
            adjacent = pair in adjacent_pairs
            threshold = (
                SOURCE_ADJACENT_BBOX_OVERLAP_ERROR_RATIO
                if adjacent
                else SOURCE_NONADJACENT_BBOX_OVERLAP_ERROR_RATIO
            )
            item = {
                "left_part_id": left_id,
                "right_part_id": right_id,
                "adjacent_source_mate": adjacent,
                "overlap_volume": overlap_volume,
                "overlap_to_smaller_bbox_ratio": ratio,
                "threshold": threshold,
            }
            if _is_allowed_mount_overlap(pair):
                overlap_observations.append({**item, "allowed": True})
                continue
            if ratio >= threshold:
                overlap_failures.append(item)
            else:
                overlap_observations.append(item)

    details = {
        "checked_part_count": len(bboxes),
        "missing_part_bbox_ids": missing,
        "adjacent_overlap_error_ratio": SOURCE_ADJACENT_BBOX_OVERLAP_ERROR_RATIO,
        "nonadjacent_overlap_error_ratio": SOURCE_NONADJACENT_BBOX_OVERLAP_ERROR_RATIO,
        "overlap_failures": overlap_failures[:SOURCE_BBOX_OVERLAP_REPORT_LIMIT],
        "overlap_observation_count": len(overlap_observations),
    }
    if overlap_failures:
        _add(
            issues,
            "source_joint_part_bbox_interference",
            "error",
            "Source-joint assembly has excessive part-level bbox overlap; this usually indicates visible interference or pass-through geometry.",
            details,
        )
        return

    _add(
        issues,
        "source_joint_part_bbox_clearance_screened",
        "pass",
        "Source-joint part-level bbox overlap stayed within coarse clearance thresholds.",
        details,
    )


def _bbox_volume(bbox: CadBoundingBox) -> float:
    return max(0.0, bbox.xlen) * max(0.0, bbox.ylen) * max(0.0, bbox.zlen)


def _bbox_intersection_volume(left: CadBoundingBox, right: CadBoundingBox) -> float:
    xlen = min(left.xmax, right.xmax) - max(left.xmin, right.xmin)
    ylen = min(left.ymax, right.ymax) - max(left.ymin, right.ymin)
    zlen = min(left.zmax, right.zmax) - max(left.zmin, right.zmin)
    if xlen <= 0.0 or ylen <= 0.0 or zlen <= 0.0:
        return 0.0
    return xlen * ylen * zlen


def _is_allowed_mount_overlap(pair: tuple[str, str]) -> bool:
    return pair == ("J1", "base")


def _review_bbox_metrics(name: str, bbox: Any | None) -> dict[str, object]:
    if bbox is None or not bool(getattr(bbox, "valid", False)):
        return {"name": name, "bbox_valid": False}
    xlen = float(getattr(bbox, "xlen", 0.0))
    ylen = float(getattr(bbox, "ylen", 0.0))
    zlen = float(getattr(bbox, "zlen", 0.0))
    cross = max(ylen, zlen, 1e-9)
    major = max(xlen, ylen, zlen, 1e-9)
    return {
        "name": name,
        "bbox_valid": True,
        "bbox": bbox.to_dict() if callable(getattr(bbox, "to_dict", None)) else {},
        "x_to_cross_ratio": xlen / cross,
        "z_to_x_ratio": zlen / max(xlen, 1e-9),
        "y_to_x_ratio": ylen / max(xlen, 1e-9),
        "minor_to_major_ratio": min(xlen, ylen, zlen) / major,
    }


def _review_bbox_failure(
    name: str,
    metrics: dict[str, object],
) -> dict[str, object] | None:
    if not bool(metrics.get("bbox_valid")):
        return {"name": name, "reason": "bbox missing or degenerate", "metrics": metrics}
    x_to_cross = float(metrics["x_to_cross_ratio"])
    z_to_x = float(metrics["z_to_x_ratio"])
    y_to_x = float(metrics["y_to_x_ratio"])
    minor_to_major = float(metrics["minor_to_major_ratio"])

    if name == "base_shoulder":
        if z_to_x < 0.2 or y_to_x < 0.3:
            return {
                "name": name,
                "reason": "base/shoulder lacks vertical mass or lateral support",
                "metrics": metrics,
                "thresholds": {"min_z_to_x_ratio": 0.2, "min_y_to_x_ratio": 0.3},
            }
        return None
    if name in {"upper_arm", "forearm"}:
        if x_to_cross < 2.0:
            return {
                "name": name,
                "reason": "arm segment is not elongated enough",
                "metrics": metrics,
                "thresholds": {"min_x_to_cross_ratio": 2.0},
            }
        return None
    if name == "wrist_l5_j6":
        if x_to_cross > 2.5 or minor_to_major < 0.25:
            return {
                "name": name,
                "reason": "wrist review assembly is too stretched or too flat",
                "metrics": metrics,
                "thresholds": {
                    "max_x_to_cross_ratio": 2.5,
                    "min_minor_to_major_ratio": 0.25,
                },
            }
        return None
    if name == "tool_end":
        if x_to_cross > 2.2 or minor_to_major < 0.28:
            return {
                "name": name,
                "reason": "tool-end review assembly is too stretched or too flat",
                "metrics": metrics,
                "thresholds": {
                    "max_x_to_cross_ratio": 2.2,
                    "min_minor_to_major_ratio": 0.28,
                },
            }
        return None
    return None


def _is_source_joint_result(cad_result: Any | None) -> bool:
    if cad_result is None:
        return False
    metadata = _source_metadata(cad_result)
    if not metadata:
        return False
    return (
        metadata.get("production_assembly_source") == "source_joint"
        or metadata.get("assembly_mode") == "source_joint"
        or metadata.get("semantic_constraints_applied_to_solver") == "source_joint"
    )


def _source_part_ids(cad_result: Any) -> list[str]:
    catalog = getattr(cad_result, "part_catalog", None)
    part_ids_method = getattr(catalog, "part_ids", None)
    if callable(part_ids_method):
        return [str(part_id) for part_id in part_ids_method()]
    metadata = _source_metadata(cad_result)
    raw = metadata.get("part_ids") if isinstance(metadata, dict) else None
    if isinstance(raw, list):
        return [str(part_id) for part_id in raw]
    return []


def _source_mates(cad_result: Any) -> list[dict[str, Any]]:
    metadata = _source_metadata(cad_result)
    raw = metadata.get("source_mates")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _source_metadata(cad_result: Any | None) -> dict[str, Any]:
    if cad_result is None:
        return {}
    metadata = getattr(cad_result, "metadata", {})
    if callable(metadata):
        metadata = metadata()
    if not isinstance(metadata, dict):
        return {}
    return metadata


def _source_edges(source_mates: list[dict[str, Any]]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for mate in source_mates:
        fixed = str(_endpoint(mate, "fixed_endpoint").get("part") or "")
        moving = str(_endpoint(mate, "moving_endpoint").get("part") or "")
        if fixed and moving and fixed != moving:
            edges.append((fixed, moving))
    return edges


def _component_report(
    part_ids: list[str],
    edges: list[tuple[str, str]],
) -> dict[str, object]:
    parent = {part_id: part_id for part_id in part_ids}

    def find(part_id: str) -> str:
        while parent[part_id] != part_id:
            parent[part_id] = parent[parent[part_id]]
            part_id = parent[part_id]
        return part_id

    def union(left: str, right: str) -> None:
        if left not in parent or right not in parent:
            return
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left, right in edges:
        union(left, right)

    clusters_by_root: dict[str, list[str]] = {}
    for part_id in part_ids:
        clusters_by_root.setdefault(find(part_id), []).append(part_id)
    clusters = [sorted(values) for values in clusters_by_root.values()]
    root_part_id = "base" if "base" in parent else part_ids[0]
    terminal_part_id = "end_effector" if "end_effector" in parent else part_ids[-1]
    connected = find(root_part_id) == find(terminal_part_id)
    return {
        "root_part_id": root_part_id,
        "terminal_part_id": terminal_part_id,
        "cluster_count": len(clusters),
        "clusters": clusters,
        "base_to_terminal_connected": connected,
    }


def _duplicate_edges(edges: list[tuple[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    duplicates: list[dict[str, str]] = []
    for left, right in edges:
        key = tuple(sorted((left, right)))
        if key in seen:
            duplicates.append({"fixed_part_id": left, "moving_part_id": right})
        seen.add(key)
    return duplicates


def _endpoint(mate: dict[str, Any], key: str) -> dict[str, Any]:
    endpoint = mate.get(key)
    return endpoint if isinstance(endpoint, dict) else {}


def _add(
    issues: list[SourceJointValidationIssue],
    code: str,
    severity: SourceJointValidationSeverity,
    message: str,
    details: dict[str, object] | None = None,
) -> None:
    issues.append(
        SourceJointValidationIssue(
            code=code,
            severity=severity,
            message=message,
            details=details or {},
        )
    )


__all__ = [
    "SourceJointValidationIssue",
    "SourceJointValidationReport",
    "SourceJointValidationSeverity",
    "validate_source_joint_assembly",
]
