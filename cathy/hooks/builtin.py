"""内置 hook 三件套（演示性，非完整安全策略）。

- audit_log              -> PostToolUse：把每次 tool call 写一行到 data/audit.jsonl
- block_dangerous_paths  -> PreToolUse ：拦截 file_ops 写到 ~/.ssh / /etc / /System 等敏感路径
- strip_secrets          -> UserPromptSubmit：用 regex 剔除疑似 API key 字串

这些函数都是无状态纯函数：入参 HookEvent，出参 HookDecision。
通过 'cathy.hooks.builtin:func_name' 在配置里挂入。
"""

from __future__ import annotations

import json
import os
import re
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
