"""Source-first assembly plan built from MechanicalLayout mate frames.

The plan is intentionally CAD-kernel neutral. It records occurrences, named
part-local/world datum intent, and directed fixed->moving mate relationships so
the CAD layer can compile or validate assembly placement without treating
CadQuery selector strings as the source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from robot_sdk.assembly.constraints import (
    AssemblyMateConstraintRule,
    build_assembly_mate_constraint_plan,
)
from robot_sdk.assembly.mate_frames import MateFrame, Vector3
from robot_sdk.types import MechanicalLayout


@dataclass(frozen=True)
class AssemblyOccurrence:
    """One part occurrence in the production assembly graph."""

    part_id: str
    feature_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "feature_ids": list(self.feature_ids),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PartDatum:
    """Named datum owned by a part occurrence."""

    id: str
    part_id: str
    feature_id: str
    origin: Vector3
    normal: Vector3
    tangent: Vector3
    semantic: str
    source_frame: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mate_frame(cls, frame: MateFrame) -> "PartDatum":
        return cls(
            id=frame.id,
            part_id=frame.part_id,
            feature_id=frame.feature_id,
            origin=frame.origin,
            normal=frame.normal,
            tangent=frame.tangent,
            semantic=frame.semantic,
            source_frame=frame.source_frame,
            metadata=dict(frame.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "part_id": self.part_id,
            "feature_id": self.feature_id,
            "origin": self.origin,
            "normal": self.normal,
            "tangent": self.tangent,
            "semantic": self.semantic,
            "source_frame": self.source_frame,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AssemblyMate:
    """Directed mate relation from a fixed datum to a moving datum."""

    id: str
    kind: str
    normalized_kind: str
    fixed_part_id: str
    moving_part_id: str
    fixed_datum_id: str
    moving_datum_id: str
    fixed_feature_id: str
    moving_feature_id: str
    expected_fixed_origin: Vector3
    expected_moving_origin: Vector3
    expected_fixed_normal: Vector3
    expected_moving_normal: Vector3
    expected_fixed_tangent: Vector3
    expected_moving_tangent: Vector3
    offset: float | None = None
    flipped: bool = False
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_rule(cls, rule: AssemblyMateConstraintRule) -> "AssemblyMate":
        fixed = rule.fixed_mate
        moving = rule.moving_mate
        return cls(
            id=rule.constraint_id,
            kind=rule.kind,
            normalized_kind=rule.normalized_kind,
            fixed_part_id=fixed.part_id,
            moving_part_id=moving.part_id,
            fixed_datum_id=fixed.id,
            moving_datum_id=moving.id,
            fixed_feature_id=fixed.feature_id,
            moving_feature_id=moving.feature_id,
            expected_fixed_origin=fixed.origin,
            expected_moving_origin=moving.origin,
            expected_fixed_normal=fixed.normal,
            expected_moving_normal=moving.normal,
            expected_fixed_tangent=fixed.tangent,
            expected_moving_tangent=moving.tangent,
            offset=rule.offset,
            flipped=rule.flipped,
            rationale=rule.rationale,
            metadata=dict(rule.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "normalized_kind": self.normalized_kind,
            "fixed_part_id": self.fixed_part_id,
            "moving_part_id": self.moving_part_id,
            "fixed_datum_id": self.fixed_datum_id,
            "moving_datum_id": self.moving_datum_id,
            "fixed_feature_id": self.fixed_feature_id,
            "moving_feature_id": self.moving_feature_id,
            "expected_fixed_origin": self.expected_fixed_origin,
            "expected_moving_origin": self.expected_moving_origin,
            "expected_fixed_normal": self.expected_fixed_normal,
            "expected_moving_normal": self.expected_moving_normal,
            "expected_fixed_tangent": self.expected_fixed_tangent,
            "expected_moving_tangent": self.expected_moving_tangent,
            "offset": self.offset,
            "flipped": self.flipped,
            "rationale": self.rationale,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AssemblyChain:
    """Connectivity chain induced by directed assembly mates."""

    root_part_id: str
    terminal_part_id: str
    edges: list[tuple[str, str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_part_id": self.root_part_id,
            "terminal_part_id": self.terminal_part_id,
            "edges": [
                {
                    "fixed_part_id": fixed,
                    "moving_part_id": moving,
                    "mate_id": mate_id,
                }
                for fixed, moving, mate_id in self.edges
            ],
        }


@dataclass(frozen=True)
class AssemblyPlan:
    """CAD-neutral assembly plan for source-first placement and validation."""

    root_part_id: str
    terminal_part_id: str
    occurrences: list[AssemblyOccurrence]
    datums: list[PartDatum]
    mates: list[AssemblyMate]
    chain: AssemblyChain
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_part_id": self.root_part_id,
            "terminal_part_id": self.terminal_part_id,
            "occurrences": [occurrence.to_dict() for occurrence in self.occurrences],
            "datums": [datum.to_dict() for datum in self.datums],
            "mates": [mate.to_dict() for mate in self.mates],
            "chain": self.chain.to_dict(),
            "metadata": dict(self.metadata),
        }


def build_assembly_plan(
    layout: MechanicalLayout,
    *,
    root_part_id: str = "base",
    terminal_part_id: str = "end_effector",
) -> AssemblyPlan:
    """Build a source-first assembly plan from a MechanicalLayout."""

    mate_plan = build_assembly_mate_constraint_plan(layout)
    datums = [
        PartDatum.from_mate_frame(frame)
        for frame in mate_plan.mate_catalog.by_id.values()
    ]
    feature_ids_by_part: dict[str, list[str]] = {}
    for datum in datums:
        feature_ids_by_part.setdefault(datum.part_id, []).append(datum.feature_id)

    part_ids = sorted(feature_ids_by_part)
    if root_part_id not in part_ids:
        part_ids.insert(0, root_part_id)
    if terminal_part_id not in part_ids:
        part_ids.append(terminal_part_id)

    mates = [AssemblyMate.from_rule(rule) for rule in mate_plan.rules]
    edges = [
        (mate.fixed_part_id, mate.moving_part_id, mate.id)
        for mate in mates
        if mate.fixed_part_id != mate.moving_part_id
    ]
    return AssemblyPlan(
        root_part_id=root_part_id,
        terminal_part_id=terminal_part_id,
        occurrences=[
            AssemblyOccurrence(
                part_id=part_id,
                feature_ids=sorted(feature_ids_by_part.get(part_id, [])),
            )
            for part_id in part_ids
        ],
        datums=datums,
        mates=mates,
        chain=AssemblyChain(
            root_part_id=root_part_id,
            terminal_part_id=terminal_part_id,
            edges=edges,
        ),
        metadata={
            "source": "mechanical_layout",
            "constraint_count": len(mates),
            "datum_count": len(datums),
            "occurrence_count": len(part_ids),
        },
    )


__all__ = [
    "AssemblyChain",
    "AssemblyMate",
    "AssemblyOccurrence",
    "AssemblyPlan",
    "PartDatum",
    "build_assembly_plan",
]
