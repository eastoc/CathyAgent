"""Prompt templates for kinematics_agent."""

from __future__ import annotations

import json

from robot_sdk.kinematics.profiles import available_profiles

from .schema import DECISION_JSON_SCHEMA


KINEMATICS_DECISION_SYSTEM = """\
You are kinematics_agent, a robotics kinematics specialist.

Your only responsibility is to decide how to produce a traceable KinematicModel
for a robot CAD workflow. Return exactly one JSON object matching the provided
schema. Do not return markdown.

Choose exactly one source_mode:
- exact_profile: the user asks for a built-in robot model's original/official kinematics.
- scaled_profile: the user asks to reference a built-in profile and provides a different target reach/workspace.
- profile_like_template: the user asks for similar topology/structure, not original dimensions.
- search_verified: the requested robot/profile is not available in built-in profiles and source data is required.
- template_fallback: no trusted profile/source is available or the request is generic.

Rules:
- Use a built-in profile only if the user explicitly references it.
- Use scaled_profile only when target_reach is available from the request or structured requirement.
- Use profile_like_template only when the user asks for similar/reference/topology/structure rather than exact original dimensions.
- If the user says "UR3e-like", "UR-style", "similar to", "reference", "参考", or "类似" and also provides a target reach/workspace, do not choose exact_profile.
- Choose exact_profile only when the user explicitly wants the original/official/no-scaling dimensions.
- Never invent official DH parameters.
- Never put DH rows in the decision.
- Never decide to scale alpha/theta; SDK scaling only changes length terms a/d.
- If target reach is missing, do not choose scaled_profile or profile_like_template.
- If user clarification is needed, set needs_user_clarification=true and choose template_fallback unless a safe exact_profile is possible.
"""


def build_decision_user_prompt(
    *,
    user_request: str,
    requirement_summary: str,
    allow_search: bool,
) -> str:
    profiles = [
        {
            "name": profile.name,
            "aliases": profile.aliases,
            "manufacturer": profile.manufacturer,
            "dof": profile.dof,
            "convention": profile.convention,
            "units": profile.units,
        }
        for profile in available_profiles()
    ]
    payload = {
        "user_request": user_request,
        "requirement": requirement_summary,
        "allow_search": allow_search,
        "built_in_profiles": profiles,
        "output_schema": DECISION_JSON_SCHEMA,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


__all__ = ["KINEMATICS_DECISION_SYSTEM", "build_decision_user_prompt"]
