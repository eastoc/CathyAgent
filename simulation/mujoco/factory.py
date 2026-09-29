"""MuJoCo Runtime 工厂。"""

from __future__ import annotations

from pathlib import Path

from cathy.artifacts import LocalArtifactStore

from .config import DEFAULT_ENVIRONMENT, load_environment_config
from .runtime import MujocoRuntime


def create_runtime(
    environment: str | Path = DEFAULT_ENVIRONMENT,
    *,
    artifact_store: LocalArtifactStore | None = None,
    viewer_enabled: bool | None = None,
) -> MujocoRuntime:
    return MujocoRuntime(
        load_environment_config(environment),
        artifact_store=artifact_store,
        viewer_enabled=viewer_enabled,
    )
