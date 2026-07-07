"""RobotDesignAgent CAD naming and export rules."""

from __future__ import annotations

import re

from robot_sdk.assembly.local_promotion import ready_local_subassembly_names
from robot_sdk.cad.cq_assembly import CadQueryAssemblyResult
from robot_sdk.cad.export import (
    CadQueryStepPackageExportResult,
    CadQuerySubassemblyExportSpec,
)


def build_cad_export_subassembly_specs(
    cad_result: CadQueryAssemblyResult,
) -> list[CadQuerySubassemblyExportSpec]:
    """Build semantic CAD export names for the current robot MVP parts.

    The SDK only executes export plans. Naming is owned by RobotDesignAgent so it
    can evolve from serial-arm names (`joint1`, `link1`) to robot-specific names
    such as `left_frontleg` without changing the exporter.
    """

    specs: list[CadQuerySubassemblyExportSpec] = []
    for part_id in cad_result.part_catalog.part_ids():
        assembly_name = cad_subassembly_name(part_id)
        specs.append(
            CadQuerySubassemblyExportSpec(
                name=assembly_name,
                part_ids=[part_id],
                part_export_names={part_id: cad_part_export_name(part_id)},
            )
        )
    specs.extend(_source_joint_review_subassembly_specs(cad_result, specs))
    ready_names = ready_local_subassembly_names(
        [
            subassembly
            for subassembly in cad_result.metadata.get("local_subassemblies") or []
            if isinstance(subassembly, dict)
        ]
    )
    for result in cad_result.local_subassembly_results:
        if not bool(getattr(result, "solved", False)):
            continue
        part_ids = list(getattr(result, "part_ids", []))
        if not part_ids:
            continue
        local_name = str(result.name)
        specs.append(
            CadQuerySubassemblyExportSpec(
                name=cad_local_subassembly_name(local_name),
                part_ids=part_ids,
                part_export_names={
                    part_id: cad_part_export_name(part_id)
                    for part_id in part_ids
                },
                source_local_subassembly_name=local_name,
                local_solve_usage=(
                    "production_local_solve"
                    if local_name in ready_names
                    else "experimental_local_solve"
                ),
            )
        )
    return specs


def _source_joint_review_subassembly_specs(
    cad_result: CadQueryAssemblyResult,
    existing_specs: list[CadQuerySubassemblyExportSpec],
) -> list[CadQuerySubassemblyExportSpec]:
    """Return source_joint review assemblies for STEP/snapshot regression."""

    metadata = getattr(cad_result, "metadata", {})
    if not isinstance(metadata, dict):
        return []
    if metadata.get("assembly_mode") != "source_joint":
        return []
    if int(metadata.get("joint_count") or 0) < 5:
        return []
    available = set(cad_result.part_catalog.part_ids())
    existing_names = {spec.name for spec in existing_specs}
    candidates = [
        ("base_shoulder", ["base", "J1", "L1", "J2"]),
        ("upper_arm", ["J2", "L2", "J3"]),
        ("forearm", ["J3", "L3", "J4"]),
        ("wrist_l5_j6", ["J5", "L5", "J6"]),
        ("tool_end", ["J6", "L6", "end_effector"]),
    ]
    specs: list[CadQuerySubassemblyExportSpec] = []
    for name, part_ids in candidates:
        if name in existing_names:
            continue
        if not set(part_ids).issubset(available):
            continue
        specs.append(
            CadQuerySubassemblyExportSpec(
                name=name,
                part_ids=part_ids,
                part_export_names={
                    part_id: cad_part_export_name(part_id)
                    for part_id in part_ids
                },
            )
        )
    return specs


def build_cad_snapshot_subassembly_names(
    package_result: CadQueryStepPackageExportResult,
) -> list[str]:
    """Return semantically important subassemblies for visual snapshots.

    Whole-machine snapshots catch global silhouette problems. Focused
    subassembly snapshots are for local wrist/tool/forearm review, so this
    intentionally prefers multi-part local solved assemblies and terminal tool
    assemblies while skipping base-only mount checks.
    """

    names: list[str] = []
    for subassembly in package_result.subassemblies:
        if _snapshot_subassembly_is_focus(subassembly):
            names.append(subassembly.name)
    return names


def _snapshot_subassembly_is_focus(subassembly: object) -> bool:
    name = str(getattr(subassembly, "name", "") or "")
    part_ids = [str(part_id) for part_id in getattr(subassembly, "part_ids", []) or []]
    source_local_name = str(getattr(subassembly, "source_local_subassembly_name", "") or "")
    local_usage = str(getattr(subassembly, "local_solve_usage", "") or "")
    if not part_ids:
        return False
    if "base" in part_ids and len(part_ids) <= 2:
        return False
    if source_local_name and local_usage != "not_applicable" and len(part_ids) > 1:
        return True
    lowered = f"{name} {source_local_name} {' '.join(part_ids)}".lower()
    focus_terms = ("shoulder", "upper_arm", "wrist", "forearm", "tool", "end_effector")
    return any(term in lowered for term in focus_terms)


def cad_subassembly_name(part_id: str) -> str:
    """Return the directory/assembly STEP stem for a generated part id."""

    return _cad_name_from_part_id(part_id)


def cad_part_export_name(part_id: str) -> str:
    """Return the part STEP stem inside its subassembly directory."""

    base_name = _cad_name_from_part_id(part_id)
    if base_name == "base":
        return "base_body"
    if base_name.startswith("joint"):
        return f"{base_name}_housing"
    if base_name.startswith("link"):
        return f"{base_name}_body"
    if base_name == "end_effector":
        return "end_effector_mount"
    return f"{base_name}_body"


def cad_local_subassembly_name(local_subassembly_name: str) -> str:
    """Return a CAD-friendly name for a solved local subassembly."""

    tokens = [
        _cad_name_from_part_id(token)
        for token in str(local_subassembly_name or "").split("_")
        if token
    ]
    return _normalize_cad_name("_".join(tokens))


def _cad_name_from_part_id(part_id: str) -> str:
    raw = str(part_id or "").strip()
    if re.fullmatch(r"J\d+", raw):
        return f"joint{raw[1:]}"
    if re.fullmatch(r"L\d+", raw):
        return f"link{raw[1:]}"
    return _normalize_cad_name(raw)


def _normalize_cad_name(name: str) -> str:
    text = str(name or "").strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text or "unnamed"
