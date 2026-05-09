"""Sandbox 抽象层（可插拔后端）。

如果你第一次接触 sandbox，可以把这里理解成“统一插座”：

- 上层（shell_exec / file_ops）只会调用 `SandboxExecutor.run(...)`
- 底层具体怎么隔离（Seatbelt、nsjail、local 限制）由后端实现决定

这样做的好处：
1) 先在 mac 上用 Seatbelt 跑通；
2) 以后换 Linux nsjail 时不改上层业务代码；
3) 测试环境也可以用 local_restricted 兜底。
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SandboxLimits:
    """运行时限制（防卡死/防输出爆炸）。"""

    timeout_sec: float = 10.0
    max_output_bytes: int = 64 * 1024


@dataclass
class SandboxPolicy:
    """隔离策略（文件系统 + 网络）。

    注意这里是“策略描述”，不直接执行隔离。
    真正执行由具体后端（seatbelt/local/nsjail）负责。
    """

    workspace_root: Path
    allow_network: bool = False
    allowed_domains: list[str] = field(default_factory=list)
    deny_read: list[str] = field(default_factory=list)
    deny_write: list[str] = field(default_factory=list)


@dataclass
class SandboxResult:
    """统一返回结构，便于上层序列化给 LLM。"""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    backend: str
    command: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SandboxError(RuntimeError):
    """sandbox 相关错误，供插件层转成用户可读错误。"""

    pass


class SandboxExecutor(ABC):
    """所有 sandbox 后端必须实现的统一接口。"""

    @property
    @abstractmethod
    def backend_name(self) -> str: ...

    @abstractmethod
    def run(
        self,
        *,
        command: str,
        cwd: Path,
        env: dict[str, str],
        limits: SandboxLimits,
        policy: SandboxPolicy,
    ) -> SandboxResult:
        """执行命令并返回标准化结果。

        参数说明：
        - command: 原始命令字符串（上层已做基本危险命令校验）
        - cwd: 期望工作目录（后端仍需做越界校验）
        - env: 允许透传的环境变量
        - limits/policy: 限制与隔离策略
        """
        ...


def resolve_in_workspace(root: Path, cwd: str | Path | None = None) -> Path:
    """把 cwd 解析为 workspace 内绝对路径，越界直接抛错。

    这是最重要的“第一道栏杆”：即使后端是 local_restricted，也不能跑到
    workspace 之外执行命令。
    """
    base = root.expanduser().resolve()
    if not base.exists():
        base.mkdir(parents=True, exist_ok=True)
    if cwd is None or str(cwd).strip() == "":
        target = base
    else:
        p = Path(cwd)
        target = (p if p.is_absolute() else base / p).expanduser().resolve()
    if target != base and base not in target.parents:
        raise SandboxError(f"cwd 越界（必须在 {base} 内）: {cwd}")
    return target


def run_subprocess(
    *,
    command: str,
    cwd: Path,
    env: dict[str, str],
    limits: SandboxLimits,
    backend_name: str,
) -> SandboxResult:
    """执行命令的公共实现（统一超时/输出截断/返回格式）。

    说明：
    - 这里故意保留 `shell=True`，因为我们要支持自然 shell 命令；
    - 风险由上层 `_validate_command` + workspace 校验 + sandbox 后端共同兜底。
    """
    try:
        proc = subprocess.run(  # noqa: S602
            command,
            shell=True,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(0.1, float(limits.timeout_sec)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "")[: limits.max_output_bytes]
        err = (exc.stderr or "")[: limits.max_output_bytes]
        return SandboxResult(
            stdout=out,
            stderr=err,
            exit_code=124,
            timed_out=True,
            backend=backend_name,
            command=command,
        )

    return SandboxResult(
        stdout=(proc.stdout or "")[: limits.max_output_bytes],
        stderr=(proc.stderr or "")[: limits.max_output_bytes],
        exit_code=int(proc.returncode),
        timed_out=False,
        backend=backend_name,
        command=command,
    )

