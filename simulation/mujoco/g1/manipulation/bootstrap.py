"""注册 G1 manipulation 内部插件。"""

from __future__ import annotations

from pathlib import Path

from cathy.artifacts import LocalArtifactStore
from cathy.plugins.manifest import parse_manifest

from .config import load_g1_manipulation_config
from .plugin import G1ManipulationToolPlugin


def register_g1_manipulation(
    registry,
    *,
    artifacts: LocalArtifactStore,
    navigation_pose_provider=None,
    viewer_enabled: bool = False,
) -> G1ManipulationToolPlugin:
    plugin = G1ManipulationToolPlugin()
    plugin.initialize({
        "artifact_store": artifacts,
        "navigation_pose_provider": navigation_pose_provider,
        "viewer_enabled": viewer_enabled,
        "manipulation": load_g1_manipulation_config().to_plugin_config(),
    })
    manifest = parse_manifest(Path(__file__).with_name("plugin.yaml"))
    registry.register_internal_plugin(manifest, plugin, skip_initialize=True)
    return plugin


__all__ = ["register_g1_manipulation"]
