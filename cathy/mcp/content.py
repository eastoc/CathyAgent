"""把 MCP 多模态结果转换成 Agent 的内容块，不丢弃混合图片/JSON。"""
from __future__ import annotations

import base64
import io
from typing import Any

from cathy.artifacts import LocalArtifactStore
from cathy.contracts import ImageBlock, JsonBlock, TextBlock, ToolResult


def convert_result(result: Any, tool_name: str, artifacts: LocalArtifactStore) -> ToolResult:
    blocks = []
    ids = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", None)
        if kind == "text":
            blocks.append(TextBlock(item.text))
        elif kind == "image":
            from PIL import Image

            if item.mimeType not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("不支持的 MCP 图像格式")
            if len(item.data) > 24_000_000:
                raise ValueError("MCP 图像过大")
            data = base64.b64decode(item.data, validate=True)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > 16_000_000:
                    raise ValueError("MCP 图像像素数过大")
                if Image.MIME.get(image.format) != item.mimeType:
                    raise ValueError("MCP 图像 MIME 与内容不一致")
                image.verify()
            ref = artifacts.put_bytes(data, mime_type=item.mimeType, width=width, height=height)
            blocks.append(ImageBlock(ref, source=tool_name))
            ids.append(ref.artifact_id)
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "data", None)
    if structured is not None:
        blocks.append(JsonBlock(structured, label="mcp_result"))
    failed = bool(getattr(result, "is_error", False))
    return ToolResult(
        call_id="", tool_name=tool_name, status="failed" if failed else "succeeded",
        content=tuple(blocks), artifacts=tuple(ids),
        error_code="mcp_error" if failed else None,
        error_message="MCP 工具执行失败" if failed else None,
    )
