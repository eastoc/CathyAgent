"""本地受限执行后端（无 OS 级隔离，作为可移植兜底）。

这个后端的定位是：
- 开发/测试环境保证“可运行”
- 通过 workspace 越界校验 + timeout/输出限制做最小防护

它不是强隔离，生产高安全场景请优先用 seatbelt/nsjail。
"""

from __future__ import annotations

from pathlib import Path

from .base import (
    SandboxExecutor,
    SandboxLimits,
    SandboxPolicy,
    SandboxResult,
    resolve_in_workspace,
    run_subprocess,
)


class LocalRestrictedExecutor(SandboxExecutor):
    @property
    def backend_name(self) -> str:
        return "local_restricted"

    def run(
        self,
        *,
        command: str,
        cwd: Path,
        env: dict[str, str],
        limits: SandboxLimits,
        policy: SandboxPolicy,
    ) -> SandboxResult:
        # 即使是 local 后端，也必须先过 workspace 越界校验。
        safe_cwd = resolve_in_workspace(policy.workspace_root, cwd)
        return run_subprocess(
            command=command,
            cwd=safe_cwd,
            env=env,
            limits=limits,
            backend_name=self.backend_name,
        )

