"""shell_exec 插件：通过可插拔 SandboxExecutor 执行命令。

读这个文件可以按 4 步走：
1) initialize()：读取配置，选 sandbox 后端；
2) execute() 前半段：做命令/cwd/timeout/env 的输入校验与归一化；
3) execute() 中段：构造 SandboxLimits + SandboxPolicy；
4) execute() 后半段：调用 executor.run 并把结果转成 JSON 字符串返回给 LLM。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from cathy.plugins import PluginError, ToolPlugin
from cathy.sandbox import (
    SandboxError,
    SandboxLimits,
    SandboxPolicy,
    create_executor,
    resolve_in_workspace,
)

_DANGEROUS_PATTERNS = (
    re.compile(r"\brm\s+-rf\s+/"),
    re.compile(r":\(\)\s*\{\s*:\|\:&\s*\};:"),
    re.compile(r"\bsudo\b"),
)


class ShellExecPlugin(ToolPlugin):
    def __init__(self) -> None:
        self._executor = None
        self._workspace_parent: Path | None = None
        self._workspace_root: Path | None = None
        self._default_timeout = 10.0
        self._max_timeout = 30.0
        self._max_output_bytes = 64 * 1024
        self._allow_network = False
        self._allowed_domains: list[str] = []

    def initialize(self, config: dict[str, Any]) -> None:
        # backend 是可插拔关键：seatbelt / local_restricted。
        backend = str(config.get("backend") or "local_restricted")
        workspace_root = str(config.get("workspace_root") or ".")
        self._workspace_parent = Path(workspace_root).expanduser().resolve()
        self._workspace_parent.mkdir(parents=True, exist_ok=True)
        self._workspace_root = self._workspace_parent

        self._default_timeout = float(config.get("default_timeout_sec", 10.0))
        self._max_timeout = float(config.get("max_timeout_sec", 30.0))
        self._max_output_bytes = int(config.get("max_output_bytes", 64 * 1024))
        self._allow_network = bool(config.get("allow_network", False))
        self._allowed_domains = list(config.get("allowed_domains") or [])

        try:
            self._executor = create_executor(backend)
        except SandboxError as exc:
            raise PluginError(str(exc)) from exc

    def attach_session(self, session_id: str) -> None:
        """切换到会话专属工作空间：<workspace_root>/<session_id>/。"""
        if self._workspace_parent is None:
            raise PluginError("shell_exec 未初始化")
        sid = (session_id or "").strip()
        if not sid:
            raise PluginError("session_id 不能为空")
        root = (self._workspace_parent / sid).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._workspace_root = root

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != "shell_exec":
            raise PluginError(f"未知工具: {tool_name}")
        if self._executor is None or self._workspace_root is None:
            raise PluginError("shell_exec 未初始化")

        command = str(params.get("command") or "").strip()
        if not command:
            raise PluginError("command 不能为空")
        # 这层是“命令级”防护（模式匹配），与 sandbox 的“系统级”防护互补。
        self._validate_command(command)

        cwd_raw = str(params.get("cwd") or ".")
        try:
            cwd = resolve_in_workspace(self._workspace_root, cwd_raw)
        except SandboxError as exc:
            raise PluginError(str(exc)) from exc

        timeout = self._default_timeout
        if params.get("timeout_sec") is not None:
            timeout = float(params["timeout_sec"])
        # 用户可调，但不能突破系统上限，防止长时间占用执行槽位。
        timeout = min(max(0.1, timeout), self._max_timeout)

        env = self._build_env(params.get("env_allowlist") or [])
        limits = SandboxLimits(timeout_sec=timeout, max_output_bytes=self._max_output_bytes)
        # policy 是“声明式策略”，具体如何生效由后端实现。
        policy = SandboxPolicy(
            workspace_root=self._workspace_root,
            allow_network=self._allow_network,
            allowed_domains=self._allowed_domains,
            deny_read=["~/.ssh", "~/.aws", "~/.gnupg"],
            deny_write=["~/.ssh", "~/.aws", "~/.gnupg", ".env"],
        )

        try:
            result = self._executor.run(
                command=command,
                cwd=cwd,
                env=env,
                limits=limits,
                policy=policy,
            )
        except SandboxError as exc:
            raise PluginError(f"sandbox 执行失败: {exc}") from exc

        # 对 LLM 输出统一 JSON，便于后续 hook 或上层解析。
        return json.dumps(result.to_dict(), ensure_ascii=False)

    def _build_env(self, allowlist: list[Any]) -> dict[str, str]:
        # 默认只给 PATH，避免把宿主机敏感环境变量泄露给命令进程。
        env: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
        for k in allowlist:
            key = str(k)
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
                continue
            if key in os.environ:
                env[key] = os.environ[key]
        return env

    @staticmethod
    def _validate_command(command: str) -> None:
        # 先用“硬规则”挡掉明显高危命令；更细粒度策略由 hooks 再补。
        if "\n" in command or "\r" in command:
            raise PluginError("command 必须是单行字符串")
        for pat in _DANGEROUS_PATTERNS:
            if pat.search(command):
                raise PluginError(f"命令命中高危规则，已阻止: {command}")

