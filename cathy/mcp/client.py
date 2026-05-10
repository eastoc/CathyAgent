"""MCP 客户端：FastMCP Client + 后台 asyncio loop。

为什么这么写：
- FastMCP `Client` 是 async API，而 Agent 主循环是同步的；把它跑在守护线程
  里维持长连接，调用从主线程通过 `run_coroutine_threadsafe` 投递到 loop。
- `async with client` 必须在打开它的同一 task 里关闭（anyio 的 cancel scope
  约束），所以生命周期任务 `_lifecycle` 一直挂在 `stop_event.wait()` 上，
  shutdown 时由主线程 `call_soon_threadsafe(stop_event.set)` 让它自然退出。
- 工具名暴露给 LLM 时按 `mcp__<server>__<tool>` 命名（与 Claude Code 同款），
  这样 hook matcher / permission allowlist 能按 server 维度做细粒度策略；
  当上游没有传入 `server_names`（例如 in-memory 单 server 测试）时退化为
  `mcp__<tool>`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def normalize_root_uri(value: str) -> str:
    """把任意路径/URI 规范化为合法的 `file://` URI。

    FastMCP 的 `convert_roots_list` 会把字符串走 `pydantic.FileUrl(...)` 构造，
    要求必须是 URL 形态；裸路径如 `/tmp` 会触发 url_parsing。
    """
    s = (value or "").strip()
    if not s:
        return s
    if s.startswith("file://"):
        return s
    if s.startswith("/"):
        return f"file://{s}"
    return s


def infer_mcp_client_roots_from_servers(mcp_servers: dict[str, Any]) -> list[str]:
    """从 mcp_servers 配置里推断 FastMCP Client 应声明的 roots。

    官方 `@modelcontextprotocol/server-filesystem` 会在初始化时向客户端请求
    `roots/list`；若 Client 未配置 `roots`，代理在多 server 场景下可能报
    -32603（No active context）。此处收集各 stdio server 命令行里出现在
    filesystem 包名之后的目录参数，并统一转成 `file://...` URI 形态。
    """
    roots: list[str] = []
    for spec in mcp_servers.values():
        if not isinstance(spec, dict):
            continue
        args = spec.get("args")
        if not isinstance(args, list):
            continue
        for i, arg in enumerate(args):
            if not isinstance(arg, str) or "server-filesystem" not in arg:
                continue
            for tail in args[i + 1 :]:
                if not isinstance(tail, str):
                    continue
                if tail.startswith("/") or tail.startswith("file:"):
                    roots.append(normalize_root_uri(tail))
            break
    seen: set[str] = set()
    out: list[str] = []
    for r in roots:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


try:
    from fastmcp import Client as _FastMCPClient

    HAS_FASTMCP = True
except ImportError:  # pragma: no cover - 仅在 fastmcp 缺席时走到
    _FastMCPClient = None  # type: ignore[assignment]
    HAS_FASTMCP = False


_TOOL_NAME_PREFIX = "mcp__"
_SAFE_NAME_RE = re.compile(r"[^a-z0-9_]")


class McpError(RuntimeError):
    """MCP 相关错误：连接失败 / 工具未注册 / 单次调用超时等。"""


@dataclass(frozen=True)
class McpToolInfo:
    """一个 MCP 工具暴露给宿主的视图。

    - `inner_name`：FastMCP `list_tools()` 返回的原名（多 server 时含 `<server>_` 前缀）。
    - `outer_name`：注册到 `PluginRegistry` 的名字，形如 `mcp__<server>__<tool>`。
    - `server_name`：解析出的 server 标识；不可识别时为空字符串。
    """

    inner_name: str
    outer_name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str = ""


def _sanitize_segment(s: str) -> str:
    """把单段名字规范成 `^[a-z][a-z0-9_]*$`（不含双下划线分隔逻辑）。"""
    out = (s or "").strip().lower()
    out = _SAFE_NAME_RE.sub("_", out)
    if not out:
        out = "x"
    if not out[0].isalpha():
        out = f"t_{out}"
    return out


def _split_server_prefix(inner: str, server_names: Iterable[str]) -> tuple[str, str]:
    """从 `<server>_<tool>` 形式的 inner 名里拆出 (server, tool)。

    优先匹配最长 server 名，避免歧义（例如 server 名既有 `fs` 又有 `fs_foo`）。
    匹配不到返回 ("", inner)，调用方按单段处理。
    """
    if not inner:
        return ("", inner)
    for name in sorted({n for n in server_names if n}, key=len, reverse=True):
        prefix = f"{name}_"
        if inner.startswith(prefix):
            return (name, inner[len(prefix):])
    return ("", inner)


def build_outer_tool_name(inner: str, server_names: Iterable[str] | None = None) -> str:
    """生成对外的 outer_name。

    - 多 server 且能匹配 server 前缀时：`mcp__<server>__<tool>`
    - 否则：`mcp__<sanitized_inner>`（兼容单 server / in-memory 测试）
    """
    if server_names:
        server, tool = _split_server_prefix(inner, server_names)
        if server:
            return f"{_TOOL_NAME_PREFIX}{_sanitize_segment(server)}__{_sanitize_segment(tool)}"
    return f"{_TOOL_NAME_PREFIX}{_sanitize_segment(inner)}"


def _sanitize_outer_name(inner: str) -> str:
    """兼容旧函数名：等价于无 server 信息的 `build_outer_tool_name`。"""
    return build_outer_tool_name(inner, None)


def _render_call_result(result: Any) -> str:
    """把 FastMCP `CallToolResult` 转成给模型看的字符串。

    优先级：error → text content 拼接 → structured data → 空串。
    """
    if getattr(result, "is_error", False):
        err_parts: list[str] = []
        for c in getattr(result, "content", None) or []:
            text = getattr(c, "text", None)
            if text:
                err_parts.append(text)
        return f"[McpError] {'; '.join(err_parts) or 'unknown error'}"

    parts: list[str] = []
    for c in getattr(result, "content", None) or []:
        text = getattr(c, "text", None)
        if text:
            parts.append(text)
    if parts:
        return "\n".join(parts)

    data = getattr(result, "data", None)
    if data is not None:
        if isinstance(data, str):
            return data
        try:
            return json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(data)

    sc = getattr(result, "structured_content", None)
    if sc is not None:
        try:
            return json.dumps(sc, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(sc)

    return ""


@dataclass
class _HubState:
    loop: asyncio.AbstractEventLoop | None = None
    thread: threading.Thread | None = None
    client: Any = None
    stop_event: asyncio.Event | None = None
    tools: list[McpToolInfo] = field(default_factory=list)
    outer_to_inner: dict[str, str] = field(default_factory=dict)
    error: BaseException | None = None


class McpHub:
    """聚合多个 MCP server 的同步入口。

    Args:
        source: 任何 `fastmcp.Client(...)` 接受的入参；推荐两种：
            * `MCPConfig` 字典：`{"mcpServers": {<id>: {...}, ...}}`，与 CC 同构。
            * `FastMCP` 实例 / 路径 / URL：用于测试或单 server 直连。
        connect_timeout: 启动期等待 ready 的最大秒数。
        default_call_timeout: 单次 `call_tool` 的最大秒数。
    """

    def __init__(
        self,
        source: Any,
        *,
        connect_timeout: float = 60.0,
        default_call_timeout: float = 120.0,
        roots: list[str] | None = None,
        server_names: Iterable[str] | None = None,
    ) -> None:
        self._source = source
        self._connect_timeout = connect_timeout
        self._default_call_timeout = default_call_timeout
        self._roots = roots
        self._server_names: tuple[str, ...] = tuple(server_names or ())
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._state = _HubState()
        self._started = False

    # ----- 生命周期 ----- #

    def start(self) -> None:
        """阻塞到 client 连接成功并完成 `tools/list`，或抛错。"""
        if not HAS_FASTMCP:
            raise McpError(
                "fastmcp 未安装；请 `pip install fastmcp` 后再启用 MCP（或在 config.yaml 里关闭 MCP）"
            )
        if self._started:
            return
        self._started = True

        thread = threading.Thread(target=self._thread_main, daemon=True, name="cathy-mcp")
        self._state.thread = thread
        thread.start()

        if not self._ready.wait(timeout=self._connect_timeout):
            raise McpError(
                f"MCP 启动超时（>{self._connect_timeout:.0f}s）"
            )
        if self._state.error is not None:
            raise McpError(f"MCP 启动失败: {self._state.error}") from self._state.error

    def shutdown(self, timeout: float = 5.0) -> None:
        loop = self._state.loop
        stop_event = self._state.stop_event
        if loop is None or stop_event is None:
            return
        try:
            loop.call_soon_threadsafe(stop_event.set)
        except RuntimeError:
            return
        self._stopped.wait(timeout=timeout)

    # ----- 同步访问接口 ----- #

    def list_tools(self) -> list[McpToolInfo]:
        return list(self._state.tools)

    def call_tool(
        self,
        outer_name: str,
        arguments: dict[str, Any] | None,
        *,
        timeout: float | None = None,
    ) -> str:
        inner = self._state.outer_to_inner.get(outer_name)
        if inner is None:
            raise McpError(f"未知 MCP 工具: {outer_name}")
        loop = self._state.loop
        client = self._state.client
        if loop is None or client is None:
            raise McpError("MCP hub 尚未就绪或已关闭")

        future = asyncio.run_coroutine_threadsafe(
            self._do_call(inner, arguments or {}), loop,
        )
        try:
            return future.result(timeout=timeout or self._default_call_timeout)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise McpError(f"调用 {outer_name} 超时") from exc

    # ----- 内部协程 / 线程 ----- #

    async def _do_call(self, inner: str, args: dict[str, Any]) -> str:
        result = await self._state.client.call_tool(inner, args)
        return _render_call_result(result)

    def _thread_main(self) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            self._state.loop = loop
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._lifecycle())
        except BaseException as exc:  # pragma: no cover - 防御性
            self._state.error = exc
            self._ready.set()
        finally:
            if loop is not None:
                try:
                    loop.close()
                except Exception:  # pragma: no cover - close 失败不影响主流程
                    pass
            self._state.loop = None
            self._stopped.set()

    async def _lifecycle(self) -> None:
        self._state.stop_event = asyncio.Event()
        try:
            if self._roots is not None:
                client = _FastMCPClient(self._source, roots=self._roots)
            else:
                client = _FastMCPClient(self._source)
        except Exception as exc:  # pragma: no cover - 配置非法等
            self._state.error = exc
            self._ready.set()
            return

        try:
            async with client:
                self._state.client = client
                try:
                    await self._collect_tools()
                except Exception as exc:
                    self._state.error = exc
                    self._ready.set()
                    return
                self._ready.set()
                await self._state.stop_event.wait()
        except BaseException as exc:
            if not self._ready.is_set():
                self._state.error = exc
                self._ready.set()
            else:
                logger.warning("MCP hub crashed after start: %s", exc)
        finally:
            self._state.client = None

    async def _collect_tools(self) -> None:
        raw_tools = await self._state.client.list_tools()
        infos: list[McpToolInfo] = []
        outer_to_inner: dict[str, str] = {}
        for t in raw_tools:
            inner = getattr(t, "name", None)
            if not inner:
                continue
            outer = build_outer_tool_name(inner, self._server_names)
            server_name, _ = _split_server_prefix(inner, self._server_names)
            if outer in outer_to_inner and outer_to_inner[outer] != inner:
                logger.warning(
                    "MCP 工具名 sanitize 冲突，跳过: outer=%s 已绑定 %s，新 inner=%s",
                    outer, outer_to_inner[outer], inner,
                )
                continue
            outer_to_inner[outer] = inner
            schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
            infos.append(
                McpToolInfo(
                    inner_name=inner,
                    outer_name=outer,
                    description=(getattr(t, "description", "") or "").strip()
                                or f"MCP tool: {inner}",
                    input_schema=dict(schema),
                    server_name=server_name,
                )
            )
        self._state.tools = infos
        self._state.outer_to_inner = outer_to_inner
