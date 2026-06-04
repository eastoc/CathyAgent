"""RobotDesignAgent CAD naming and export rules."""

from __future__ import annotations

import re

from robot_sdk.cad.cq_assembly import CadQueryAssemblyResult
from robot_sdk.cad.export import CadQuerySubassemblyExportSpec


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
    return specs


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
