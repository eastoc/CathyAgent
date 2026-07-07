"""Named robot kinematic profiles.

Profiles are small, traceable reference models. They are useful when a user
asks for a known robot family, such as UR3e, and the design flow should start
from published kinematic dimensions instead of the MVP length-distribution
template.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

from robot_sdk.types import DHParam, JointSpec, KinematicConvention, KinematicModel, LinkSpec


ProfileUnit = Literal["mm"]


@dataclass
class RobotKinematicProfile:
    """A reusable, source-backed DH profile for a known robot model."""

    name: str
    aliases: list[str]
    manufacturer: str | None
    convention: KinematicConvention
    units: ProfileUnit
    dh_params: list[DHParam]
    source: str
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("RobotKinematicProfile.name is required")
        if self.convention not in {"dh", "modified_dh"}:
            raise ValueError("RobotKinematicProfile requires a DH convention")
        if self.units != "mm":
            raise ValueError("RobotKinematicProfile.units must be mm")
        if not self.dh_params:
            raise ValueError("RobotKinematicProfile.dh_params cannot be empty")
        if not self.source.strip():
            raise ValueError("RobotKinematicProfile.source is required")
        for param in self.dh_params:
            if param.length_unit != self.units:
                raise ValueError(
                    f"{param.joint_id} length_unit must match profile units {self.units}"
                )

    @property
    def dof(self) -> int:
        return len(self.dh_params)

    @property
    def normalized_aliases(self) -> list[str]:
        return [_normalize_alias(alias) for alias in [self.name, *self.aliases]]

    def clone_dh_params(self) -> list[DHParam]:
        """Return independent DHParam objects so callers can safely mutate them."""

        return [
            DHParam(
                joint_id=param.joint_id,
                a=param.a,
                alpha=param.alpha,
                d=param.d,
                theta=param.theta,
                joint_type=param.joint_type,
                variable=param.variable,
                length_unit=param.length_unit,
                angle_unit=param.angle_unit,
            )
            for param in self.dh_params
        ]

    def to_kinematic_model(self) -> KinematicModel:
        """Convert this profile into the SDK's current serial-chain model."""

        dh_params = self.clone_dh_params()
        joints: list[JointSpec] = []
        links: list[LinkSpec] = []
        previous_link = "base"
        for index, param in enumerate(dh_params, start=1):
            link_id = f"L{index}"
            joint_id = param.joint_id
            joints.append(
                JointSpec(
                    id=joint_id,
                    type=param.joint_type,
                    parent_link=previous_link,
                    child_link=link_id,
                    limit=(-math.pi, math.pi)
                    if param.joint_type == "revolute"
                    else None,
                    notes=[f"Generated from {self.name} kinematic profile."],
                )
            )
            links.append(
                LinkSpec(
                    id=link_id,
                    length=_positive_link_length(param),
                    length_unit=self.units,
                    parent_joint=joint_id,
                    material="profile_reference",
                    notes=[
                        "Reference link length is estimated from this DH row; "
                        "mechanical layout owns final CAD body dimensions."
                    ],
                )
            )
            previous_link = link_id

        return KinematicModel(
            convention=self.convention,
            joints=joints,
            links=links,
            dh_params=dh_params,
            assumptions=[
                f"Uses built-in kinematic profile `{self.name}`.",
                f"Profile source: {self.source}",
                *self.notes,
            ],
            warnings=[
                "Published nominal DH parameters are not a substitute for robot-specific calibration."
            ],
        )


def available_profiles() -> list[RobotKinematicProfile]:
    """Return all built-in profiles."""

    return list(_BUILTIN_PROFILES.values())


def profile_names() -> list[str]:
    """Return built-in profile names in registry order."""

    return list(_BUILTIN_PROFILES)


def get_profile(name_or_alias: str) -> RobotKinematicProfile | None:
    """Look up a profile by exact normalized name or alias."""

    normalized = _normalize_alias(name_or_alias)
    for profile in _BUILTIN_PROFILES.values():
        if normalized in profile.normalized_aliases:
            return profile
    return None


def require_profile(name_or_alias: str) -> RobotKinematicProfile:
    """Look up a profile or raise a readable error."""

    profile = get_profile(name_or_alias)
    if profile is None:
        supported = ", ".join(profile_names())
        raise ValueError(f"Unknown kinematic profile `{name_or_alias}`. Supported: {supported}")
    return profile


def _normalize_alias(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _positive_link_length(param: DHParam) -> float | None:
    length = math.hypot(param.a, param.d)
    return length if length > 0 else None


def _ur3e_profile() -> RobotKinematicProfile:
    """Return the Universal Robots UR3e nominal DH profile.

    Source table values are published in meters by Universal Robots. This SDK
    stores length values in millimeters to match the rest of the CAD pipeline.
    """

    return RobotKinematicProfile(
        name="ur3e",
        aliases=[
            "UR3e",
            "UR3 e",
            "UR-3e",
            "Universal Robots UR3e",
            "Universal Robots UR3 e",
        ],
        manufacturer="Universal Robots",
        convention="dh",
        units="mm",
        dh_params=[
            DHParam(
                joint_id="J1",
                a=0.0,
                alpha=math.pi / 2,
                d=151.85,
                theta=0.0,
                variable="theta1",
            ),
            DHParam(
                joint_id="J2",
                a=-243.55,
                alpha=0.0,
                d=0.0,
                theta=0.0,
                variable="theta2",
            ),
            DHParam(
                joint_id="J3",
                a=-213.2,
                alpha=0.0,
                d=0.0,
                theta=0.0,
                variable="theta3",
            ),
            DHParam(
                joint_id="J4",
                a=0.0,
                alpha=math.pi / 2,
                d=131.05,
                theta=0.0,
                variable="theta4",
            ),
            DHParam(
                joint_id="J5",
                a=0.0,
                alpha=-math.pi / 2,
                d=85.35,
                theta=0.0,
                variable="theta5",
            ),
            DHParam(
                joint_id="J6",
                a=0.0,
                alpha=0.0,
                d=92.1,
                theta=0.0,
                variable="theta6",
            ),
        ],
        source=(
            "Universal Robots DH parameters for calculations of kinematics "
            "and dynamics, UR3e table: "
            "https://www.universal-robots.com/articles/ur/application-installation/"
            "dh-parameters-for-calculations-of-kinematics-and-dynamics/"
        ),
        notes=[
            "Universal Robots publishes the table in meters; values are converted to millimeters.",
            "The sign of a2/a3 follows the published UR table and is preserved.",
        ],
    )


_BUILTIN_PROFILES: dict[str, RobotKinematicProfile] = {
    "ur3e": _ur3e_profile(),
}


__all__ = [
    "RobotKinematicProfile",
    "available_profiles",
    "get_profile",
    "profile_names",
    "require_profile",
]
