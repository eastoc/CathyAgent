"""Deterministic profile scaling helpers.

This module executes explicit kinematic scaling requests. It does not inspect
free-form user text or decide whether scaling is appropriate; those decisions
belong in a subagent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from robot_sdk.kinematics.dh import estimate_reach
from robot_sdk.kinematics.profiles import RobotKinematicProfile, require_profile
from robot_sdk.types import (
    DHParam,
    JointSpec,
    KinematicModel,
    LengthUnit,
    LinkSpec,
    SerializableMixin,
)


KinematicProfileMode = Literal["exact_profile", "scaled_profile", "profile_like_template"]


@dataclass
class KinematicScalingRequest(SerializableMixin):
    """Explicit request to build a kinematic model from a named profile."""

    profile_name: str
    mode: KinematicProfileMode = "exact_profile"
    target_reach: float | None = None
    target_reach_unit: LengthUnit = "mm"

    def __post_init__(self) -> None:
        if not self.profile_name.strip():
            raise ValueError("KinematicScalingRequest.profile_name is required")
        if self.mode not in {"exact_profile", "scaled_profile", "profile_like_template"}:
            raise ValueError(f"Unsupported kinematic scaling mode: {self.mode}")
        if self.target_reach is not None and self.target_reach <= 0:
            raise ValueError("KinematicScalingRequest.target_reach must be positive")
        if self.target_reach_unit not in {"mm", "m"}:
            raise ValueError("KinematicScalingRequest.target_reach_unit must be mm or m")


@dataclass
class KinematicScalingResult(SerializableMixin):
    """Kinematic model plus trace data needed by agents and requirement docs."""

    model: KinematicModel
    mode: KinematicProfileMode
    profile_name: str
    source_reach: float
    source_reach_unit: LengthUnit = "mm"
    target_reach: float | None = None
    target_reach_unit: LengthUnit = "mm"
    scale_factor: float = 1.0
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_profile_kinematic_model(
    request: KinematicScalingRequest,
) -> KinematicScalingResult:
    """Build a KinematicModel from a profile using an explicit scaling mode."""

    profile = require_profile(request.profile_name)
    source_reach = estimate_reach(profile.dh_params)
    target_reach_mm = _target_reach_mm(request)

    if request.mode == "exact_profile":
        model = profile.to_kinematic_model()
        assumptions = [
            f"Using exact built-in kinematic profile `{profile.name}`.",
            "Scale factor: 1.0.",
        ]
        warnings = list(model.warnings)
        model.assumptions.extend(assumptions)
        return KinematicScalingResult(
            model=model,
            mode=request.mode,
            profile_name=profile.name,
            source_reach=source_reach,
            target_reach=target_reach_mm,
            scale_factor=1.0,
            assumptions=assumptions,
            warnings=warnings,
        )

    if target_reach_mm is None:
        raise ValueError(f"{request.mode} requires target_reach")
    if source_reach <= 0:
        raise ValueError(f"Profile `{profile.name}` has non-positive source reach")

    scale_factor = target_reach_mm / source_reach
    if request.mode == "scaled_profile":
        return _build_scaled_profile_result(
            profile=profile,
            source_reach=source_reach,
            target_reach=target_reach_mm,
            scale_factor=scale_factor,
        )
    if request.mode == "profile_like_template":
        return _build_profile_like_template_result(
            profile=profile,
            source_reach=source_reach,
            target_reach=target_reach_mm,
            scale_factor=scale_factor,
        )
    raise ValueError(f"Unsupported kinematic scaling mode: {request.mode}")


def _build_scaled_profile_result(
    *,
    profile: RobotKinematicProfile,
    source_reach: float,
    target_reach: float,
    scale_factor: float,
) -> KinematicScalingResult:
    dh_params = [
        DHParam(
            joint_id=param.joint_id,
            a=param.a * scale_factor,
            alpha=param.alpha,
            d=param.d * scale_factor,
            theta=param.theta,
            joint_type=param.joint_type,
            variable=param.variable,
            length_unit=param.length_unit,
            angle_unit=param.angle_unit,
        )
        for param in profile.dh_params
    ]
    assumptions = [
        f"Using scaled kinematic profile `{profile.name}`.",
        f"Source reach: {source_reach:g} mm.",
        f"Target reach: {target_reach:g} mm.",
        f"Scale factor: {scale_factor:g}.",
        "Only DH length terms a/d were scaled; alpha/theta were preserved.",
    ]
    warnings = [
        "Scaled DH parameters are geometric approximations and are no longer official manufacturer DH values.",
        "Scaled DH parameters are not a substitute for mechanical sizing, collision checks, or robot-specific calibration.",
    ]
    model = _kinematic_model_from_dh(
        profile=profile,
        dh_params=dh_params,
        material="scaled_profile_reference",
        assumptions=assumptions,
        warnings=warnings,
    )
    return KinematicScalingResult(
        model=model,
        mode="scaled_profile",
        profile_name=profile.name,
        source_reach=source_reach,
        target_reach=target_reach,
        scale_factor=scale_factor,
        assumptions=assumptions,
        warnings=warnings,
    )


def _build_profile_like_template_result(
    *,
    profile: RobotKinematicProfile,
    source_reach: float,
    target_reach: float,
    scale_factor: float,
) -> KinematicScalingResult:
    dh_params = _profile_like_template_dh(profile, target_reach)
    assumptions = [
        f"Using profile-like template based on `{profile.name}` topology.",
        f"Source profile reach reference: {source_reach:g} mm.",
        f"Template target reach: {target_reach:g} mm.",
        f"Reference scale factor: {scale_factor:g}.",
        "The template preserves joint count, joint types, variables, and alpha/theta structure.",
    ]
    warnings = [
        "Profile-like template DH values are generated design placeholders and are not official manufacturer DH values.",
        "Profile-like template output must be reviewed before being treated as a mechanical design basis.",
    ]
    model = _kinematic_model_from_dh(
        profile=profile,
        dh_params=dh_params,
        material="profile_like_template",
        assumptions=assumptions,
        warnings=warnings,
    )
    return KinematicScalingResult(
        model=model,
        mode="profile_like_template",
        profile_name=profile.name,
        source_reach=source_reach,
        target_reach=target_reach,
        scale_factor=scale_factor,
        assumptions=assumptions,
        warnings=warnings,
    )


def _profile_like_template_dh(
    profile: RobotKinematicProfile,
    target_reach: float,
) -> list[DHParam]:
    weights = [_row_length_weight(param) for param in profile.dh_params]
    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [1.0 for _ in profile.dh_params]
        total_weight = float(len(weights))

    dh_params: list[DHParam] = []
    for param, weight in zip(profile.dh_params, weights, strict=True):
        row_length = target_reach * weight / total_weight
        a, d = _template_length_slots(param, row_length)
        dh_params.append(
            DHParam(
                joint_id=param.joint_id,
                a=a,
                alpha=param.alpha,
                d=d,
                theta=param.theta,
                joint_type=param.joint_type,
                variable=param.variable,
                length_unit=param.length_unit,
                angle_unit=param.angle_unit,
            )
        )
    return dh_params


def _kinematic_model_from_dh(
    *,
    profile: RobotKinematicProfile,
    dh_params: list[DHParam],
    material: str,
    assumptions: list[str],
    warnings: list[str],
) -> KinematicModel:
    joints: list[JointSpec] = []
    links: list[LinkSpec] = []
    previous_link = "base"
    for index, param in enumerate(dh_params, start=1):
        link_id = f"L{index}"
        joints.append(
            JointSpec(
                id=param.joint_id,
                type=param.joint_type,
                parent_link=previous_link,
                child_link=link_id,
                limit=(-math.pi, math.pi) if param.joint_type == "revolute" else None,
                notes=[f"Generated from {profile.name} scaling mode."],
            )
        )
        links.append(
            LinkSpec(
                id=link_id,
                length=_positive_link_length(param),
                length_unit=profile.units,
                parent_joint=param.joint_id,
                material=material,
                notes=[
                    "Reference link length is estimated from this DH row; "
                    "mechanical layout owns final CAD body dimensions."
                ],
            )
        )
        previous_link = link_id

    return KinematicModel(
        convention=profile.convention,
        joints=joints,
        links=links,
        dh_params=dh_params,
        assumptions=[
            f"Profile source: {profile.source}",
            *profile.notes,
            *assumptions,
        ],
        warnings=warnings,
    )


def _target_reach_mm(request: KinematicScalingRequest) -> float | None:
    if request.target_reach is None:
        return None
    if request.target_reach_unit == "mm":
        return request.target_reach
    if request.target_reach_unit == "m":
        return request.target_reach * 1000.0
    raise ValueError(f"Unsupported target reach unit: {request.target_reach_unit}")


def _row_length_weight(param: DHParam) -> float:
    return math.hypot(param.a, param.d)


def _template_length_slots(param: DHParam, row_length: float) -> tuple[float, float]:
    if abs(param.a) >= abs(param.d) and param.a != 0:
        return math.copysign(row_length, param.a), 0.0
    if param.d != 0:
        return 0.0, math.copysign(row_length, param.d)
    return row_length, 0.0


def _positive_link_length(param: DHParam) -> float | None:
    length = math.hypot(param.a, param.d)
    return length if length > 0 else None


__all__ = [
    "KinematicProfileMode",
    "KinematicScalingRequest",
    "KinematicScalingResult",
    "build_profile_kinematic_model",
]
