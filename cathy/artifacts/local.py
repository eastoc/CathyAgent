"""基于 SHA-256 的本地内容寻址附件存储。"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from ..contracts.content import AttachmentRef


class LocalArtifactStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self,
        data: bytes,
        *,
        mime_type: str,
        filename: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> AttachmentRef:
        if not isinstance(data, bytes):
            raise TypeError("artifact data 必须是 bytes")
        if not mime_type:
            raise ValueError("mime_type 不能为空")

        digest = hashlib.sha256(data).hexdigest()
        path = self._path_for_digest(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)

        return AttachmentRef(
            artifact_id=f"sha256:{digest}",
            mime_type=mime_type,
            sha256=digest,
            size_bytes=len(data),
            filename=filename,
            width=width,
            height=height,
        )

    def put_file(
        self,
        path: Path | str,
        *,
        mime_type: str,
        width: int | None = None,
        height: int | None = None,
    ) -> AttachmentRef:
        source = Path(path)
        return self.put_bytes(
            source.read_bytes(),
            mime_type=mime_type,
            filename=source.name,
            width=width,
            height=height,
        )

    def read_bytes(self, ref: AttachmentRef) -> bytes:
        expected_id = f"sha256:{ref.sha256}"
        if ref.artifact_id != expected_id:
            raise ValueError("artifact_id 与 sha256 不一致")
        data = self._path_for_digest(ref.sha256).read_bytes()
        if hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError(f"附件校验失败: {ref.artifact_id}")
        return data

    def data_url(self, ref: AttachmentRef) -> str:
        encoded = base64.b64encode(self.read_bytes(ref)).decode("ascii")
        return f"data:{ref.mime_type};base64,{encoded}"

    def _path_for_digest(self, digest: str) -> Path:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("无效的 SHA-256")
        return self.root / "sha256" / digest[:2] / digest
