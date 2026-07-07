"""Apply layout-agent morphology decisions to `MechanicalLayout`.

The SDK intentionally does not import subagent classes here. The adapter accepts
dataclasses or plain dictionaries with the same field names, then writes stable
metadata for CAD builders and validators.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from robot_sdk.layout.mechanical_layout import (
    build_tabletop_serial_mechanical_layout_from_model,
)
from robot_sdk.types import (
    InterfaceSpec,
    JointLayout,
    KinematicModel,
    LinkLayout,
    MechanicalLayout,
)


MORPHOLOGY_METADATA_KEY = "morphology"
LAYOUT_DECISION_METADATA_KEY = "layout_decision"


def build_mechanical_layout_with_decision(
    model: KinematicModel,
    decision: Any,
) -> MechanicalLayout:
    """Build the deterministic skeleton, then apply a morphology decision."""

    layout = build_tabletop_serial_mechanical_layout_from_model(model)
    return apply_layout_decision(layout, decision)


def apply_layout_decision(layout: MechanicalLayout, decision: Any) -> MechanicalLayout:
    """Return a copy of `layout` with agent morphology metadata attached."""

    joint_decisions = _indexed_items(_get(decision, "joints", []), "joint_id")
    link_decisions = _indexed_items(_get(decision, "links", []), "link_id")
    interface_decisions = _indexed_items(
        _get(decision, "interfaces", []),
        "interface_id",
    )
    layout_metadata = {
        **layout.metadata,
        "layout_source": _get(decision, "layout_source", "unknown"),
        "robot_family": _get(decision, "robot_family", "unknown"),
        "layout_decision_confidence": _get(decision, "confidence", 0.0),
        "local_subassemblies": _local_subassemblies_metadata(
            _get(decision, "subassemblies", [])
        ),
    }

    return replace(
        layout,
        joints=[
            _apply_joint_decision(joint, joint_decisions.get(joint.id))
            for joint in layout.joints
        ],
        links=[
            _apply_link_decision(link, link_decisions.get(link.id))
            for link in layout.links
        ],
        interfaces=[
            _apply_interface_decision(
                interface,
                interface_decisions.get(interface.id),
            )
            for interface in layout.interfaces
        ],
        assumptions=[
            *layout.assumptions,
            *_string_list(_get(decision, "assumptions", [])),
        ],
        warnings=[
            *layout.warnings,
            *_string_list(_get(decision, "warnings", [])),
        ],
        metadata=layout_metadata,
    )


def _apply_joint_decision(
    joint: JointLayout,
    decision: Any | None,
) -> JointLayout:
    if decision is None:
        return joint
    return replace(joint, metadata=_metadata_with_decision(joint.metadata, decision))


def _apply_link_decision(
    link: LinkLayout,
    decision: Any | None,
) -> LinkLayout:
    if decision is None:
        return link
    return replace(link, metadata=_metadata_with_decision(link.metadata, decision))


def _apply_interface_decision(
    interface: InterfaceSpec,
    decision: Any | None,
) -> InterfaceSpec:
    if decision is None:
        return interface
    return replace(interface, metadata=_metadata_with_decision(interface.metadata, decision))


def _metadata_with_decision(metadata: dict[str, Any], decision: Any) -> dict[str, Any]:
    morphology = _get(decision, "morphology", None)
    return {
        **metadata,
        MORPHOLOGY_METADATA_KEY: morphology,
        LAYOUT_DECISION_METADATA_KEY: {
            "reason": _get(decision, "reason", ""),
            "confidence": _get(decision, "confidence", 0.0),
            "source_signals": _string_list(_get(decision, "source_signals", [])),
        },
    }


def _local_subassemblies_metadata(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items or []:
        name = _get(item, "name", "")
        part_ids = _string_list(_get(item, "part_ids", []))
        anchor_part_id = _get(item, "anchor_part_id", "")
        if not name or not part_ids or anchor_part_id not in part_ids:
            continue
        result.append(
            {
                "name": str(name),
                "part_ids": part_ids,
                "anchor_part_id": str(anchor_part_id),
                "role": str(_get(item, "role", "local_subassembly")),
                "reason": str(_get(item, "reason", "")),
                "confidence": float(_get(item, "confidence", 0.0) or 0.0),
                "source_signals": _string_list(_get(item, "source_signals", [])),
            }
        )
    return result


def _indexed_items(items: Any, key: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for item in items or []:
        item_id = _get(item, key, None)
        if item_id:
            indexed[str(item_id)] = item
    return indexed


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


__all__ = [
    "LAYOUT_DECISION_METADATA_KEY",
    "MORPHOLOGY_METADATA_KEY",
    "apply_layout_decision",
    "build_mechanical_layout_with_decision",
]
