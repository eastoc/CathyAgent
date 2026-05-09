"""file_ops 插件实现：read_file / list_dir / write_file。

所有路径都通过 _resolve_in_root() 强制限制在 root 子树内。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cathy.plugins import PluginError, ToolPlugin


class FileOpsPlugin(ToolPlugin):
    def __init__(self) -> None:
        self._root: Path | None = None
        self._workspace_parent: Path | None = None

    def initialize(self, config: dict[str, Any]) -> None:
        # 新配置：workspace_root（推荐）；旧配置：root（兼容）
        parent = Path(
            config.get("workspace_root")
            or config.get("root")
            or "."
        ).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        if not parent.is_dir():
            raise PluginError(f"file_ops.workspace_root 不是有效目录: {parent}")
        self._workspace_parent = parent
        self._root = parent

    def attach_session(self, session_id: str) -> None:
        """切换到会话专属工作空间：<workspace_root>/<session_id>/。"""
        if self._workspace_parent is None:
            raise PluginError("file_ops 未初始化")
        sid = (session_id or "").strip()
        if not sid:
            raise PluginError("session_id 不能为空")
        root = (self._workspace_parent / sid).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._root = root

    def execute(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "read_file":
            return self._read_file(**params)
        if tool_name == "list_dir":
            return self._list_dir(**params)
        if tool_name == "write_file":
            return self._write_file(**params)
        raise PluginError(f"未知工具: {tool_name}")

    # ---------- 实现 ---------- #

    def _read_file(
        self,
        path: str,
        max_bytes: int = 50_000,
        **_: Any,
    ) -> str:
        target = self._resolve_in_root(path)
        if not target.exists():
            raise PluginError(f"文件不存在: {path}")
        if target.is_dir():
            raise PluginError(f"目标是目录而非文件: {path}")

        max_bytes = max(1, int(max_bytes))
        try:
            all_data = target.read_bytes()
        except OSError as exc:
            raise PluginError(f"读取失败: {exc}") from exc

        truncated = len(all_data) > max_bytes
        data = all_data[:max_bytes]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            # 截断恰好砍在多字节字符中间时，逐字节回退到上一个完整 UTF-8 边界
            if truncated:
                text = ""
                for back in range(1, 5):
                    try:
                        text = data[:-back].decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        continue
                if not text:
                    raise PluginError(f"非 UTF-8 文本文件: {exc}") from exc
            else:
                raise PluginError(f"非 UTF-8 文本文件: {exc}") from exc
        suffix = "\n\n[...已截断]" if truncated else ""
        rel = target.relative_to(self._root)  # type: ignore[arg-type]
        return f"# {rel}\n\n{text}{suffix}"

    def _list_dir(
        self,
        path: str = ".",
        show_hidden: bool = False,
        **_: Any,
    ) -> str:
        target = self._resolve_in_root(path or ".")
        if not target.exists():
            raise PluginError(f"目录不存在: {path}")
        if not target.is_dir():
            raise PluginError(f"目标不是目录: {path}")

        rel_root = target.relative_to(self._root)  # type: ignore[arg-type]
        entries: list[str] = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            if not show_hidden and child.name.startswith("."):
                continue
            mark = "/" if child.is_dir() else ""
            try:
                size = "" if child.is_dir() else f"  ({child.stat().st_size}B)"
            except OSError:
                size = ""
            entries.append(f"  {child.name}{mark}{size}")

        if not entries:
            return f"# {rel_root}/\n\n(空目录)"
        return f"# {rel_root}/\n\n" + "\n".join(entries)

    def _write_file(
        self,
        path: str,
        content: str,
        overwrite: bool = True,
        **_: Any,
    ) -> str:
        target = self._resolve_in_root(path)
        if target.exists():
            if target.is_dir():
                raise PluginError(f"目标已存在且是目录: {path}")
            if not overwrite:
                raise PluginError(f"文件已存在且 overwrite=false: {path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise PluginError(f"写入失败: {exc}") from exc

        rel = target.relative_to(self._root)  # type: ignore[arg-type]
        return f"已写入 {rel}（{len(content)} 字符）"

    # ---------- 工具方法 ---------- #

    def _resolve_in_root(self, path: str) -> Path:
        if self._root is None:
            raise PluginError("file_ops 未初始化")
        if not path:
            raise PluginError("path 不能为空")
        try:
            target = (self._root / path).resolve()
        except OSError as exc:
            raise PluginError(f"路径解析失败: {exc}") from exc
        if self._root != target and self._root not in target.parents:
            raise PluginError(f"路径越界（必须在 {self._root} 内）: {path}")
        return target
