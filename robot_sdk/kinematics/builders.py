"""Small reusable kinematic specs for tests, docs, and early templates."""

from __future__ import annotations

from math import pi

from .model import DHJoint, SerialManipulatorSpec


_UR3E_D1 = 0.15185
_UR3E_A2 = -0.24355
_UR3E_A3 = -0.2132
_UR3E_D4 = 0.13105
_UR3E_D5 = 0.08535
_UR3E_D6 = 0.0921


def planar_two_dof_spec(*, link1: float = 0.3, link2: float = 0.2) -> SerialManipulatorSpec:
    """Return a simple 2DoF planar arm using standard DH parameters."""

    return SerialManipulatorSpec(
        name="planar_two_dof",
        dof=2,
        representation="dh",
        joints=[
            DHJoint("shoulder", alpha=0.0, a=link1, d=0.0, theta="q1", limit=(-pi, pi), role="shoulder_yaw"),
            DHJoint("elbow", alpha=0.0, a=link2, d=0.0, theta="q2", limit=(-pi, pi), role="elbow_yaw"),
        ],
        metadata={"family": "serial_manipulator", "template": "planar_two_dof"},
    )


def demo_three_dof_spec() -> SerialManipulatorSpec:
    """Return a compact 3DoF arm spec for regression coverage."""

    return SerialManipulatorSpec(
        name="demo_three_dof",
        dof=3,
        representation="dh",
        joints=[
            DHJoint("shoulder_yaw", alpha=0.0, a=0.0, d=0.08, theta="q1", limit=(-pi, pi), role="base_yaw"),
            DHJoint("shoulder_pitch", alpha=0.0, a=0.32, d=0.0, theta="q2", limit=(-2.5, 2.5), role="upper_arm"),
            DHJoint("wrist_pitch", alpha=0.0, a=0.26, d=0.0, theta="q3", limit=(-1.57, 1.57), role="wrist"),
        ],
        metadata={"family": "serial_manipulator", "template": "demo_three_dof"},
    )


def demo_six_dof_spec() -> SerialManipulatorSpec:
    """Return a UR-style 6DoF serial-chain approximation.

    This is intentionally generic; exact vendor dimensions belong in a later
    template-specific phase.
    """

    return SerialManipulatorSpec(
        name="demo_six_dof",
        dof=6,
        representation="modified_dh",
        joints=[
            DHJoint("joint_1", alpha=0.0, a=0.0, d=0.15, theta="q1", limit=(-2 * pi, 2 * pi), role="base_yaw"),
            DHJoint("joint_2", alpha=pi / 2, a=0.0, d=0.0, theta="q2", limit=(-pi, pi), role="shoulder_pitch"),
            DHJoint("joint_3", alpha=0.0, a=0.25, d=0.0, theta="q3", limit=(-pi, pi), role="elbow_pitch"),
            DHJoint("joint_4", alpha=0.0, a=0.22, d=0.11, theta="q4", limit=(-2 * pi, 2 * pi), role="wrist_roll"),
            DHJoint("joint_5", alpha=pi / 2, a=0.0, d=0.09, theta="q5", limit=(-2 * pi, 2 * pi), role="wrist_pitch"),
            DHJoint("joint_6", alpha=-pi / 2, a=0.0, d=0.08, theta="q6", limit=(-2 * pi, 2 * pi), role="wrist_yaw"),
        ],
        metadata={"family": "serial_manipulator", "template": "demo_six_dof"},
    )


def ur3e_like_spec(*, scale: float = 1.0) -> SerialManipulatorSpec:
    """Return a UR3e-like 6DoF serial arm approximation.

    Dimensions are approximate and expressed in meters. The goal is to provide
    a stable kinematics-first scaffold with UR-style link roles, not a
    manufacturer-certified model. The negative ``a`` values follow the
    common UR modified-DH convention.
    """

    s = float(scale)
    if s <= 0.0:
        raise ValueError("scale must be positive")
    return SerialManipulatorSpec(
        name="ur3e_like",
        dof=6,
        representation="modified_dh",
        base_frame="base_link",
        tool_frame="tool0",
        joints=[
            DHJoint(
                "shoulder",
                alpha=pi / 2,
                a=0.0,
                d=_UR3E_D1 * s,
                theta="q1",
                limit=(-2 * pi, 2 * pi),
                role="shoulder_yaw",
                axis_hint=(0.0, 0.0, 1.0),
            ),
            DHJoint(
                "upper_arm",
                alpha=0.0,
                a=_UR3E_A2 * s,
                d=0.0,
                theta="q2",
                limit=(-2 * pi, 2 * pi),
                role="shoulder_pitch",
                axis_hint=(0.0, 1.0, 0.0),
            ),
            DHJoint(
                "forearm",
                alpha=0.0,
                a=_UR3E_A3 * s,
                d=0.0,
                theta="q3",
                limit=(-pi, pi),
                role="elbow_pitch",
                axis_hint=(0.0, 1.0, 0.0),
            ),
            DHJoint(
                "wrist_1",
                alpha=pi / 2,
                a=0.0,
                d=_UR3E_D4 * s,
                theta="q4",
                limit=(-2 * pi, 2 * pi),
                role="wrist_1_pitch",
                axis_hint=(0.0, 1.0, 0.0),
            ),
            DHJoint(
                "wrist_2",
                alpha=-pi / 2,
                a=0.0,
                d=_UR3E_D5 * s,
                theta="q5",
                limit=(-2 * pi, 2 * pi),
                role="wrist_2_yaw",
                axis_hint=(0.0, 0.0, 1.0),
            ),
            DHJoint(
                "wrist_3",
                alpha=0.0,
                a=0.0,
                d=_UR3E_D6 * s,
                theta="q6",
                limit=(-2 * pi, 2 * pi),
                role="wrist_3_roll",
                axis_hint=(0.0, 1.0, 0.0),
            ),
        ],
        metadata={
            "family": "serial_manipulator",
            "template": "ur3e_like",
            "source": "approximate_ur3e_dh",
            "scale": s,
        },
    )
