"""HookManager：配置加载、matcher 过滤、串联派发、与 .claude/settings.json 兼容。

配置形态（YAML，与 Claude Code settings.json 形态对齐）：

```yaml
HOOKS:
  PreToolUse:
    - matcher: "write_file"          # 可选；缺省 / "*" / "" 都视为全匹配
      hooks:
        - type: python
          target: "cathy.hooks.builtin:block_dangerous_paths"
          timeout: 5                 # 仅 command 用；python 忽略
    - hooks:
        - type: command
          command: "python tools/cc_hook.py"
  PostToolUse:
    - hooks:
        - type: python
          target: "cathy.hooks.builtin:audit_log"
```

matcher 语义（保持简单，不引入正则；够用即可）：
- 缺省 / "*" / ""  -> 任意工具/对象
- 含 "|" 的字符串 -> split 成多段，**任一段**命中即可（每段独立支持 glob）
- 含 "*"/"?"/"[]" 的字符串 -> 走 fnmatch glob 匹配（例如 `mcp__fs__*`）
- 其它字符串       -> 整串 == matcher_target 即命中

manager.dispatch(event) 的语义：
1. 选出本事件类型下，matcher 命中 event.matcher_target 的所有 hook（按配置顺序）。
2. 串行执行；遇到 block=True 的 hook **立刻短路**（不再执行后续）。
3. 把已执行 hook 的 decision 通过 merge_decisions 合并返回。
4. 单个 hook 自身崩溃只记 audit log，不影响主循环。
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .events import ALL_EVENTS, HookDecision, HookEvent, merge_decisions
from .runners import (
    CommandRunner,
    HookSpec,
    PythonRunner,
    RunnerInvocation,
    build_runner,
)


def _matcher_hit(pattern: str, target: str) -> bool:
    """单个 matcher 与 target 的匹配；含通配符走 fnmatch，否则字面量。"""
    if not pattern:
        return False
    if any(ch in pattern for ch in ("*", "?", "[")):
        return fnmatch.fnmatchcase(target or "", pattern)
    return target == pattern


@dataclass
class _MatcherGroup:
    matcher: str  # 已规范化：'*' 表示任意；其它含字面量或 'A|B|C'
    specs: list[HookSpec] = field(default_factory=list)
    runners: list[PythonRunner | CommandRunner] = field(default_factory=list)

    def matches(self, target: str) -> bool:
        m = self.matcher
        if m == "*" or m == "":
            return True
        if "|" in m:
            for piece in m.split("|"):
                piece = piece.strip()
                if not piece:
                    continue
                if _matcher_hit(piece, target):
                    return True
            return False
        return _matcher_hit(m, target)


class HookManager:
    """管理所有 hook 的注册与派发。

    使用方式：
        mgr = HookManager.from_config(cfg.get("HOOKS"), project_root=PROJECT_ROOT)
        decision = mgr.dispatch(HookEvent(type=PRE_TOOL_USE, matcher_target="write_file", ...))
    """

    def __init__(
        self,
        config: dict[str, list[dict[str, Any]]] | None = None,
        *,
        on_log: "callable | None" = None,
    ) -> None:
        # event_type -> [_MatcherGroup, ...]
        self._groups: dict[str, list[_MatcherGroup]] = {ev: [] for ev in ALL_EVENTS}
        self._on_log = on_log or (lambda _entry: None)
        self.last_invocations: list[RunnerInvocation] = []  # 仅最近一次 dispatch 的执行记录
        if config:
            self._ingest(config)

    # ---------------- 配置摄入 ----------------

    @classmethod
    def from_config(
        cls,
        yaml_hooks: dict[str, Any] | None,
        *,
        project_root: Path | None = None,
        on_log: "callable | None" = None,
    ) -> "HookManager":
        """合并 YAML config 的 HOOKS 段 + 项目根 .claude/settings.json 的 hooks 段。

        合并顺序：先 YAML，后 .claude/settings.json（后者追加在末尾，优先级靠后即可被覆盖）。
        """
        mgr = cls(on_log=on_log)
        if yaml_hooks:
            mgr._ingest(yaml_hooks)
        if project_root:
            cc_path = Path(project_root) / ".claude" / "settings.json"
            if cc_path.exists() and cc_path.is_file():
                try:
                    raw = json.loads(cc_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    mgr._log(
                        {"phase": "load", "source": str(cc_path), "error": str(exc)}
                    )
                else:
                    cc_hooks = (raw or {}).get("hooks") if isinstance(raw, dict) else None
                    if cc_hooks:
                        mgr._ingest(cc_hooks)
        return mgr

    def _ingest(self, hooks_cfg: dict[str, Any]) -> None:
        """规范化并注册一段配置（可被多次调用以追加注册）。"""
        if not isinstance(hooks_cfg, dict):
            return
        for event_type, groups in hooks_cfg.items():
            if event_type not in self._groups:
                self._log(
                    {"phase": "load", "warn": f"未知 hook 事件类型: {event_type!r}"}
                )
                continue
            if not isinstance(groups, list):
                continue
            for group_def in groups:
                if not isinstance(group_def, dict):
                    continue
                matcher = self._normalize_matcher(group_def.get("matcher"))
                hooks_list = group_def.get("hooks") or []
                specs: list[HookSpec] = []
                runners: list[PythonRunner | CommandRunner] = []
                for hk in hooks_list:
                    if not isinstance(hk, dict):
                        continue
                    spec = self._build_spec(hk)
                    if spec is None:
                        continue
                    try:
                        runner = build_runner(spec)
                    except Exception as exc:
                        self._log(
                            {
                                "phase": "load",
                                "event": event_type,
                                "hook": spec.display_name(),
                                "error": f"build_runner 失败: {exc}",
                            }
                        )
                        continue
                    specs.append(spec)
                    runners.append(runner)
                if specs:
                    self._groups[event_type].append(
                        _MatcherGroup(matcher=matcher, specs=specs, runners=runners)
                    )

    @staticmethod
    def _normalize_matcher(raw: Any) -> str:
        if raw is None:
            return "*"
        s = str(raw).strip()
        if not s:
            return "*"
        return s

    @staticmethod
    def _build_spec(hk: dict[str, Any]) -> HookSpec | None:
        runner_type = str(hk.get("type") or "").strip().lower()
        if runner_type not in {"python", "command"}:
            return None
        if runner_type == "python":
            target = str(hk.get("target") or "").strip()
            if not target:
                return None
            return HookSpec(
                runner_type="python",
                target=target,
                name=str(hk.get("name") or "") or target,
            )
        cmd = str(hk.get("command") or "").strip()
        if not cmd:
            return None
        return HookSpec(
            runner_type="command",
            command=cmd,
            timeout_sec=float(hk.get("timeout") or 5),
            name=str(hk.get("name") or "") or cmd[:40],
        )

    # ---------------- 派发 ----------------

    def dispatch(self, event: HookEvent) -> HookDecision:
        """同步派发；返回合并后的 HookDecision。"""
        groups = self._groups.get(event.type) or []
        invocations: list[RunnerInvocation] = []
        decisions: list[HookDecision] = []

        for group in groups:
            if not group.matches(event.matcher_target or ""):
                continue
            for spec, runner in zip(group.specs, group.runners):
                started_ms = int(__import__("time").time() * 1000)
                decision, error = runner.run(event)
                inv = RunnerInvocation.from_run(spec, decision, error, started_ms)
                invocations.append(inv)
                if error:
                    self._log(
                        {
                            "phase": "exec",
                            "event": event.type,
                            "hook": inv.spec_name,
                            "error": error.splitlines()[-1] if error else "",
                            "latency_ms": inv.latency_ms,
                        }
                    )
                decisions.append(decision)
                if decision.block:
                    self.last_invocations = invocations
                    return merge_decisions(decisions)

        self.last_invocations = invocations
        return merge_decisions(decisions)

    # ---------------- 观测 ----------------

    def has_hooks_for(self, event_type: str) -> bool:
        return bool(self._groups.get(event_type))

    def summary(self) -> dict[str, list[str]]:
        """返回 {event_type: [hook_name, ...]}，便于 CLI 启动 banner 打印。"""
        out: dict[str, list[str]] = {}
        for ev, groups in self._groups.items():
            names: list[str] = []
            for g in groups:
                for spec in g.specs:
                    names.append(spec.display_name())
            if names:
                out[ev] = names
        return out

    def _log(self, entry: dict[str, Any]) -> None:
        try:
            self._on_log(entry)
        except Exception:
            pass


# ---------------- 便利构造 ----------------


def make_event(event_type: str, **kwargs: Any) -> HookEvent:
    """快速构造 HookEvent；HookManager.has_hooks_for 检查通过后再 build 可省开销。"""
    return HookEvent(type=event_type, **kwargs)


def empty_manager() -> HookManager:
    return HookManager()
