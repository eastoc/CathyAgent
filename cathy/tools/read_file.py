"""read_file：受限读文件工具。

Phase 0 仅做最低限度的路径约束（必须位于 root 子树内），
Phase 4 会被沙盒/权限分级机制接管。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Tool, ToolError


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "读取本地文本文件内容。仅允许访问当前工作目录子树。"
        "适用于查看代码、配置或日志。返回 utf-8 文本。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对工作目录的文件路径，例如 README.md 或 cathy/agent.py",
            },
            "max_bytes": {
                "type": "integer",
                "description": "最多读取的字节数，默认 50000",
                "default": 50000,
                "minimum": 1,
            },
        },
        "required": ["path"],
    }

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def execute(self, path: str, max_bytes: int = 50000, **_: Any) -> str:
        if not path:
            raise ToolError("path 不能为空")

        try:
            target = (self._root / path).resolve()
        except OSError as exc:
            raise ToolError(f"路径解析失败: {exc}") from exc

        if self._root != target and self._root not in target.parents:
            raise ToolError(f"路径越界（必须在 {self._root} 内）: {path}")

        if not target.exists():
            raise ToolError(f"文件不存在: {path}")
        if target.is_dir():
            raise ToolError(f"目标是目录而非文件: {path}")

        max_bytes = max(1, int(max_bytes))
        try:
            data = target.read_bytes()[:max_bytes]
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"非 UTF-8 文本文件: {exc}") from exc
        except OSError as exc:
            raise ToolError(f"读取失败: {exc}") from exc

        truncated = target.stat().st_size > max_bytes
        suffix = "\n\n[...已截断]" if truncated else ""
        rel = target.relative_to(self._root)
        return f"# {rel}\n\n{text}{suffix}"
