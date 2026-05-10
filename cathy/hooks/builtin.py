"""内置 hook（演示性，非完整安全策略）。

- audit_log              -> PostToolUse：把每次 tool call 写一行到 data/audit.jsonl
- block_dangerous_paths  -> PreToolUse ：拦截 file_ops 写到 ~/.ssh / /etc / /System 等敏感路径
- strip_secrets          -> UserPromptSubmit：用 regex 剔除疑似 API key 字串
- block_dangerous_shell_commands -> PreToolUse：拦截明显高危 shell_exec 命令
- permission_gate        -> PreToolUse：按 trust_level 执行 allow/audit/ask/deny

这些函数都是无状态纯函数：入参 HookEvent，出参 HookDecision。
通过 'cathy.hooks.builtin:func_name' 在配置里挂入。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

from .events import HookDecision, HookEvent

# 写 JSONL 时只用进程内锁；多进程场景下后续 Phase 替换为 fcntl。
_AUDIT_LOCK = threading.Lock()

# 默认审计日志路径；由 audit_log 在第一次调用时按 CWD 解析为绝对路径。
_DEFAULT_AUDIT_PATH = "data/audit.jsonl"


def _resolve_audit_path(event: HookEvent) -> Path:
    """优先取 event.meta.audit_path，否则 fallback 到 CWD/data/audit.jsonl。"""
    raw = (event.meta or {}).get("audit_path")
    if raw:
        return Path(str(raw))
    return Path(os.getcwd()) / _DEFAULT_AUDIT_PATH


def audit_log(event: HookEvent) -> HookDecision:
    """PostToolUse：把 tool 调用持久化到 JSONL。

    每行写一条形如：
    {"ts_ms":..., "session":"abc", "tool":"web_search", "params":{...},
     "result_preview":"...", "latency_ms": 12, "result_len": 1234}
    """
    if event.type != "PostToolUse":
        return HookDecision.noop()

    payload = event.payload or {}
    result = str(payload.get("result", ""))
    entry: dict[str, Any] = {
        "ts_ms": event.ts_ms,
        "session": event.session_id,
        "tool": payload.get("tool") or event.matcher_target,
        "params": payload.get("params"),
        "result_len": len(result),
        "result_preview": result[:300],
        "latency_ms": payload.get("latency_ms"),
    }

    path = _resolve_audit_path(event)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False)
        with _AUDIT_LOCK, open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        # 静默失败：审计 hook 不应因磁盘问题影响主循环
        return HookDecision.noop()

    return HookDecision.noop()


# ---- block_dangerous_paths ----------------------------------------------------

# 任何写入到这些目录或其子树的 file_ops 都会被阻断。
# macOS 上 /etc 是 /private/etc 的 symlink；realpath 解析后落在 /private 下，所以两套前缀都列。
# 注意：故意不放 /var（macOS 的 /var/folders 是 tempdir 默认根，开发期会大量正常写入）。
_DEFAULT_DANGEROUS = (
    "/etc",
    "/private/etc",
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/Library/LaunchDaemons",
)
# 用户根下也禁止写到 .ssh / .aws / .gnupg / .env*
_DEFAULT_DANGEROUS_HOME_RE = re.compile(
    r"(^|/)(\.ssh|\.aws|\.gnupg|\.kube)(/|$)|(^|/)\.env(\.|$)",
)


def _expand_path(raw: str) -> str:
    if not raw:
        return ""
    return os.path.realpath(os.path.expanduser(os.path.expandvars(raw)))


def block_dangerous_paths(event: HookEvent) -> HookDecision:
    """PreToolUse：file_ops 类工具写入受保护路径时阻断。

    生效工具：write_file（其它工具透传）。
    可配置：event.meta.dangerous_prefixes 覆盖默认列表。
    """
    if event.type != "PreToolUse":
        return HookDecision.noop()

    payload = event.payload or {}
    tool = payload.get("tool") or event.matcher_target
    if tool != "write_file":
        return HookDecision.noop()

    params = payload.get("params") or {}
    raw_path = str(params.get("path") or params.get("file") or "")
    if not raw_path:
        return HookDecision.noop()

    abs_path = _expand_path(raw_path)
    extra_prefixes = (event.meta or {}).get("dangerous_prefixes") or ()
    prefixes = tuple(_DEFAULT_DANGEROUS) + tuple(_expand_path(p) for p in extra_prefixes if p)

    if any(abs_path == p or abs_path.startswith(p + "/") for p in prefixes):
        return HookDecision(
            block=True,
            block_reason=(
                f"路径 {raw_path!r} 命中受保护前缀（解析为 {abs_path}）。"
                "若确为合法需求，请改写到工作目录内。"
            ),
        )

    if _DEFAULT_DANGEROUS_HOME_RE.search(abs_path):
        return HookDecision(
            block=True,
            block_reason=(
                f"路径 {raw_path!r}（解析为 {abs_path}）触及用户敏感目录"
                "（.ssh / .aws / .gnupg / .kube / .env*），已阻断。"
            ),
        )

    return HookDecision.noop()


# ---- strip_secrets ------------------------------------------------------------

# 匹配常见 API key / token；保守起见只保留相对独特的前缀模式，避免误伤普通文本。
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),                  # openai / generic
    re.compile(r"xox[abposr]-[A-Za-z0-9\-]{10,}"),          # slack
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),                    # github personal token
    re.compile(r"AKIA[0-9A-Z]{12,}"),                       # aws access key id
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                 # google
    re.compile(r"(?i)\b(api[_-]?key|token|secret)\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{16,})[\"']?"),
)

# shell 级危险命令：用于 PreToolUse + shell_exec。
_DANGEROUS_SHELL_PATTERNS = (
    re.compile(r"\brm\s+-rf\s+/"),
    re.compile(r":\(\)\s*\{\s*:\|\:&\s*\};:"),  # fork bomb
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs(\.[a-z0-9_]+)?\b"),
    re.compile(r">\s*/dev/(sd[a-z]\d*|disk\d+)"),
)


def strip_secrets(event: HookEvent) -> HookDecision:
    """UserPromptSubmit：把疑似 secret 替换成 [REDACTED]，并附 inject_context 提醒。"""
    if event.type != "UserPromptSubmit":
        return HookDecision.noop()
    text = str((event.payload or {}).get("user_input") or "")
    if not text:
        return HookDecision.noop()

    redacted = text
    hits = 0
    for pat in _SECRET_PATTERNS:
        new_text, n = pat.subn("[REDACTED]", redacted)
        redacted = new_text
        hits += n

    if hits == 0:
        return HookDecision.noop()

    return HookDecision(
        rewrite_user_input=redacted,
        inject_context=(
            f"[hook:strip_secrets] 用户输入中检测到 {hits} 处疑似密钥/令牌，已替换为 [REDACTED]。"
            "如需正常处理这些字面量，请改用 read_file 等显式来源。"
        ),
    )


def block_dangerous_shell_commands(event: HookEvent) -> HookDecision:
    """PreToolUse：拦截 shell_exec 的明显高危命令。"""
    if event.type != "PreToolUse":
        return HookDecision.noop()
    payload = event.payload or {}
    tool = payload.get("tool") or event.matcher_target
    if tool != "shell_exec":
        return HookDecision.noop()
    params = payload.get("params") or {}
    cmd = str(params.get("command") or "").strip()
    if not cmd:
        return HookDecision.noop()
    for pat in _DANGEROUS_SHELL_PATTERNS:
        if pat.search(cmd):
            return HookDecision(
                block=True,
                block_reason=(
                    f"命令 {cmd!r} 命中高危规则 {pat.pattern!r}，已阻断。"
                    "请使用更安全的替代命令。"
                ),
            )
    return HookDecision.noop()


def _match_mcp_rules(tool: str, mcp_rules: dict[str, Any]) -> str | None:
    """按 deny → ask → allow 的优先级匹配工具名 glob 规则。

    返回命中的 action（deny/ask/allow）或 None；mcp_rules 形如：
        {"deny": ["mcp__fs__delete_*"],
         "ask":  ["mcp__fs__write_*"],
         "allow": ["mcp__memory__*"]}
    匹配只对 `mcp__` 前缀的工具生效，避免误伤内置工具。
    """
    if not tool or not tool.startswith("mcp__") or not isinstance(mcp_rules, dict):
        return None
    for action in ("deny", "ask", "allow"):
        patterns = mcp_rules.get(action) or []
        if not isinstance(patterns, (list, tuple)):
            continue
        for pat in patterns:
            if isinstance(pat, str) and fnmatch.fnmatchcase(tool, pat):
                return action
    return None


def permission_gate(event: HookEvent) -> HookDecision:
    """PreToolUse 权限闸门（Phase 4C+）。

    依赖 event.meta:
      - trust_policy: {builtin|verified|untrusted -> allow|audit|ask|deny}
      - mcp_rules: 可选，{deny|ask|allow: [glob,...]}，仅对 `mcp__*` 工具生效，
                   命中后**覆盖** trust_policy 的判定（CC `permissions` 同款语义）。
      - interaction_mode: interactive | non_interactive
      - non_interactive_fallback: allow | deny
    """
    if event.type != "PreToolUse":
        return HookDecision.noop()

    payload = event.payload or {}
    tool = str(payload.get("tool") or event.matcher_target or "")
    trust_level = str(payload.get("trust_level") or "untrusted")

    meta = event.meta or {}
    trust_policy = dict(meta.get("trust_policy") or {})
    action = str(trust_policy.get(trust_level) or "allow").lower()

    # mcp_rules 优先级最高（CC/Cursor 同款 deny→ask→allow），命中即覆盖 trust_policy。
    rule_hit = _match_mcp_rules(tool, dict(meta.get("mcp_rules") or {}))
    if rule_hit is not None:
        action = rule_hit

    if action in {"allow", "audit"}:
        return HookDecision.noop()

    if action == "deny":
        return HookDecision(
            block=True,
            block_reason=f"权限策略拒绝工具 {tool!r}（trust_level={trust_level}，action=deny）。",
        )

    if action != "ask":
        return HookDecision(
            block=True,
            block_reason=(
                f"权限策略 action={action!r} 非法，按 deny 处理（tool={tool}, trust={trust_level}）。"
            ),
        )

    mode = str(meta.get("interaction_mode") or "non_interactive")
    if mode != "interactive":
        fallback = str(meta.get("non_interactive_fallback") or "deny").lower()
        if fallback == "allow":
            return HookDecision.noop()
        return HookDecision(
            block=True,
            block_reason=(
                f"工具 {tool!r} 需要审批（trust_level={trust_level}），"
                "当前为非交互模式，默认拒绝。"
            ),
        )

    try:
        sys.stdout.write(
            f"\n[permission_gate] 工具 {tool}（trust={trust_level}）请求执行，允许吗？[y/N]: "
        )
        sys.stdout.flush()
        answer = input().strip().lower()
    except Exception:
        answer = ""
    if answer in {"y", "yes"}:
        return HookDecision.noop()
    return HookDecision(
        block=True,
        block_reason=f"工具 {tool!r} 未通过人工审批（trust_level={trust_level}）。",
    )
