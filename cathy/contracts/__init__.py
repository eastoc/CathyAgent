"""CathyAgent 内部稳定接口契约。"""

from .agent import AgentRequest
from .content import (
    AttachmentRef,
    AttachmentResolver,
    ContentBlock,
    FileBlock,
    ImageBlock,
    JsonBlock,
    TextBlock,
)
from .model import (
    ModelClient,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelToolCall,
    ModelTurnState,
)

__all__ = [
    "AgentRequest",
    "AttachmentRef",
    "AttachmentResolver",
    "ContentBlock",
    "FileBlock",
    "ImageBlock",
    "JsonBlock",
    "ModelClient",
    "ModelRequest",
    "ModelResponse",
    "ModelTool",
    "ModelToolCall",
    "ModelTurnState",
    "TextBlock",
]
