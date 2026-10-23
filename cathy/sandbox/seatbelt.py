"""通过 macOS 自带的 sandbox-exec CLI 执行受限命令。"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from .base import (
    SandboxError,
    SandboxExecutor,
    SandboxLimits,
    SandboxPolicy,
    SandboxResult,
    resolve_in_workspace,
    run_subprocess,
)

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")


def _sbpl_string(path: str | Path) -> str:
    """将路径作为 SBPL 字符串字面量，不能插入额外规则。"""
    value = str(path)
    if "\n" in value or "\r" in value or "\x00" in value:
        raise SandboxError("sandbox 路径包含不支持的控制字符")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _profile(policy: SandboxPolicy) -> str:
    if policy.allow_network or policy.allowed_domains:
        raise SandboxError("直接使用 sandbox-exec 时不支持域名网络白名单；已拒绝执行")

    root = policy.workspace_root.expanduser().resolve()
    rules = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        '(allow file-write* (literal "/dev/null"))',
        f"(allow file-write* (subpath {_sbpl_string(root)}))",
        "(deny network*)",
    ]
    for path in policy.deny_read:
        rules.append(f"(deny file-read* (subpath {_sbpl_string(Path(path).expanduser().resolve())}))")
    for path in policy.deny_write:
        rules.append(f"(deny file-write* (subpath {_sbpl_string(Path(path).expanduser().resolve())}))")
    return "\n".join(rules)


class SeatbeltExecutor(SandboxExecutor):
    @property
    def backend_name(self) -> str:
        return "seatbelt"

    def run(
        self,
        *,
        command: str,
        cwd: Path,
        env: dict[str, str],
        limits: SandboxLimits,
        policy: SandboxPolicy,
    ) -> SandboxResult:
        if os.uname().sysname.lower() != "darwin":
            raise SandboxError("seatbelt 后端仅支持 macOS")
        if not SANDBOX_EXEC.is_file():
            raise SandboxError(f"找不到 macOS sandbox CLI: {SANDBOX_EXEC}")

        safe_cwd = resolve_in_workspace(policy.workspace_root, cwd)
        profile = _profile(policy)
        prefix = [str(SANDBOX_EXEC), "-p", profile, "--"]
        try:
            probe = subprocess.run(
                [*prefix, "/usr/bin/true"], cwd=safe_cwd, env=env,
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError(f"Seatbelt 功能探测失败: {exc}") from exc
        if probe.returncode != 0:
            raise SandboxError(f"Seatbelt 拒绝规则或不可用: {probe.stderr.strip()[:500]}")

        # run_subprocess 使用 shell=True；shlex.join 防止命令在外层 shell 执行。
        wrapped = shlex.join([*prefix, "/bin/sh", "-c", command])
        result = run_subprocess(
            command=wrapped, cwd=safe_cwd, env=env, limits=limits,
            backend_name=self.backend_name,
        )
        result.command = command
        return result
