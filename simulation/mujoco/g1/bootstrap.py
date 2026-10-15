"""将 G1 导航插件注册到当前 CathyAgent Registry。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cathy.artifacts import LocalArtifactStore
from cathy.plugins.manifest import parse_manifest

from .config import DEFAULT_G1_ENVIRONMENT
from .plugin import G1NavigationToolPlugin


def register_g1_navigation(
    registry,
    *,
    environment: str | Path | None,
    artifacts: LocalArtifactStore,
) -> None:
    plugin = G1NavigationToolPlugin()
    plugin.initialize(
        {
            "environment": str(environment or DEFAULT_G1_ENVIRONMENT),
            "artifact_store": artifacts,
        }
    )
    manifest = parse_manifest(Path(__file__).with_name("plugin.yaml"))
    registry.register_internal_plugin(manifest, plugin, skip_initialize=True)


__all__ = ["register_g1_navigation"]
