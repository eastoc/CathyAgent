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
) -> G1NavigationToolPlugin:
    plugin = G1NavigationToolPlugin()
    plugin.initialize(
        {
            "environment": str(environment or DEFAULT_G1_ENVIRONMENT),
            "artifact_store": artifacts,
        }
    )
    manifest = parse_manifest(Path(__file__).with_name("plugin.yaml"))
    registry.register_internal_plugin(manifest, plugin, skip_initialize=True)
    return plugin


def register_g1_vla(
    registry,
    *,
    environment: str | Path | None,
    artifacts: LocalArtifactStore,
    viewer_enabled: bool | None = None,
) -> tuple[G1NavigationToolPlugin, Any]:
    """同时注册导航与GN1x Arena manipulation，并用真实导航位姿约束技能交接。"""

    navigation = register_g1_navigation(
        registry,
        environment=environment,
        artifacts=artifacts,
    )
    from .manipulation.bootstrap import register_g1_manipulation

    assert navigation.service is not None
    manipulation = register_g1_manipulation(
        registry,
        artifacts=artifacts,
        navigation_pose_provider=navigation.current_state,
        viewer_enabled=viewer_enabled is True,
    )
    return navigation, manipulation


__all__ = ["register_g1_navigation", "register_g1_vla"]
