"""连接真实 π MCP，注册本地高层技能 MCP；Agent 看不到原始预测接口。"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from pathlib import Path

from cathy.mcp import McpHub, McpToolPlugin, build_mcp_manifest

from .config import load_vla_config
from .executor import SkillExecutor
from .policy import PiMcpPolicy
from .runtime import PandaRuntime
from .aloha_runtime import AlohaRuntime
from .aloha_policy import PiAlohaMcpPolicy
from .server import create_skill_server


class RobotMcpPlugin(McpToolPlugin):
    def __init__(self, hub, *, artifact_store, executor, policy_hub):
        super().__init__(hub, artifact_store=artifact_store)
        self.executor, self.policy_hub = executor, policy_hub

    async def aexecute(self, tool_name, params):
        params = dict(params)
        skill = tool_name.endswith("robot_execute_skill")
        if skill:
            params["request_id"] = params.get("request_id") or str(uuid.uuid4())
        try:
            return await super().aexecute(tool_name, params)
        except BaseException:
            if skill:
                self.executor.request_stop(params["request_id"])
                # 本地 stop_event 直接作用于动作执行线程，不依赖网络取消通知到达。
                await asyncio.to_thread(self.executor.idle.wait, 5)
            raise

    def shutdown(self):
        self.executor.close()
        self._hub.shutdown()
        self.policy_hub.shutdown()
        self.executor.runtime.close()


def register_vla(registry, config: dict, environment, artifacts, viewer_enabled=None):
    env_cfg = load_vla_config(environment)
    aloha = env_cfg["backend"] == "native_aloha"
    runtime_class = AlohaRuntime if aloha else PandaRuntime
    runtime_class.check_dependencies()
    policy_cfg = env_cfg["policy"]
    mcp = config.get("MCP") or {}
    if not mcp.get("enabled"):
        raise ValueError("VLA 后端要求 MCP.enabled=true")
    server_name = policy_cfg["mcp_server"]
    server = (mcp.get("mcp_servers") or {}).get(server_name)
    if not server:
        raise ValueError(f"MCP.mcp_servers 缺少 {server_name}")
    remote = McpHub({"mcpServers": {server_name: server}}, server_names=[server_name],
                    connect_timeout=float(mcp.get("connect_timeout_sec", 60)),
                    default_call_timeout=policy_cfg["inference_timeout_seconds"])
    runtime = local = None
    try:
        remote.start()
        expected = policy_cfg["tool"]
        candidates = [tool for tool in remote.list_tools()
                      if tool.inner_name in {expected, f"{server_name}_{expected}"}]
        if len(candidates) != 1:
            raise ValueError(f"π MCP 缺少唯一工具 {expected}; 实际工具: {[t.inner_name for t in remote.list_tools()]}")
        tool = candidates[0]
        fields = tool.input_schema.get("properties", {})
        required = {"prompt", "state", "images_base64"} if aloha else {"prompt", "state", "image_base64", "wrist_image_base64"}
        if not required <= fields.keys():
            raise ValueError(f"{expected} schema 与当前场景契约不匹配，请部署对应的 AutoDL MCP 服务")
        if aloha:
            policy = PiAlohaMcpPolicy(remote, tool.outer_name, timeout=policy_cfg["inference_timeout_seconds"])
        else:
            policy = PiMcpPolicy(remote, tool.outer_name, timeout=policy_cfg["inference_timeout_seconds"],
                                rotate_180=env_cfg["observation"]["policy_rotate_180"])
        runtime = runtime_class(env_cfg, viewer_enabled=viewer_enabled)
        executor = SkillExecutor(runtime, policy, env_cfg)
        local = McpHub(create_skill_server(executor), server_names=["robot_vla"],
                       connect_timeout=20, default_call_timeout=policy_cfg["skill_timeout_seconds"] + 15)
        local.start()
        manifest = build_mcp_manifest(local, project_root=Path(__file__).resolve().parents[4])
        manifest = replace(manifest, tools=[replace(
            item, execution_mode="background" if item.name.endswith("robot_execute_skill") else "inline",
            timeout_seconds=policy_cfg["skill_timeout_seconds"] + 30,
            side_effect="reversible" if item.name.endswith("robot_execute_skill") else "none",
            concurrency_key="local_vla_environment", max_concurrency=1,
        ) for item in manifest.tools])
        registry.register_internal_plugin(manifest, RobotMcpPlugin(
            local, artifact_store=artifacts, executor=executor, policy_hub=remote,
        ), skip_initialize=True)
    except BaseException:
        if local is not None:
            local.shutdown()
        remote.shutdown()
        if runtime is not None:
            runtime.close()
        raise
