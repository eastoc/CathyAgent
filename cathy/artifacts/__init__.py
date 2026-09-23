"""二进制附件存储。"""

from .base import ArtifactStore
from .local import LocalArtifactStore

__all__ = ["ArtifactStore", "LocalArtifactStore"]
