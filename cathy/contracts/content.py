"""供应商无关的多模态内容与附件契约。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, Union, runtime_checkable

try:
    from typing import TypeAlias
except ImportError:  # Python 3.8
    from typing_extensions import TypeAlias


@dataclass(frozen=True)
class AttachmentRef:
    """对二进制附件的稳定引用；消息中不直接保存大块 base64。"""

    artifact_id: str
    mime_type: str
    sha256: str
    size_bytes: int
    filename: str | None = None
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if not self.artifact_id:
            raise ValueError("attachment.artifact_id 不能为空")
        if not self.mime_type:
            raise ValueError("attachment.mime_type 不能为空")
        if len(self.sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in self.sha256
        ):
            raise ValueError("attachment.sha256 必须是 64 位小写十六进制")
        if self.size_bytes < 0:
            raise ValueError("attachment.size_bytes 不能为负数")
        if self.width is not None and self.width <= 0:
            raise ValueError("attachment.width 必须是正整数")
        if self.height is not None and self.height <= 0:
            raise ValueError("attachment.height 必须是正整数")


@runtime_checkable
class AttachmentResolver(Protocol):
    """模型适配器把附件引用解析为可发送数据时依赖的最小接口。"""

    def data_url(self, ref: AttachmentRef) -> str:
        """返回 ``data:<mime>;base64,...``。"""


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = field(default="text", init=False)


@dataclass(frozen=True)
class ImageBlock:
    attachment: AttachmentRef
    detail: str = "auto"
    source: str | None = None
    captured_at_ns: int | None = None
    frame_id: str | None = None
    type: str = field(default="image", init=False)

    def __post_init__(self) -> None:
        if self.detail not in {"auto", "low", "high", "original"}:
            raise ValueError("image.detail 必须是 auto/low/high/original")
        if not self.attachment.mime_type.startswith("image/"):
            raise ValueError("ImageBlock 的附件 mime_type 必须以 image/ 开头")


@dataclass(frozen=True)
class FileBlock:
    attachment: AttachmentRef
    type: str = field(default="file", init=False)


@dataclass(frozen=True)
class JsonBlock:
    value: Any
    label: str | None = None
    schema_id: str | None = None
    type: str = field(default="json", init=False)

    def __post_init__(self) -> None:
        try:
            json.dumps(self.value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("JsonBlock.value 必须可 JSON 序列化") from exc


ContentBlock: TypeAlias = Union[TextBlock, ImageBlock, FileBlock, JsonBlock]


def attachment_to_dict(ref: AttachmentRef) -> dict[str, Any]:
    out: dict[str, Any] = {
        "artifact_id": ref.artifact_id,
        "mime_type": ref.mime_type,
        "sha256": ref.sha256,
        "size_bytes": ref.size_bytes,
    }
    for key in ("filename", "width", "height"):
        value = getattr(ref, key)
        if value is not None:
            out[key] = value
    return out


def attachment_from_dict(value: Mapping[str, Any]) -> AttachmentRef:
    required = ("artifact_id", "mime_type", "sha256", "size_bytes")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"attachment 缺少字段: {', '.join(missing)}")
    return AttachmentRef(
        artifact_id=str(value.get("artifact_id") or ""),
        mime_type=str(value.get("mime_type") or ""),
        sha256=str(value.get("sha256") or ""),
        size_bytes=int(value.get("size_bytes") or 0),
        filename=(str(value["filename"]) if value.get("filename") is not None else None),
        width=(int(value["width"]) if value.get("width") is not None else None),
        height=(int(value["height"]) if value.get("height") is not None else None),
    )


def content_block_to_dict(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ImageBlock):
        out: dict[str, Any] = {
            "type": "image",
            "attachment": attachment_to_dict(block.attachment),
            "detail": block.detail,
        }
        for key in ("source", "captured_at_ns", "frame_id"):
            value = getattr(block, key)
            if value is not None:
                out[key] = value
        return out
    if isinstance(block, FileBlock):
        return {"type": "file", "attachment": attachment_to_dict(block.attachment)}
    if isinstance(block, JsonBlock):
        out = {"type": "json", "value": block.value}
        if block.label is not None:
            out["label"] = block.label
        if block.schema_id is not None:
            out["schema_id"] = block.schema_id
        return out
    raise TypeError(f"不支持的 ContentBlock: {type(block).__name__}")


def content_block_from_dict(value: Mapping[str, Any]) -> ContentBlock:
    block_type = str(value.get("type") or "")
    if block_type in {"text", "input_text"}:
        return TextBlock(text=str(value.get("text") or ""))
    if block_type == "image":
        attachment = value.get("attachment")
        if not isinstance(attachment, Mapping):
            raise ValueError("image content block 缺少 attachment")
        return ImageBlock(
            attachment=attachment_from_dict(attachment),
            detail=str(value.get("detail") or "auto"),
            source=(str(value["source"]) if value.get("source") is not None else None),
            captured_at_ns=(
                int(value["captured_at_ns"])
                if value.get("captured_at_ns") is not None
                else None
            ),
            frame_id=(str(value["frame_id"]) if value.get("frame_id") is not None else None),
        )
    if block_type == "file":
        attachment = value.get("attachment")
        if not isinstance(attachment, Mapping):
            raise ValueError("file content block 缺少 attachment")
        return FileBlock(attachment=attachment_from_dict(attachment))
    if block_type == "json":
        return JsonBlock(
            value=value.get("value"),
            label=(str(value["label"]) if value.get("label") is not None else None),
            schema_id=(
                str(value["schema_id"]) if value.get("schema_id") is not None else None
            ),
        )
    raise ValueError(f"未知 content block type: {block_type!r}")


def coerce_content_blocks(
    value: str | Mapping[str, Any] | Sequence[ContentBlock | Mapping[str, Any]],
) -> tuple[ContentBlock, ...]:
    if isinstance(value, str):
        return (TextBlock(value),)
    if isinstance(value, Mapping):
        return (content_block_from_dict(value),)
    out: list[ContentBlock] = []
    for item in value:
        if isinstance(item, (TextBlock, ImageBlock, FileBlock, JsonBlock)):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(content_block_from_dict(item))
        else:
            raise TypeError(f"内容块必须是契约对象或映射，实际为 {type(item).__name__}")
    return tuple(out)


def serialize_content_blocks(blocks: Sequence[ContentBlock]) -> list[dict[str, Any]]:
    return [content_block_to_dict(block) for block in blocks]


def deserialize_content_blocks(values: Sequence[Mapping[str, Any]]) -> tuple[ContentBlock, ...]:
    return tuple(content_block_from_dict(value) for value in values)


def content_blocks_to_text(blocks: Sequence[ContentBlock]) -> str:
    """生成日志、旧字段与 Hook 使用的可读文本，不替代原始多模态内容。"""
    chunks: list[str] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            chunks.append(block.text)
        elif isinstance(block, JsonBlock):
            body = json.dumps(block.value, ensure_ascii=False, sort_keys=True)
            chunks.append(f"{block.label}: {body}" if block.label else body)
    return "\n".join(chunk for chunk in chunks if chunk)


def content_blocks_to_model_content(blocks: Sequence[ContentBlock]) -> list[dict[str, Any]]:
    """模型层统一使用内容块列表；供应商 adapter 决定最终线协议。"""
    return serialize_content_blocks(blocks)


def text_content(text: str) -> tuple[ContentBlock, ...]:
    """把文本显式包装为唯一的内部内容表示。"""
    return (TextBlock(text),)


def text_model_content(text: str) -> list[dict[str, Any]]:
    """构造内部 ModelMessage 使用的文本内容块列表。"""
    return serialize_content_blocks(text_content(text))


def collect_attachment_ids(blocks: Sequence[ContentBlock]) -> tuple[str, ...]:
    return tuple(
        block.attachment.artifact_id
        for block in blocks
        if isinstance(block, (ImageBlock, FileBlock))
    )
