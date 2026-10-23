"""自包含的 CathyAgent + MuJoCo 组装入口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cathy.agent import Agent, AgentConfig
from cathy.artifacts import LocalArtifactStore
from cathy.context import ContextAssembler
from cathy.hooks import HookManager
from cathy.model_clients import build_model_client
from cathy.plugins import PluginRegistry, build_tool_catalog
from cathy.session import AsyncSessionStore
from cathy.telemetry import RunJournal
from config.config import get_llm, load_config

from .config import DEFAULT_ENVIRONMENT, DEFAULT_WMA_ENVIRONMENT, MODULE_ROOT
from .prompt import MUJOCO_AGENT_SYSTEM_PROMPT
from .unitree_z1_tools import load_unitree_z1_tools_config

OUTPUT_ROOT = MODULE_ROOT / "outputs"
CONTROL_MODES = frozenset({"local_direct", "g1_navigation", "g1_vla"})


@dataclass(frozen=True)
class SimulationAgentRuntime:
    agent: Agent
    store: AsyncSessionStore
    artifacts: LocalArtifactStore

    async def close(self) -> None:
        await self.agent.task_registry.shutdown(cancel=True)
        if self.agent.event_sink is not None:
            await self.agent.event_sink.aflush()
        self.agent.tools.shutdown()
        model_aclose = getattr(self.agent.llm, "aclose", None)
        if callable(model_aclose):
            await model_aclose()
        else:
            model_close = getattr(self.agent.llm, "close", None)
            if callable(model_close):
                model_close()
        await self.store.aclose()


def _uses_default_environment(environment: str | Path | None) -> bool:
    """判断调用方是否未覆盖当前控制模式的默认环境。"""
    return environment in {None, DEFAULT_ENVIRONMENT, DEFAULT_WMA_ENVIRONMENT}


def _resolve_environment(
    control_mode: str,
    environment: str | Path | None,
) -> str | Path | None:
    """为 G1 模式选择对应默认环境，显式传入的环境保持不变。"""
    if control_mode == "local_direct" or not _uses_default_environment(environment):
        return environment

    from .g1.config import DEFAULT_G1_ENVIRONMENT, DEFAULT_G1_SONIC_ENVIRONMENT

    return (
        DEFAULT_G1_SONIC_ENVIRONMENT
        if control_mode == "g1_vla"
        else DEFAULT_G1_ENVIRONMENT
    )


def _build_local_direct_registry(
    environment: str | Path | None,
    artifacts: LocalArtifactStore,
    viewer_enabled: bool | None,
) -> PluginRegistry:
    """发现并注册 Z1 本地直接控制插件。"""
    registry = PluginRegistry(
        plugins_dirs=[MODULE_ROOT.parent],
        plugin_configs={
            "mujoco": {
                "environment": str(environment),
                "artifact_store": artifacts,
                "viewer_enabled": viewer_enabled,
                "unitree_z1_tools": load_unitree_z1_tools_config().to_plugin_config(),
            }
        },
    )
    try:
        loaded = registry.discover_and_load()
        if loaded != ["mujoco"]:
            raise RuntimeError(f"MuJoCo 插件加载失败: loaded={loaded}")
    except BaseException:
        registry.shutdown()
        raise
    return registry


def _build_g1_navigation_registry(
    environment: str | Path | None,
    artifacts: LocalArtifactStore,
) -> PluginRegistry:
    """注册只提供地图观察与二维导航的 G1 工具。"""
    from .g1.bootstrap import register_g1_navigation

    registry = PluginRegistry(plugins_dirs=[], plugin_configs={})
    try:
        register_g1_navigation(registry, environment=environment, artifacts=artifacts)
    except BaseException:
        registry.shutdown()
        raise
    return registry


def _build_g1_vla_registry(
    environment: str | Path | None,
    artifacts: LocalArtifactStore,
    viewer_enabled: bool | None,
) -> PluginRegistry:
    """注册 G1 导航与 GN1x/Dex3 高层操作工具。"""
    from .g1.bootstrap import register_g1_vla

    registry = PluginRegistry(plugins_dirs=[], plugin_configs={})
    try:
        register_g1_vla(
            registry,
            environment=environment,
            artifacts=artifacts,
            viewer_enabled=viewer_enabled is True,
        )
    except BaseException:
        registry.shutdown()
        raise
    return registry


def _build_registry(
    control_mode: str,
    environment: str | Path | None,
    artifacts: LocalArtifactStore,
    viewer_enabled: bool | None,
) -> PluginRegistry:
    """按 control_mode 分派到对应的 Registry 装配器。"""
    if control_mode == "local_direct":
        return _build_local_direct_registry(environment, artifacts, viewer_enabled)
    if control_mode == "g1_navigation":
        return _build_g1_navigation_registry(environment, artifacts)
    if control_mode == "g1_vla":
        return _build_g1_vla_registry(environment, artifacts, viewer_enabled)
    raise ValueError(f"未知 control_mode: {control_mode}")


async def build_simulation_agent(
    cfg: Mapping[str, Any] | None = None,
    *,
    environment: str | Path = DEFAULT_WMA_ENVIRONMENT,
    llm: Any | None = None,
    output_root: str | Path = OUTPUT_ROOT,
    viewer_enabled: bool | None = None,
    control_mode: str = "local_direct",
) -> SimulationAgentRuntime:
    """按 control_mode 装配工具 Registry，再创建统一提示词的 Agent。"""
    resolved_cfg = dict(cfg) if cfg is not None else load_config()
    root = Path(output_root).resolve()
    artifacts = LocalArtifactStore(root / "artifacts")
    if control_mode not in CONTROL_MODES:
        raise ValueError(f"未知 control_mode: {control_mode}")
    resolved_environment = _resolve_environment(control_mode, environment)
    registry = _build_registry(
        control_mode, resolved_environment, artifacts, viewer_enabled
    )

    try:
        model_client = llm or build_model_client(get_llm(resolved_cfg))
        store = await AsyncSessionStore.open(root / "sessions.db")
    except BaseException:
        registry.shutdown()
        raise

    hooks = HookManager()
    tools = registry
    assembler = ContextAssembler(
        project_root=None,
        system_override=MUJOCO_AGENT_SYSTEM_PROMPT,
        tool_catalog=build_tool_catalog(tools.list_tools()),
        token_budget=int((resolved_cfg.get("SESSION") or {}).get("token_budget", 16000)),
        hooks=hooks,
    )
    agent_cfg = resolved_cfg.get("AGENT") or {}
    agent = Agent(
        llm=model_client,
        tools=tools,
        assembler=assembler,
        store=store,
        config=AgentConfig(max_steps=max(12, int(agent_cfg.get("max_steps", 30)))),
        hooks=hooks,
        permission_cfg={},
        attachment_resolver=artifacts,
        event_sink=RunJournal(store),
    )
    return SimulationAgentRuntime(agent=agent, store=store, artifacts=artifacts)


__all__ = ["OUTPUT_ROOT", "SimulationAgentRuntime", "build_simulation_agent"]
