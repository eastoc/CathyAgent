"""macOS Seatbelt 后端（通过 py-sandboxrt/srt）。

关键路径：
1) 用 srt 的 config 组装文件/网络策略；
2) `wrap_with_sandbox(command)` 生成被 Seatbelt 包裹的命令；
3) 仍通过统一的 `run_subprocess` 执行并返回标准化结果。

注意：这个后端只在 macOS 生效；其他平台会抛 SandboxError。
"""

from __future__ import annotations

import asyncio
import os
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


def _run_coro(coro):
    """在同步上下文里跑协程；避免和已有 event loop 冲突。

    srt 的 API 是 async，而插件体系是同步接口，所以这里做桥接。
    """
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


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
        # Seatbelt 是 macOS 专属能力，提前 fail-fast。
        if os.uname().sysname.lower() != "darwin":
            raise SandboxError("seatbelt 后端仅支持 macOS")

        safe_cwd = resolve_in_workspace(policy.workspace_root, cwd)

        try:
            from srt import SandboxManager, SandboxRuntimeConfig
            from srt.config import FilesystemConfig, NetworkConfig
        except Exception as exc:
            raise SandboxError(
                "seatbelt 后端依赖 py-sandboxrt（import srt 失败），请先安装 py-sandboxrt"
            ) from exc

        # srt 使用 allow-only 网络模型：不在 allowed_domains 就默认拒绝。
        network = NetworkConfig(
            allowed_domains=(policy.allowed_domains if policy.allow_network else []),
            denied_domains=[],
        )
        # 写权限严格收敛到 workspace_root；deny_* 再做补充黑名单。
        filesystem = FilesystemConfig(
            deny_read=policy.deny_read,
            allow_write=[str(policy.workspace_root)],
            deny_write=policy.deny_write,
        )
        config = SandboxRuntimeConfig(network=network, filesystem=filesystem)

        mgr = SandboxManager()
        wrapped_command = command
        try:
            # initialize -> wrap -> run：三步固定顺序，不可交换。
            _run_coro(mgr.initialize(config))
            wrapped_command = _run_coro(mgr.wrap_with_sandbox(command))
            result = run_subprocess(
                command=wrapped_command,
                cwd=safe_cwd,
                env=env,
                limits=limits,
                backend_name=self.backend_name,
            )
        finally:
            # cleanup/reset 失败不应影响主流程返回，因此全部吞掉。
            try:
                mgr.cleanup_after_command()
            except Exception:
                pass
            try:
                _run_coro(mgr.reset())
            except Exception:
                pass

        return result

