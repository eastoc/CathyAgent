"""Validation helpers for mechanical layout objects.

The layout layer is the boundary between kinematics and CAD. These checks keep
that boundary honest before CadQuery code tries to create parts or solve
assembly constraints.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from robot_sdk.types import MechanicalLayout


@dataclass
class LayoutValidationIssue:
    """One validation finding for a `MechanicalLayout`."""

    code: str
    message: str
    object_id: str | None = None


@dataclass
class LayoutValidationResult:
    """Validation result that agents can turn into a report."""

    errors: list[LayoutValidationIssue] = field(default_factory=list)
    warnings: list[LayoutValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error_messages(self) -> list[str]:
        return [_format_issue(issue) for issue in self.errors]

    def warning_messages(self) -> list[str]:
        return [_format_issue(issue) for issue in self.warnings]


def validate_mechanical_layout(layout: MechanicalLayout) -> LayoutValidationResult:
    """Return structural validation findings for a `MechanicalLayout`.

    This function checks references and mechanical semantics only. It does not
    solve CAD constraints and does not judge whether the robot is manufacturable.
    """

    result = LayoutValidationResult()
    frame_ids = {frame.id for frame in layout.frames}
    interface_ids = {interface.id for interface in layout.interfaces}
    envelope_ids = {envelope.id for envelope in layout.envelopes}
    feature_ids = {feature.id for feature in layout.part_features}
    link_ids = {link.id for link in layout.links}

    _check_duplicates(
        result,
        "duplicate_frame_id",
        "Duplicate frame id",
        [frame.id for frame in layout.frames],
    )
    _check_duplicates(
        result,
        "duplicate_interface_id",
        "Duplicate interface id",
        [interface.id for interface in layout.interfaces],
    )
    _check_duplicates(
        result,
        "duplicate_envelope_id",
        "Duplicate envelope id",
        [envelope.id for envelope in layout.envelopes],
    )
    _check_duplicates(
        result,
        "duplicate_part_feature_id",
        "Duplicate part feature id",
        [feature.id for feature in layout.part_features],
    )
    _check_duplicates(
        result,
        "duplicate_assembly_constraint_id",
        "Duplicate assembly constraint id",
        [constraint.id for constraint in layout.assembly_constraints],
    )

    if layout.base_frame.id not in frame_ids:
        _error(
            result,
            "missing_base_frame",
            "MechanicalLayout.base_frame must be included in frames.",
            layout.base_frame.id,
        )

    for frame in layout.frames:
        if frame.parent and frame.parent not in frame_ids:
            _error(
                result,
                "unknown_frame_parent",
                f"Frame `{frame.id}` references missing parent frame `{frame.parent}`.",
                frame.id,
            )
        if not frame.semantic:
            _error(
                result,
                "missing_frame_semantic",
                f"Frame `{frame.id}` must declare a semantic.",
                frame.id,
            )

    _check_frame_graph(layout, frame_ids, result)

    for joint in layout.joints:
        if joint.axis_frame not in frame_ids:
            _error(
                result,
                "missing_joint_axis_frame",
                f"Joint `{joint.id}` references missing axis frame `{joint.axis_frame}`.",
                joint.id,
            )
        elif _frame_semantic(layout, joint.axis_frame) != "joint_axis":
            _error(
                result,
                "invalid_joint_axis_semantic",
                f"Joint `{joint.id}` axis frame `{joint.axis_frame}` must be semantic `joint_axis`.",
                joint.id,
            )
        if joint.child_link not in link_ids:
            _error(
                result,
                "missing_joint_child_link",
                f"Joint `{joint.id}` references missing child link `{joint.child_link}`.",
                joint.id,
            )
        if joint.actuator_envelope and joint.actuator_envelope.id not in envelope_ids:
            _error(
                result,
                "unknown_joint_envelope",
                f"Joint `{joint.id}` actuator envelope `{joint.actuator_envelope.id}` is not registered in layout.envelopes.",
                joint.id,
            )

    for link in layout.links:
        if link.body_frame not in frame_ids:
            _error(
                result,
                "missing_link_body_frame",
                f"Link `{link.id}` references missing body frame `{link.body_frame}`.",
                link.id,
            )
        elif _frame_semantic(layout, link.body_frame) != "link_body":
            _error(
                result,
                "invalid_link_body_semantic",
                f"Link `{link.id}` body frame `{link.body_frame}` must be semantic `link_body`.",
                link.id,
            )
        for role, interface_id in (
            ("from_interface", link.from_interface),
            ("to_interface", link.to_interface),
        ):
            if interface_id and interface_id not in interface_ids:
                _error(
                    result,
                    "unknown_link_interface",
                    f"Link `{link.id}` {role} references missing interface `{interface_id}`.",
                    link.id,
                )
        if link.envelope.id not in envelope_ids:
            _error(
                result,
                "unknown_link_envelope",
                f"Link `{link.id}` envelope `{link.envelope.id}` is not registered in layout.envelopes.",
                link.id,
            )

    for interface in layout.interfaces:
        if interface.frame not in frame_ids:
            _error(
                result,
                "missing_interface_frame",
                f"Interface `{interface.id}` references missing frame `{interface.frame}`.",
                interface.id,
            )
        elif _frame_semantic(layout, interface.frame) != "interface":
            _warning(
                result,
                "non_interface_frame_semantic",
                f"Interface `{interface.id}` frame `{interface.frame}` is not semantic `interface`.",
                interface.id,
            )
        if interface.mates_to and interface.mates_to not in interface_ids:
            _error(
                result,
                "unknown_mating_interface",
                f"Interface `{interface.id}` mates_to missing interface `{interface.mates_to}`.",
                interface.id,
            )
        if interface.envelope and interface.envelope.id not in envelope_ids:
            _error(
                result,
                "unknown_interface_envelope",
                f"Interface `{interface.id}` envelope `{interface.envelope.id}` is not registered in layout.envelopes.",
                interface.id,
            )

    for envelope in layout.envelopes:
        if envelope.frame and envelope.frame not in frame_ids:
            _error(
                result,
                "missing_envelope_frame",
                f"Envelope `{envelope.id}` references missing frame `{envelope.frame}`.",
                envelope.id,
            )

    for mapping in layout.frame_mappings:
        if mapping.kinematic_frame_id not in frame_ids:
            _error(
                result,
                "missing_mapping_kinematic_frame",
                f"FrameMapping references missing kinematic frame `{mapping.kinematic_frame_id}`.",
                mapping.kinematic_frame_id,
            )
        elif _frame_semantic(layout, mapping.kinematic_frame_id) != "kinematic":
            _warning(
                result,
                "non_kinematic_mapping_source",
                f"FrameMapping source `{mapping.kinematic_frame_id}` is not semantic `kinematic`.",
                mapping.kinematic_frame_id,
            )
        if mapping.mechanical_frame_id not in frame_ids:
            _error(
                result,
                "missing_mapping_mechanical_frame",
                f"FrameMapping references missing mechanical frame `{mapping.mechanical_frame_id}`.",
                mapping.mechanical_frame_id,
            )
        if not mapping.rationale.strip():
            _error(
                result,
                "missing_mapping_rationale",
                "FrameMapping must include a rationale.",
                mapping.mechanical_frame_id,
            )

    _check_part_features(layout, frame_ids, result)
    _check_assembly_constraints(layout, feature_ids, result)

    return result


def assert_valid_mechanical_layout(layout: MechanicalLayout) -> None:
    """Raise `ValueError` when `layout` has validation errors."""

    result = validate_mechanical_layout(layout)
    if not result.ok:
        joined = "\n".join(result.error_messages())
        raise ValueError(f"MechanicalLayout validation failed:\n{joined}")


def _check_part_features(
    layout: MechanicalLayout,
    frame_ids: set[str],
    result: LayoutValidationResult,
) -> None:
    for feature in layout.part_features:
        if feature.frame and feature.frame not in frame_ids:
            _error(
                result,
                "missing_part_feature_frame",
                f"PartFeature `{feature.id}` references missing frame `{feature.frame}`.",
                feature.id,
            )
        if feature.type == "axis" and feature.semantic not in {"joint_axis", "custom"}:
            _warning(
                result,
                "axis_feature_semantic_mismatch",
                f"Axis feature `{feature.id}` has semantic `{feature.semantic}`.",
                feature.id,
            )
        if feature.type in {"plane", "face"} and feature.semantic == "joint_axis":
            _warning(
                result,
                "plane_feature_semantic_mismatch",
                f"Plane/face feature `{feature.id}` should not use semantic `joint_axis`.",
                feature.id,
            )


def _check_assembly_constraints(
    layout: MechanicalLayout,
    feature_ids: set[str],
    result: LayoutValidationResult,
) -> None:
    for constraint in layout.assembly_constraints:
        if constraint.fixed not in feature_ids:
            _error(
                result,
                "unknown_constraint_fixed_feature",
                f"AssemblyConstraint `{constraint.id}` fixed feature `{constraint.fixed}` is missing.",
                constraint.id,
            )
        if constraint.moving not in feature_ids:
            _error(
                result,
                "unknown_constraint_moving_feature",
                f"AssemblyConstraint `{constraint.id}` moving feature `{constraint.moving}` is missing.",
                constraint.id,
            )
        if not constraint.rationale.strip():
            _error(
                result,
                "missing_constraint_rationale",
                f"AssemblyConstraint `{constraint.id}` must include a rationale.",
                constraint.id,
            )


def _check_frame_graph(
    layout: MechanicalLayout,
    frame_ids: set[str],
    result: LayoutValidationResult,
) -> None:
    children_by_parent: dict[str, list[str]] = {}
    for frame in layout.frames:
        if frame.parent in frame_ids:
            children_by_parent.setdefault(frame.parent, []).append(frame.id)

    reachable = {layout.base_frame.id}
    queue: deque[str] = deque([layout.base_frame.id])
    while queue:
        parent = queue.popleft()
        for child in children_by_parent.get(parent, []):
            if child in reachable:
                continue
            reachable.add(child)
            queue.append(child)

    for frame_id in sorted(frame_ids - reachable):
        _warning(
            result,
            "unreachable_frame",
            f"Frame `{frame_id}` is not reachable from base frame `{layout.base_frame.id}`.",
            frame_id,
        )


def _check_duplicates(
    result: LayoutValidationResult,
    code: str,
    label: str,
    values: list[str],
) -> None:
    for value, count in Counter(values).items():
        if count > 1:
            _error(result, code, f"{label} `{value}` appears {count} times.", value)


def _frame_semantic(layout: MechanicalLayout, frame_id: str) -> str | None:
    for frame in layout.frames:
        if frame.id == frame_id:
            return frame.semantic
    return None


def _error(
    result: LayoutValidationResult,
    code: str,
    message: str,
    object_id: str | None = None,
) -> None:
    result.errors.append(LayoutValidationIssue(code, message, object_id))


def _warning(
    result: LayoutValidationResult,
    code: str,
    message: str,
    object_id: str | None = None,
) -> None:
    result.warnings.append(LayoutValidationIssue(code, message, object_id))


def _format_issue(issue: LayoutValidationIssue) -> str:
    if issue.object_id:
        return f"{issue.code}: {issue.object_id}: {issue.message}"
    return f"{issue.code}: {issue.message}"
