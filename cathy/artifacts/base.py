"""附件存储抽象；可替换为对象存储或远端数据服务。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..contracts.content import AttachmentRef, AttachmentResolver


@runtime_checkable
class ArtifactStore(AttachmentResolver, Protocol):
    def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str,
        filename: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> AttachmentRef:
        """写入二进制内容并返回稳定引用。"""

    def read_bytes(self, ref: AttachmentRef) -> bytes:
        """读取并校验附件内容。"""
