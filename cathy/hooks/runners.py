"""Hook 执行后端：Python 反射 + 子进程命令。

两种 runner 接口对齐：输入 HookEvent，输出 (HookDecision, error_text)。
- error_text 非空 = 该 hook 自身崩溃（被吞掉，不影响主循环），仅记日志。
- HookDecision 即使在 error 时也会返回一个 noop，方便上层无脑 merge。
"""

from __future__ import annotations

import importlib
import json
import subprocess
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from .events import HookDecision, HookEvent


@dataclass
class HookSpec:
    """运行时 hook 规格（HookManager 解析配置后产出）。

    Attributes:
        runner_type: "python" | "command"
        target: python 模式下的 "module:func"
        command: command 模式下的 shell 命令字符串（透传给 /bin/sh -c）
        timeout_sec: command 模式的硬超时；python 模式忽略
        name: 用于日志的可读名（默认取 target / command 前缀）
    """

    runner_type: str
    target: str = ""
    command: str = ""
    timeout_sec: float = 5.0
    name: str = ""

    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.runner_type == "python":
            return self.target or "<python:?>"
        return (self.command or "<cmd:?>")[:40]


class PythonRunner:
    """反射调用 'module.path:callable'。

    被调用的函数签名约定：def hook(event: HookEvent) -> HookDecision | dict | None
    返回 None / 空 dict 视为 noop。
    """

    def __init__(self, spec: HookSpec) -> None:
        if spec.runner_type != "python":
            raise ValueError(f"PythonRunner 不接受 runner_type={spec.runner_type!r}")
        self._spec = spec
        self._fn: Callable[[HookEvent], Any] | None = None

    def _resolve(self) -> Callable[[HookEvent], Any]:
        if self._fn is not None:
            return self._fn
        target = self._spec.target
        if ":" not in target:
            raise ValueError(f"python hook target 必须为 'module:func'，得到 {target!r}")
        module_path, func_name = target.split(":", 1)
        module = importlib.import_module(module_path)
        fn = getattr(module, func_name, None)
        if fn is None or not callable(fn):
            raise ValueError(f"在 {module_path} 中找不到可调用对象 {func_name!r}")
        self._fn = fn
        return fn

    def run(self, event: HookEvent) -> tuple[HookDecision, str]:
        try:
            fn = self._resolve()
            result = fn(event)
        except Exception:
            return HookDecision.noop(), traceback.format_exc()

        if result is None:
            return HookDecision.noop(), ""
        if isinstance(result, HookDecision):
            return result, ""
        if isinstance(result, dict):
            return HookDecision.from_dict(result), ""
        return (
            HookDecision.noop(),
            f"hook 返回类型不支持: {type(result).__name__}（应为 HookDecision/dict/None）",
        )


class CommandRunner:
    """子进程后端，与 Claude Code 的 hook 协议对齐。

    协议：
    - 子进程启动后，通过 stdin 收到 event 的 JSON。
    - stdout 为 JSON 形态的 HookDecision（可选，缺失视为 noop）。
    - exit code 含义：
        0 -> 正常返回（看 stdout 决定是否 block / 改写）
        2 -> 等价 block=True，block_reason 取 stderr 内容（兼容 CC 写法）
        其它非零 -> 视为 hook 崩溃，吞掉错误（不影响主循环）
    - 超时：硬 kill，等价 hook 崩溃。
    """

    def __init__(self, spec: HookSpec) -> None:
        if spec.runner_type != "command":
            raise ValueError(f"CommandRunner 不接受 runner_type={spec.runner_type!r}")
        if not spec.command:
            raise ValueError("command hook 必须提供 command 字符串")
        self._spec = spec

    def run(self, event: HookEvent) -> tuple[HookDecision, str]:
        payload = event.to_json()
        try:
            proc = subprocess.run(  # noqa: S603 - 用户配置，明文意图
                ["/bin/sh", "-c", self._spec.command],
                input=payload,
                capture_output=True,
                text=True,
                timeout=max(0.1, float(self._spec.timeout_sec)),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return (
                HookDecision.noop(),
                f"command hook 超时 ({self._spec.timeout_sec}s): {self._spec.command!r}",
            )
        except Exception:
            return HookDecision.noop(), traceback.format_exc()

        if proc.returncode == 2:
            reason = (proc.stderr or proc.stdout or "command hook blocked").strip()
            return HookDecision(block=True, block_reason=reason), ""

        if proc.returncode != 0:
            return (
                HookDecision.noop(),
                f"command hook exit={proc.returncode} stderr={proc.stderr!r}",
            )

        stdout = (proc.stdout or "").strip()
        if not stdout:
            return HookDecision.noop(), ""

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            return (
                HookDecision.noop(),
                f"command hook stdout 不是合法 JSON: {exc}; 原文={stdout[:200]!r}",
            )

        if not isinstance(data, dict):
            return (
                HookDecision.noop(),
                f"command hook stdout JSON 顶层必须是对象，得到 {type(data).__name__}",
            )
        return HookDecision.from_dict(data), ""


def build_runner(spec: HookSpec) -> PythonRunner | CommandRunner:
    if spec.runner_type == "python":
        return PythonRunner(spec)
    if spec.runner_type == "command":
        return CommandRunner(spec)
    raise ValueError(f"不支持的 hook runner_type: {spec.runner_type!r}")


@dataclass
class RunnerInvocation:
    """一次具体执行的可观测记录，便于 manager 写日志。"""

    spec_name: str
    decision: HookDecision
    error: str
    latency_ms: int

    @classmethod
    def from_run(
        cls, spec: HookSpec, decision: HookDecision, error: str, started_ms: int
    ) -> "RunnerInvocation":
        return cls(
            spec_name=spec.display_name(),
            decision=decision,
            error=error,
            latency_ms=max(0, int(time.time() * 1000) - started_ms),
        )
