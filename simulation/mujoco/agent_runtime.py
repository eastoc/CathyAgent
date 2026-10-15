"""自包含的 CathyAgent + MuJoCo 组装入口。"""

from __future__ import annotations

import asyncio
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
from .prompt import SIMULATION_AGENT_SYSTEM_PROMPT, VLA_AGENT_SYSTEM_PROMPT
from .unitree_z1_tools import load_unitree_z1_tools_config

OUTPUT_ROOT = MODULE_ROOT / "outputs"


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


async def build_simulation_agent(
    cfg: Mapping[str, Any] | None = None,
    *,
    environment: str | Path = DEFAULT_WMA_ENVIRONMENT,
    llm: Any | None = None,
    output_root: str | Path = OUTPUT_ROOT,
    viewer_enabled: bool | None = None,
    control_mode: str = "local_direct",
) -> SimulationAgentRuntime:
    """装配 Z1、VLA 或 G1 静态地图导航；不加载通用插件。"""
    resolved_cfg = dict(cfg) if cfg is not None else load_config()
    root = Path(output_root).resolve()
    artifacts = LocalArtifactStore(root / "artifacts")
    if control_mode not in {"local_direct", "vla_mcp", "g1_navigation"}:
        raise ValueError(f"未知 control_mode: {control_mode}")
    if control_mode == "vla_mcp" and environment in {
        DEFAULT_ENVIRONMENT,
        DEFAULT_WMA_ENVIRONMENT,
    }:
        from .vla.config import DEFAULT_VLA_ENVIRONMENT
        environment = DEFAULT_VLA_ENVIRONMENT
    if control_mode == "g1_navigation" and environment in {
        None,
        DEFAULT_ENVIRONMENT,
        DEFAULT_WMA_ENVIRONMENT,
    }:
        from .g1.config import DEFAULT_G1_ENVIRONMENT

        environment = DEFAULT_G1_ENVIRONMENT
    z1_tools_config = load_unitree_z1_tools_config()
    registry = PluginRegistry(
        plugins_dirs=[] if control_mode in {"vla_mcp", "g1_navigation"} else [MODULE_ROOT.parent],
        plugin_configs={
            "mujoco": {
                "environment": str(environment),
                "artifact_store": artifacts,
                "viewer_enabled": viewer_enabled,
                "unitree_z1_tools": z1_tools_config.to_plugin_config(),
            }
        },
    )
    if control_mode == "g1_navigation":
        from .g1.bootstrap import register_g1_navigation

        register_g1_navigation(
            registry,
            environment=environment,
            artifacts=artifacts,
        )
    elif control_mode == "vla_mcp":
        from .vla.bootstrap import register_vla
        startup = asyncio.create_task(asyncio.to_thread(
            register_vla, registry, resolved_cfg, environment, artifacts, viewer_enabled,
        ))
        try:
            await asyncio.shield(startup)
        except asyncio.CancelledError:
            try:
                await startup
            finally:
                registry.shutdown()
            raise
    else:
        loaded = registry.discover_and_load()
        if loaded != ["mujoco"]:
            registry.shutdown()
            raise RuntimeError(f"MuJoCo 插件加载失败: loaded={loaded}")

    try:
        model_client = llm or build_model_client(get_llm(resolved_cfg))
        store = await AsyncSessionStore.open(root / "sessions.db")
    except BaseException:
        registry.shutdown()
        raise

    hooks = HookManager()
    tools = registry
    if control_mode == "g1_navigation":
        from .g1.prompt import G1_NAVIGATION_SYSTEM_PROMPT

        system_prompt = G1_NAVIGATION_SYSTEM_PROMPT
    elif control_mode == "vla_mcp":
        system_prompt = VLA_AGENT_SYSTEM_PROMPT
    else:
        system_prompt = SIMULATION_AGENT_SYSTEM_PROMPT
    assembler = ContextAssembler(
        project_root=None,
        system_override=system_prompt,
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
